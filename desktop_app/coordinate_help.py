"""A dimensioned top view of the fixed overhead measurement fixture."""
import tkinter as tk
from tkinter import ttk


def show(parent, frame):
    window = tk.Toplevel(parent)
    window.title("底面坐标与接收器定位")
    canvas = tk.Canvas(window, width=740, height=570, bg="white", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    scale, left, bottom = 2.25, 70, 490

    def xy(x, y):
        return left+x*scale, bottom-y*scale

    def box(x1, y1, x2, y2, color):
        canvas.create_rectangle(*xy(x1, y1), *xy(x2, y2), fill=color, outline="#52677c")

    box(0, 0, 44, 188, "#e0e7ee")
    box(200, 0, 244, 188, "#e0e7ee")
    box(98.727, 73.872, 174.727, 128.491, "#dcefe9")
    box(106.727, 83.872, 166.727, 118.491, "#a9d4c5")
    canvas.create_line(*xy(0, 0), *xy(270, 0), arrow="last", width=2)
    canvas.create_line(*xy(0, 0), *xy(0, 210), arrow="last", width=2)
    canvas.create_text(*xy(266, -8), text="X 向右 / mm")
    canvas.create_text(*xy(20, 211), text="Y 向后 / mm")
    canvas.create_text(*xy(6, -13), text="O (0,0) 左底座左前外角", anchor="w")
    canvas.create_text(*xy(122, -29), text="操作者站在这一侧；桌面 Z=0，向上为正")
    canvas.create_text(*xy(22, 168), text="左底座")
    canvas.create_text(*xy(222, 168), text="右底座")
    for label, x, y, anchor in (
        ("A 托盘左前", 98.727, 73.872, "ne"),
        ("B 右前", 174.727, 73.872, "nw"),
        ("C 右后", 174.727, 128.491, "sw"),
        ("D 左后", 98.727, 128.491, "se")):
        px, py = xy(x, y)
        canvas.create_oval(px-3, py-3, px+3, py+3, fill="#bd5b30")
        canvas.create_text(px, py+(8 if y < 100 else -8), anchor=anchor,
                           text=f"{label}\n({x:.3f}, {y:.3f})")
    rx, ry = xy(frame.center_x_mm, frame.center_y_mm)
    canvas.create_oval(rx-5, ry-5, rx+5, ry+5, fill="#ce4937")
    canvas.create_text(rx, ry+16, text=f"R 接收头 ({frame.center_x_mm:g}, {frame.center_y_mm:g})")
    canvas.create_text(*xy(179, 100), anchor="w", text="4针口 →")
    canvas.create_text(*xy(110, 115), anchor="w", text="电源口")
    canvas.create_text(370, 24, text="加高 30 mm 的 P5 支架 · 俯视 · 名义尺寸", font=("Microsoft YaHei UI", 12))
    canvas.create_text(370, 67, text=(f"接收面 Z={frame.receiver_height_mm:g} mm；发射面 Z={frame.face_height_mm:g} mm\n"
        f"输入托盘 A 时：R = A + ({frame.tray_offset_x_mm:g}, {frame.tray_offset_y_mm:g}) mm\n"
        "保持接收面向上、4针口朝右；托盘角取直边延长线交点。"))
    ttk.Button(window, text="关闭", command=window.destroy).pack(pady=(0, 8))
