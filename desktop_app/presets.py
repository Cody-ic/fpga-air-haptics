"""Static previews of the user's selected figure, separate from device feedback."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from .model import SHAPES, trajectory_path


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
            return
        config = self.config
        self.title.set(f"{SHAPES[config.shape]} · 待发送")
        path = trajectory_path(config)[:, :2]
        low, high = path.min(axis=0), path.max(axis=0)
        origin = (low+high)/2
        span = np.maximum((high-low)*1.35, 20)
        scale = min((width-100)/span[0], (height-100)/span[1])
        positions = [(width/2+(x-origin[0])*scale, height/2-(y-origin[1])*scale) for x, y in path]
        c.create_rectangle(35, 25, width-25, height-55, outline="#dce5ef")
        c.create_line(*[value for point in positions for value in point], fill="#2463c5", width=4)
        if np.all(np.ptp(path, axis=0) == 0):
            x, y = positions[0]
            c.create_oval(x-7, y-7, x+7, y+7, fill="#2463c5", outline="")
        c.create_text(width/2, height-30,
                      text=f"宽 {high[0]-low[0]:g} mm · 高 {high[1]-low[1]:g} mm · 显示高度 {config.z_um/1000:g} mm",
                      fill="#526680", font=("Microsoft YaHei UI", 10))
