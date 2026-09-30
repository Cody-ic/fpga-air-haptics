"""Debug-only fixed-receiver calibration controls; I/O remains in workers."""
import json
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .calibration import CalibrationRunner


class CalibrationPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=16)
        self.app = app
        self.runner = None
        self.result = None
        self.processed = True
        self.variables = [tk.StringVar(value=v) for v in ('0', '0', '100', '0')]
        ttk.Label(self, text='固定接收器 · 逐路相位校准', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(self, wraplength=760, text='将接收换能器固定在已知位置，朝向阵列；测量期间保持位置和接收增益不变。'
                  '坐标原点与阵列文件一致，平面夹具以发声面中心为原点，z 正方向朝手掌。'
                  '校准会暂时输出连续 40 kHz 测试信号；结束后恢复原图形并保持停止。').pack(anchor='w', pady=12)
        row = ttk.Frame(self)
        row.pack(anchor='w')
        for label, variable in zip(('x / mm', 'y / mm', 'z / mm', '参考通道（从 0 起）'), self.variables):
            ttk.Label(row, text=label).pack(side='left', padx=(8, 3))
            ttk.Entry(row, textvariable=variable, width=8).pack(side='left')
        buttons = ttk.Frame(self)
        buttons.pack(anchor='w', pady=16)
        self.start_button = ttk.Button(buttons, text='开始校准', command=self.start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(buttons, text='取消并停止', command=self.cancel)
        self.cancel_button.pack(side='left', padx=8)
        self.save_button = ttk.Button(buttons, text='保存校准文件', command=self.save)
        self.save_button.pack(side='left')
        self.load_button = ttk.Button(buttons, text='导入并应用校准', command=self.load)
        self.load_button.pack(side='left', padx=8)
        self.status = tk.StringVar(value='连接设备后可校准。Demo 使用合成通道误差演示流程。')
        ttk.Label(self, textvariable=self.status, wraplength=760).pack(anchor='w', pady=12)
        ttk.Label(self, wraplength=760, text='修正值存于板端 RAM，复位后需重新导入。文件绑定阵列标识，Demo 文件不能用于真实设备。'
                  '这是固定测点的相对相位修正，不是自动测绘实际阵元位置，也不能替代多位置声场测量。'
                  '朝向、反射和接收电路失真仍会影响结果；没有硬件实测前仅验证软件流程。').pack(anchor='w', pady=12)

    @property
    def busy(self):
        return self.runner is not None and self.runner.is_alive()

    def start(self, document=None):
        if self.busy or self.app.busy or self.app.await_revision is not None:
            return
        try:
            position = tuple(float(v.get()) for v in self.variables[:3])
            reference = int(self.variables[3].get())
            if not self.app.session or not self.app.ready:
                raise ValueError('请先连接设备')
            if any(p.verb != 'PING' for p in self.app.session.pending.values()):
                raise ValueError('请等待当前采样或命令完成')
            self.app.receiver_panel.continuous.set(False)
            self.runner = CalibrationRunner(self.app.session, position, reference, apply_document=document)
            self.app.confirmed_config = None
            self.processed = False
            self.runner.start()
            self.status.set('正在核对设备并准备测量…' if document is None else '正在应用校准并核对回读…')
        except (ValueError, AttributeError) as error:
            messagebox.showerror('无法开始校准', str(error), parent=self)

    def cancel(self):
        if self.busy:
            self.runner.cancel()
            self.status.set('正在停止并恢复原图形…')

    def tick(self, allowed):
        if self.runner:
            while True:
                try:
                    self.status.set(('Demo · ' if self.runner.session.is_demo else '接收板实测 · ')+self.runner.progress.get_nowait())
                except queue.Empty:
                    break
            if not self.busy and not self.processed:
                self.processed = True
                if self.runner.error:
                    self.status.set(self.runner.error)
                    self.app.status_line.set(self.runner.error)
                else:
                    self.result = self.runner.result
                    self.status.set(('Demo 校准完成（合成数据）' if self.result['simulated'] else '相位校准已应用')+
                                    '。设备保持停止，可保存文件；重新发送图形后播放。')
                    self.app.status_line.set('校准已确认，设备保持停止。')
        for b in (self.start_button, self.load_button):
            b.configure(state='normal' if allowed and not self.busy else 'disabled')
        self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        self.save_button.configure(state='normal' if self.result and not self.busy else 'disabled')

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', initialfile='array-calibration.json', filetypes=[('校准文件', '*.json')])
        if path and self.result:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(self.result, f, ensure_ascii=False, indent=2)
                    f.write('\n')
            except OSError as error:
                messagebox.showerror('保存失败', str(error), parent=self)

    def load(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('校准文件', '*.json')])
        if path:
            try:
                with open(path, encoding='utf-8-sig') as f:
                    document = json.load(f)
                if not isinstance(document, dict):
                    raise ValueError('校准文件格式无效')
                self.start(document)
            except (OSError, ValueError) as error:
                messagebox.showerror('读取失败', str(error), parent=self)
