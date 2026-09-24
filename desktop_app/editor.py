"""Free-position discrete path editor. Coordinates are stored in integer um."""

import math
import tkinter as tk
from tkinter import ttk

from .model import MAX_PATH_POINTS

MAX_EDITOR_POINTS = MAX_PATH_POINTS


class PathEditor(ttk.Frame):
    def __init__(self, parent, on_preview, on_apply, on_save, on_load, on_change=None):
        super().__init__(parent)
        self.points = []
        self.cx = self.cy = 0.0
        self.view_span = 60.0
        self.closed = tk.BooleanVar(value=True)
        self.undo_stack = []
        self.drag_index = None
        self.drag_original = None
        self.selected = None
        self.locations = []
        self.on_change = on_change or (lambda: None)
        self.message = tk.StringVar()
        self.cursor_label = tk.StringVar(value="坐标 / mm")
        self.x_entry = tk.StringVar()
        self.y_entry = tk.StringVar()
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="撤销", command=self.undo).pack(side="left", padx=2)
        ttk.Button(toolbar, text="清空", command=self.clear).pack(side="left", padx=2)
        ttk.Checkbutton(toolbar, text="闭合图形", variable=self.closed, command=self.changed).pack(side="left", padx=8)
        ttk.Button(toolbar, text="保存图形", command=on_save).pack(side="right", padx=2)
        ttk.Button(toolbar, text="打开图形", command=on_load).pack(side="right", padx=2)
        ttk.Label(self, text="点击空白处添加节点，拖动调整位置，右键删除。", padding=(12, 4)).pack(fill="x")
        position = ttk.Frame(self, padding=(12, 4))
        position.pack(fill="x")
        ttk.Label(position, text="X / mm").pack(side="left")
        ttk.Entry(position, textvariable=self.x_entry, width=9).pack(side="left", padx=(5, 12))
        ttk.Label(position, text="Y / mm").pack(side="left")
        ttk.Entry(position, textvariable=self.y_entry, width=9).pack(side="left", padx=5)
        ttk.Button(position, text="添加节点", command=self.add_coordinates).pack(side="left", padx=4)
        self.move_button = ttk.Button(position, text="移动选中点", command=self.move_coordinates)
        self.move_button.pack(side="left", padx=4)
        ttk.Label(position, textvariable=self.cursor_label, foreground="#526680").pack(side="right")
        self.canvas = tk.Canvas(self, bg="#f8fbff", highlightthickness=0, height=420)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=5)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Button-3>", self.remove)
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<MouseWheel>", self.wheel)
        footer = ttk.Frame(self, padding=(8, 4))
        footer.pack(side="bottom", fill="x", before=self.canvas)
        ttk.Label(footer, textvariable=self.message).pack(side="left")
        for text, command in (("＋", lambda: self.zoom(.8)), ("－", lambda: self.zoom(1.25)), ("适合窗口", self.fit)):
            ttk.Button(footer, text=text, command=command, width=8).pack(side="right", padx=2)
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(side="bottom", fill="x", before=footer)
        self.preview_button = ttk.Button(bottom, text="声场预览", command=on_preview)
        self.apply_button = ttk.Button(bottom, text="发送到设备", command=on_apply)
        self.apply_button.pack(side="left", padx=4)
        ttk.Label(bottom, text="发送后点击播放。", foreground="#526680").pack(side="left", padx=10)

    def set_debug(self, enabled):
        if enabled:
            self.preview_button.pack(side="right", padx=4)
        else:
            self.preview_button.pack_forget()

    def set_origin(self, cx, cy):
        self.cx, self.cy = cx, cy
        self.draw()
        self.update_selection()

    def set_path(self, points, closed):
        if len(points) > MAX_EDITOR_POINTS:
            raise ValueError(f"最多支持 {MAX_EDITOR_POINTS} 个节点")
        self.points = [tuple(point) for point in points]
        self.closed.set(bool(closed))
        self.selected = None
        self.drag_index = self.drag_original = None
        self.undo_stack = []
        self.fit()

    def remember(self):
        self.undo_stack.append((self.points[:], self.closed.get()))
        self.undo_stack = self.undo_stack[-100:]

    def changed(self):
        if not self.candidate_valid(self.points):
            self.closed.set(False)
        self.draw()
        self.on_change()

    def clear(self):
        self.remember()
        self.points = []
        self.selected = None
        self.changed()

    def undo(self):
        if self.undo_stack:
            self.points, closed = self.undo_stack.pop()
            self.closed.set(closed)
            self.selected = None
            self.changed()

    def to_screen(self, point):
        x, y = point
        return self.center_x + x / 1000 * self.scale, self.center_y - y / 1000 * self.scale

    def from_screen(self, x, y):
        if not (self.left <= x <= self.right and self.top <= y <= self.bottom):
            return None
        point = (round((x-self.center_x)/self.scale*1000), round((self.center_y-y)/self.scale*1000))
        return point if max(map(abs, point)) <= 300000 else None

    def nearest(self, event):
        if not self.locations:
            return None
        index = min(range(len(self.locations)), key=lambda i: math.hypot(self.locations[i][0]-event.x, self.locations[i][1]-event.y))
        x, y = self.locations[index]
        return index if math.hypot(x-event.x, y-event.y) <= 9 else None

    def update_selection(self):
        valid = self.selected is not None and self.selected < len(self.points)
        self.move_button.configure(state="normal" if valid else "disabled")
        if valid:
            x, y = self.points[self.selected]
            self.x_entry.set(f"{x/1000+self.cx:.3f}")
            self.y_entry.set(f"{y/1000+self.cy:.3f}")

    def candidate_valid(self, candidate):
        return (not any(a == b for a, b in zip(candidate, candidate[1:]))
                and not (self.closed.get() and len(candidate) > 1 and candidate[0] == candidate[-1]))

    def append_point(self, point):
        if len(self.points) >= MAX_EDITOR_POINTS:
            self.message.set(f"最多添加 {MAX_EDITOR_POINTS} 个节点，请先删除一个节点。")
            return False
        if not self.candidate_valid(self.points + [point]):
            self.message.set("请添加不同位置的节点。")
            return False
        self.remember()
        self.points.append(point)
        self.selected = len(self.points)-1
        self.changed()
        return True

    def press(self, event):
        index = self.nearest(event)
        if index is not None:
            self.selected = self.drag_index = index
            self.drag_original = self.points[:]
            self.draw()
        else:
            point = self.from_screen(event.x, event.y)
            if point is not None:
                self.append_point(point)

    def motion(self, event):
        self.hover(event)
        if self.drag_index is None:
            return
        point = self.from_screen(event.x, event.y)
        if point is not None:
            candidate = self.points[:]
            candidate[self.drag_index] = point
            if self.candidate_valid(candidate):
                self.points = candidate
                self.changed()

    def release(self, _event):
        if self.drag_original is not None and self.points != self.drag_original:
            self.undo_stack.append((self.drag_original, self.closed.get()))
            self.undo_stack = self.undo_stack[-100:]
        self.drag_index = self.drag_original = None

    def remove(self, event):
        index = self.nearest(event)
        if index is not None:
            candidate = self.points[:]
            candidate.pop(index)
            if not self.candidate_valid(candidate):
                self.message.set("删除后会有相邻节点重合，请先移动节点。")
                return
            self.remember()
            self.points = candidate
            self.selected = None
            self.changed()

    def hover(self, event):
        point = self.from_screen(event.x, event.y)
        if point is not None:
            self.cursor_label.set(f"({point[0]/1000+self.cx:.2f}, {point[1]/1000+self.cy:.2f}) mm")

    def input_point(self):
        x, y = float(self.x_entry.get()), float(self.y_entry.get())
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError()
        point = (round((x-self.cx)*1000), round((y-self.cy)*1000))
        if max(map(abs, point)) > 300000:
            raise ValueError()
        return point

    def add_coordinates(self):
        try:
            if self.append_point(self.input_point()):
                self.fit()
        except (ValueError, OverflowError):
            self.message.set("请输入有效坐标，距图形中心不超过 300 mm。")

    def move_coordinates(self):
        if self.selected is None:
            return
        try:
            candidate = self.points[:]
            candidate[self.selected] = self.input_point()
            if not self.candidate_valid(candidate):
                raise ValueError()
            self.remember()
            self.points = candidate
            self.changed()
            self.fit()
        except (ValueError, OverflowError):
            self.message.set("请输入有效坐标，避免相邻节点重合。")

    def fit(self):
        extent = max((abs(v)/1000 for point in self.points for v in point), default=0)
        self.view_span = min(660, max(60, extent*2.4))
        self.draw()

    def zoom(self, factor):
        self.view_span = max(2, min(660, self.view_span*factor))
        self.draw()

    def wheel(self, event):
        self.zoom(.8 if event.delta > 0 else 1.25)
        return "break"

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 260), max(canvas.winfo_height(), 180)
        self.left, self.top, self.right, self.bottom = 55, 35, width-30, height-45
        self.center_x, self.center_y = (self.left+self.right)/2, (self.top+self.bottom)/2
        self.scale = min(self.right-self.left, self.bottom-self.top) / self.view_span
        canvas.create_rectangle(self.left, self.top, self.right, self.bottom, outline="#dce5ef", fill="#ffffff")
        canvas.create_line(self.left, self.center_y, self.right, self.center_y, fill="#b8c8d9", arrow="last")
        canvas.create_line(self.center_x, self.bottom, self.center_x, self.top, fill="#b8c8d9", arrow="last")
        raw = self.view_span/6
        magnitude = 10 ** math.floor(math.log10(raw))
        tick = next(v*magnitude for v in (1, 2, 5, 10) if v*magnitude >= raw)
        for axis in (0, 1):
            half_span = ((self.right-self.left) if axis == 0 else (self.bottom-self.top))/self.scale/2
            for i in range(math.ceil(-half_span/tick), math.floor(half_span/tick)+1):
                value = i*tick
                if axis == 0:
                    x = self.center_x+value*self.scale
                    canvas.create_line(x, self.center_y-3, x, self.center_y+3, fill="#92a8c4")
                    canvas.create_text(x, self.center_y+14, text=f"{value+self.cx:g}", fill="#647994", font=("Microsoft YaHei UI", 8))
                elif i:
                    y = self.center_y-value*self.scale
                    canvas.create_line(self.center_x-3, y, self.center_x+3, y, fill="#92a8c4")
                    canvas.create_text(self.center_x-7, y, text=f"{value+self.cy:g}", anchor="e", fill="#647994", font=("Microsoft YaHei UI", 8))
        canvas.create_text(self.right-5, self.center_y-14, text="X / mm", anchor="e", fill="#526680")
        canvas.create_text(self.center_x+10, self.top+9, text="Y / mm", anchor="w", fill="#526680")
        self.locations = [self.to_screen(point) for point in self.points]
        route = self.locations + ([self.locations[0]] if self.closed.get() and len(self.points) > 1 else [])
        for a, b in zip(route, route[1:]):
            clipped = self.clip_segment(a, b)
            if clipped:
                canvas.create_line(*clipped, fill="#2463c5", width=2.5, arrow="last", arrowshape=(8, 10, 4))
        for i, (x, y) in enumerate(self.locations):
            if self.left <= x <= self.right and self.top <= y <= self.bottom:
                if i == self.selected:
                    canvas.create_oval(x-10, y-10, x+10, y+10, outline="#087e8b", width=2)
                canvas.create_oval(x-5, y-5, x+5, y+5, fill="#087e8b", outline="white", width=1)
                canvas.create_text(x+10, y-12, text=str(i+1), fill="#172b45", font=("Microsoft YaHei UI", 10))
        if not self.points:
            canvas.create_text(width/2, self.top+45, text="点击任意位置，开始绘制", fill="#526680", font=("Microsoft YaHei UI", 13))
        canvas.create_text(width/2, 16, text="图形编辑", fill="#172b45", font=("Microsoft YaHei UI", 11, "bold"))
        canvas.create_text(width/2, height-15, text="滚轮缩放 · 坐标单位：毫米", fill="#526680", font=("Microsoft YaHei UI", 9))
        self.message.set(f"{len(self.points)} / {MAX_EDITOR_POINTS} 个节点 · " + ("闭合播放" if self.closed.get() else "往返播放"))
        self.update_selection()

    def clip_segment(self, start, end):
        x, y = start
        dx, dy = end[0]-x, end[1]-y
        low, high = 0.0, 1.0
        for p, q in ((-dx, x-self.left), (dx, self.right-x), (-dy, y-self.top), (dy, self.bottom-y)):
            if p == 0:
                if q < 0:
                    return None
            elif p < 0:
                low = max(low, q/p)
            else:
                high = min(high, q/p)
            if low > high:
                return None
        return (x+low*dx, y+low*dy, x+high*dx, y+high*dy)
