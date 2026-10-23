"""Visible sound-field page: worker prediction and manually located ADC samples."""
import csv
from dataclasses import asdict
import json
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from .acoustics import FieldSettings, compute_field
from .field_scan import ScanBook
from .measurement_frame import TableFrame, placement_record
from .coordinate_help import show as show_coordinate_help
from .field_compare import ScanModel, compare_scan
from .model import Config, focus_phases, trajectory_point


class AcousticPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.future = None
        self.future_key = None
        self.rendered_key = None
        self.payload = None
        self.next_model = 0.
        self.next_capture = 0.
        self.pending_scan = None
        self.last_scan_condition = None
        self.scan_book = ScanBook()
        self.wave_point = None
        self.wave_index = None
        self.wave_caption = ""
        self.wave_choice = tk.StringVar()
        self.wave_span = tk.StringVar(value="前 100 μs")
        self.wave_status = tk.StringVar(value="采集后显示该位置的 ADC 波形；点击测点可回看已保存窗口。")
        self.scan_scatter = None
        self.scan_selection = None
        self.scan_plane_z = None
        self.compare_future = None
        self.compare_key = self.compare_future_key = None
        self.comparison = None
        self.compare_colorbar = None
        self.compare_refs = []
        self.compare_reference_position = None
        self.compare_reference = tk.StringVar()
        self.compare_direction = tk.StringVar(value="x 剖面")
        self.compare_fixed = tk.StringVar(value="94")
        self.legacy_phase = tk.StringVar(value="请选择时序")
        self.legacy_diameter = tk.StringVar(value="10")
        self.compare_status = tk.StringVar(value="先采集或导入测点，再进行同坐标对比。")
        self.compare_stats = tk.StringVar()
        self.source = tk.StringVar(value="待发图形")
        self.plane = tk.StringVar(value="XY")
        self.quantity = tk.StringVar(value="相对声压")
        self.diameter = tk.StringVar(value="10")
        self.span = tk.StringVar(value="60")
        self.depth = tk.StringVar(value="220")
        self.phase_encoding = tk.StringVar(value="自动识别")
        self.status = tk.StringVar(value="离线也可查看预测；测绘使用接收板的实际采样。")
        self.stats = tk.StringVar()
        self.scan_status = tk.StringVar(value="先固定发射阵列，手动移动接收器到已知坐标，再采集该测点。")
        self.table_frame = TableFrame()
        self.coordinate_mode = tk.StringVar(value="底面坐标（加高版）")
        self.position_reference = tk.StringVar(value="接收头 R")
        self.previous_mode = self.coordinate_mode.get()
        self.previous_reference = self.position_reference.get()
        self.coordinate_status = tk.StringVar()
        self.positions = [tk.StringVar(value=v) for v in ("132", "94", "24.7")]
        self.colorbar = self.scan_colorbar = None
        self.parameters_shown = False
        ttk.Style(self).configure("Scan.TButton", padding=(6, 4))
        notebook = self.notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        prediction = ttk.Frame(notebook)
        self.scan_tab = scan = ttk.Frame(notebook)
        self.compare_tab = comparison = ttk.Frame(notebook)
        notebook.add(prediction, text="计算声场")
        notebook.add(scan, text="接收板测点")
        notebook.add(comparison, text="预测 / 实测对比")

        row = ttk.Frame(prediction, padding=8)
        row.pack(fill="x")
        ttk.Label(row, text="数据来源").pack(side="left")
        ttk.Combobox(row, textvariable=self.source, values=("待发图形", "设备回读"),
                     state="readonly", width=12).pack(side="left", padx=5)
        ttk.Combobox(row, textvariable=self.plane, values=("XY", "XZ"),
                     state="readonly", width=4).pack(side="left", padx=5)
        ttk.Combobox(row, textvariable=self.quantity,
                     values=("相对声压", "相对辐射压力", "相对 dB"), state="readonly",
                     width=14).pack(side="left", padx=5)
        ttk.Button(row, text="更新", width=5, command=self.refresh).pack(side="left", padx=5)
        ttk.Button(row, text="模型参数", width=8, command=self.toggle_parameters).pack(side="left", padx=5)
        ttk.Button(row, text="导出预测 CSV", command=self.export_prediction).pack(side="right")
        row = ttk.Frame(prediction, padding=(8, 0, 8, 5))
        self.profile_row = row
        row.pack(fill="x")
        ttk.Label(row, text="离线阵列模板").pack(side="left", padx=(0, 6))
        self.profile_buttons = []
        for size, pitch, text in ((4, 11, "4×4 样机 / 11 mm"), (8, 10.3, "8×8 R2 / 10.3 mm")):
            button = ttk.Button(row, text=text, command=lambda n=size, p=pitch: self.select_profile(n, p))
            button.pack(side="left", padx=4)
            self.profile_buttons.append(button)
        ttk.Label(row, text="连接后采用设备声明的坐标", style="Muted.TLabel").pack(side="left", padx=8)
        row = ttk.Frame(prediction, padding=(8, 0, 8, 5))
        self.parameter_row = row
        row.pack(fill="x")
        for label, variable in (("等效口径 / mm", self.diameter),
                                ("视野半宽 / mm", self.span), ("深度上限 / mm", self.depth)):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 4))
            ttk.Entry(row, textvariable=variable, width=6).pack(side="left", padx=(0, 10))
        ttk.Button(row, text="模型说明", command=self.model_help).pack(side="right")
        self.phase_row = ttk.Frame(prediction, padding=(8, 0, 8, 5))
        self.phase_row.pack(fill="x")
        ttk.Label(self.phase_row, text="相位码时序").pack(side="left", padx=(0, 8))
        ttk.Combobox(self.phase_row, textvariable=self.phase_encoding, state="readonly", width=19,
                     values=("自动识别", "相位码表示提前", "相位码表示延迟")).pack(side="left")
        ttk.Label(self.phase_row, text="按控制器方波发生器选择", style="Muted.TLabel").pack(side="left", padx=8)
        self.status_widget = self.paragraph(prediction, textvariable=self.status)
        self.paragraph(prediction, textvariable=self.stats)
        self.figure = Figure(figsize=(8, 4.4), facecolor="white")
        self.figure.subplots_adjust(left=.08, right=.96, bottom=.22, top=.88, wspace=.55)
        self.axis = self.figure.add_subplot(121)
        self.section = self.figure.add_subplot(122)
        self.canvas = FigureCanvasTkAgg(self.figure, prediction)
        NavigationToolbar2Tk(self.canvas, prediction, pack_toolbar=False).pack(side="bottom", fill="x")
        self.paragraph(prediction, side="bottom", text="预测未做声压标定，白线不代表可感知范围。"
                       "相对幅值以单阵元轴上100 mm为基准。z 是离发射面的距离，PCB竖放时不是离桌高度。")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        row = ttk.Frame(scan, padding=8)
        row.pack(fill="x")
        ttk.Label(row, text="位置坐标").pack(side="left")
        self.coordinate_selector = ttk.Combobox(row, textvariable=self.coordinate_mode, width=20,
            state="readonly", values=("底面坐标（加高版）", "阵列坐标（旧方式）"))
        self.coordinate_selector.pack(side="left", padx=6)
        self.coordinate_selector.bind("<<ComboboxSelected>>", self.change_coordinates)
        self.reference_selector = ttk.Combobox(row, textvariable=self.position_reference, width=14,
            state="readonly", values=("接收头 R", "托盘左前角 A"))
        self.reference_selector.pack(side="left")
        self.reference_selector.bind("<<ComboboxSelected>>", self.change_coordinates)
        ttk.Button(row, text="坐标示意图", command=lambda: show_coordinate_help(self, self.table_frame)).pack(side="left", padx=8)
        self.paragraph(scan, text="原点：左增高底座左前外角。X 向右、Y 向后；Z 是接收面离桌高度。托盘保持4针口朝右。")
        row = ttk.Frame(scan, padding=(8, 0, 8, 4))
        row.pack(fill="x")
        self.position_entries = []
        for name, variable in zip(("x / mm", "y / mm", "z / mm"), self.positions):
            ttk.Label(row, text=name).pack(side="left", padx=(0, 4))
            entry = ttk.Entry(row, textvariable=variable, width=7)
            entry.pack(side="left", padx=(0, 10))
            self.position_entries.append(entry)
        self.capture_button = ttk.Button(row, text="采集当前测点（3 次）", command=self.acquire, style="Scan.TButton")
        self.capture_button.pack(side="left")
        self.paragraph(scan, textvariable=self.coordinate_status)
        for variable in self.positions:
            variable.trace_add("write", lambda *_: self.update_coordinate_status())
        self.update_coordinate_status()
        row = ttk.Frame(scan, padding=(8, 0, 8, 5))
        row.pack(fill="x")
        self.cancel_button = ttk.Button(row, text="取消采集", command=self.cancel_scan, style="Scan.TButton")
        self.cancel_button.pack(side="left")
        self.new_button = ttk.Button(row, text="新建测绘", command=self.new_scan, style="Scan.TButton")
        self.new_button.pack(side="left", padx=8)
        self.import_button = ttk.Button(row, text="导入 CSV", command=self.import_scan, style="Scan.TButton")
        self.import_button.pack(side="left")
        self.export_button = ttk.Button(row, text="导出 CSV", command=self.export_scan, style="Scan.TButton")
        self.export_button.pack(side="left", padx=8)
        ttk.Button(row, text="逐通道校准", command=self.show_calibration, style="Scan.TButton").pack(side="left")
        ttk.Button(row, text="显示此 z 平面", command=self.render_scan, style="Scan.TButton").pack(side="right")
        self.paragraph(scan, textvariable=self.scan_status)
        row = ttk.Frame(scan, padding=(8, 0, 8, 4))
        row.pack(fill="x")
        ttk.Label(row, text="波形窗口").pack(side="left", padx=(0, 5))
        self.wave_selector = ttk.Combobox(row, textvariable=self.wave_choice, width=47,
                                          state="disabled")
        self.wave_selector.pack(side="left", fill="x", expand=True)
        self.wave_selector.bind("<<ComboboxSelected>>", self.select_wave)
        ttk.Label(row, text="时间范围").pack(side="left", padx=(12, 5))
        self.wave_range = ttk.Combobox(row, textvariable=self.wave_span, width=12,
            values=("前 100 μs", "完整窗口"), state="readonly")
        self.wave_range.pack(side="left")
        self.wave_range.bind("<<ComboboxSelected>>", lambda _: self.render_wave())
        self.paragraph(scan, textvariable=self.wave_status)
        self.scan_figure = Figure(figsize=(8, 4), facecolor="white")
        self.scan_figure.subplots_adjust(left=.08, right=.97, bottom=.24, top=.83, wspace=.70)
        grid = self.scan_figure.add_gridspec(1, 2, width_ratios=(1, 1.65))
        self.scan_axis = self.scan_figure.add_subplot(grid[0])
        self.wave_axis = self.scan_figure.add_subplot(grid[1])
        self.scan_canvas = FigureCanvasTkAgg(self.scan_figure, scan)
        self.scan_canvas.mpl_connect("pick_event", self.select_map_point)
        footer = ttk.Frame(scan, padding=(8, 4))
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="测量说明", command=self.scan_help, style="Scan.TButton").pack(side="right")
        ttk.Label(footer, text="手动填写接收器位置。电压未标定声压，未测区域留空。",
                  style="Muted.TLabel").pack(side="left")
        self.scan_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.render_scan()
        self.build_comparison(comparison)
        self.profile_row.pack_forget()
        self.parameter_row.pack_forget()
        self.phase_row.pack_forget()

    @property
    def busy(self):
        return self.pending_scan is not None

    @staticmethod
    def paragraph(parent, side="top", **kwargs):
        widget = ttk.Label(parent, padding=(8, 4), wraplength=800, **kwargs)
        widget.pack(side=side, fill="x")
        widget.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(300, e.width-20)))
        return widget

    @property
    def table_mode(self):
        return self.coordinate_mode.get() == "底面坐标（加高版）"

    def coordinate_label(self):
        return "底面坐标" if self.table_mode else "阵列坐标"

    def current_position(self):
        values = tuple(float(v.get()) for v in self.positions)
        if not self.table_mode:
            return values, None
        reference = "TRAY_A" if self.position_reference.get() == "托盘左前角 A" else "RECEIVER_R"
        placement = placement_record(self.table_frame, values, reference)
        return self.table_frame.to_array(placement["receiver_table_mm"]), placement

    def display_position(self, position):
        return self.table_frame.to_table(position) if self.table_mode else tuple(position)

    def update_coordinate_status(self):
        try:
            position, placement = self.current_position()
            coordinates = ", ".join(f"{v:.3f}" for v in position)
            if placement:
                table = ", ".join(f"{v:.3f}" for v in placement["receiver_table_mm"])
                self.coordinate_status.set(f"接收头 R：底面 ({table}) mm → 阵列 ({coordinates}) mm · 支架名义尺寸，尚未实测校准")
            else:
                self.coordinate_status.set(f"接收头 R：阵列 ({coordinates}) mm · 原点为发射面中心，z 朝向接收器")
        except (ValueError, OverflowError):
            self.coordinate_status.set("请输入有效的位置坐标。")

    def change_coordinates(self, event=None):
        new_mode, new_ref = self.coordinate_mode.get(), self.position_reference.get()
        self.coordinate_mode.set(self.previous_mode)
        self.position_reference.set(self.previous_reference)
        try:
            position, _ = self.current_position()
        except (ValueError, OverflowError):
            position = (0., 0., 100.)
        self.coordinate_mode.set(new_mode)
        self.position_reference.set(new_ref if self.table_mode else "接收头 R")
        values = self.display_position(position)
        if self.table_mode and self.position_reference.get() == "托盘左前角 A":
            values = (values[0]-self.table_frame.tray_offset_x_mm,
                      values[1]-self.table_frame.tray_offset_y_mm, values[2])
        self.previous_mode = self.coordinate_mode.get()
        self.previous_reference = self.position_reference.get()
        for variable, value in zip(self.positions, values):
            variable.set(f"{value:.6f}".rstrip("0").rstrip("."))
        self.compare_fixed.set("94" if self.table_mode else "0")
        self.update_wave_choices()
        self.refresh_comparison()
        self.update_coordinate_status()
        self.render_scan()

    def model_help(self):
        messagebox.showinfo("声场模型与数据边界",
            "预测叠加每路的单频声波，使用阵元坐标、朝向、启用通道和电气相位。\n\n"
            "口径10 mm为等效圆活塞假设，不是MA40S4S已标定的指向性；填0使用各向同性点声源。"
            "相对基准是单位阵元在轴上100 mm的载波幅值，换阵列或停用通道不会重新归一化。\n\n"
            "未包含反射、皮肤、固有幅相误差和实测驱动电压，也不预测触觉阈值或门控平均力。"
            "回读相位包含板端校准修正，不再重复添加；未测得的声学误差不会被自动假定已知。\n\n"
            "F103/F411使用(slot+code)生成方波，相位码代表提前。Demo与离线默认使用抽象延迟码。"
            "其他真实设备必须核实发生器时序后选择；同一数字按不同含义解释会产生不同声场。\n\n"
            "半幅边界取切片最大峰值的−6 dB连通区域，尺寸受采样步长和视野边界限制。"
            "XZ最大声压位置可能与设置焦点不同。\n\n"
            "真实测点使用接收板ADC端40 kHz峰值电压；接收增益、方向、削顶和位置误差都会影响结果，"
            "未触及ADC电源轨也不能排除前级放大器削顶。",
            parent=self)

    def toggle_parameters(self):
        self.parameters_shown = not self.parameters_shown
        for row in (self.profile_row, self.parameter_row, self.phase_row):
            if self.parameters_shown:
                row.pack(fill="x", before=self.status_widget)
            else:
                row.pack_forget()

    def scan_help(self):
        messagebox.showinfo("接收板测量与波形",
            "接收板模拟输出接采样器并共地：MG24 用 D0，旧 STM32 原型用 PA0；供电按接收板说明。\n\n"
            "底面坐标使用加高版 P5 支架。未装30 mm增高件时不能套用本高度；实际尺寸仍需测量。\n\n"
            "测绘前使用固定点、100%等级、关闭调制，再主动启动输出。"
            "位置由你填写，软件不能检测接收器位置；每点采集三个原始窗口，取40 kHz峰值中位数，"
            "不插值补造未测区域。Demo仅演示采集流程。\n\n"
            "波形的蓝线是原始ADC电压，橙色虚线是40 kHz拟合分量加直流偏置，"
            "不是另外测得的波形。统计针对完整窗口，均为ADC端电压，不是声压或触觉强度。\n\n"
            "选择波形窗口，或点击测点回看该位置最近的窗口。停止/断连后显示历史记录；"
            "取消未完成采集后回到已保存窗口。CSV保留原码、位置与采集参数，导入后可回放。\n\n"
            "新采集的参考电压/分压参数沿用接收波形页，历史按记录内参数换算。"
            "ADC未触及量程边界仍不能排除前级削顶；40 kHz拟合不会恢复失真信号。",
            parent=self)

    def on_connect(self):
        self.cancel_scan()
        self.source.set("设备回读")
        self.payload = None
        self.rendered_key = None
        self.next_model = 0
        self.clear_prediction("等待设备状态")

    def settings(self, measurement=False):
        encoding = self.phase_encoding.get()
        direction = 1
        if encoding == "相位码表示提前":
            direction = -1
        elif encoding == "自动识别" and (measurement or self.source.get() == "设备回读") and self.app.ready:
            session = self.app.session
            device = (session.hello_fields or {}).get("device", "")
            if not session.is_demo:
                if device in ("STM32F103C8T6", "NUCLEO-F411RE"):
                    direction = -1
                elif "TANG" in device.upper() or device == "FPGA":
                    direction = 1
                else:
                    raise ValueError("请在模型参数中核实并选择此设备的相位码时序")
        return FieldSettings(self.plane.get(), float(self.diameter.get()),
                             float(self.span.get()), float(self.depth.get()),
                             phase_direction=direction).validate()

    def select_profile(self, size, pitch):
        if self.app.session and self.app.session.is_alive():
            return
        self.app.geometry_profile = None
        self.app.demo_array_choice.set(f"{size} × {size} · {size*size} 路")
        self.app.hw_rows.set(str(size))
        self.app.hw_cols.set(str(size))
        self.app.hw_pitch.set(str(pitch))
        self.source.set("待发图形")
        self.refresh()

    def show_calibration(self):
        self.app.debug_mode.set(True)
        self.app.toggle_debug()
        self.app.tabs.select(self.app.measured_tab)
        self.app.measurement_tabs.select(self.app.calibration_panel)

    def request(self, fresh):
        if self.source.get() == "设备回读" and not fresh:
            raise ValueError("设备未连接或回传已过期，不能显示实时声场")
        settings = self.settings()
        if self.source.get() == "设备回读":
            if not fresh:
                raise ValueError("设备未连接或回传已过期，不能显示实时声场")
            state = self.app.state
            config, array, phases, focus = state.config, state.array, state.phases, state.focus_mm
            mask, enabled = state.channel_mask, state.output
            source = "Demo 相位回读 → 预测" if state.simulated else "设备相位回读 → 预测（非实测）"
            reason = "同相通道测试；标记仅为坐标参考" if "TEST" in state.reason else "十字为设备报告的焦点"
            details = f"{source} · {array.rows}×{array.cols} / {array.pitch_um/1000:g} mm · #{state.sample} · {reason}"
            identity = (state.boot, state.revision)
        else:
            config, array = self.app.get_config(), self.app.preview_array()
            focus = tuple(trajectory_point(config, 0))
            phases = tuple(focus_phases(config, focus, array))
            if settings.phase_direction == -1:
                phases = tuple((-int(p)) % config.phase_steps for p in phases)
            mask, enabled = (1 << array.count)-1, True
            details = f"待发图形预测 · {array.rows}×{array.cols} / {array.pitch_um/1000:g} mm · 尚未发送"
            identity = None
        details += " · 提前码" if settings.phase_direction == -1 else " · 延迟码"
        phases = tuple(int(value) for value in phases)
        key = (self.app.generation, self.source.get(), identity, config,
               array.resolved_geometry().identity, tuple(phases), tuple(focus), mask, enabled, settings)
        return key, (array, config, tuple(phases), tuple(focus), enabled, mask, settings, details)

    @staticmethod
    def compute(request):
        array, config, phases, focus, enabled, mask, settings, details = request
        return request, compute_field(array, config, phases, focus, enabled, mask, settings)

    def refresh(self):
        self.rendered_key = None
        self.next_model = 0

    def tick(self, fresh, visible):
        if self.busy:
            pending = self.pending_scan
            try:
                if not fresh or ScanBook.context_for(self.app.state) != ScanBook.context_for(pending["state"]):
                    raise ValueError("采集期间设备状态改变，当前测点未保存")
                if time.monotonic()-pending["sent"] > 4:
                    raise ValueError("采集等待超时，当前测点未保存")
                if not pending["waiting"] and time.monotonic() >= pending["next"]:
                    if self.app.request_capture():
                        pending["waiting"] = True
                        pending["sent"] = time.monotonic()
            except ValueError as error:
                self.cancel_scan(str(error))
        allowed = bool(fresh and self.app.session and "ADC_CAPTURE" in self.app.capabilities
                       and not self.app.busy and not self.app.calibration_panel.busy
                       and not self.app.receiver_panel.pending)
        condition = ""
        if not fresh:
            condition = "未连接或回传已过期，不能采集新测点"
        elif "ADC_CAPTURE" not in self.app.capabilities:
            condition = "当前固件没有接收采样功能，不能采集新测点"
        elif self.app.busy or self.app.calibration_panel.busy:
            condition = "等待当前设备操作完成"
        elif self.app.receiver_panel.pending:
            condition = "等待接收波形页的采样完成"
        if allowed:
            try:
                ScanBook.context_for(self.app.state)
            except ValueError as error:
                allowed = False
                condition = str(error)
        if not self.busy and condition != self.last_scan_condition:
            self.last_scan_condition = condition
            if condition:
                self.scan_status.set(condition+f" · 已有 {len(self.scan_book.points)} 个原始窗口仅作历史记录。")
            else:
                self.render_scan()
        self.capture_button.configure(state="normal" if allowed and not self.busy
                                      and time.monotonic() >= self.next_capture else "disabled")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self.export_button.configure(state="normal" if self.scan_book.points and not self.busy else "disabled")
        for widget in (*self.position_entries, self.new_button, self.import_button):
            widget.configure(state="disabled" if self.busy else "normal")
        self.wave_selector.configure(state="readonly" if self.scan_book.points and not self.busy else "disabled")
        self.coordinate_selector.configure(state="disabled" if self.busy else "readonly")
        self.reference_selector.configure(state="disabled" if self.busy or not self.table_mode else "readonly")
        connected = bool(self.app.session and self.app.session.is_alive())
        for button in self.profile_buttons:
            button.configure(state="disabled" if connected else "normal")
        if not visible:
            return
        if self.notebook.select() == str(self.compare_tab):
            self.tick_comparison()
            return
        try:
            key, request = self.request(fresh)
        except (ValueError, OverflowError) as error:
            self.clear_prediction(str(error))
            self.status.set(str(error))
            return
        if self.future is not None and self.future.done():
            try:
                payload = self.future.result()
                # A moving focus may change during calculation. Render the
                # labelled snapshot if its boot/config/model/output context is
                # still current; do not starve animation by requiring the next
                # telemetry packet to have exactly the same focus and phases.
                if (self.future_key[:5], self.future_key[7:]) == (key[:5], key[7:]):
                    self.payload = payload
                    self.rendered_key = self.future_key
                    self.render_prediction()
            except Exception as error:
                self.status.set(f"声场计算失败：{error}")
            finally:
                self.future = None
        if (self.payload is not None and self.rendered_key is not None
                and (self.rendered_key[:5], self.rendered_key[7:]) != (key[:5], key[7:])):
            self.clear_prediction("条件改变，正在更新预测")
        if self.future is None and key != self.rendered_key and time.monotonic() >= self.next_model:
            self.future_key = key
            self.future = self.app.executor.submit(self.compute, request)
            self.next_model = time.monotonic()+.7
        if self.payload and self.quantity.get() != getattr(self, "rendered_quantity", None):
            self.render_prediction()

    def clear_prediction(self, text):
        if getattr(self, "clear_text", None) == text and self.payload is None:
            return
        self.clear_text = text
        self.payload = None
        self.rendered_key = None
        if self.colorbar is not None:
            self.colorbar.remove()
            self.colorbar = None
        for axis in (self.axis, self.section):
            axis.clear()
        self.axis.set_title(text, fontsize=10)
        self.stats.set("")
        self.canvas.draw_idle()

    def render_prediction(self):
        request, result = self.payload
        array, config, phases, focus, enabled, mask, settings, details = request
        quantity = self.quantity.get()
        values, unit = result.values(quantity)
        if self.colorbar is not None:
            self.colorbar.remove()
        self.axis.clear()
        self.section.clear()
        reference_max = max(1, array.count)
        vmax = reference_max**2 if quantity == "相对辐射压力" else reference_max
        vmin = 0
        if quantity == "相对 dB":
            vmin, vmax = -30, 20*np.log10(reference_max)
        image = self.axis.imshow(values, origin="lower", aspect="equal", cmap="magma",
            extent=(result.horizontal[0], result.horizontal[-1], result.vertical[0], result.vertical[-1]),
            vmin=vmin, vmax=vmax)
        self.colorbar = self.figure.colorbar(image, ax=self.axis, fraction=.046, pad=.04)
        self.colorbar.set_label(unit, fontsize=8)
        peak = result.peak
        if peak > 1e-10 and result.amplitude.min() < peak/2 < result.amplitude.max():
            self.axis.contour(result.horizontal, result.vertical, result.amplitude,
                              levels=[peak/2], colors="white", linewidths=.8)
        second = focus[1] if settings.plane == "XY" else focus[2]
        self.axis.plot(focus[0], second, "+", color="#56dacb", ms=10)
        slice_title = (f"XY · z={focus[2]:g} mm（预测）" if settings.plane == "XY"
                       else f"XZ · y={focus[1]:g} mm（预测）")
        self.axis.set(xlabel="x / mm", ylabel="y / mm" if settings.plane == "XY" else "z / mm",
                      title=slice_title)
        row = int(np.argmin(abs(result.vertical-second)))
        self.section.plot(result.horizontal, result.amplitude[row], color="#2463c5")
        if peak > 1e-10:
            self.section.axhline(peak/2, color="#b46125", ls="--", label="切片峰值的一半（−6 dB）")
            self.section.legend(fontsize=7)
        self.section.set(xlabel="x / mm", ylabel="相对载波幅值", title="穿过参考坐标的横向剖面")
        self.section.grid(alpha=.2)
        self.status.set(details)
        width = (f"主瓣半幅宽约 {result.width_mm:.1f} × {result.height_mm:.1f} mm"
                 if result.width_mm is not None else "输出关闭，预测为零")
        if result.clipped:
            width += "（边界截断，尺寸不完整）"
        self.stats.set(f"启用 {result.enabled_channels}/{array.count} 路 · 切片峰值 {peak:.2f} · "
                       f"参考坐标幅值 {result.target_amplitude:.2f} · {width}\n"
                       f"等级 {config.level}% / 调制 {config.mod_hz} Hz：未按等级线性缩放电压，"
                       "未折算门控平均力；白线仅表示计算声压半幅边界。")
        self.rendered_quantity = quantity
        self.canvas.draw_idle()

    def acquire(self):
        if self.busy or str(self.capture_button["state"]) != "normal":
            return
        try:
            position, placement = self.current_position()
            if not np.isfinite(position).all() or any(abs(v) > 300 for v in position[:2]) or not 10 <= position[2] <= 400:
                raise ValueError("测点坐标无效")
            state = self.app.state
            context = ScanBook.context_for(state)
            if self.scan_book.context is not None and context != self.scan_book.context:
                raise ValueError("输出配置与旧测绘不同，请先导出并新建测绘")
            if any(p.verb == "CAPTURE" for p in self.app.session.pending.values()):
                raise ValueError("请等待前一次接收采样完成")
            receiver = self.app.receiver_panel
            vref = float(receiver.vref.get()) if receiver.capture is not None else None
            divider = float(receiver.divider.get())
            prediction_model = self.scan_book.prediction_model
            if prediction_model is None:
                settings = self.settings(measurement=True)
                prediction_model = ScanModel(settings.diameter_mm, settings.phase_direction).document()
            self.app.receiver_panel.continuous.set(False)
            if not self.app.request_capture():
                raise ValueError("设备未接受采样请求")
            self.pending_scan = dict(state=state, position=position, captures=[], vref=vref,
                                     divider=divider, sent=time.monotonic(), waiting=True, next=0.,
                                     prediction_model=prediction_model, placement=placement)
            self.scan_status.set(f"正在测量 {self.display_position(position)} mm，保持接收器不动（0/3）。")
            self.wave_point = None
            self.wave_index = None
            self.wave_choice.set("等待本次第 1 个窗口")
            self.render_wave()
        except (ValueError, OverflowError) as error:
            self.scan_status.set(str(error))

    def accept(self, capture, when):
        # Includes captures requested on another page: the ADC rate limit is
        # shared by the device, not local to this panel.
        self.next_capture = max(self.next_capture, when+.25)
        if not self.busy or not self.pending_scan["waiting"]:
            return
        pending = self.pending_scan
        try:
            # Validate without appending partial results.
            probe = ScanBook()
            if pending["vref"] is None:
                pending["vref"] = capture.full_scale_mv
            probe.add_windows(pending["state"], [capture], pending["position"], pending["vref"], pending["divider"], placement=pending["placement"])
            if when < pending["sent"]:
                raise ValueError("采样早于本次请求，当前测点未保存")
            pending["captures"].append(capture)
            pending["waiting"] = False
            pending["next"] = time.monotonic()+.25
            count = len(pending["captures"])
            self.scan_status.set(f"正在测量 {pending['position']} mm，保持接收器不动（{count}/3）。")
            self.wave_point = probe.points[0]
            self.wave_caption = f"本次第 {count}/3 个窗口 · 采集未完成，尚未保存"
            self.wave_choice.set(f"本次第 {count}/3 个窗口")
            self.render_wave()
            if count == 3:
                self.scan_book.add_windows(pending["state"], pending["captures"],
                    pending["position"], pending["vref"], pending["divider"], pending["prediction_model"], pending["placement"])
                amplitudes = [c.analyze(pending["vref"], pending["divider"])["peak40_mv"] for c in pending["captures"]]
                self.pending_scan = None
                self.update_wave_choices(select_latest=True)
                self.render_scan()
                if max(amplitudes)-min(amplitudes) > max(2., .2*np.median(amplitudes)):
                    self.scan_status.set(self.scan_status.get()+" · 三次读数波动明显，建议固定接收器后复测。")
        except (ValueError, OverflowError) as error:
            self.cancel_scan(str(error))

    def reject(self, text):
        if self.busy:
            self.cancel_scan(f"采集未完成：{text}；当前测点未保存")

    def cancel_scan(self, text="采集已取消；当前未完成测点没有保存"):
        if self.busy:
            self.pending_scan = None
            self.scan_status.set(text)
            self.update_wave_choices(select_latest=True)
            self.render_wave()

    def new_scan(self):
        if self.scan_book.points and not messagebox.askyesno("新建测绘", "已有测点将从页面清除；未导出的数据会丢失，继续吗？", parent=self):
            return
        self.scan_book = ScanBook()
        self.compare_reference_position = None
        self.update_wave_choices()
        self.render_scan()

    def window_label(self, index, point):
        coordinates = ", ".join(f"{v:g}" for v in self.display_position(point.position_mm))
        return f"窗口 {index+1} · ({coordinates}) mm · 设备时间 {point.capture.uptime_ms/1000:.3f} s"

    def update_wave_choices(self, select_latest=False):
        values = [self.window_label(i, p) for i, p in enumerate(self.scan_book.points)]
        self.wave_selector.configure(values=values)
        if not values:
            self.wave_index = None
            self.wave_point = None
            self.wave_choice.set("")
            return
        if select_latest or self.wave_index is None or self.wave_index >= len(values):
            self.wave_index = len(values)-1
        self.wave_selector.current(self.wave_index)
        self.select_wave()

    def select_wave(self, event=None):
        if self.busy:
            return
        index = self.wave_selector.current()
        if not 0 <= index < len(self.scan_book.points):
            return
        self.wave_index = index
        self.wave_point = self.scan_book.points[index]
        self.wave_caption = f"已保存窗口 {index+1} · 记录回放"
        if self.scan_book.imported:
            self.wave_caption = "导入记录（来源由文件声明） · "+self.wave_caption
        self.render_wave()

    def select_map_point(self, event):
        if self.busy or event.artist is not self.scan_scatter or not len(event.ind):
            return
        x, y = self.scan_scatter.get_offsets()[event.ind[0]]
        z = self.scan_plane_z
        for index in range(len(self.scan_book.points)-1, -1, -1):
            position = self.scan_book.points[index].position_mm
            display = self.display_position(position)
            if np.allclose(display[:2], (x, y), atol=1e-6) and abs(position[2]-z) <= .001:
                self.wave_selector.current(index)
                self.select_wave()
                break

    def render_wave(self):
        self.wave_axis.clear()
        if self.scan_selection is not None:
            self.scan_selection.remove()
            self.scan_selection = None
        self.wave_axis.set(xlabel="窗口内时间 / μs", ylabel="ADC 端电压 / mV")
        point = self.wave_point
        if point is None:
            text = "等待本次采样" if self.busy else "尚无波形"
            self.wave_axis.set_title(text, fontsize=10)
            self.wave_status.set(text+" · 采集后显示原始 ADC 电压与 40 kHz 拟合分量。")
        else:
            capture = point.capture
            result = capture.analyze(point.vref_mv, point.divider)
            time_us = np.arange(len(capture.raw))*1e6/capture.fs_hz
            self.wave_axis.plot(time_us, result["mv"], color="#2463c5", lw=1.2,
                                marker=".", ms=2.5, label="原始 ADC")
            fitted = np.asarray(result["filtered_mv"])+result["mean_mv"]
            self.wave_axis.plot(time_us, fitted, color="#c35e22", lw=1.1, ls="--",
                                label="40 kHz 拟合 + 直流")
            limit = min(100., time_us[-1]) if self.wave_span.get() == "前 100 μs" else time_us[-1]
            self.wave_axis.set_xlim(0, limit)
            source = "Demo 合成 · 非实测" if capture.simulated else "接收板 ADC · 未标定声压"
            self.wave_axis.set_title(source, fontsize=10)
            self.wave_axis.legend(loc="upper right", fontsize=8)
            coordinates = ", ".join(f"{v:g}" for v in self.display_position(point.position_mm))
            self.wave_status.set(f"{self.wave_caption} · ({coordinates}) mm · "
                f"设备时间 {capture.uptime_ms/1000:.3f} s · {capture.fs_hz/1000:g} kS/s / {len(capture.raw)} 点\n"
                f"ADC 端：直流 {result['mean_mv']:.1f} mV · 峰峰值 {result['pp_mv']:.1f} mV · "
                f"交流 RMS {result['rms_mv']:.1f} mV · 40 kHz 峰值 {result['peak40_mv']:.1f} mV（整个窗口）")
            if (self.wave_index is not None and self.scan_plane_z is not None
                    and abs(point.position_mm[2]-self.scan_plane_z) <= .001):
                self.scan_selection, = self.scan_axis.plot(*self.display_position(point.position_mm)[:2], marker="o",
                    ms=14, mfc="none", mec="#b85d20", mew=1.6, linestyle="none")
        self.wave_axis.grid(alpha=.2)
        self.scan_canvas.draw_idle()

    def render_scan(self):
        try:
            z = self.current_position()[0][2]
            if not np.isfinite(z):
                raise ValueError
        except ValueError:
            self.scan_status.set("请输入有效的显示平面 z 坐标")
            return
        self.scan_plane_z = z
        if self.scan_colorbar is not None:
            self.scan_colorbar.remove()
            self.scan_colorbar = None
        self.scan_axis.clear()
        self.scan_scatter = None
        self.scan_selection = None
        points = self.scan_book.plane_points(z)
        source = "尚无测点"
        if self.scan_book.context:
            source = "Demo 合成测点 · 非实测" if self.scan_book.context["simulated"] else "接收板 ADC 测点 · 未标定声压"
            if self.scan_book.imported:
                source = "导入记录（来源由文件声明） · "+source
        self.scan_axis.set(xlabel="x / mm", ylabel="y / mm", title=f"{self.coordinate_label()} · Z={self.display_position((0, 0, z))[2]:g} mm")
        if points:
            data = np.asarray([(*self.display_position((x, y, z))[:2], value, count) for x, y, value, count in points])
            scatter = self.scan_axis.scatter(data[:, 0], data[:, 1], c=data[:, 2], s=105,
                                            cmap="viridis", edgecolors="white", vmin=0, picker=8)
            self.scan_scatter = scatter
            self.scan_colorbar = self.scan_figure.colorbar(scatter, ax=self.scan_axis)
            self.scan_colorbar.set_label("40 kHz 峰值 / mV", fontsize=8)
            self.scan_axis.set_aspect("equal", adjustable="datalim")
            self.scan_status.set(f"{source} · 此平面 {len(points)} 个位置 / 总计 {len(self.scan_book.points)} 个原始窗口。")
        else:
            self.scan_axis.set_aspect("equal", adjustable="box")
            self.scan_axis.set(xlim=(72, 192), ylim=(34, 154)) if self.table_mode else self.scan_axis.set(xlim=(-60, 60), ylim=(-60, 60))
            self.scan_status.set(f"{source} · 此 z 平面没有数据。每次采集保留三个原始窗口，位置由人工填写。")
        self.scan_axis.grid(alpha=.2)
        self.render_wave()

    def export_scan(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile="receiver-field-scan.csv", filetypes=[("测绘 CSV", "*.csv")])
        if path:
            try:
                self.scan_book.save_csv(path)
                self.scan_status.set(f"已导出原始窗口、坐标和配置：{path}")
            except (ValueError, OSError) as error:
                messagebox.showerror("导出失败", str(error), parent=self)

    def import_scan(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("测绘 CSV", "*.csv")])
        if path:
            try:
                book = ScanBook.load_csv(path)
                if self.scan_book.points and not messagebox.askyesno("导入测绘", "导入将替换页面当前测点，继续吗？", parent=self):
                    return
                self.scan_book = book
                placement = book.points[0].placement
                if placement:
                    self.table_frame = TableFrame.parse(placement["frame"])
                    self.coordinate_mode.set("底面坐标（加高版）")
                else:
                    self.coordinate_mode.set("阵列坐标（旧方式）")
                self.position_reference.set("接收头 R")
                self.previous_mode = self.coordinate_mode.get()
                self.previous_reference = self.position_reference.get()
                for variable, value in zip(self.positions, self.display_position(book.points[0].position_mm)):
                    variable.set(f"{value:g}")
                self.compare_fixed.set("94" if self.table_mode else "0")
                self.update_coordinate_status()
                self.update_wave_choices(select_latest=True)
                self.render_scan()
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
                messagebox.showerror("导入失败", str(error), parent=self)

    def build_comparison(self, parent):
        row = ttk.Frame(parent, padding=8)
        row.pack(fill="x")
        ttk.Label(row, text="接收面 Z / mm（同测点页）").pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=self.positions[2], width=6).pack(side="left", padx=(0, 8))
        ttk.Combobox(row, textvariable=self.compare_direction, values=("x 剖面", "y 剖面"),
                     width=8, state="readonly").pack(side="left", padx=(0, 8))
        ttk.Label(row, text="固定轴（同测点坐标）/ mm").pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=self.compare_fixed, width=6).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="更新", command=self.refresh_comparison, style="Scan.TButton").pack(side="left")
        self.compare_export = ttk.Button(row, text="导出对比 CSV", command=self.export_comparison,
                                         state="disabled", style="Scan.TButton")
        self.compare_export.pack(side="right")
        row = ttk.Frame(parent, padding=(8, 0, 8, 4))
        row.pack(fill="x")
        ttk.Label(row, text="参考测点").pack(side="left", padx=(0, 5))
        self.compare_ref_selector = ttk.Combobox(row, textvariable=self.compare_reference,
                                                 state="disabled", width=32)
        self.compare_ref_selector.pack(side="left", fill="x", expand=True)
        self.compare_ref_selector.bind("<<ComboboxSelected>>", self.choose_reference)
        ttk.Button(row, text="对比说明", command=self.comparison_help,
                   style="Scan.TButton").pack(side="right", padx=(8, 0))
        self.legacy_model_row = row = ttk.Frame(parent, padding=(8, 0, 8, 4))
        ttk.Label(row, text="旧记录模型：口径 / mm").pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=self.legacy_diameter, width=5).pack(side="left", padx=(0, 10))
        ttk.Combobox(row, textvariable=self.legacy_phase, state="readonly", width=20,
                     values=("请选择时序", "相位码表示提前", "相位码表示延迟")).pack(side="left")
        self.compare_status_widget = self.paragraph(parent, textvariable=self.compare_status)
        self.paragraph(parent, textvariable=self.compare_stats)
        self.compare_figure = Figure(figsize=(8, 4), facecolor="white")
        self.compare_figure.subplots_adjust(left=.08, right=.97, bottom=.23, top=.84, wspace=.70)
        grid = self.compare_figure.add_gridspec(1, 2, width_ratios=(1, 1.65))
        self.compare_map = self.compare_figure.add_subplot(grid[0])
        self.compare_profile = self.compare_figure.add_subplot(grid[1])
        self.compare_canvas = FigureCanvasTkAgg(self.compare_figure, parent)
        self.paragraph(parent, side="bottom", text="本页图轴为阵列坐标：原点在发射面中心，z朝接收器；测点页输入会自动换算。底图/线为预测，圆点为采样。")
        self.compare_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.clear_comparison("先采集或导入测点，再进行同坐标对比。")

    def comparison_help(self):
        messagebox.showinfo("怎样比对预测和实测",
            "使用测绘记录中的阵列坐标、启用通道、相位和输出配置，"
            "在每个接收器测点坐标上计算预测值。不是把当前编辑图形与旧测量拼在一起。\n\n"
            "选择一个参考测点：预测值除以该处的预测幅值，实测值除以该处的40 kHz峰值电压。"
            "两者在同一位置都为1，比较其他位置的幅值变化、峰值位置和剖面趋势。"
            "参考点不参与差异RMSE；只有一个位置时不能验证空间分布。\n\n"
            "左图底色是模型预测，圆点是同位置实测，用同一色标。"
            "右图是选定直线上的模型曲线与测点，x剖面固定y，y剖面固定x；"
            "重复范围为原始窗口幅值的最小/最大值，未测位置不补线。\n\n"
            "这一步没有标定绝对声压，也不能证明触觉强度。"
            "请保持接收器朝向、增益和驱动状态一致，确保模拟链路未削顶。"
            "ADC电压与载波声压近似成比例是本对比的前提；差异也可能来自反射、器件误差或定位。\n\n"
            "新记录保存采集时的模型口径和相位码时序。旧记录缺少这些参数时需手动核实，"
            "不会根据当前连接的另一个设备猜测。Demo采样不随手填位置变化，仅演示对比界面。",
            parent=self)

    def refresh_comparison(self):
        self.compare_key = None

    def choose_reference(self, event=None):
        index = self.compare_ref_selector.current()
        if 0 <= index < len(self.compare_refs):
            self.compare_reference_position = self.compare_refs[index]
            self.refresh_comparison()

    def comparison_request(self):
        book = self.scan_book
        if not book.points:
            self.compare_ref_selector.configure(values=(), state="disabled")
            self.compare_refs = []
            self.compare_reference.set("")
            self.legacy_model_row.pack_forget()
            raise ValueError("先采集或导入测点，再进行同坐标对比。")
        z = self.current_position()[0][2]
        fixed = float(self.compare_fixed.get())
        if self.table_mode:
            fixed = (self.table_frame.center_y_mm-fixed if self.compare_direction.get() == "x 剖面"
                     else fixed-self.table_frame.center_x_mm)
        if not np.isfinite((z, fixed)).all() or not 10 <= z <= 400 or abs(fixed) > 300:
            raise ValueError("对比平面或剖面坐标无效")
        refs = list(dict.fromkeys(p.position_mm for p in book.points if abs(p.position_mm[2]-z) <= .001))
        if not refs:
            self.compare_ref_selector.configure(values=(), state="disabled")
            self.compare_refs = []
            self.compare_reference.set("")
            raise ValueError("此 z 平面没有测点")
        if refs != self.compare_refs:
            self.compare_refs = refs
            if self.compare_reference_position not in refs:
                config = Config.from_wire(book.context["config"])
                self.compare_reference_position = min(refs, key=lambda p:
                    (p[0]-config.cx_um/1000)**2+(p[1]-config.cy_um/1000)**2)
            self.compare_ref_selector.configure(values=["("+", ".join(f"{v:g}" for v in p)+") mm" for p in refs])
            self.compare_ref_selector.current(refs.index(self.compare_reference_position))
        self.compare_ref_selector.configure(state="readonly")
        model = book.prediction_model
        model_source = "RECORDED_AT_CAPTURE"
        if model is None:
            self.legacy_model_row.pack(fill="x", before=self.compare_status_widget)
            phase = self.legacy_phase.get()
            if phase == "请选择时序" and not book.context["simulated"]:
                raise ValueError("旧记录缺少相位时序，请按采集时的控制器选择提前或延迟")
            direction = -1 if phase == "相位码表示提前" else 1
            model = ScanModel(float(self.legacy_diameter.get()), direction).document()
            model_source = "LEGACY_USER_SELECTED"
        else:
            self.legacy_model_row.pack_forget()
        key = (id(book), len(book.points), z, self.compare_reference_position, self.compare_direction.get(),
               fixed, tuple(sorted(model.items())), model_source)
        request = (book.context, tuple(book.points), model, z, self.compare_reference_position,
                   self.compare_direction.get()[0], fixed, model_source)
        return key, request

    def tick_comparison(self):
        if self.busy:
            self.compare_key = None
            self.compare_status.set("采集中，完成后更新记录对比。")
            self.compare_export.configure(state="disabled")
            return
        try:
            key, request = self.comparison_request()
        except (ValueError, OverflowError) as error:
            self.clear_comparison(str(error))
            return
        if self.compare_future is not None and self.compare_future.done():
            try:
                result = self.compare_future.result()
                if self.compare_future_key == key:
                    self.comparison = result
                    self.compare_key = key
                    self.render_comparison()
            except Exception as error:
                if self.compare_future_key == key:
                    self.clear_comparison(str(error))
                    self.compare_key = key  # Retry only after input changes or Update.
            finally:
                self.compare_future = None
        if self.compare_key != key and self.comparison is not None:
            self.clear_comparison("正在更新同坐标对比…")
        if self.compare_future is None and self.compare_key != key:
            self.compare_future_key = key
            self.compare_future = self.app.executor.submit(compare_scan, *request)
        self.compare_export.configure(state="normal" if self.comparison is not None and self.compare_key == key else "disabled")

    def clear_comparison(self, text):
        self.compare_export.configure(state="disabled")
        if self.comparison is None and self.compare_status.get() == text and self.compare_map.get_title():
            return
        self.comparison = None
        self.compare_key = None
        if self.compare_colorbar is not None:
            self.compare_colorbar.remove()
            self.compare_colorbar = None
        self.compare_map.clear()
        self.compare_profile.clear()
        self.compare_map.set_title("尚无有效对比", fontsize=10)
        self.compare_status.set(text)
        self.compare_stats.set("")
        self.compare_canvas.draw_idle()

    def render_comparison(self):
        result = self.comparison
        if self.compare_colorbar is not None:
            self.compare_colorbar.remove()
        self.compare_map.clear()
        self.compare_profile.clear()
        limit = max(1., float(result.field_ratio.max()), float(result.measured_ratio.max()))
        image = self.compare_map.imshow(result.field_ratio, origin="lower", aspect="equal", cmap="viridis",
            extent=(result.horizontal[0], result.horizontal[-1], result.horizontal[0], result.horizontal[-1]),
            vmin=0, vmax=limit)
        self.compare_map.scatter(result.positions[:, 0], result.positions[:, 1], c=result.measured_ratio,
            cmap="viridis", vmin=0, vmax=limit, edgecolors="white", linewidths=1.2, s=70)
        self.compare_map.plot(*result.reference[:2], marker="*", mfc="none", mec="#b44120", ms=14, linestyle="none")
        self.compare_colorbar = self.compare_figure.colorbar(image, ax=self.compare_map, fraction=.046, pad=.04)
        self.compare_colorbar.set_label("相对参考测点幅值", fontsize=8)
        self.compare_map.set(xlabel="x / mm", ylabel="y / mm")
        self.compare_map.set_title(f"同坐标对比 · z={result.reference[2]:g} mm", fontsize=10)
        axis = self.compare_profile
        axis.plot(result.horizontal, result.profile_ratio, color="#2463c5", label="模型预测")
        indices = result.profile_indices
        column = 0 if result.direction == "x" else 1
        if len(indices):
            indices = indices[np.argsort(result.positions[indices, column])]
            reference_index = list(map(tuple, result.positions)).index(result.reference)
            ref_mv = result.measured_mv[reference_index]
            lower = (result.measured_mv[indices]-result.minimum_mv[indices])/ref_mv
            upper = (result.maximum_mv[indices]-result.measured_mv[indices])/ref_mv
            axis.errorbar(result.positions[indices, column], result.measured_ratio[indices],
                yerr=(lower, upper), fmt="o", ls="none", color="#cf6123", capsize=3,
                label="Demo采样/重复范围" if result.context["simulated"] else "实测/重复范围")
        else:
            axis.text(.5, .65, "这条剖面尚无测点", transform=axis.transAxes, ha="center")
        axis.axhline(1, color="#aaaaaa", ls=":", lw=.8)
        axis.set(xlabel=f"{result.direction} / mm", ylabel="相对参考测点幅值")
        fixed_axis = "y" if result.direction == "x" else "x"
        axis.set_title(f"{result.direction} 剖面 · {fixed_axis}={result.section_mm:g} mm", fontsize=10)
        axis.legend(fontsize=8)
        axis.grid(alpha=.2)
        source = "Demo 合成记录 · 不验证声学效果" if result.context["simulated"] else "真实接收记录 · 电压未标定声压"
        if self.scan_book.imported:
            source = "导入记录（来源由文件声明） · "+source
        timing = "提前" if result.model["phase_direction"] == -1 else "延迟"
        model_origin = "记录模型" if result.model_source == "RECORDED_AT_CAPTURE" else "手动选择旧记录模型"
        self.compare_status.set(f"{source} · 采集时阵列/相位 · {model_origin}：{result.model['diameter_mm']:g} mm / {timing}码")
        index = list(map(tuple, result.positions)).index(result.reference)
        reference = ", ".join(f"{v:g}" for v in result.reference)
        measured_peak = result.positions[np.argmax(result.measured_mv)]
        predicted_peak = result.positions[np.argmax(result.predicted)]
        describe = lambda p: "("+", ".join(f"{v:g}" for v in p[:2])+")"
        deviation = (f"其余 {len(result.positions)-1} 点差异 RMSE {result.rmse:.3f}"
                     if result.rmse is not None else "仅 1 个位置，不能验证空间分布")
        self.compare_stats.set(f"参考 ({reference}) mm：ADC {result.measured_mv[index]:.1f} mV，两侧在此处为 1 · {deviation}\n"
            f"已测 {len(result.positions)} 个位置 / 剖面 {len(indices)} 点 · 已测位置内峰值："
            f"预测 {describe(predicted_peak)} / 采样 {describe(measured_peak)} mm")
        self.compare_canvas.draw_idle()

    def export_comparison(self):
        if self.comparison is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv",
            initialfile="field-comparison.csv", filetypes=[("对比 CSV", "*.csv")])
        if path:
            try:
                self.comparison.save_csv(path)
                self.compare_status.set(f"已导出同坐标预测、实测电压、归一化值和差值：{path}")
            except (ValueError, OSError) as error:
                messagebox.showerror("导出失败", str(error), parent=self)

    def export_prediction(self):
        if self.payload is None:
            self.status.set("先更新预测后再导出")
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile="predicted-field.csv", filetypes=[("预测 CSV", "*.csv")])
        if not path:
            return
        request, result = self.payload
        array, config, phases, focus, enabled, mask, settings, details = request
        context = json.dumps(dict(source=details, geometry=array.resolved_geometry().document(),
                                  config=config.wire(), phases=phases, channel_mask=mask,
                                  enabled=enabled, diameter_mm=settings.diameter_mm,
                                  focus_mm=focus, settings=asdict(settings),
                                  model="FREE_FIELD_PISTON_APPROXIMATION", unit="RELATIVE_CARRIER_AMPLITUDE"),
                             ensure_ascii=False, separators=(",", ":"))
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["source", "x_mm", "y_mm", "z_mm", "relative_amplitude", "relative_pressure_squared", "context_json"])
                for iy, vertical in enumerate(result.vertical):
                    for ix, horizontal in enumerate(result.horizontal):
                        y, z = (vertical, focus[2]) if settings.plane == "XY" else (focus[1], vertical)
                        value = result.amplitude[iy, ix]
                        writer.writerow(["THEORY_NOT_MEASUREMENT", horizontal, y, z, value, value*value,
                                         context if iy == ix == 0 else ""])
            self.status.set(f"已导出预测（非实测）：{path}")
        except OSError as error:
            messagebox.showerror("导出失败", str(error), parent=self)
