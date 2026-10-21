"""独立中文 FPGA 校准窗口。只运行真实连接；界面自检不会连接硬件。"""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from calibrate_fpga import FpgaLink, Settings, calibrate, scope_power
from receiver_client import BleConnection, ReceiverClient, SerialConnection

STAGES = ("背景：全部关闭", "参考通道单独开启", "目标通道单独开启",
          "两路：附加 0°", "两路：附加 90°", "两路：附加 180°",
          "两路：附加 270°", "应用修正后的两路复测")
MODES = ("示波器人工读数", "MG24 自带 USB", "MG24 蓝牙")
ROOT = Path(__file__).resolve().parents[2]


def configure_display():
    if os.name == "nt":
        import ctypes
        with suppress(OSError, AttributeError):
            ctypes.windll.shcore.SetProcessDpiAwareness(1)


class CalibrationWindow:
    def __init__(self, root):
        self.root = root
        root.title("TouchSee · 逐通道实测校准")
        root.tk.call("tk", "scaling", 4 / 3)
        root.geometry(f"{min(1050, root.winfo_screenwidth()-60)}x{min(820, root.winfo_screenheight()-80)}")
        root.minsize(900, 700)
        self.events = queue.Queue()
        self.worker = None
        self.loop = self.task = self.pending = None
        self.cancelled = threading.Event()
        self.result = None
        self.imported = None
        self.current_token = None
        self.mode = tk.StringVar(value=MODES[0])
        self.fpga_port = tk.StringVar()
        self.receiver_port = tk.StringVar()
        self.ble_address = tk.StringVar()
        self.x = tk.StringVar(value="0")
        self.y = tk.StringVar(value="0")
        self.z = tk.StringVar(value="100")
        self.level = tk.StringVar(value="30")
        self.reference = tk.StringVar(value="0")
        self.fixed = tk.BooleanVar()
        self.clean = tk.BooleanVar()
        self.vpp = tk.StringVar()
        self.status = tk.StringVar(value="等待连接。先固定接收头；理论相位不是实测修正。")
        self.step = tk.StringVar(value="尚未开始，输出关闭")
        self.table = tk.StringVar(value="理论相位：等待板端计算\n实测修正：尚无结果")
        self.import_label = tk.StringVar(value="未导入；从零修正开始测量")
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="逐通道相位校准", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="FPGA（现场可编程门阵列）求解并应用修正；ADC（模数转换器）模式由 MG24 采样。",
                  wraplength=870).pack(anchor="w", pady=(3, 10))
        connection = ttk.LabelFrame(outer, text="连接与测点", padding=10)
        connection.pack(fill="x")
        self.editable = []
        for row, label, variable in ((0, "FPGA 串口", self.fpga_port), (1, "MG24 串口（USB 模式）", self.receiver_port)):
            ttk.Label(connection, text=label).grid(row=row, column=0, sticky="w", pady=3)
            widget = ttk.Combobox(connection, textvariable=variable, width=20)
            widget.grid(row=row, column=1, sticky="w", padx=8)
            self.editable.append(widget)
        self.fpga_combo, self.receiver_combo = self.editable
        ttk.Button(connection, text="刷新串口", command=self.refresh_ports).grid(row=0, column=2, sticky="w")
        ttk.Label(connection, text="测量方式").grid(row=0, column=3, sticky="w", padx=(20, 0))
        combo = ttk.Combobox(connection, textvariable=self.mode, values=MODES, state="readonly", width=20)
        combo.grid(row=0, column=4, padx=8)
        self.mode_combo = combo
        ttk.Label(connection, text="蓝牙设备地址").grid(row=1, column=3, sticky="w", padx=(20, 0))
        entry = ttk.Entry(connection, textvariable=self.ble_address, width=23)
        entry.grid(row=1, column=4, padx=8)
        self.editable.append(entry)
        params = ttk.Frame(connection)
        params.grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 2))
        for label, var, width in (("X / mm", self.x, 7), ("Y / mm", self.y, 7), ("Z / mm", self.z, 7),
                                  ("发射等级", self.level, 5), ("参考通道", self.reference, 5)):
            ttk.Label(params, text=label).pack(side="left", padx=(0, 5))
            entry = ttk.Entry(params, textvariable=var, width=width)
            entry.pack(side="left", padx=(0, 14))
            self.editable.append(entry)
        check = ttk.Checkbutton(outer, text="已固定接收头，并核对坐标、通道接线、低增益和供电；本轮保持位置与增益不变", variable=self.fixed)
        check.pack(anchor="w", pady=8)
        self.editable.append(check)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="开始 / 失败后重新开始", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止并关闭输出", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.import_button = ttk.Button(buttons, text="导入实测修正并复验", command=self.load)
        self.import_button.pack(side="left")
        self.clear_button = ttk.Button(buttons, text="清除导入", command=self.clear_import)
        self.clear_button.pack(side="left", padx=8)
        self.save_button = ttk.Button(buttons, text="另存校准文件", command=self.save, state="disabled")
        self.save_button.pack(side="right")
        ttk.Label(outer, textvariable=self.import_label, wraplength=860).pack(anchor="w", pady=(6, 8))
        reading = ttk.LabelFrame(outer, text="当前测量", padding=12)
        reading.pack(fill="x")
        ttk.Label(reading, textvariable=self.step, font=("Microsoft YaHei UI", 12, "bold"), wraplength=840).pack(anchor="w")
        row = ttk.Frame(reading)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="ADCV_P 输出峰峰值 / mV：").pack(side="left")
        self.reading_entry = ttk.Entry(row, textvariable=self.vpp, width=14, state="disabled")
        self.reading_entry.pack(side="left", padx=6)
        self.submit_button = ttk.Button(row, text="提交本次读数", command=self.submit, state="disabled")
        self.submit_button.pack(side="left", padx=8)
        self.clean_check = ttk.Checkbutton(reading, text="波形稳定、近正弦且没有削顶（背景步骤确认无异常即可）", variable=self.clean, state="disabled")
        self.clean_check.pack(anchor="w")
        ttk.Label(reading, text="每次输入上限 120 秒。接收头不随通道移动；信号最强时也应留出量程余量。", wraplength=840).pack(anchor="w", pady=(6, 0))
        ttk.Label(outer, textvariable=self.status, wraplength=860).pack(anchor="w", pady=8)
        ttk.Label(outer, textvariable=self.table, wraplength=860).pack(anchor="w", pady=(0, 8))
        self.log = tk.Text(outer, height=7, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True)
        self.refresh_ports()
        root.after(80, self.drain)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def refresh_ports(self):
        from serial.tools import list_ports
        ports = [item.device for item in list_ports.comports()]
        self.fpga_combo["values"] = ports
        self.receiver_combo["values"] = ports

    def message(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_running(self, running):
        for widget in self.editable:
            widget.configure(state="disabled" if running else "normal")
        self.mode_combo.configure(state="disabled" if running else "readonly")
        for widget in (self.start_button, self.import_button, self.clear_button):
            widget.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self.reading_entry.configure(state="disabled")
            self.submit_button.configure(state="disabled")
            self.clean_check.configure(state="disabled")
            self.current_token = None

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            if not self.fixed.get():
                raise ValueError("请先完成摆放检查并勾选确认")
            if not self.fpga_port.get().strip():
                raise ValueError("请选择 FPGA 串口")
            source = MODES.index(self.mode.get())
            position = tuple(round(float(var.get()) * 1000) for var in (self.x, self.y, self.z))
            settings = Settings(position, int(self.level.get()), int(self.reference.get()))
            settings.validate(256)  # actual count is checked after board handshake
            if source == 1 and not self.receiver_port.get().strip():
                raise ValueError("请选择 MG24 串口")
            if source == 1 and self.fpga_port.get().strip().upper() == self.receiver_port.get().strip().upper():
                raise ValueError("FPGA 与 MG24 不能共用串口")
            if source == 2 and not self.ble_address.get().strip():
                raise ValueError("请填写扫描到的 MG24 蓝牙地址；扫描命令见说明")
            args = dict(fpga=self.fpga_port.get().strip(), receiver=self.receiver_port.get().strip(),
                        ble=self.ble_address.get().strip(), source=source, settings=settings, imported=self.imported)
        except (ValueError, OverflowError) as error:
            messagebox.showerror("请检查设置", str(error), parent=self.root)
            return
        except Exception as error:
            messagebox.showerror("设置无效", str(error), parent=self.root)
            return
        self.result = None
        self.save_button.configure(state="disabled")
        self.table.set("理论相位：等待板端计算\n实测修正：本轮尚未提交")
        self.cancelled.clear()
        self.set_running(True)
        self.status.set("连接中；如果固件宏或协议不匹配，将停止并提示。")
        self.worker = threading.Thread(target=self.work, args=(args,), daemon=True)
        self.worker.start()

    def work(self, args):
        async def session():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            folder = ROOT / ".runtime" / "calibration"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jsonl")
            receiver_connection = link = None
            try:
                with path.open("x", encoding="utf-8") as stream:
                    def record(value):
                        stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), **value), ensure_ascii=False) + "\n")
                        stream.flush()
                        if value.get("type") == "result":
                            result_path = path.with_suffix(".json")
                            with result_path.open("x", encoding="utf-8") as output:
                                json.dump(value["document"], output, ensure_ascii=False, indent=2)
                            self.events.put(dict(type="saved", path=str(result_path)))
                    if self.cancelled.is_set():
                        raise asyncio.CancelledError()
                    if args["source"]:
                        receiver_connection = (SerialConnection(args["receiver"]) if args["source"] == 1
                                               else await BleConnection.connect(args["ble"]))
                    link = FpgaLink(SerialConnection(args["fpga"]))
                    async def manual(status):
                        self.pending = self.loop.create_future()
                        self.events.put(dict(type="manual", **status))
                        try:
                            return await self.pending
                        finally:
                            self.pending = None
                    result = await calibrate(link, ReceiverClient(receiver_connection) if receiver_connection else None,
                                             args["source"] == 2, record, args["settings"],
                                             manual if args["source"] == 0 else None, self.events.put, args["imported"])
            finally:
                if link:
                    await link.close()
                if receiver_connection:
                    await receiver_connection.close()
                self.events.put(dict(type="journal", path=str(path)))
        try:
            asyncio.run(session())
        except asyncio.CancelledError:
            self.events.put(dict(type="error", message="已停止并尝试发送关闭指令；断线时板端心跳超时会关闭输出。"))
        except Exception as error:
            self.events.put(dict(type="error", message=str(error)))
        finally:
            self.loop = self.task = None
            self.events.put(dict(type="idle"))

    def submit(self):
        try:
            value = float(self.vpp.get())
            scope_power(value, self.clean.get())
        except Exception as error:
            messagebox.showerror("读数无效", str(error), parent=self.root)
            return
        token = self.current_token
        self.submit_button.configure(state="disabled")
        self.reading_entry.configure(state="disabled")
        self.clean_check.configure(state="disabled")
        self.current_token = None
        self.status.set("已提交，等待板端确认下一次测量。")
        def deliver():
            if self.pending is not None and not self.pending.done() and token is not None:
                self.pending.set_result(dict(vpp_mv=value, confirmed=True, token=token))
        if self.loop:
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(deliver)

    def stop(self):
        self.cancelled.set()
        self.stop_button.configure(state="disabled")
        if self.loop and self.task:
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self.task.cancel)

    def load(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择实测修正文件（理论表不可导入）", filetypes=[("校准文件", "*.json")])
        if path:
            try:
                doc = json.loads(Path(path).read_text(encoding="utf-8"))
                if doc.get("kind") != "measured_phase_correction" or doc.get("simulated") is not False:
                    raise ValueError("只接受实测修正文件；理论相位和演示数据不能导入")
                self.imported = doc
                self.import_label.set("已选择候选修正，开始后核对参数并逐路复验：" + Path(path).name)
            except Exception as error:
                messagebox.showerror("不能导入", str(error), parent=self.root)

    def clear_import(self):
        self.imported = None
        self.import_label.set("未导入；从零修正开始测量")

    def save(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", filetypes=[("校准文件", "*.json")])
        if path:
            try:
                Path(path).write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding="utf-8")
                self.message("已另存：" + path)
            except OSError as error:
                messagebox.showerror("保存失败", str(error), parent=self.root)

    def drain(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event["type"]
            if kind == "baseline":
                self.table.set("理论相位（板端计算）：" + ", ".join(map(str, event["phases"])) + "\n实测修正：正在测量")
            elif kind in ("measurement", "manual"):
                text = f"参考 {self.reference.get()} / 目标 {event['channel']} · {STAGES[event['stage']]} · 第 {event['repeat'] + 1}/3 次 · 编号 {event['token']}"
                self.step.set(text)
                if kind == "manual":
                    self.current_token = event["token"]
                    self.vpp.set("")
                    self.clean.set(False)
                    self.reading_entry.configure(state="normal")
                    self.clean_check.configure(state="normal")
                    self.submit_button.configure(state="normal")
                    self.reading_entry.focus_set()
                    self.status.set("请读取稳定波形的峰峰值，输入毫伏；本次读数最多等待 120 秒。")
                else:
                    self.status.set("自动采样中。电脑只转发测量值，板端负责求解。")
            elif kind == "complete":
                self.result = event["document"]
                self.save_button.configure(state="normal")
                self.table.set("理论相位：" + ", ".join(map(str, self.result["theoretical_phases"])) +
                               "\n实测修正：" + ", ".join(map(str, self.result["phase_offsets"])))
                self.step.set("全部通道验证通过，输出关闭")
                self.status.set("已交还原有控制流程。关闭本工具串口后，可连接原控制程序；再次校准需复位。")
            elif kind in ("saved", "journal"):
                self.message(("校准文件：" if kind == "saved" else "过程记录：") + event["path"])
            elif kind == "error":
                self.status.set("停止：" + event["message"])
                self.step.set("本轮未完成确认，输出保持关闭；请查看原因")
                self.table.set(self.table.get().split("\n")[0] + "\n实测修正：本轮未完成确认，请按提示核对记录")
                self.message(event["message"])
            elif kind == "idle":
                self.set_running(False)
        self.root.after(80, self.drain)

    def close(self):
        self.stop()
        def finish():
            if self.worker and self.worker.is_alive():
                self.root.after(100, finish)
            else:
                self.root.destroy()
        finish()


def main():
    configure_display()
    root = tk.Tk()
    CalibrationWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
