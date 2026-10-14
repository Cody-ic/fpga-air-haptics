"""Debug-only receiver waveform panel; all widget work stays on the Tk thread."""

import csv
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class ReceiverPanel(ttk.Frame):
    def __init__(self, parent, request):
        super().__init__(parent)
        self.request = request
        self.capture = None
        self.received = 0.0
        self.pending = False
        self.requested = 0.0
        self.available = False
        self.next_capture = 0.0
        self.last_notice = None
        self.continuous = tk.BooleanVar(value=False)
        self.vref = tk.StringVar(value="3300")
        self.divider = tk.StringVar(value="3.4")
        self.notice = tk.StringVar(value="连接支持采样的设备后，将接收板输出接到 Nucleo A0 / PA0，并共地。")
        self.stats = tk.StringVar(value="尚无接收波形。")
        controls = ttk.Frame(self, padding=8)
        controls.pack(fill="x")
        self.capture_button = ttk.Button(controls, text="采集一次", command=self.acquire, state="disabled")
        self.capture_button.pack(side="left")
        self.continuous_check = ttk.Checkbutton(controls, text="连续查看（2 次/秒）", variable=self.continuous)
        self.continuous_check.pack(side="left", padx=8)
        self.export_button = ttk.Button(controls, text="导出波形 CSV", command=self.export, state="disabled")
        self.export_button.pack(side="right")
        settings = ttk.Frame(self, padding=(8, 0))
        settings.pack(fill="x")
        ttk.Label(settings, text="ADC 参考电压 / mV").pack(side="left")
        ttk.Entry(settings, textvariable=self.vref, width=7).pack(side="left", padx=5)
        ttk.Label(settings, text="分压还原倍数").pack(side="left", padx=(8, 0))
        ttk.Entry(settings, textvariable=self.divider, width=5).pack(side="left", padx=5)
        ttk.Button(settings, text="重新计算", command=self.render).pack(side="left")
        ttk.Label(self, textvariable=self.notice, padding=(8, 5), wraplength=780).pack(fill="x")
        ttk.Label(self, textvariable=self.stats, padding=(8, 5), wraplength=780).pack(fill="x")
        self.figure = Figure(figsize=(8, 3.5), facecolor="white")
        self.axis = self.figure.add_subplot(111)
        self.figure.subplots_adjust(left=.12, right=.96, bottom=.18, top=.85)
        self.canvas = FigureCanvasTkAgg(self.figure, self)
        ttk.Label(self, text="每帧仅 0.5 ms，帧间未采样；不代表整个图形。默认 3300 mV 未校准。\n"
                  "×3.4 仅还原末级分压前的等效交流幅值，不换算声压；未报警也不能排除前级削顶。",
                  padding=8, wraplength=780).pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.render()

    def reset(self):
        self.capture = None
        self.received = 0.0
        self.pending = False
        self.last_notice = None
        self.continuous.set(False)
        self.stats.set("尚无接收波形。")
        self.render()

    def acquire(self):
        if not self.available or self.pending:
            return
        if self.request():
            self.pending = True
            self.requested = time.monotonic()
            self.notice.set("正在等待本次采样…")
            self.capture_button.configure(state="disabled")

    def accept(self, capture, when):
        self.capture, self.received = capture, when
        self.pending = False
        self.next_capture = time.monotonic()+.5
        self.last_notice = None
        self.render()

    def reject(self, text):
        self.pending = False
        self.continuous.set(False)
        self.notice.set(f"采样未完成：{text}。图中旧数据不会冒充新结果。")
        self.last_notice = "error"

    def tick(self, ready, capable, visible, blocked=False):
        now = time.monotonic()
        self.available = bool(ready and capable and not blocked)
        if not ready:
            self.pending = False
            self.continuous.set(False)
        if self.pending and now-self.requested > 3:
            self.reject("等待超时")
        self.capture_button.configure(state="normal" if self.available and not self.pending else "disabled")
        self.continuous_check.configure(state="normal" if ready and capable else "disabled")
        self.export_button.configure(state="normal" if self.capture else "disabled")
        if not self.pending and self.last_notice != "error":
            if not ready:
                notice = "未连接或状态已过期；图中若有波形，仅为历史记录。"
            elif not capable:
                notice = "当前固件不支持接收采样，请使用带 ADC 采集功能的固件。"
            elif self.capture:
                source = "Demo 合成数据 · 非测量" if self.capture.simulated else "板端 ADC 采样 · 未做声压校准"
                notice = f"{source} · 距上次采样 {now-self.received:.1f} s · 400 kS/s，200 点"
            else:
                notice = "点击采集一次。真实连接需将接收板 ADCV_P 接到 A0 / PA0，并共地；悬空输入无测量意义。"
            if notice != self.last_notice:
                self.notice.set(notice)
                self.last_notice = notice
        if self.continuous.get() and visible and self.available and not self.pending and now >= self.next_capture:
            self.acquire()

    def analysis(self):
        return self.capture.analyze(float(self.vref.get()), float(self.divider.get()))

    def render(self):
        if self.capture:
            try:
                result = self.analysis()
            except (ValueError, OverflowError) as error:
                self.stats.set(f"参数无效：{error}")
                return
        self.axis.clear()
        self.axis.set(xlabel="时间 / µs", ylabel="ADCV_P / mV")
        if not self.capture:
            self.axis.set_title("等待采样")
            self.axis.set_ylim(0, 1100)
        else:
            x = [i*1e6/self.capture.fs_hz for i in range(len(self.capture.raw))]
            self.axis.plot(x, result["mv"], color="#2463c5", lw=1, label="ADC 原始波形")
            self.axis.plot(x, [v+result["mean_mv"] for v in result["filtered_mv"]],
                           color="#c26119", lw=1.2, alpha=.85, label="40 kHz 分量 + 均值")
            self.axis.legend(loc="upper right", fontsize=8)
            self.axis.set_title("Demo 合成波形 · 非实测" if self.capture.simulated else "接收电压采样 · 非声场图")
            self.axis.set_ylim(min(0, min(result["mv"])-50), max(1100, max(result["mv"])+50))
            clip = " · ADC 接近电源轨，检查削顶" if result["adc_rail"] else ""
            self.stats.set(f"直流 {result['mean_mv']:.1f} mV · 峰峰值 {result['pp_mv']:.1f} mV · "
                           f"交流 RMS {result['rms_mv']:.1f} mV\n"
                           f"40 kHz 峰值 {result['peak40_mv']:.1f} mV · 分压前等效峰值 "
                           f"{result['stage2_peak40_mv']:.1f} mV{clip}")
        self.axis.grid(alpha=.2)
        self.canvas.draw_idle()

    def save_csv(self, path):
        if self.capture is None:
            raise ValueError("尚无波形")
        result = self.analysis()
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["source", "boot", "rev", "uptime_ms", "tx_running_at_start", "pin",
                             "fs_hz", "vref_mv", "divider", "index", "time_us", "adc_code",
                             "adc_mv", "ac_mv", "fundamental40_mv"])
            for i, raw in enumerate(self.capture.raw):
                writer.writerow(["DEMO_SYNTHETIC" if self.capture.simulated else "ADC_UNCALIBRATED",
                                 self.capture.boot, self.capture.revision, self.capture.uptime_ms,
                                 int(self.capture.tx_running), self.capture.pin, self.capture.fs_hz,
                                 self.vref.get(), self.divider.get(), i, i*1e6/self.capture.fs_hz, raw,
                                 result["mv"][i], result["ac_mv"][i], result["filtered_mv"][i]])

    def export(self):
        if self.capture is None:
            return
        prefix = "demo-synthetic" if self.capture.simulated else "adc-capture"
        path = filedialog.asksaveasfilename(parent=self, initialfile=prefix+".csv",
                                          defaultextension=".csv", filetypes=[("波形 CSV", "*.csv")])
        if path:
            try:
                self.save_csv(path)
            except (OSError, ValueError) as error:
                messagebox.showerror("导出失败", str(error), parent=self)
