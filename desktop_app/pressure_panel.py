"""Frozen replay frame -> offline solve -> explicitly requested CONFIG upload."""

import tkinter as tk
import time
from tkinter import ttk

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from .egopressure import counts_to_kpa
from .model import ArraySpec, BUTTON_OUTPUT_SETTINGS, DEFAULT_OUTPUT_LEVEL
from .pressure import compile_pressure
from .theme import DisplayVar, configure_if_changed, wrap_to_width

PRESSURE_MODES = {"双点增强（集中能量）": "focus", "区域还原（原算法）": "regions"}


class PressureWindow(tk.Toplevel):
    def __init__(self, app, sequence, index):
        super().__init__(app.root)
        self.app = app
        self.title("当前帧压力 · 离线生成与发送")
        density = max(1., float(self.tk.call('tk', 'scaling')) / (96/72))
        width = min(round(1120*density), self.winfo_screenwidth()-60)
        height = min(round(820*density), self.winfo_screenheight()-120)
        self.geometry(f"{width}x{height}+{max(0,(self.winfo_screenwidth()-width)//2)}+40")
        self.minsize(min(width, round(900*density)), min(height, round(720*density)))
        self.transient(app.root)
        self.result = None
        self.submitted = False
        self.future = None
        self.closed = False
        # Copy now: replay can seek or load another sequence while solving.
        self.raw = counts_to_kpa(sequence.force[index]).copy()
        self.frame_id = int(sequence.frames[index])
        self.source_name = sequence.name
        self.status = DisplayVar(value="")
        self.connection = DisplayVar(value="")
        self.z = tk.StringVar(value="50")
        self.span = tk.StringVar(value="40")
        self.spacing = tk.StringVar(value="35")
        self.mode = tk.StringVar(value="双点增强（集中能量）")
        self.level = tk.StringVar(value=str(DEFAULT_OUTPUT_LEVEL))
        self.mod = tk.StringVar(value=str(BUTTON_OUTPUT_SETTINGS['mod_hz']))
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew")
        title = ttk.Label(header, text=f"第 {self.frame_id} 帧 · {sequence.seconds[index]:.2f} 秒 · 压力单帧",
                          style="Heading.TLabel")
        title.pack(fill="x")
        caption = ttk.Label(header, text=f"{sequence.name}\n最多保留两个主要区域；相对分布近似，不是接触压力的等值复现。",
                            style="Caption.TLabel")
        caption.pack(fill="x", pady=(4, 8))
        wrap_to_width(caption)
        settings = ttk.Frame(body)
        settings.grid(row=1, column=0, sticky="ew")
        for col in (1,3):
            settings.columnconfigure(col, weight=1)
        self.entries = []
        for i, (label, variable) in enumerate((("高度 / mm", self.z), ("原始映射范围 / mm", self.span),
                                ("双区域中心间距 / mm", self.spacing), ("驱动等级 / %", self.level),
                                ("调制 / Hz", self.mod))):
            row, col = divmod(i, 2)
            ttk.Label(settings, text=label).grid(row=row, column=2*col, sticky="w", padx=(0,8), pady=3)
            entry = ttk.Entry(settings, textvariable=variable, width=7)
            entry.grid(row=row, column=2*col+1, sticky="ew", padx=(0,16), pady=3)
            self.entries.append(entry)
            variable.trace_add("write", self.invalidate)
        ttk.Label(settings, text="生成方式").grid(row=2, column=2, sticky="w")
        self.mode_picker = ttk.Combobox(settings, textvariable=self.mode, values=list(PRESSURE_MODES), state="readonly", width=22)
        self.mode_picker.grid(row=2, column=3, sticky="ew", padx=(0,16))
        self.mode.trace_add("write", self.invalidate)
        actions = ttk.Frame(settings)
        actions.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6,0))
        self.original_button = ttk.Button(actions, text="恢复原间距", command=lambda:self.spacing.set("0"))
        self.original_button.pack(side="left", padx=(0,8))
        self.generate_button = ttk.Button(actions, text="① 重新生成", command=self.generate)
        self.generate_button.pack(side="left")
        hint = ttk.Label(body, text="双点增强将区域收紧为焦点，舍弃区域形状；原算法保留宽区域。间距填 0 恢复原值。修改后请重新生成。",
                         style="Caption.TLabel")
        hint.grid(row=2, column=0, sticky="ew", pady=(6,0))
        wrap_to_width(hint)
        self.figure = Figure(figsize=(10,3.5), dpi=100*density, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=body)
        self.canvas.get_tk_widget().grid(row=3, column=0, sticky="nsew", pady=8)
        self.status_label = ttk.Label(body, textvariable=self.status)
        self.status_label.grid(row=4, column=0, sticky="ew")
        self.connection_label = ttk.Label(body, textvariable=self.connection)
        self.connection_label.grid(row=5, column=0, sticky="ew", pady=8)
        wrap_to_width(self.status_label)
        wrap_to_width(self.connection_label)
        controls = ttk.Frame(body)
        controls.grid(row=6, column=0, sticky="ew")
        self.demo_button = ttk.Button(controls, text="连接模拟设备", command=self.connect_demo)
        self.demo_button.pack(side="left")
        self.send_button = ttk.Button(controls, text="② 发送此帧（不启动）", command=self.send, state="disabled", style="Primary.TButton")
        self.send_button.pack(side="left", padx=8)
        self.playback_button = ttk.Button(controls, text="③ 查看播放控制", command=lambda: (app.navigate("playback"), self.destroy()))
        self.playback_button.pack(side="left")
        ttk.Button(controls, text="关闭", command=self.destroy).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.generate()
        self.tick_id = self.after(100, self.tick)

    def invalidate(self, *_):
        self.result = None
        self.submitted = False
        self.status.set("参数已变更，请重新生成。")
        self.send_button.configure(state="disabled")

    def generate(self):
        if self.future is not None:
            return
        self.result = None
        self.submitted = False
        try:
            self.array = self.app.hardware if self.app.ready else ArraySpec(8,8,10300)
            options = dict(z_mm=float(self.z.get()), span_mm=float(self.span.get()), centre_spacing_mm=float(self.spacing.get()),
                           level=int(self.level.get()), mod_hz=int(self.mod.get()), mode=PRESSURE_MODES[self.mode.get()])
            self.future = self.app.executor.submit(compile_pressure, self.raw, self.array, **options)
            self.status.set(f"正在生成第 {self.frame_id} 帧 · {self.array.rows}×{self.array.cols} · 间距 {self.array.pitch_um/1000:g} mm…")
            for entry in self.entries:
                entry.configure(state="disabled")
            self.generate_button.configure(state="disabled")
            self.original_button.configure(state="disabled")
            self.mode_picker.configure(state="disabled")
            self.send_button.configure(state="disabled")
        except (ValueError, OverflowError) as error:
            self.status.set(f"无法生成：{error}")

    def connect_demo(self):
        app = self.app
        if app.session and app.session.is_alive():
            return
        app.mode_choice.set("Demo 模拟设备")
        app.geometry_profile = None
        app.demo_array_choice.set("8 × 8 · 64 路")
        app.hw_rows.set("8")
        app.hw_cols.set("8")
        app.hw_pitch.set("10.3")
        app.connect()

    def send(self):
        if self.result is None:
            return
        issue = self.app.pressure_send_issue(self.result)
        if issue:
            self.connection.set(issue)
            return
        self.app.send("CONFIG", **self.result.config.wire(), **self.result.array.wire())
        self.submitted = True

    def render(self):
        result = self.result
        self.figure.clear()
        axes = self.figure.subplots(1,3)
        raw = axes[0].pcolormesh(*result.raw_edges, result.raw_kpa, cmap="inferno", vmin=0,
                                vmax=max(1,float(result.raw_kpa.max())), shading="flat")
        axes[0].set_facecolor("#e5e5e5")
        axes[0].text(.5,.02,"灰色：传感器范围外",transform=axes[0].transAxes,ha="center",fontsize=8)
        raw_bar = self.figure.colorbar(raw, ax=axes[0], shrink=.65, location="bottom", pad=.12)
        raw_bar.set_label("接触压力 / kPa", fontsize=9)
        raw_bar.ax.tick_params(labelsize=8)
        extent = [result.axis_mm[0],result.axis_mm[-1]]*2
        for ax,values in zip(axes[1:], (result.target,result.field)):
            image = ax.imshow(values, origin="lower", extent=extent, cmap="inferno", vmin=0,
                              vmax=max(float(result.target.max()),float(result.field.max())))
            for i, (x,y) in enumerate(result.mapping['centres_mm'], 1):
                ax.plot(x,y, '+', color='#5eead4', markersize=6)
                ax.annotate(str(i),(x,y),xytext=(5,5),textcoords='offset points',color='#5eead4',fontsize=8)
        field_bar = self.figure.colorbar(image, ax=list(axes[1:]), shrink=.65, location="bottom", pad=.12)
        field_bar.set_label("相对声压幅值 · 十字为目标中心", fontsize=9)
        field_bar.ax.tick_params(labelsize=8)
        focused = result.metrics['mode'] == 'focus'
        for ax,title in zip(axes, ("原始压力（未拉开）", "目标焦点示意" if focused else "调整后的目标", "模拟声场")):
            ax.set(xlim=extent[:2],ylim=extent[2:])
            ax.set_title(title,fontsize=11)
            ax.set_xlabel("x / mm",fontsize=9)
            ax.set_ylabel("y / mm",fontsize=9)
            ax.tick_params(labelsize=8)
            ax.set_aspect("equal")
        self.canvas.draw_idle()
        fraction = result.mapping['retained_pressure_sum_fraction']*100
        spacing = (f"中心间距 {result.mapping['original_centre_spacing_mm']:.1f} → {result.mapping['centre_spacing_mm']:.1f} mm"
                   if result.metrics['patches']==2 else "单区域，中心间距不适用")
        centres = " / ".join(f"{v:.1%}" for v in result.metrics['centre_amplitudes'])
        self.status.set(f"{'双点增强' if focused else '区域还原'}已生成 · {spacing} · 保留源数据压力总和 {fraction:.1f}%\n"
                        f"目标中心模拟幅值 {centres}（相对理想中心单焦点）；不代表实测，未计入硬件补偿、等级和调制。"
                        + (" 求解未收敛，当前为近似解。" if not result.metrics['converged'] else ""))

    def tick(self):
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            try:
                self.result = future.result()
                self.render()
            except Exception as error:
                self.result = None
                self.status.set(f"生成失败，未发送：{error}")
            for entry in self.entries:
                entry.configure(state="normal")
            self.generate_button.configure(state="normal")
            self.original_button.configure(state="normal")
            self.mode_picker.configure(state="readonly")
        app = self.app
        issue = app.pressure_send_issue(self.result) if self.result else "请先生成有效压力帧。"
        configure_if_changed(self.send_button, state="disabled" if issue else "normal")
        configure_if_changed(self.demo_button, state="disabled" if app.session and app.session.is_alive() else "normal")
        confirmed = (app.ready and time.monotonic()-app.received < 1.6
                     and self.result is not None and app.config_confirmed()
                     and app.state.config == self.result.config and app.state.array == self.result.array)
        if confirmed:
            self.connection.set("模拟设备已确认此帧。" if app.state.simulated else "设备数字回读已确认此帧。")
        elif app.busy:
            self.connection.set(app.status_line.get())
        elif self.submitted:
            self.connection.set("此帧未获确认：" + app.status_line.get())
        else:
            self.connection.set(issue or "可发送此帧；发送不会启动输出，确认后在播放页单独播放。")
        self.tick_id = self.after(100, self.tick)

    def destroy(self):
        if self.closed:
            return
        self.closed = True
        if hasattr(self, "tick_id"):
            self.after_cancel(self.tick_id)
        if self.future is not None:
            self.future.cancel()
        super().destroy()
