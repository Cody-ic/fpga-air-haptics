"""Native Tk desktop console. UI thread owns widgets; workers own I/O/math."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import csv
import json
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib import rcParams
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import numpy as np

from .controller import Session
from .model import ArraySpec, ARRAY_PRESETS, Config, SHAPES, array_coordinates, config_from_document, encode_points, field_slice, focus_phases, trajectory_path, trajectory_point
from .editor import PathEditor
from .playback import PlaybackPanel
from .presets import PresetPreview
from .transport import available_ports

if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

BG = "#edf2f7"
INK = "#172b45"
MUTED = "#526680"
BLUE = "#2463c5"
TEAL = "#087e8b"
RED = "#b02d40"
AMBER = "#966414"
WHITE = "#ffffff"


class PlotPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.figure = Figure(figsize=(8.5, 5.4), dpi=100, facecolor=WHITE)
        grid = self.figure.add_gridspec(2, 3, width_ratios=[1, 1, 1.35],
                                      left=0.07, right=0.96, bottom=0.14, top=0.83,
                                      wspace=0.60, hspace=0.65)
        self.field = self.figure.add_subplot(grid[:, :2])
        self.phase = self.figure.add_subplot(grid[0, 2])
        self.path = self.figure.add_subplot(grid[1, 2])
        self.colorbar = None
        self.canvas = FigureCanvasTkAgg(self.figure, self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False).pack(side="bottom", fill="x", before=self.canvas.get_tk_widget())
        self.last_payload = None
        self.clear("尚无数据")

    def clear(self, title):
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
        for axis in (self.field, self.phase, self.path):
            axis.clear()
            axis.set_axis_off()
        self.field.text(0.5, 0.5, title, ha="center", va="center", transform=self.field.transAxes,
                        color=MUTED, fontsize=12, linespacing=1.8)
        self.canvas.draw_idle()
        self.last_payload = None

    def render(self, payload):
        config, array, phases, focus, enabled, plane, horizontal, vertical, values, title = payload
        self.last_payload = payload
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
        for axis in (self.field, self.phase, self.path):
            axis.clear()
            axis.set_axis_on()
            axis.tick_params(labelsize=8, colors=MUTED)
            for spine in axis.spines.values():
                spine.set_color("#ccd5df")
        extent = (horizontal[0], horizontal[-1], vertical[0], vertical[-1])
        image = self.field.imshow(values, extent=extent, origin="lower", cmap="magma",
                                  vmin=0, vmax=max(1.0, float(values.max())), aspect="equal")
        self.field.set_xlabel("x / mm")
        self.field.set_ylabel("y / mm" if plane == "XY" else "z / mm")
        self.field.set_title(f"{plane} 声场切片 · {'输出使能' if enabled else '输出关闭'}", fontsize=10)
        target_y = focus[1] if plane == "XY" else focus[2]
        self.field.plot(focus[0], target_y, marker="+", ms=11, mew=1.5, color="#68e4dc")
        self.colorbar = self.figure.colorbar(image, ax=self.field, fraction=0.038, pad=0.03)
        self.colorbar.ax.tick_params(labelsize=8)
        self.colorbar.set_label("相对 |p|（非声压单位）", fontsize=8)
        phase_grid = np.asarray(phases).reshape(array.rows, array.cols)
        self.phase.imshow(phase_grid, origin="lower", cmap="twilight", vmin=0,
                          vmax=config.phase_steps - 1, aspect="equal")
        self.phase.set_title(f"{array.rows}×{array.cols} 阵列 · {array.count} 路相位", fontsize=10)
        self.phase.set_xlabel("列 / +x", fontsize=8)
        self.phase.set_ylabel("行 / +y", fontsize=8)
        self.phase.set_xticks(sorted(set([0, array.cols - 1])))
        self.phase.set_yticks(sorted(set([0, array.rows - 1])))
        path = trajectory_path(config)
        self.path.plot(path[:, 0], path[:, 1], color=BLUE, lw=1.5)
        self.path.scatter([focus[0]], [focus[1]], color=TEAL, s=35, zorder=3)
        self.path.set_aspect("equal", adjustable="box")
        self.path.set_xlabel("x / mm", fontsize=8)
        self.path.set_ylabel("y / mm", fontsize=8)
        self.path.set_title(f"{SHAPES[config.shape]} · 焦点回读 / 轨迹参考", fontsize=9)
        if np.all(np.ptp(path[:, :2], axis=0) == 0):
            self.path.set_xlim(focus[0]-10, focus[0]+10)
            self.path.set_ylim(focus[1]-10, focus[1]+10)
        self.path.grid(alpha=0.18)
        self.figure.suptitle(title, x=0.05, ha="left", fontsize=11, color=INK)
        self.canvas.draw_idle()


class App:
    def __init__(self, root):
        self.root = root
        root.tk.call("tk", "scaling", 4 / 3)
        root.title("触见 · 图形工作台")
        root.geometry("1380x920")
        root.minsize(1100, 780)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                         "axes.unicode_minus": False, "font.size": 9})
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="acoustic-model")
        self.session = None
        self.state = None
        self.hardware = None
        self.workspace = None
        self.capabilities = set()
        self.received = 0.0
        self.ready = False
        self.busy = False
        self.pending_verb = None
        self.command_deadline = 0.0
        self.await_revision = None
        self.await_sample = None
        self.await_verb = None
        self.pending_config = None
        self.confirmed_config = None
        self.last_actual_plot = 0.0
        self.generation = 0
        self.jobs = {}
        self.wanted = {}
        self.log_records = deque(maxlen=3000)
        self.last_log_refresh = 0.0
        self.closing = False
        self.mode_choice = tk.StringVar(value="Demo 模拟设备")
        self.port_choice = tk.StringVar()
        self.baud_choice = tk.StringVar(value="115200")
        self.device_label = tk.StringVar(value="未连接")
        self.status_line = tk.StringVar(value="点击画布开始绘制。")
        self.actual_line = tk.StringVar(value="等待板端回读；没有实际输出数据。")
        self.source_line = tk.StringVar(value="请选择 Demo 或真实串口并连接")
        self.config_line = tk.StringVar(value="连接设备后可发送图形。")
        self.plane = tk.StringVar(value="XY")
        self.mute = tk.BooleanVar(value=False)
        self.show_heartbeat = tk.BooleanVar(value=False)
        self.debug_mode = tk.BooleanVar(value=False)
        self.demo_array_choice = tk.StringVar(value="4 × 4 · 16 路")
        self.hw_rows = tk.StringVar(value="4")
        self.hw_cols = tk.StringVar(value="4")
        self.hw_pitch = tk.StringVar(value="10")
        self.hardware_line = tk.StringVar(value="实际阵列尚未由设备确认")
        self.syncing_geometry = False
        self.vars = {}
        self.size_label = tk.StringVar(value="预设图形半径 / mm")
        self._style()
        self._layout()
        self.set_config(Config())
        for name in ("cx_um", "cy_um"):
            self.vars[name].trace_add("write", lambda *_: self.sync_editor())
        for variable in self.vars.values():
            variable.trace_add("write", lambda *_: self.refresh_pattern_preview())
        self.refresh_ports()
        self.tabs.select(self.editor_tab)
        self.toggle_debug()
        self.root.after(60, self.tick)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=WHITE)
        style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background=WHITE)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 7))
        style.configure("Primary.TButton", background=BLUE, foreground=WHITE)
        style.map("Primary.TButton", background=[("active", "#184c9c"), ("disabled", "#bcc9dc")])
        style.configure("Stop.TButton", background=RED, foreground=WHITE)
        style.map("Stop.TButton", background=[("active", "#872032"), ("disabled", "#d9bfc5")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(15, 8), font=("Microsoft YaHei UI", 10))
        style.configure("TEntry", padding=4)
        style.configure("TCombobox", padding=4)

    def _layout(self):
        header = ttk.Frame(self.root, padding=(22, 15, 22, 8))
        header.pack(fill="x")
        ttk.Label(header, text="触见", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=" /  图形工作台", style="Muted.TLabel").pack(side="left", pady=(7, 0))
        ttk.Checkbutton(header, text="调试模式", variable=self.debug_mode, command=self.toggle_debug).pack(side="left", padx=24, pady=(7, 0))
        self.header_stop = ttk.Button(header, text="停止输出", style="Stop.TButton", command=lambda: self.send("STOP"))
        self.header_stop.pack(side="right", padx=(14, 0))
        ttk.Label(header, textvariable=self.device_label, style="Heading.TLabel").pack(side="right")
        connection = ttk.Frame(self.root, padding=(22, 4, 22, 10))
        connection.pack(fill="x")
        self.connection_picker = ttk.Combobox(connection, textvariable=self.mode_choice,
                                             values=["Demo 模拟设备", "真实串口"], state="readonly", width=17)
        self.connection_picker.pack(side="left", padx=(0, 8))
        self.connection_picker.bind("<<ComboboxSelected>>", lambda _: self.update_controls())
        self.port_picker = ttk.Combobox(connection, textvariable=self.port_choice, width=11)
        self.port_picker.pack(side="left", padx=(0, 8))
        self.refresh_button = ttk.Button(connection, text="刷新串口", command=self.refresh_ports)
        self.refresh_button.pack(side="left", padx=(0, 10))
        self.baud_label = ttk.Label(connection, text="波特率")
        self.baud_label.pack(side="left")
        self.baud_picker = ttk.Combobox(connection, textvariable=self.baud_choice, width=9,
                                       values=["115200", "230400", "460800", "921600"], state="readonly")
        self.baud_picker.pack(side="left", padx=8)
        self.connect_button = ttk.Button(connection, text="连接", style="Primary.TButton", command=self.connect)
        self.connect_button.pack(side="left", padx=(0, 6))
        self.disconnect_button = ttk.Button(connection, text="断开", command=self.disconnect)
        self.disconnect_button.pack(side="left")
        self.protocol_label = ttk.Label(connection, text="HAP2 · 双向串口 · 8N1", style="Muted.TLabel")
        self.protocol_label.pack(side="right")
        ttk.Label(self.root, textvariable=self.status_line, style="Muted.TLabel", padding=(22,7),
                  wraplength=1040).pack(side="bottom", fill="x")

        body = ttk.Frame(self.root, padding=(18, 0, 18, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left_host = ttk.Frame(body, width=350)
        left_host.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_host.grid_propagate(False)
        left_host.rowconfigure(0, weight=1)
        left_host.columnconfigure(0, weight=1)
        left_canvas = tk.Canvas(left_host, bg=BG, highlightthickness=0, width=330)
        left_canvas.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left_host, orient="vertical", command=left_canvas.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left = ttk.Frame(left_canvas, padding=(4, 5, 10, 8))
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda _: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>", lambda e: left_canvas.itemconfigure(left_window, width=e.width))
        def scroll_left(event):
            widget = event.widget
            while widget is not None:
                if widget == left_host:
                    left_canvas.yview_scroll(-int(event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)
        self.root.bind_all("<MouseWheel>", scroll_left, add="+")
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="图形设置", style="Heading.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))
        settings = self.settings = ttk.Notebook(left)
        settings.grid(row=2, column=0, sticky="ew")
        shape_tab, array_tab = ttk.Frame(settings, padding=10), ttk.Frame(settings, padding=10)
        self.advanced_tab = array_tab
        settings.add(shape_tab, text="图形")
        settings.add(array_tab, text="调试参数")
        shape_fields = [("shape", "图形", None), ("cx_um", "水平偏移 / mm", 1000),
                        ("cy_um", "垂直偏移 / mm", 1000), ("z_um", "显示高度 / mm", 1000),
                        ("radius_um", "预设图形半径 / mm", 1000), ("repeat_millihz", "每秒播放次数", 1000)]
        array_fields = [("carrier_hz", "载波 / Hz", 1),
                        ("phase_steps", "相位级数", 1), ("mod_hz", "调制 / Hz", 1),
                        ("level", "输出等级 / %", 1)]
        self.scales = {}
        for parent, entries in ((shape_tab, shape_fields), (array_tab, array_fields)):
            parent.columnconfigure(1, weight=1)
            for row, (name, title, scale) in enumerate(entries):
                self.vars[name] = tk.StringVar()
                self.scales[name] = scale
                label = ttk.Label(parent, textvariable=self.size_label) if name == "radius_um" else ttk.Label(parent, text=title)
                label.grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
                if name == "shape":
                    widget = ttk.Combobox(parent, textvariable=self.vars[name], values=list(SHAPES.values()),
                                          state="readonly", width=12)
                    widget.bind("<<ComboboxSelected>>", self.select_shape)
                elif name == "phase_steps":
                    widget = ttk.Combobox(parent, textvariable=self.vars[name], values=[8,16,32,64,128,256],
                                          state="readonly", width=12)
                else:
                    widget = ttk.Entry(parent, textvariable=self.vars[name], width=13)
                widget.grid(row=row, column=1, sticky="ew")
        ttk.Label(array_tab, text="等级不是电压或声压；范围仅为协议边界。", style="Muted.TLabel", wraplength=270).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(shape_tab, text="编辑自定义图形", command=lambda: self.choose_shape("CUSTOM")).grid(row=6, column=0, columnspan=2, sticky="ew", pady=5)
        actions = ttk.Frame(left)
        actions.grid(row=3, column=0, sticky="ew", pady=10)
        actions.columnconfigure((0, 1), weight=1)
        self.apply_button = ttk.Button(actions, text="发送到设备", style="Primary.TButton", command=self.apply_config)
        self.apply_button.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="打开图形", command=self.load_config).grid(row=1, column=0, sticky="ew", pady=5, padx=(0, 4))
        ttk.Button(actions, text="保存图形", command=self.save_config).grid(row=1, column=1, sticky="ew", pady=5, padx=(4, 0))
        self.copy_button = ttk.Button(left, text="读取设备图形", command=self.copy_actual)
        self.copy_button.grid(row=4, column=0, sticky="ew")
        ttk.Label(left, textvariable=self.config_line, wraplength=295, style="Muted.TLabel").grid(row=5, column=0, sticky="w", pady=8)
        ttk.Separator(left).grid(row=6, column=0, sticky="ew", pady=4)
        ttk.Label(left, text="播放控制", style="Heading.TLabel").grid(row=7, column=0, sticky="w", pady=7)
        mode_row = ttk.Frame(left)
        mode_row.grid(row=8, column=0, sticky="ew")
        mode_row.columnconfigure((0,1), weight=1)
        self.local_button = ttk.Button(mode_row, text="设备按键控制", command=lambda: self.send("MODE", value="LOCAL"))
        self.local_button.grid(row=0, column=0, sticky="ew", padx=(0,4))
        self.remote_button = ttk.Button(mode_row, text="电脑控制", command=lambda: self.send("MODE", value="REMOTE"))
        self.remote_button.grid(row=0, column=1, sticky="ew", padx=(4,0))
        run_row = ttk.Frame(left)
        run_row.grid(row=9, column=0, sticky="ew", pady=8)
        run_row.columnconfigure((0,1,2), weight=1)
        self.start_button = ttk.Button(run_row, text="播放", style="Primary.TButton", command=lambda: self.send("START"))
        self.pause_button = ttk.Button(run_row, text="暂停", command=lambda: self.send("PAUSE"))
        self.stop_button = ttk.Button(run_row, text="停止", style="Stop.TButton", command=lambda: self.send("STOP"))
        for col, button in enumerate((self.start_button, self.pause_button, self.stop_button)):
            button.grid(row=0, column=col, sticky="ew", padx=2)
        self.demo_box = ttk.LabelFrame(left, text="Demo · 模拟板上操作", padding=8)
        self.demo_box.grid(row=10, column=0, sticky="ew", pady=5)
        demo_row = ttk.Frame(self.demo_box)
        demo_row.pack(fill="x")
        self.demo_buttons = []
        for label, action in (("下一图形", "NEXT"), ("播放/暂停", "PLAY"), ("停止", "STOP")):
            button = ttk.Button(demo_row, text=label, command=lambda a=action: self.send("_BUTTON", action=a))
            button.pack(side="left", expand=True, fill="x", padx=1)
            self.demo_buttons.append(button)
        self.mute_check = ttk.Checkbutton(self.demo_box, text="模拟回传中断（触发超时）", variable=self.mute,
                                          command=lambda: self.send("_MUTE", value=self.mute.get()))
        self.mute_check.pack(anchor="w", pady=(6,0))

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        self.source_banner = tk.Label(right, textvariable=self.source_line, bg="#e0e9f6", fg=INK,
                                      font=("Microsoft YaHei UI", 10), anchor="w", padx=12, pady=9)
        self.source_banner.pack(fill="x", pady=(0,5))
        self.hardware_detail = ttk.Label(right, textvariable=self.hardware_line, style="Heading.TLabel", wraplength=900)
        self.hardware_detail.pack(fill="x", pady=(2, 5))
        self.actual_detail = ttk.Label(right, textvariable=self.actual_line, wraplength=950, style="Muted.TLabel")
        self.actual_detail.pack(fill="x", pady=(2,8))
        quick_shapes = ttk.Frame(right)
        quick_shapes.pack(fill="x", pady=(3, 8))
        ttk.Label(quick_shapes, text="图案").pack(side="left", padx=(0, 6))
        self.shape_buttons = {}
        for code, label in SHAPES.items():
            button = ttk.Button(quick_shapes, text="自定义" if code == "CUSTOM" else label,
                                width=5, padding=(3, 6), command=lambda value=code: self.choose_shape(value))
            button.pack(side="left", expand=True, fill="x", padx=1)
            self.shape_buttons[code] = button
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        actual_tab, forecast_tab, self.editor_tab, measured_tab, log_tab, device_tab = [ttk.Frame(self.tabs) for _ in range(6)]
        self.debug_tabs = [actual_tab, forecast_tab, measured_tab, log_tab]
        self.tabs.add(actual_tab, text="设备回读")
        self.tabs.add(forecast_tab, text="电脑预测")
        self.tabs.add(self.editor_tab, text="自定义图形")
        self.tabs.add(measured_tab, text="实测数据")
        self.tabs.add(log_tab, text="通信日志")
        self.tabs.add(device_tab, text="设备设置")
        self.preset_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.preset_tab, text="图案预览")
        self.preset_preview = PresetPreview(self.preset_tab, self.apply_config)
        self.preset_preview.pack(fill="both", expand=True)
        self.playback_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.playback_tab, text="播放画面")
        self.playback = PlaybackPanel(self.playback_tab, lambda: self.send("START"),
                                      lambda: self.send("PAUSE"), lambda: self.send("STOP"),
                                      self.select_shape)
        self.playback.pack(fill="both", expand=True)
        self._device_tab(device_tab)
        view_row = ttk.Frame(actual_tab, padding=8)
        view_row.pack(fill="x")
        ttk.Label(view_row, text="基于回读相位重建的理论声场，不是传感器测量", style="Muted.TLabel").pack(side="left")
        for plane in ("XY", "XZ"):
            ttk.Radiobutton(view_row, text=plane, value=plane, variable=self.plane, command=self.refresh_plots).pack(side="right", padx=5)
        self.actual_plot = PlotPanel(actual_tab)
        self.actual_plot.pack(fill="both", expand=True)
        self.actual_plot.clear("尚无板端回读\n连接 Demo 可以体验完整双向流程")
        export_row = ttk.Frame(actual_tab, padding=(8,4))
        export_row.pack(side="bottom", fill="x", before=self.actual_plot)
        self.snap_button = ttk.Button(export_row, text="请求状态快照", command=lambda: self.send("SNAP"))
        self.snap_button.pack(side="left")
        self.export_button = ttk.Button(export_row, text="导出相位快照 CSV", command=self.export_snapshot)
        self.export_button.pack(side="left", padx=6)
        ttk.Button(forecast_tab, text="更新电脑预测", command=self.preview).pack(anchor="w", padx=8, pady=4)
        ttk.Label(forecast_tab, text="电脑期望参数的静态预览 · 不发送命令、不改变设备输出", style="Muted.TLabel", padding=8).pack(fill="x")
        self.forecast_plot = PlotPanel(forecast_tab)
        self.forecast_plot.pack(fill="both", expand=True)
        self.editor = PathEditor(self.editor_tab, self.preview_custom, self.apply_custom, self.save_custom, self.load_config,
                                 on_change=lambda: self.vars["shape"].set(SHAPES["CUSTOM"]))
        self.editor.pack(fill="both", expand=True)
        self._measurement_tab(measured_tab)
        log_controls = ttk.Frame(log_tab, padding=8)
        log_controls.pack(fill="x")
        ttk.Checkbutton(log_controls, text="显示心跳报文", variable=self.show_heartbeat, command=self.render_log).pack(side="left")
        ttk.Button(log_controls, text="导出日志", command=self.export_log).pack(side="right")
        self.log_view = tk.Text(log_tab, wrap="none", bg="#152638", fg="#d8e5f2", font=("Consolas", 10), state="disabled")
        scroll = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_view.pack(fill="both", expand=True)
        self.update_controls()

    def toggle_debug(self):
        enabled = self.debug_mode.get()
        if not enabled and self.tabs.select() in [str(tab) for tab in self.debug_tabs]:
            self.tabs.select(self.editor_tab)
        for tab in self.debug_tabs:
            self.tabs.tab(tab, state="normal" if enabled else "hidden")
        self.settings.tab(self.advanced_tab, state="normal" if enabled else "hidden")
        if enabled:
            self.hardware_detail.pack(fill="x", pady=(2, 5), before=self.tabs)
            self.actual_detail.pack(fill="x", pady=(2, 8), before=self.tabs)
            self.protocol_label.pack(side="right")
            self.demo_box.grid()
            self.device_details.pack(fill="x", pady=(18, 0))
        else:
            for widget in (self.hardware_detail, self.actual_detail, self.protocol_label, self.device_details):
                widget.pack_forget()
            self.demo_box.grid_remove()
        self.editor.set_debug(enabled)
        self.update_controls()

    def _device_tab(self, parent):
        content = ttk.Frame(parent, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="设备设置", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(content, text="选择演示设备的阵列尺寸。真实设备连接后自动识别。",
                  wraplength=650, style="Muted.TLabel").pack(anchor="w", pady=(0, 18))
        array_setup = ttk.LabelFrame(content, text="演示设备", padding=15)
        array_setup.pack(anchor="nw", fill="x")
        ttk.Label(array_setup, text="阵列预设").grid(row=0, column=0, sticky="w", pady=7, padx=(0, 12))
        self.demo_array_picker = ttk.Combobox(array_setup, textvariable=self.demo_array_choice,
                                              values=list(ARRAY_PRESETS), width=22, state="readonly")
        self.demo_array_picker.grid(row=0, column=1, sticky="w")
        self.demo_array_picker.bind("<<ComboboxSelected>>", self.select_hardware)
        self.hw_entries = []
        for row, (label, variable) in enumerate((("物理阵列行数", self.hw_rows), ("物理阵列列数", self.hw_cols),
                                                ("阵元间距 / mm", self.hw_pitch)), start=1):
            ttk.Label(array_setup, text=label).grid(row=row, column=0, sticky="w", pady=7, padx=(0, 12))
            entry = ttk.Entry(array_setup, textvariable=variable, width=14)
            entry.grid(row=row, column=1, sticky="w")
            self.hw_entries.append(entry)
        ttk.Label(content, text="断开连接后可以更改尺寸。已绘制的图形会保留。",
                  wraplength=650, style="Muted.TLabel").pack(anchor="w", pady=14)
        self.device_details = ttk.Frame(content)
        ttk.Button(self.device_details, text="用此阵列更新电脑预测", command=self.preview).pack(anchor="w")
        ttk.Label(self.device_details, text="Demo 默认 0.5 次/秒，仅演示扫描路径；运行时每 0.05 秒回传，其他状态每 0.5 秒回传。"
                  "本程序未模拟 FPGA 的高速更新时序，也未验证触觉效果。",
                  wraplength=650, style="Muted.TLabel").pack(anchor="w", pady=(14, 0))
        ttk.Label(self.device_details, textvariable=self.hardware_line, wraplength=650, style="Heading.TLabel").pack(anchor="w", pady=(24, 8))
        self.workspace_line = tk.StringVar(value="工作空间：等待设备声明。声明范围不代表已经验证触觉效果。")
        ttk.Label(self.device_details, textvariable=self.workspace_line, wraplength=650, style="Muted.TLabel").pack(anchor="w")

    def _measurement_tab(self, parent):
        top = ttk.Frame(parent, padding=10)
        top.pack(fill="x")
        ttk.Button(top, text="导入扫描 CSV", command=self.import_measurement).pack(side="left")
        ttk.Label(top, text="列：x_mm,y_mm,value · 相对接收幅值，不换算 Pa / dB SPL", style="Muted.TLabel").pack(side="left", padx=12)
        self.measurement_label = tk.StringVar(value="尚未导入实测数据。Demo 不会生成伪造的测量值。")
        ttk.Label(parent, textvariable=self.measurement_label, padding=10, wraplength=900).pack(fill="x")
        self.measurement_figure = Figure(figsize=(8,5), facecolor=WHITE)
        self.measurement_axis = self.measurement_figure.add_subplot(111)
        self.measurement_figure.subplots_adjust(left=.12,right=.90,bottom=.12,top=.9)
        self.measurement_axis.set_xlabel("x / mm")
        self.measurement_axis.set_ylabel("y / mm")
        self.measurement_canvas = FigureCanvasTkAgg(self.measurement_figure, parent)
        self.measurement_canvas.get_tk_widget().pack(fill="both",expand=True)
        self.measurement_colorbar = None

    def set_config(self, config):
        config.validate()
        self.syncing_geometry = True
        for name, variable in self.vars.items():
            value = getattr(config, name)
            variable.set(SHAPES[value] if name == "shape" else f"{value / self.scales[name]:g}")
        self.syncing_geometry = False
        self.sync_editor()
        self.editor.set_path(config.points_um(), config.path_closed)
        self.refresh_pattern_preview()

    def choose_shape(self, code):
        self.vars["shape"].set(SHAPES[code])
        self.select_shape()

    def select_shape(self, _event=None):
        custom = self.vars["shape"].get() == SHAPES["CUSTOM"]
        self.tabs.select(self.editor_tab if custom else self.preset_tab)
        self.refresh_pattern_preview()
        self.update_controls()

    def refresh_pattern_preview(self):
        if self.syncing_geometry:
            return
        try:
            self.preset_preview.set_config(self.get_config())
        except (ValueError, OverflowError):
            self.preset_preview.set_config(None)
        for code, button in self.shape_buttons.items():
            button.configure(style="Primary.TButton" if self.vars["shape"].get() == SHAPES[code] else "TButton")
        self.size_label.set("正方形半边长 / mm" if self.vars["shape"].get() == SHAPES["SQUARE"] else "预设图形半径 / mm")

    def select_hardware(self, _event=None):
        size = ARRAY_PRESETS[self.demo_array_choice.get()]
        if size is not None:
            self.hw_rows.set(str(size[0]))
            self.hw_cols.set(str(size[1]))
        self.status_line.set("已更新演示设备设置。")

    def preview_array(self):
        if self.ready and self.hardware is not None:
            return self.hardware
        pitch = float(self.hw_pitch.get()) * 1000
        if not np.isfinite(pitch) or abs(pitch - round(pitch)) > 1e-6:
            raise ValueError("阵元间距精度为 1 µm")
        return ArraySpec(int(self.hw_rows.get()), int(self.hw_cols.get()), round(pitch)).validate()

    def sync_editor(self):
        if self.syncing_geometry:
            return
        try:
            cx, cy = float(self.vars["cx_um"].get()), float(self.vars["cy_um"].get())
            if not np.isfinite([cx, cy]).all():
                return
            self.editor.set_origin(cx, cy)
        except (ValueError, OverflowError):
            pass

    def preview_custom(self):
        self.vars["shape"].set(SHAPES["CUSTOM"])
        self.preview()

    def apply_custom(self):
        self.vars["shape"].set(SHAPES["CUSTOM"])
        self.apply_config()

    def save_custom(self):
        self.vars["shape"].set(SHAPES["CUSTOM"])
        self.save_config()

    def get_config(self):
        values = {}
        for name, variable in self.vars.items():
            if name == "shape":
                values[name] = next((key for key,value in SHAPES.items() if value == variable.get()), "")
            else:
                number = float(variable.get()) * self.scales[name]
                if not np.isfinite(number) or abs(number - round(number)) > 1e-6:
                    raise ValueError(f"{name} 数值精度或格式不正确")
                values[name] = round(number)
        values["path_xy_um"] = encode_points(self.editor.points)
        values["path_closed"] = int(self.editor.closed.get())
        return Config(**values).validate()

    def refresh_ports(self):
        try:
            ports = available_ports()
            self.port_picker["values"] = [name for name,_ in ports]
            if ports and not self.port_choice.get():
                self.port_choice.set(ports[0][0])
            self.status_line.set("点击画布开始绘制；没有设备也可使用 Demo 演示。")
        except Exception as error:
            self.status_line.set(str(error))

    def connect(self):
        if self.session and self.session.is_alive():
            return
        demo = self.mode_choice.get().startswith("Demo")
        if not demo and not self.port_choice.get().strip():
            messagebox.showinfo("选择串口", "请先选择或输入 FPGA 的 COM 端口。", parent=self.root)
            return
        try:
            baud = int(self.baud_choice.get())
        except ValueError:
            return
        self.generation += 1
        self.state, self.received, self.ready, self.busy = None, 0.0, False, False
        self.hardware, self.workspace, self.capabilities = None, None, set()
        self.await_revision, self.await_sample, self.pending_verb = None, None, None
        self.await_verb = self.pending_config = self.confirmed_config = None
        self.mute.set(False)
        self.actual_plot.clear("等待握手与第一帧设备状态")
        self.device_label.set("正在连接…")
        self.source_line.set("正在连接演示设备…" if demo else "正在连接设备…")
        try:
            demo_array = self.preview_array() if demo else None
        except (ValueError, OverflowError):
            self.status_line.set("Demo 阵列参数无效，请检查行列数和间距。")
            return
        self.hardware_line.set("等待设备报告实际阵列；软件最大容量不代表已安装数量")
        self.session = Session(demo, self.port_choice.get().strip(), baud, demo_array=demo_array)
        self.session.start()
        self.update_controls()

    def disconnect(self):
        if self.session:
            self.confirmed_config = self.pending_config = None
            self.session.close()
            self.status_line.set("正在断开连接…")
            self.ready = False
            self.update_controls()

    def send(self, verb, **fields):
        if not self.session or not self.ready:
            return
        if verb == "START" and not self.can_play():
            self.status_line.set("请先发送当前图形，等待设备确认后再播放。")
            return
        if self.session.command(verb, **fields):
            if verb == "CONFIG":
                self.confirmed_config = None
                self.pending_config = Config.from_wire(fields)
            if verb == "STOP":
                self.pending_config = None
            if verb in ("CONFIG", "MODE", "START", "PAUSE", "STOP"):
                self.busy, self.pending_verb = True, verb
                self.command_deadline = time.monotonic() + 3.5
                self.await_revision, self.await_sample = None, None
                self.await_verb = None
                if verb in ("CONFIG", "START", "PAUSE", "STOP"):
                    self.tabs.select(self.playback_tab)
            self.status_line.set("正在等待设备确认…")
            self.update_controls()

    def config_confirmed(self):
        return bool(self.state and self.confirmed_config ==
                    (self.state.boot, self.state.revision, self.state.config))

    def can_play(self):
        if not (self.ready and self.state and time.monotonic()-self.received < 1.6
                and not self.busy and self.await_revision is None and self.config_confirmed()
                and self.state.mode == "REMOTE" and self.state.state in ("IDLE", "PAUSED")):
            return False
        try:
            return self.get_config() == self.state.config
        except (ValueError, OverflowError):
            return False

    def apply_config(self):
        try:
            config = self.get_config()
            if self.workspace:
                mismatch = self.workspace.incompatibility(config)
                if mismatch:
                    raise ValueError("图形超出设备范围，请调整位置、尺寸或高度。")
        except (ValueError, OverflowError) as error:
            messagebox.showerror("参数无效", str(error), parent=self.root)
            return
        self.send("CONFIG", **config.wire())

    def copy_actual(self):
        if self.state:
            try:
                self.set_config(self.state.config)
                self.select_shape()
            except ValueError as error:
                messagebox.showerror("无法载入编辑器", str(error), parent=self.root)

    def _queue_plot(self, name, config, phases, focus, enabled, title, array=None):
        array = array or self.preview_array()
        self.wanted[name] = (self.generation, config, array, tuple(phases), tuple(focus), enabled, self.plane.get(), title)

    def preview(self):
        if not self.debug_mode.get():
            self.debug_mode.set(True)
            self.toggle_debug()
        try:
            config = self.get_config()
            focus = trajectory_point(config, 0)
            array = self.preview_array()
            self._queue_plot("forecast", config, focus_phases(config, focus, array), focus, True,
                             "电脑预测 · 理想点声源 / 未下发 · " + ("采用已连接阵列" if self.ready else "采用独立设置的预测阵列"), array)
            self.tabs.select(1)
        except (ValueError, OverflowError) as error:
            messagebox.showerror("参数无效", str(error), parent=self.root)

    def refresh_plots(self):
        if self.state and self.ready:
            self.plot_actual()
        try:
            config = self.get_config()
            focus = trajectory_point(config,0)
            array = self.preview_array()
            self._queue_plot("forecast", config, focus_phases(config,focus,array), focus, True, "电脑预测 · 未下发", array)
        except ValueError:
            pass

    def plot_actual(self):
        state = self.state
        source = "Demo 模拟回读" if state.simulated else "FPGA 数字回读"
        self._queue_plot("actual", state.config, state.phases, state.focus_mm, state.output,
                         f"{source} · 相位快照 #{state.sample} → 理论声场（非实测）", state.array)

    @staticmethod
    def _compute_plot(request):
        generation, config, array, phases, focus, enabled, plane, title = request
        x,y,values = field_slice(config, phases, focus, enabled=enabled, resolution=71, plane=plane, array=array)
        return generation, (config, array, phases, focus, enabled, plane, x,y,values,title)

    def _pump_plots(self):
        for name, job in list(self.jobs.items()):
            if job.done():
                del self.jobs[name]
                try:
                    generation, payload = job.result()
                    if generation == self.generation and (name != "actual" or self.ready and time.monotonic()-self.received < 1.6):
                        (self.actual_plot if name == "actual" else self.forecast_plot).render(payload)
                except Exception as error:
                    self.status_line.set(f"预测计算失败：{error}")
                    self.log("error", self.status_line.get())
        for name in list(self.wanted):
            if name not in self.jobs:
                self.jobs[name] = self.executor.submit(self._compute_plot, self.wanted.pop(name))

    def log(self, kind, text):
        self.log_records.append({"time": datetime.now().isoformat(timespec="milliseconds"), "kind":kind, "text":text})

    def render_log(self):
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        records = [r for r in self.log_records if self.show_heartbeat.get() or " PING" not in r["text"]][-350:]
        lines=[]
        for record in records:
            text = record["text"]
            if " phases=" in text:
                text = text.split(" phases=")[0] + " phases=…（完整数据保留在导出日志）"
            lines.append(f"{record['time'][11:]}  {record['kind'].upper():8} {text}\n")
        self.log_view.insert("end", "".join(lines))
        self.log_view.configure(state="disabled")
        self.log_view.see("end")

    def tick(self):
        if self.closing:
            return
        if self.session:
            for _ in range(150):
                try:
                    event = self.session.events.get_nowait()
                except queue.Empty:
                    break
                kind = event["kind"]
                if kind in ("tx", "rx", "error", "warning", "rejected", "opening"):
                    self.log(kind, event.get("text", ""))
                if kind == "ready":
                    self.ready = True
                    self.hardware = event["array"]
                    self.workspace = event["workspace"]
                    self.workspace_line.set(f"设备声明的命令范围：x [{self.workspace.x_min_um/1000:g}, {self.workspace.x_max_um/1000:g}] mm，"
                                            f"y [{self.workspace.y_min_um/1000:g}, {self.workspace.y_max_um/1000:g}] mm，"
                                            f"z [{self.workspace.z_min_um/1000:g}, {self.workspace.z_max_um/1000:g}] mm。触觉范围尚待实测。")
                    self.capabilities = set(event["capabilities"])
                    self.hardware_line.set(f"设备报告：{self.hardware.rows}×{self.hardware.cols} · {self.hardware.count} 个阵元 · 间距 {self.hardware.pitch_um/1000:g} mm · 全阵列参与聚焦")
                    self.device_label.set("Demo 已连接" if event["demo"] else f"已连接：{event['device']}")
                    self.status_line.set("连接成功，发送图形后即可播放。")
                elif kind == "ack":
                    verb = event["verb"]
                    if verb == self.pending_verb:
                        self.pending_verb = None
                        self.await_verb = verb
                        self.await_sample = event["after_sample"]
                        self.await_revision = int(event["fields"]["rev"])
                    if verb not in ("PING", "HELLO"):
                        self.status_line.set("设备已接收，正在确认状态…")
                elif kind == "state":
                    self.state, self.received = event["state"], event["when"]
                    if (self.await_sample is not None and self.state.sample > self.await_sample
                            and self.state.revision >= self.await_revision):
                        completed = self.await_verb
                        if completed == "CONFIG":
                            if self.state.config == self.pending_config and self.state.revision == self.await_revision:
                                self.confirmed_config = (self.state.boot, self.state.revision, self.state.config)
                            self.pending_config = None
                        self.await_revision, self.await_sample = None, None
                        self.await_verb = None
                        self.busy = False
                        self.status_line.set({"CONFIG": "图形发送成功，设备已确认，可以播放。" if self.config_confirmed()
                                              else "设备返回的图形与发送内容不一致，请重新发送。",
                                              "START": "设备已开始播放。", "PAUSE": "设备已暂停。",
                                              "STOP": "设备已停止。"}.get(completed, "设备已确认。"))
                elif kind in ("error", "rejected"):
                    if kind == "error" or event.get("verb") == self.pending_verb:
                        self.busy = False
                        self.pending_verb = None
                        self.await_revision, self.await_sample = None, None
                        self.await_verb = self.pending_config = None
                    text = event.get("text", "设备操作未完成")
                    friendly = {"BUSY": "请先停止播放。", "LOCAL_CONTROL": "请先切换到电脑控制。",
                                "OUT_OF_WORKSPACE": "图形超出设备范围，请调整位置、尺寸或高度。",
                                "HARDWARE_MISMATCH": "设备设置发生变化，请重新连接。",
                                "BAD_CONFIG": "设备无法使用这些设置，请检查图形参数。",
                                "NOT_RUNNING": "设备当前没有播放。", "BAD_MODE": "设备不支持此控制模式。"}
                    self.status_line.set(text if self.debug_mode.get() else friendly.get(text, text))
                elif kind == "closed":
                    self.ready = self.busy = False
                    self.await_revision, self.await_sample = None, None
                    self.await_verb = self.pending_config = self.confirmed_config = None
                    self.device_label.set("未连接")
                    self.source_line.set("连接已断开 · 当前设备状态未知")
                    self.source_banner.configure(bg="#fbe9e9", fg=RED)
                    if self.hardware:
                        self.hardware_line.set(f"历史设备信息：{self.hardware.rows}×{self.hardware.cols} · 已断开，当前配置未确认")
        if self.busy and self.ready and time.monotonic() > self.command_deadline:
            self.status_line.set("未收到命令生效后的匹配状态；实际输出未知，正在断开。")
            self.log("error", self.status_line.get())
            self.session.close()
            self.ready = self.busy = False
        self._pump_plots()
        self.update_controls()
        # Expensive acoustic plots must not throttle ordinary playback feedback.
        if (self.debug_mode.get() and self.ready and self.state and time.monotonic()-self.received < 1.6
                and time.monotonic()-self.last_actual_plot >= .5):
            self.plot_actual()
            self.last_actual_plot = time.monotonic()
        if time.monotonic() - self.last_log_refresh > 0.8:
            if self.tabs.index(self.tabs.select()) == 4:
                self.render_log()
            self.last_log_refresh = time.monotonic()
        self.root.after(80, self.tick)

    def update_controls(self):
        alive = bool(self.session and self.session.is_alive())
        fresh = bool(self.ready and self.state and time.monotonic() - self.received < 1.6)
        remote = fresh and self.state.mode == "REMOTE"
        idle = fresh and self.state.state == "IDLE"
        free = not self.busy and self.await_revision is None
        for button, enabled in ((self.connect_button,not alive), (self.disconnect_button,alive),
                                (self.apply_button,remote and idle and free), (self.local_button,idle and free),
                                (self.remote_button,idle and free), (self.copy_button,fresh),
                                (self.start_button,self.can_play()),
                                (self.pause_button,remote and free and self.state.state == "RUNNING" if fresh else False),
                                (self.stop_button,self.ready), (self.header_stop,self.ready),
                                (self.snap_button,self.ready), (self.export_button,self.state is not None)):
            button.configure(state="normal" if enabled else "disabled")
        for target, source in ((self.playback.play_button, self.start_button),
                               (self.playback.pause_button, self.pause_button),
                               (self.playback.stop_button, self.stop_button)):
            target.configure(state=source["state"])
        play_label = "继续播放" if fresh and self.state.state == "PAUSED" else "播放"
        self.start_button.configure(text=play_label)
        self.playback.play_button.configure(text=play_label)
        self.connection_picker.configure(state="disabled" if alive else "readonly")
        serial_selected = self.mode_choice.get() == "真实串口"
        self.port_picker.configure(state="normal" if not alive and serial_selected else "disabled")
        self.baud_picker.configure(state="readonly" if not alive and serial_selected else "disabled")
        self.refresh_button.configure(state="normal" if not alive else "disabled")
        self.demo_array_picker.configure(state="readonly" if not alive and not serial_selected else "disabled")
        for entry in self.hw_entries:
            entry.configure(state="normal" if not alive and not serial_selected else "disabled")
        self.editor.apply_button.configure(state="normal" if remote and idle and free and "CUSTOM_XY" in self.capabilities else "disabled")
        demo_live = self.ready and self.session and self.session.is_demo
        for button in self.demo_buttons:
            button.configure(state="normal" if demo_live else "disabled")
        self.mute_check.configure(state="normal" if demo_live else "disabled")
        if self.state:
            age = time.monotonic()-self.received
            state = self.state
            status = {"IDLE":"待机","RUNNING":"运行","PAUSED":"暂停","FAULT":"故障"}[state.state]
            self.actual_line.set(f"{state.mode} · {status} · 输出{'启用' if state.output else '关闭'} · "
                                 f"焦点 ({state.focus_mm[0]:.1f}, {state.focus_mm[1]:.1f}, {state.focus_mm[2]:.1f}) mm · "
                                 f"配置 v{state.revision} · 快照 #{state.sample} · {age:.1f} s 前 · {state.reason}")
            if fresh:
                if self.debug_mode.get():
                    self.source_line.set("DEMO 模拟设备 · 数字状态为模拟值，声场为理论预测" if state.simulated else
                                         "真实 FPGA 回读 · 仅确认数字输出寄存器状态，不代表换能器实测")
                else:
                    mode = "设备按键控制" if state.mode == "LOCAL" else "电脑控制"
                    self.source_line.set(f"{'Demo 路径演示' if state.simulated else '设备已连接'} · {status} · {mode}")
                self.source_banner.configure(bg="#fff0d5" if state.simulated else "#dff2ed", fg=AMBER if state.simulated else TEAL)
            elif self.ready:
                self.source_line.set("设备暂未响应 · 当前状态未知")
                self.source_banner.configure(bg="#fbe9e9", fg=RED)
            try:
                config = self.get_config()
                same = config == state.config
                message = "图形发送成功，可以播放。" if same and self.config_confirmed() else (
                    "请先发送图形，确认后才能播放。" if same else "图形有更改，请重新发送后播放。")
                if same and self.config_confirmed() and state.state == "RUNNING":
                    message = "正在播放设备已接收的图形。"
                if same and self.config_confirmed() and state.state == "PAUSED":
                    message = "已暂停，可以继续播放。"
                mismatch = self.workspace.incompatibility(config) if self.workspace else ""
                if mismatch:
                    message = "图形超出设备范围，请调整位置、尺寸或高度。"
                    self.apply_button.configure(state="disabled")
                    self.editor.apply_button.configure(state="disabled")
                if self.await_revision is not None:
                    message = "正在确认设备状态…"
                if not fresh:
                    message = "设备未连接或暂未响应。"
                self.config_line.set(message)
            except (ValueError, OverflowError):
                self.config_line.set("请完成图形并检查输入的数值。")
        else:
            self.config_line.set("请先连接设备，再发送图形。")
        feedback = self.config_line.get()
        if self.busy:
            action = self.pending_verb or self.await_verb
            feedback = {"CONFIG": "正在发送图形，等待设备确认…", "START": "正在等待设备开始播放…",
                        "PAUSE": "正在等待设备暂停…", "STOP": "正在等待设备停止…"}.get(action, "正在等待设备确认…")
        if fresh and self.state.mode == "LOCAL":
            feedback = "设备按键控制中；电脑显示设备返回的播放状态。"
        self.playback.update_state(self.state, fresh, feedback)
        self.preset_preview.apply_button.configure(state=self.apply_button["state"])

    def save_config(self):
        try:
            config = self.get_config()
            filename = filedialog.asksaveasfilename(parent=self.root,defaultextension=".json",filetypes=[("配置 JSON","*.json")],initialfile="haptics-config.json")
            if filename:
                Path(filename).write_text(json.dumps({"schema":"haptics-config-2","config":config.wire()},ensure_ascii=False,indent=2),encoding="utf-8")
        except (OSError, ValueError, OverflowError) as error:
            messagebox.showerror("保存失败",str(error),parent=self.root)

    def load_config(self):
        filename = filedialog.askopenfilename(parent=self.root,filetypes=[("配置 JSON","*.json")])
        if not filename:
            return
        try:
            if Path(filename).stat().st_size > 65536:
                raise ValueError("配置文件过大")
            data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
            config = config_from_document(data)
            self.set_config(config)
            self.select_shape()
            self.status_line.set("图形已打开，可继续编辑或发送到设备。")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            messagebox.showerror("读取失败",str(error),parent=self.root)

    def export_snapshot(self):
        if not self.state:
            return
        filename = filedialog.asksaveasfilename(parent=self.root,defaultextension=".csv",initialfile="phase-snapshot.csv",filetypes=[("CSV","*.csv")])
        if not filename:
            return
        state = self.state
        try:
            with open(filename,"w",newline="",encoding="utf-8-sig") as stream:
                writer=csv.writer(stream)
                writer.writerow(["source","boot","sample","config_rev","state","output","age_s","channel","x_mm","y_mm","phase_code","phase_steps"])
                age=time.monotonic()-self.received
                for channel,(xyz,phase) in enumerate(zip(array_coordinates(state.array),state.phases)):
                    writer.writerow(["DEMO" if state.simulated else "FPGA_DIGITAL_READBACK",state.boot,state.sample,state.revision,
                                     state.state,int(state.output),round(age,3),channel,xyz[0],xyz[1],phase,state.config.phase_steps])
        except OSError as error:
            messagebox.showerror("导出失败",str(error),parent=self.root)

    def export_log(self):
        filename=filedialog.asksaveasfilename(parent=self.root,defaultextension=".jsonl",initialfile="haptics-session.jsonl",filetypes=[("JSON Lines","*.jsonl")])
        if filename:
            try:
                Path(filename).write_text("".join(json.dumps(record,ensure_ascii=False)+"\n" for record in self.log_records),encoding="utf-8")
            except OSError as error:
                messagebox.showerror("导出失败",str(error),parent=self.root)

    def import_measurement(self):
        filename=filedialog.askopenfilename(parent=self.root,filetypes=[("扫描 CSV","*.csv")])
        if not filename:
            return
        try:
            if Path(filename).stat().st_size > 5_000_000:
                raise ValueError("首版支持不超过 5 MB 的扫描文件")
            with open(filename,encoding="utf-8-sig",newline="") as stream:
                reader=csv.DictReader(stream)
                rows=[]
                for row in reader:
                    if len(rows)>=50000:
                        raise ValueError("扫描点超过 50,000")
                    rows.append([float(row[key]) for key in ("x_mm","y_mm","value")])
            points=np.asarray(rows)
            if len(points)==0 or not np.isfinite(points).all() or np.any(points[:,2]<0):
                raise ValueError("数据应为有限坐标和非负接收幅值")
            if self.measurement_colorbar:
                self.measurement_colorbar.remove()
            self.measurement_axis.clear()
            scatter=self.measurement_axis.scatter(points[:,0],points[:,1],c=points[:,2],cmap="viridis",s=22)
            self.measurement_axis.set(xlabel="x / mm",ylabel="y / mm",title="导入扫描值 · 单位与校准由数据提供者确认")
            self.measurement_axis.set_aspect("equal",adjustable="datalim")
            self.measurement_colorbar=self.measurement_figure.colorbar(scatter,ax=self.measurement_axis)
            self.measurement_colorbar.set_label("原始相对幅值")
            self.measurement_label.set(f"{Path(filename).name} · {len(points)} 个点 · 导入数据，软件未验证采集来源")
            self.measurement_canvas.draw_idle()
        except (OSError,ValueError,KeyError,TypeError) as error:
            messagebox.showerror("导入失败",str(error),parent=self.root)

    def close(self):
        if self.closing:
            return
        self.closing=True
        if self.session:
            self.session.close()
        self.executor.shutdown(wait=False,cancel_futures=True)
        deadline=time.monotonic()+1.5
        def finish():
            if self.session and self.session.is_alive() and time.monotonic()<deadline:
                self.root.after(50,finish)
            else:
                self.root.destroy()
        finish()


def main():
    import argparse
    parser=argparse.ArgumentParser(description="触见上位机")
    parser.add_argument("--demo",action="store_true",help="启动后连接模拟设备，不自动播放")
    args=parser.parse_args()
    root=tk.Tk()
    app=App(root)
    if args.demo:
        root.after(300,app.connect)
    root.mainloop()


if __name__ == "__main__":
    main()
