"""Static previews of the user's selected figure, separate from device feedback."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from .model import SHAPES, trajectory_path, trajectory_paths
from .hand_view import HandView


class PresetPreview(ttk.Frame):
    def __init__(self, parent, on_apply):
        super().__init__(parent, padding=12)
        self.config = None
        self.title = tk.StringVar(value="选择基本图形")
        ttk.Label(self, textvariable=self.title, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self, text="这是待发送的图形。可在左侧调整大小、位置和显示高度。",
                  style="Muted.TLabel", wraplength=620).pack(anchor="w", pady=8)
        self.apply_button = ttk.Button(self, text="发送到设备", style="Primary.TButton", command=on_apply)
        self.apply_button.pack(side="bottom", anchor="w", pady=(10, 0))
        self.dimensions = tk.StringVar()
        ttk.Label(self, textvariable=self.dimensions, style="Muted.TLabel").pack(side="bottom", pady=(6, 0))
        self.canvas = tk.Canvas(self, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.draw())

    def set_config(self, config):
        if config != self.config:
            self.config = config
            self.draw()

    def draw(self):
        c = self.canvas
        c.delete("all")
        width, height = max(c.winfo_width(), 260), max(c.winfo_height(), 180)
        if self.config is None:
            self.title.set("请检查图形参数")
            self.dimensions.set("")
            return
        config = self.config
        self.title.set(f"{SHAPES[config.shape]} · 待发送")
        path = trajectory_path(config)[:, :2]
        low, high = np.nanmin(path, axis=0), np.nanmax(path, axis=0)
        self.view = HandView(c, path)
        self.view.draw()
        for stroke in trajectory_paths(config):
            positions = [self.view.screen(point[:2]) for point in stroke]
            if len(positions) > 1:
                c.create_line(*[value for point in positions for value in point], fill="#2463c5", width=3, tags='preview_path')
            if len(positions) == 1 or np.all(np.ptp(stroke, axis=0) == 0):
                x, y = positions[0]
                c.create_oval(x-7, y-7, x+7, y+7, fill="#2463c5", outline="", tags='preview_path')
        self.dimensions.set(f"宽 {high[0]-low[0]:g} mm · 高 {high[1]-low[1]:g} mm · 显示高度 {config.z_um/1000:g} mm")
