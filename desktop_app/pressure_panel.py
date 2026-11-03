"""Frozen replay frame -> offline solve -> explicitly requested CONFIG upload."""

import tkinter as tk
import time
from tkinter import ttk

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from .egopressure import counts_to_kpa
from .model import ArraySpec, BUTTON_OUTPUT_SETTINGS, DEFAULT_OUTPUT_LEVEL
from .pressure import compile_pressure
from .theme import DisplayVar, configure_if_changed


class PressureWindow(tk.Toplevel):
    def __init__(self, app, sequence, index, consumer=False):
        super().__init__(app.root)
        self.app = app
        self.consumer = consumer
        self.title("当前帧压力 · 离线生成与发送")
        self.geometry('1300x860' if consumer else '1060x680')
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
        self.level = tk.StringVar(value=str(DEFAULT_OUTPUT_LEVEL))
        self.mod = tk.StringVar(value=str(BUTTON_OUTPUT_SETTINGS['mod_hz']))
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{sequence.name} · 第 {self.frame_id} 帧 · {sequence.seconds[index]:.2f} 秒",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="固定所选帧，最多保留两个主要接触区域；这是相对分布近似，不是接触压力的等值复现。",
                  style="Caption.TLabel").pack(anchor="w", pady=8)
        settings = ttk.Frame(body)
        settings.pack(fill="x")
        self.entries = []
        for label, variable in (("高度 / mm", self.z), ("接触范围 / mm", self.span),
                                ("驱动等级 / %", self.level), ("调制 / Hz", self.mod)):
            if consumer and variable in (self.level, self.mod):
                continue
            ttk.Label(settings, text=label).pack(side="left", padx=(0,5))
            entry = ttk.Entry(settings, textvariable=variable, width=7)
            entry.pack(side="left", padx=(0,14))
            self.entries.append(entry)
            variable.trace_add("write", self.invalidate)
        self.generate_button = ttk.Button(settings, text="重新生成", command=self.generate)
        self.generate_button.pack(side="left")
        self.figure = Figure(figsize=(10,3.5), dpi=100, layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=body)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, pady=12)
        ttk.Label(body, textvariable=self.status, wraplength=1000).pack(anchor="w", fill="x")
        ttk.Label(body, textvariable=self.connection, wraplength=1000).pack(anchor="w", fill="x", pady=8)
        controls = ttk.Frame(body)
        controls.pack(fill="x")
        self.demo_button = ttk.Button(controls, text="连接模拟设备", command=self.connect_demo)
        self.demo_button.pack(side="left")
        self.send_button = ttk.Button(controls, text="发送此帧（不启动）", command=self.send, state="disabled", style="Primary.TButton")
        self.send_button.pack(side="left", padx=8)
        ttk.Button(controls, text="查看播放控制", command=lambda: (app.navigate("playback"), self.destroy())).pack(side="left")
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
            options = dict(z_mm=float(self.z.get()), span_mm=float(self.span.get()),
                           level=DEFAULT_OUTPUT_LEVEL if self.consumer else int(self.level.get()),
                           mod_hz=BUTTON_OUTPUT_SETTINGS['mod_hz'] if self.consumer else int(self.mod.get()))
            self.future = self.app.executor.submit(compile_pressure, self.raw, self.array, **options)
            self.status.set(f"正在生成第 {self.frame_id} 帧 · {self.array.rows}×{self.array.cols} · 间距 {self.array.pitch_um/1000:g} mm…")
            for entry in self.entries:
                entry.configure(state="disabled")
            self.generate_button.configure(state="disabled")
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
        self.figure.colorbar(raw, ax=axes[0], shrink=.65, label="接触压力 / kPa")
        extent = [result.axis_mm[0],result.axis_mm[-1]]*2
        for ax,values in zip(axes[1:], (result.target,result.field)):
            image = ax.imshow(values, origin="lower", extent=extent, cmap="inferno", vmin=0,
                              vmax=max(float(result.target.max()),float(result.field.max())))
            self.figure.colorbar(image, ax=ax, shrink=.65, label="相对声压幅值")
        for ax,title in zip(axes, ("原始压力 · 坐标缩放", "简化目标", "模拟声场 · 量化相位")):
            ax.set(title=title,xlabel="x / mm",ylabel="y / mm",xlim=extent[:2],ylim=extent[2:])
            ax.set_aspect("equal")
        self.canvas.draw_idle()
        fraction = result.mapping['retained_pressure_sum_fraction']*100
        self.status.set(f"第 {self.frame_id} 帧已生成 · {result.metrics['patches']} 个区域 · 保留压力总和 {fraction:.1f}% · "
                        f"{result.array.count} 路 / 64 级相位。模拟峰值为理想中心单焦点的 {result.metrics['field_peak']:.1%}，不代表实测强度。"
                        "预览不含硬件补偿、驱动等级和调制包络。"
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
