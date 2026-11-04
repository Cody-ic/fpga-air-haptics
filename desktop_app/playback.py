"""User-facing playback driven exclusively by received device snapshots."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from .model import SHAPES, trajectory_path, trajectory_paths
from .hand_view import HandView
from .theme import wrap_to_width, DisplayVar, configure_if_changed


class PlaybackPanel(ttk.Frame):
    def __init__(self, parent, on_play, on_pause, on_stop, on_edit):
        super().__init__(parent, padding=12)
        self.snapshot = None
        self.fresh = False
        self.path = None
        self.plot_config = None
        self.marker = None
        self.halo = None
        self.position_mm = None
        self.heading = DisplayVar(value="等待发送图形")
        self.message = DisplayVar(value="发送成功后，这里会显示设备收到的图形。")
        self.source = DisplayVar(value="尚未连接设备")
        self.feedback = DisplayVar(value="请先连接设备，再发送图形。")
        ttk.Label(self, textvariable=self.heading, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.source, style="Muted.TLabel").pack(anchor="w", pady=(5, 8))
        self.notice = tk.Label(self, textvariable=self.feedback, bg="#e0e9f6", fg="#1d1d1f",
                               anchor="w", padx=12, pady=10, font=("Microsoft YaHei UI", 10),
                               wraplength=650)
        self.notice.pack(fill="x")
        controls = ttk.Frame(self)
        controls.pack(side="bottom", fill="x", pady=(8, 0))
        self.play_button = ttk.Button(controls, text="播放", style="Primary.TButton", command=on_play)
        self.pause_button = ttk.Button(controls, text="暂停", command=on_pause)
        self.stop_button = ttk.Button(controls, text="停止", style="Stop.TButton", command=on_stop)
        for button in (self.play_button, self.pause_button, self.stop_button):
            button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="返回编辑", command=on_edit).pack(side="right")
        ttk.Label(self, textvariable=self.message, style="Muted.TLabel", wraplength=650).pack(
            side="bottom", anchor="w", pady=(8, 0))
        self.canvas = tk.Canvas(self, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(10, 0))
        self.canvas.bind("<Configure>", lambda _: self.draw())
        wrap_to_width(self.notice, 24)
        for child in self.winfo_children():
            if isinstance(child, ttk.Label):
                child.pack_configure(fill='x')
                wrap_to_width(child)

    def update_state(self, state, fresh, feedback, connection_label="串口"):
        self.snapshot, self.fresh = state, fresh
        self.feedback.set(feedback)
        configure_if_changed(self.notice, bg="#e5f3ee" if fresh else "#fff0d5")
        if state is None:
            self.source.set("尚未收到设备图形")
            self.heading.set("等待发送图形")
            self.message.set("发送成功后，这里会显示设备收到的图形。")
        else:
            self.source.set("Demo · 模拟设备反馈" if state.simulated else f"设备反馈 · {connection_label}")
            status = {"IDLE": "已停止", "RUNNING": "正在播放", "PAUSED": "已暂停", "FAULT": "设备故障"}[state.state]
            static = state.config.phase_table != "NONE"
            self.heading.set(f"{status if fresh else '状态未知'} · {'压力单帧' if static else SHAPES[state.config.shape]}")
            if not fresh:
                self.message.set("未收到最新反馈，当前位置不再显示。")
            elif state.state == "RUNNING" and state.output:
                stationary = state.config.shape == "POINT" or (state.config.shape == "CUSTOM"
                              and len(state.config.points_um()) == 1) or (state.config.shape != "CUSTOM"
                              and state.config.radius_um == 0)
                self.message.set("亮点表示正在呈现的固定位置。" if stationary else "亮点表示设备返回的当前位置。")
            elif state.state == "RUNNING":
                self.message.set("正在切换线段，此时不输出触觉。" if not state.scan_on else "正在运行，但设备报告输出已关闭。")
            elif state.state == "PAUSED":
                self.message.set("已暂停，橙色圆点保留暂停位置。" if state.scan_on else "已暂停在线段切换期间，此时没有触觉输出。")
            elif state.state == "FAULT":
                self.message.set("设备报告故障，请停止并检查设备。")
            else:
                self.message.set("轮廓是设备当前保存的图形，点击播放后查看位置变化。")
            if static and fresh:
                self.message.set("设备保存了完整静态相位表；没有移动焦点。声场预测请查看压力帧窗口。")
        config = state.config if state else None
        if config != self.plot_config:
            self.plot_config = config
            self.path = trajectory_path(config)[:, :2] if config and config.phase_table == "NONE" else None
            self.draw()
        else:
            self.draw_marker()

    def screen(self, point):
        return self.view.screen(point)

    def draw(self):
        c = self.canvas
        c.delete("all")
        self.marker = self.halo = None
        self.view = HandView(c, self.path)
        self.view.draw()
        width, height = max(c.winfo_width(), 260), max(c.winfo_height(), 180)
        if self.path is None:
            static = self.plot_config and self.plot_config.phase_table != "NONE"
            c.create_text(width/2, height-8, text="静态压力分布 · 无单焦点轨迹" if static else "发送图形后在这里查看播放", fill="#6e6e73",
                          font=("Microsoft YaHei UI", 13))
            return
        for stroke in trajectory_paths(self.plot_config):
            locations = [self.screen(point[:2]) for point in stroke]
            if len(locations) > 1:
                c.create_line(*[value for pair in locations for value in pair], fill="#0071e3", width=3, tags="device_path")
            if len(locations) == 1 or np.all(np.ptp(stroke, axis=0) == 0):
                x, y = locations[0]
                c.create_oval(x-5, y-5, x+5, y+5, fill="#9abce2", outline="", tags="device_path")
        self.halo = c.create_oval(0, 0, 0, 0, outline="#77cebe", width=3, state="hidden")
        self.marker = c.create_oval(0, 0, 0, 0, outline="white", width=2, state="hidden", tags="position")
        self.draw_marker()

    def draw_marker(self):
        state = self.snapshot
        visible = bool(self.fresh and state and state.config.phase_table == "NONE" and
                       ((state.state == "PAUSED" and state.scan_on) or (state.state == "RUNNING" and state.output)))
        self.position_mm = tuple(state.focus_mm[:2]) if visible else None
        if self.marker is None:
            return
        if not visible:
            self.canvas.itemconfigure(self.marker, state="hidden")
            self.canvas.itemconfigure(self.halo, state="hidden")
            return
        x, y = self.screen(self.position_mm)
        paused = state.state == "PAUSED"
        self.canvas.coords(self.marker, x-8, y-8, x+8, y+8)
        self.canvas.coords(self.halo, x-15, y-15, x+15, y+15)
        self.canvas.itemconfigure(self.marker, state="normal", fill="#ba7a11" if paused else "#008e7d")
        self.canvas.itemconfigure(self.halo, state="hidden" if paused else "normal")
