"""Dataset replay workspace. Deliberately has no reference to App or Session."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, filedialog

import numpy as np
from PIL import Image, ImageTk

from .egopressure import load_sequence, sample_path, counts_to_kpa, pressure_rgb, FPS
from .theme import BG, BLUE, INK, MUTED, FONT, DisplayVar, wrap_to_width


class ReplayPanel(ttk.Frame):
    def __init__(self, parent, on_pressure=None):
        super().__init__(parent)
        self.sequence = None
        self.index = 0
        self.playing = False
        self.active = False
        self.loading = None
        self.on_pressure = on_pressure
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dataset-loader")
        self.title = DisplayVar(value="掌心轻压 · 右手")
        self.subtitle = DisplayVar(value="EgoPressure / P001")
        self.frame_text = DisplayVar(value="正在读取样本…")
        self.time_text = DisplayVar(value="00.00 / 00.00 s")
        self.force_text = DisplayVar(value="—")
        self.peak_text = DisplayVar(value="—")
        self.status = DisplayVar(value="")
        self.joints = tk.BooleanVar(value=True)
        self.speed = tk.StringVar(value="1×")
        self.timeline_value = tk.DoubleVar(value=0)

        heading = ttk.Frame(self, padding=(4, 2, 4, 16))
        heading.pack(fill="x")
        ttk.Label(heading, textvariable=self.title, style="Page.TLabel").pack(side="left")
        ttk.Button(heading, text="导入序列", command=self.import_sequence).pack(side="right", padx=(8,0))
        ttk.Button(heading, text="官方样本", command=lambda: self.load(sample_path())).pack(side="right")
        self.pressure_button = ttk.Button(heading, text="发送当前帧压力…", command=self.open_pressure, state="disabled")
        self.pressure_button.pack(side="right", padx=8)
        ttk.Label(heading, textvariable=self.subtitle, style="Caption.TLabel").pack(side="left", padx=18)

        bottom = ttk.Frame(self, padding=(4, 14, 4, 6))
        bottom.pack(side="bottom", fill="x")
        source = ttk.Label(bottom, text="EgoPressure 公开采集数据 · 手部拟合顶点 / 接触压力实测 · 离线回放", style="Caption.TLabel")
        source.pack(side="bottom", fill="x", pady=(12,0))
        wrap_to_width(source)
        controls = ttk.Frame(bottom)
        controls.pack(side="bottom", fill="x", pady=(8,0))
        self.play_button = ttk.Button(controls, text="播放回放", style="Primary.TButton", command=self.toggle)
        self.play_button.pack(side="left")
        self.previous_button = ttk.Button(controls, text="上一帧", command=lambda: self.step(-1))
        self.previous_button.pack(side="left", padx=(8,4))
        self.next_button = ttk.Button(controls, text="下一帧", command=lambda: self.step(1))
        self.next_button.pack(side="left")
        ttk.Label(controls, textvariable=self.time_text, style="Heading.TLabel").pack(side="left", padx=20)
        self.speed_picker = ttk.Combobox(controls, textvariable=self.speed, values=("0.25×","0.5×","1×","2×"), width=5, state="readonly")
        self.speed_picker.pack(side="right")
        self.speed_picker.bind("<<ComboboxSelected>>", lambda _: self.pause())
        ttk.Label(controls, text="速度", style="Caption.TLabel").pack(side="right", padx=8)
        self.timeline = ttk.Scale(bottom, from_=0, to=1, variable=self.timeline_value, command=self.seek)
        self.timeline.pack(side="bottom", fill="x")
        self.curve = tk.Canvas(bottom, height=52, bg="#ffffff", highlightthickness=0)
        self.curve.pack(side="bottom", fill="x", pady=(0,6))
        self.curve.bind("<Configure>", lambda _: self.draw_curve())
        self.curve.bind("<Button-1>", self.curve_seek)

        content = ttk.Frame(self)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        stage = ttk.Frame(content)
        stage.grid(row=0, column=0, sticky="nsew")
        stage.rowconfigure(1, weight=1)
        stage.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(stage, padding=(4,0,4,10))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="三维场景", style="Heading.TLabel").pack(side="left")
        self.reset_button = ttk.Button(toolbar, text="复位视角", command=lambda: self.scene.reset_camera())
        self.reset_button.pack(side="right")
        ttk.Button(toolbar, text="俯视", command=lambda: self.scene.reset_camera(top=True)).pack(side="right", padx=6)
        self.scene = None
        try:
            from .replay_scene import ReplayScene
            self.scene = ReplayScene(stage, width=640, height=420)
            self.scene.grid(row=1, column=0, sticky="nsew")
        except (ImportError, OSError) as error:
            ttk.Label(stage, text=f"三维组件未能加载：{error}", wraplength=500).grid(row=1,column=0)
            self.reset_button.configure(state="disabled")
        ttk.Label(stage, text="左键旋转   ·   右键平移   ·   滚轮缩放   ·   双击复位", style="Caption.TLabel").grid(row=2,column=0,sticky="w",pady=(8,0))
        ttk.Label(stage,textvariable=self.status,style="Caption.TLabel").grid(row=3,column=0,sticky="w")

        self.image_width = round(210*float(self.tk.call("tk","scaling"))/(96/72))
        inspector_host = ttk.Frame(content,padding=(24,0,0,0))
        inspector_host.grid(row=0,column=1,sticky="ns")
        scroll = ttk.Scrollbar(inspector_host,orient="vertical")
        scroll.pack(side="right",fill="y")
        viewport = tk.Canvas(inspector_host,width=self.image_width+4,highlightthickness=0,bg="#ffffff",yscrollcommand=scroll.set)
        viewport.pack(side="left",fill="both",expand=True)
        scroll.configure(command=viewport.yview)
        inspector = ttk.Frame(viewport)
        window = viewport.create_window((0,0),window=inspector,anchor="nw")
        inspector.bind("<Configure>",lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>",lambda event: viewport.itemconfigure(window,width=event.width))
        def scroll_inspector(event):
            widget=event.widget
            while widget is not None:
                if widget==inspector_host:
                    viewport.yview_scroll(-1 if event.delta>0 else 1,"units")
                    return "break"
                widget=getattr(widget,"master",None)
        self.bind_all("<MouseWheel>",scroll_inspector,add="+")
        ttk.Label(inspector, text="接触压力", style="Heading.TLabel").pack(anchor="w",pady=(4,12))
        self.heatmap = ttk.Label(inspector)
        self.heatmap.pack(anchor="w")
        self.legend = ttk.Label(inspector)
        self.legend.pack(fill="x",pady=(12,0))
        ttk.Label(inspector, text="0                       ≥20 kPa", style="Caption.TLabel").pack(anchor="w")
        ttk.Separator(inspector).pack(fill="x",pady=12)
        metrics = ttk.Frame(inspector)
        metrics.pack(fill="x",pady=(0,10))
        metrics.columnconfigure((0,1),weight=1)
        ttk.Label(metrics, text="总力 / N", style="Caption.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Label(metrics, text="峰值 / kPa", style="Caption.TLabel").grid(row=0,column=1,sticky="w",padx=(12,0))
        ttk.Label(metrics, textvariable=self.force_text, style="Page.TLabel").grid(row=1,column=0,sticky="w",pady=6)
        ttk.Label(metrics, textvariable=self.peak_text, style="Page.TLabel").grid(row=1,column=1,sticky="w",padx=(12,0),pady=6)
        ttk.Separator(inspector).pack(fill="x",pady=(0,10))
        ttk.Label(inspector, text="手部表示", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(inspector, text="778 个原始顶点\n未加载手部表面模型", style="Caption.TLabel").pack(anchor="w",pady=8)
        self.joints_toggle = ttk.Checkbutton(inspector,text="显示关节连线",variable=self.joints,command=self.toggle_joints)
        self.joints_toggle.pack(anchor="w")
        self.frame_label = ttk.Label(inspector,textvariable=self.frame_text,style="Caption.TLabel")
        self.frame_label.pack(anchor="w",pady=(12,0))
        self.pressure_note = ttk.Label(inspector,text="压力来自接触传感器，\n不是超声声压或触觉强度。",style="Caption.TLabel")
        self.pressure_note.pack(anchor="w",pady=(12,0))
        gradient = pressure_rgb(np.linspace(0,20,210)[None,:] * (1736*.00125**2*1000))
        self.legend_image = ImageTk.PhotoImage(Image.fromarray(gradient).resize((self.image_width,8)))
        self.legend.configure(image=self.legend_image)
        self.load(sample_path())
        self.tick_id = self.after(25, self.tick)

    def load(self, path):
        if self.loading is not None:
            return
        self.pause()
        self.status.set("正在读取序列…")
        self.loading = self.executor.submit(load_sequence, path)
        self.pressure_button.configure(state="disabled")

    def open_pressure(self):
        if self.sequence is None or self.loading is not None or self.on_pressure is None:
            return
        self.pause()
        self.on_pressure(self.sequence, self.index)

    def import_sequence(self):
        filename = filedialog.askopenfilename(parent=self, title="导入 EgoPressure 序列",
            filetypes=[("EgoPressure 数据", "*.npz *.parquet"), ("所有文件", "*.*")])
        if filename:
            self.load(Path(filename))

    def set_active(self, active):
        self.active = active
        if not active:
            self.pause()

    def pause(self):
        self.playing = False
        self.play_button.configure(text="播放回放")

    def toggle(self):
        if self.sequence is None or self.loading is not None:
            return
        if self.playing:
            self.pause()
        else:
            if self.index == len(self.sequence.frames)-1:
                self.show_frame(0)
            self.start_time = time.monotonic()
            self.start_offset = self.sequence.seconds[self.index]
            self.playing = True
            self.play_button.configure(text="暂停回放")

    def step(self, direction):
        self.pause()
        if self.sequence:
            self.show_frame(self.index+direction)

    def seek(self, value):
        if self.sequence:
            self.pause()
            self.show_frame(self.sequence.index_at(float(value)))

    def curve_seek(self, event):
        if self.sequence:
            self.seek(event.x/max(1,self.curve.winfo_width()) * self.sequence.seconds[-1])

    def toggle_joints(self):
        if self.scene:
            self.scene.show_joints = self.joints.get()
            self.scene.render()

    def show_frame(self, index):
        seq = self.sequence
        if seq is None:
            return
        self.index = max(0, min(len(seq.frames)-1, index))
        i = self.index
        hand, pressure = bool(seq.hand_valid[i]), bool(seq.pressure_valid[i])
        self.pressure_button.configure(state="normal" if pressure and self.on_pressure and self.loading is None else "disabled")
        if self.scene:
            self.scene.set_frame(seq.vertices[i] if hand else None, seq.joints[i] if hand else None,
                                 seq.force[i] if pressure else None)
        self.frame_text.set(f"帧 {seq.frames[i]} / {seq.frames[-1]} · 30 帧/秒")
        self.time_text.set(f"{seq.seconds[i]:05.2f} / {seq.seconds[-1]:05.2f} s")
        self.timeline_value.set(float(seq.seconds[i]))
        self.force_text.set(f"{seq.total_force_n[i]:.2f}" if pressure else "—")
        self.peak_text.set(f"{counts_to_kpa(seq.force[i]).max():.2f}" if pressure else "—")
        self.status.set(" · ".join(text for ok,text in ((hand,"此帧无手部标注"),(pressure,"此帧压力缺失")) if not ok))
        pixels = pressure_rgb(seq.force[i]) if pressure else np.full((105,185,3),150,np.uint8)
        self.heat_image = ImageTk.PhotoImage(Image.fromarray(pixels).resize((self.image_width,round(self.image_width*105/185)),Image.Resampling.BILINEAR))
        self.heatmap.configure(image=self.heat_image)
        self.draw_cursor()

    def draw_curve(self):
        self.curve.delete("all")
        if not self.sequence:
            return
        w,h = self.curve.winfo_width(),self.curve.winfo_height()
        seq = self.sequence
        values = seq.total_force_n
        peak = max(1.,float(np.nanmax(values))) if np.isfinite(values).any() else 1.
        xs = seq.seconds/max(.001,seq.seconds[-1])*w
        points = []
        for index,value in enumerate(values):
            if np.isfinite(value):
                points.extend((float(xs[index]),float(h-5-value/peak*(h-16))))
            else:
                if len(points)>=4: self.curve.create_line(points,fill=BLUE,width=2)
                points=[]
        if len(points)>=4: self.curve.create_line(points,fill=BLUE,width=2)
        self.curve.create_text(4,0,text="总接触力",anchor="nw",fill=MUTED,font=(FONT,8))
        self.draw_cursor()

    def draw_cursor(self):
        self.curve.delete("cursor")
        if self.sequence:
            x = self.sequence.seconds[self.index]/max(.001,self.sequence.seconds[-1])*self.curve.winfo_width()
            self.curve.create_line(x,0,x,self.curve.winfo_height(),fill=INK,width=2,tags="cursor")

    def tick(self):
        if self.loading is not None and self.loading.done():
            future, self.loading = self.loading, None
            try:
                self.sequence = future.result()
                seq = self.sequence
                sample = seq.name == "p_001_press_palm_low_x5_right"
                self.title.set("掌心轻压 · 右手" if sample else "手部动作回放")
                self.subtitle.set("EgoPressure / P001" if sample else seq.name)
                self.timeline.configure(to=max(.001,seq.seconds[-1]))
                # Open on an informative measured contact frame, paused.
                values = seq.total_force_n
                index = int(np.nanargmax(values)) if np.isfinite(values).any() else 0
                self.show_frame(index)
                self.draw_curve()
            except Exception as error:
                self.status.set(f"读取失败：{error}")
        if self.playing and self.active and self.sequence:
            seconds = self.start_offset + (time.monotonic()-self.start_time)*float(self.speed.get().rstrip("×"))
            index = self.sequence.index_at(seconds)
            if index != self.index:
                self.show_frame(index)
            if seconds >= self.sequence.seconds[-1]:
                self.pause()
        self.tick_id = self.after(25,self.tick)

    def destroy(self):
        self.after_cancel(self.tick_id)
        self.executor.shutdown(wait=False,cancel_futures=True)
        super().destroy()
