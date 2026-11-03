"""User workspace using the existing session, geometry and receipt lifecycle.

Calibration and protocol tools remain in App (--developer). The dedicated
sound-field demonstration reuses the shared prediction and measurement panel.
"""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from .app import App, BAUD_RATES
from .acoustic_panel import AcousticPanel
from .hand_view import HandView
from .model import BUTTON_OUTPUT_SETTINGS, Config, SHAPES, trajectory_paths
from .palm import HAND_OUTLINE, palm_warning
from .playback import PlaybackPanel
from .sketch_editor import SketchEditor
from .session_mirror import SessionMirror


BG = '#f4f6fb'
WHITE = '#ffffff'
INK = '#1c2942'
MUTED = '#63728a'
ACCENT = '#425fe0'
LINE = '#e0e6f0'
NAV = '#fafbfe'
SELECTED = '#edf1ff'
SOFT = '#f6f8fd'
USER_OUTPUT_LEVEL = 100


class _InactiveEngineering:
    """Disabled view hooks for the shared session loop; never sends commands."""
    busy = False

    def reset(self): pass
    def on_connect(self): pass
    def clear(self, *_): pass
    def accept(self, *_): pass
    def recorded(self, *_): pass
    def record_failed(self, *_): pass
    def reject(self, *_): pass
    def cancel(self): pass
    def cancel_scan(self, *_): pass


class _ReceiverSettings(_InactiveEngineering):
    """Shared acquisition settings; waveforms are shown in the sound-field page."""
    pending = False

    def __init__(self, root):
        self.vref = tk.StringVar(root, value='3300')
        self.divider = tk.StringVar(root, value='3.4')
        self.continuous = tk.BooleanVar(root, value=False)


def user_hand(canvas, path=None):
    """Shared physical coordinates without engineering axes or measurements."""
    view = HandView(canvas, path)
    outline = [value for p in HAND_OUTLINE for value in view.screen(p)]
    canvas.create_polygon(*outline, fill='#f0f3f9', outline='#cad3e2', width=2,
                          tags='hand_reference')
    for start, segments in [((-26, 17), [((-13, 11), (11, 14), (32, 22))]),
                            ((-21, -1), [((-6, 3), (13, -3), (28, -8))]),
                            ((-29, 5), [((-14, -7), (-12, -23), (-22, -33))])]:
        canvas.create_line(*view.curve(start, segments), fill='#dbe1ec', width=1.5)
    canvas.create_text(view.width-20, 18, text='手掌位置示意', anchor='e',
                       fill=MUTED, font=('Microsoft YaHei UI', 9))
    return view


class FigurePreview(ttk.Frame):
    def __init__(self, parent, on_apply):
        super().__init__(parent, padding=16)
        self.config = None
        self.title = tk.StringVar(value='图形预览')
        self.dimensions = tk.StringVar()
        self.warning = tk.StringVar()
        ttk.Label(self, textvariable=self.title, style='Section.TLabel').pack(anchor='w')
        self.canvas = tk.Canvas(self, bg=WHITE, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, pady=(10, 0))
        self.canvas.bind('<Configure>', lambda _: self.draw())
        ttk.Label(self, textvariable=self.dimensions, style='Muted.TLabel').pack(pady=5)
        ttk.Label(self, textvariable=self.warning, foreground='#997236',
                  wraplength=540).pack(pady=4)
        # Primary action belongs to the adjustment column, not on the picture.
        self.apply_button = ttk.Button(self, command=on_apply)

    def set_config(self, config):
        if config != self.config:
            self.config = config
            self.draw()

    def draw(self):
        c = self.canvas
        c.delete('all')
        if self.config is None:
            self.title.set('完成图形后即可预览')
            self.dimensions.set('')
            self.warning.set('')
            user_hand(c)
            return
        paths = trajectory_paths(self.config)
        points = np.concatenate(paths)[:, :2]
        view = user_hand(c, points)
        self.title.set(SHAPES[self.config.shape])
        for stroke in paths:
            xy = [v for p in stroke for v in view.screen(p[:2])]
            if len(xy) > 2 and np.any(np.ptp(stroke[:, :2], axis=0)>0):
                c.create_line(*xy, fill=ACCENT, width=4, capstyle='round', joinstyle='round', tags='preview_path')
            else:
                x, y = xy[:2]
                c.create_oval(x-6, y-6, x+6, y+6, fill=ACCENT, outline='', tags='preview_path')
        extent = np.ptp(points, axis=0)
        self.dimensions.set(f'宽 {extent[0]:g} mm  ·  高 {extent[1]:g} mm  ·  呈现距离 {self.config.z_um/1000:g} mm')
        self.warning.set(palm_warning([p[:, :2] for p in paths]))


class UserPlayback(PlaybackPanel):
    def draw(self):
        c = self.canvas
        c.delete('all')
        self.marker = self.halo = None
        self.view = user_hand(c, self.path)
        if self.path is None:
            static = self.plot_config and self.plot_config.phase_table != 'NONE'
            c.create_text(max(c.winfo_width(), 260)/2, max(c.winfo_height(), 180)-30,
                          text='静态压力分布 · 无移动焦点' if static else '图形发送成功后，在这里开始呈现', fill=MUTED,
                          font=('Microsoft YaHei UI', 11))
            return
        for stroke in trajectory_paths(self.plot_config):
            xy = [v for p in stroke for v in self.screen(p[:2])]
            if len(xy) > 2 and np.any(np.ptp(stroke[:, :2], axis=0)>0):
                c.create_line(*xy, fill='#a6b6f1', width=4, capstyle='round', tags='device_path')
            else:
                x, y = xy[:2]
                c.create_oval(x-5, y-5, x+5, y+5, fill='#a6b6f1', outline='', tags='device_path')
        self.halo = c.create_oval(0, 0, 0, 0, outline='#c2cef9', width=3, state='hidden')
        self.marker = c.create_oval(0, 0, 0, 0, outline=WHITE, width=2, state='hidden', tags='position')
        self.draw_marker()

    def update_state(self, state, fresh, feedback, connection_label='串口'):
        super().update_state(state, fresh, feedback, connection_label)
        self.heading.set(self.heading.get().replace('播放', '呈现'))
        self.message.set(self.message.get().replace('点击播放', '点击“开始呈现”').replace('播放', '呈现'))
        self.source.set('演示体验 · 模拟设备反馈' if state and state.simulated else
                        '设备实时反馈' if fresh else '等待设备连接')
        self.play_button.configure(text='继续呈现' if fresh and state.state == 'PAUSED' else '开始呈现')
        self.notice.configure(bg=SELECTED if fresh else '#fff6e6', fg=INK)

    def draw_marker(self):
        super().draw_marker()
        if self.marker is not None:
            paused = self.snapshot and self.snapshot.state == 'PAUSED'
            self.canvas.itemconfigure(self.marker, fill='#b77621' if paused else ACCENT)


class ShapeCard(ttk.Frame):
    def __init__(self, parent, code, on_select, on_open):
        super().__init__(parent, padding=1)
        self.code = code
        self.active = False
        self.canvas = tk.Canvas(self, height=120, bg=SOFT, highlightthickness=1,
                                highlightbackground=LINE, cursor='hand2', takefocus=True)
        self.canvas.pack(fill='x')
        self.button = ttk.Button(self, text=SHAPES[code], command=on_select)
        self.button.pack(fill='x')
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.canvas.bind('<Button-1>', lambda _: on_select())
        self.canvas.bind('<Double-Button-1>', lambda _: on_open())
        self.canvas.bind('<Return>', lambda _: on_open())
        self.canvas.bind('<space>', lambda _: on_select())
        self.canvas.bind('<FocusIn>', lambda _: self.canvas.configure(highlightbackground=ACCENT, highlightthickness=2))
        self.canvas.bind('<FocusOut>', lambda _: self.set_active(self.active))

    def set_active(self, active):
        self.active = active
        self.canvas.configure(bg=SELECTED if active else SOFT,
                              highlightbackground=ACCENT if active else LINE,
                              highlightthickness=2 if active else 1)
        self.button.configure(style='Selected.TButton' if active else 'TButton')
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete('all')
        w, h = max(c.winfo_width(), 100), 120
        cfg = replace(Config(), shape=self.code)
        if self.code == 'CUSTOM':
            c.create_line(w/2-27, 67, w/2-12, 35, w/2+4, 61, w/2+29, 31,
                          fill=ACCENT, width=3, smooth=True)
            c.create_text(w/2, 87, text='自由创作', fill=MUTED, font=('Microsoft YaHei UI', 9))
        else:
            for path in trajectory_paths(cfg):
                xy = [v for p in path for v in (w/2+p[0]*1.5, h/2-p[1]*1.5)]
                if len(xy) > 2 and np.any(np.ptp(path[:, :2], axis=0)>0):
                    c.create_line(*xy, fill=ACCENT, width=3, capstyle='round', joinstyle='round')
                else:
                    c.create_oval(w/2-5, h/2-5, w/2+5, h/2+5, fill=ACCENT, outline='')
        if self.active:
            c.create_text(w-10, 13, text='已选', fill=ACCENT, anchor='e', font=('Microsoft YaHei UI', 8))


class UserApp(App):
    PAGE_INFO = {
        'library': ('图形库', '选择一种形状，或打开你保存的作品。'),
        'replay': ('三维回放', '查看手部动作与接触压力记录，选择单帧生成目标声场。'),
        'editor': ('自由创作', '绘制轮廓，调整线条，让想法成为可呈现的图形。'),
        'playback': ('当前呈现', '将手掌放在约定的位置，依据设备反馈查看呈现状态。'),
        'field': ('声场演示', '查看理论声场，采集多个测点，并对照接收波形与空间分布。'),
        'device': ('连接设备', '连接真实设备，也可以先体验无硬件演示。'),
        'help': ('使用指南', '从第一个图形开始，熟悉触见的使用方法。'),
    }

    def __init__(self, root, preferences_path=None):
        self.ui_scaling = float(root.tk.call('tk', 'scaling'))
        self.preferences_path = Path(preferences_path) if preferences_path else (
            Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'TouchSee' / 'user-workspace.json')
        self.recent_files = []
        self.cards = {}
        self.page_name = 'library'
        self.pending_presentation = None
        self.advancing_presentation = False
        self.return_to_confirmation = False
        self.has_presented = False
        self.confirmation_visible = False
        self.presentation_error = ''
        self.session_mirror = None
        self.disconnecting = False
        self.quick_connection = None
        self.measurement_draft = False
        self.replay = None
        super().__init__(root)
        root.title('触见 · 体验台')
        width = min(1680, root.winfo_screenwidth()-80)
        height = min(1080, root.winfo_screenheight()-120)
        root.geometry(f'{width}x{height}')
        root.minsize(min(1280, width), min(860, height))
        self.mode_choice.set('真实串口')
        self.hw_rows.set('8')
        self.hw_cols.set('8')
        self.hw_pitch.set('10.3')
        self._load_preferences()
        self.saved_signature = self.draft_signature()
        self.show_page('library')
        self.update_controls()

    def _style(self):
        self.root.tk.call('tk', 'scaling', self.ui_scaling)
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('.', font=('Microsoft YaHei UI', 10))
        s.configure('TFrame', background=WHITE)
        s.configure('TLabel', background=WHITE, foreground=INK, font=('Microsoft YaHei UI', 10))
        s.configure('Muted.TLabel', foreground=MUTED)
        s.configure('Caption.TLabel', foreground=MUTED, font=('Microsoft YaHei UI', 9))
        s.configure('Page.TLabel', foreground=INK, font=('Microsoft YaHei UI', 15, 'bold'))
        s.configure('Heading.TLabel', font=('Microsoft YaHei UI', 12, 'bold'))
        s.configure('Section.TLabel', font=('Microsoft YaHei UI', 13, 'bold'))
        s.configure('TButton', background=WHITE, foreground=INK, borderwidth=1,
                    bordercolor=LINE, padding=(14, 9), focusthickness=2, focuscolor=ACCENT)
        s.map('TButton', background=[('active', SELECTED), ('disabled', '#f1f3f8')],
              foreground=[('disabled', '#98a3b5')])
        for name, color in [('Primary', ACCENT), ('Selected', SELECTED), ('Stop', '#c34e5b')]:
            s.configure(name+'.TButton', background=color, foreground=ACCENT if name=='Selected' else WHITE,
                        bordercolor=color, font=('Microsoft YaHei UI', 10, 'bold'))
            s.map(name+'.TButton', background=[('disabled', '#edf0f6'), ('active', '#344dc0' if name=='Primary' else color)],
                  foreground=[('disabled', '#98a3b5')], bordercolor=[('disabled', LINE)])
        s.configure('TEntry', padding=7, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        s.configure('Tool.TButton', background=SOFT, foreground=INK, bordercolor=LINE,
                    padding=(10, 12), font=('Microsoft YaHei UI', 10, 'bold'))
        s.map('Tool.TButton', background=[('active', SELECTED)])
        s.configure('ActiveTool.TButton', background=ACCENT, foreground=WHITE, bordercolor=ACCENT,
                    padding=(10, 12), font=('Microsoft YaHei UI', 10, 'bold'))
        s.map('ActiveTool.TButton', background=[('active', '#344dc0')], foreground=[('active', WHITE)])
        s.configure('TCombobox', padding=7, bordercolor=LINE, arrowsize=14)
        s.configure('TNotebook', background=WHITE, borderwidth=0, tabmargins=0)
        s.configure('TNotebook.Tab', padding=(9, 7), background=SOFT)
        s.map('TNotebook.Tab', background=[('selected', SELECTED)], foreground=[('selected', ACCENT)])
        s.layout('Pages.TNotebook.Tab', [])
        s.configure('TRadiobutton', background=WHITE, foreground=INK, padding=5)
        s.configure('TCheckbutton', background=WHITE)
        s.configure('TLabelframe', background=WHITE, bordercolor=LINE)
        s.configure('TLabelframe.Label', background=WHITE, foreground=MUTED)

    def _layout(self):
        self.calibration_panel = _InactiveEngineering()
        self.acoustic_panel = _InactiveEngineering()
        self.receiver_panel = _ReceiverSettings(self.root)
        self.actual_plot = _InactiveEngineering()
        self.workspace_line = tk.StringVar()
        self.user_device = tk.StringVar(value='尚未连接')
        self.header_source = tk.StringVar(value='未连接')
        self.page_title = tk.StringVar()
        self.page_subtitle = tk.StringVar()
        self.next_step = tk.StringVar(value='先选一个图形，或创建自己的轮廓。')
        self.selected_title = tk.StringVar(value='当前选择：圆')
        self.progress_text = tk.StringVar()
        self.scales = {'shape': None, 'cx_um': 1000, 'cy_um': 1000, 'z_um': 1000,
                       'radius_um': 1000, 'repeat_millihz': 1000, 'carrier_hz': 1,
                       'phase_steps': 1, 'mod_hz': 1, 'level': 1}
        self.vars = {name: tk.StringVar() for name in self.scales}
        nav = tk.Frame(self.root, bg=NAV, width=230)
        nav.pack(side='left', fill='y')
        nav.pack_propagate(False)
        tk.Label(nav, text='触见', bg=NAV, fg=INK, font=('Microsoft YaHei UI', 25, 'bold')).pack(anchor='w', padx=25, pady=(30, 0))
        tk.Label(nav, text='图形 · 触觉 · 体验', bg=NAV, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=26, pady=(8, 35))
        self.nav_buttons = {}
        for key, label in [('library','图形库'), ('editor','自由创作'), ('playback','当前呈现')]:
            self._nav_button(nav, key, label)
        self.nav_buttons['playback'].pack_forget()
        self._nav_button(nav, 'replay', '三维回放')
        self._nav_button(nav, 'field', '声场演示')
        tk.Frame(nav, bg=LINE, height=1).pack(fill='x', padx=22, pady=22)
        self._nav_button(nav, 'device', '连接设备')
        self._nav_button(nav, 'help', '使用指南')
        tk.Label(nav, text='让图形触手可及', bg=NAV, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(side='bottom', pady=25)

        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill='both', expand=True)
        header = tk.Frame(shell, bg=WHITE, padx=26, pady=20)
        header.pack(fill='x')
        self.header_stop = ttk.Button(header, text='停止呈现', style='Stop.TButton', command=lambda: self.send('STOP'))
        self.header_stop.pack(side='right', padx=(10, 0))
        self.connection_shortcut = ttk.Button(header, textvariable=self.user_device, command=self.toggle_connection)
        self.connection_shortcut.pack(side='right')
        ttk.Label(header, textvariable=self.header_source, style='Muted.TLabel').pack(side='right', padx=12)
        tk.Label(header, textvariable=self.page_title, bg=WHITE, fg=INK,
                 font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        tk.Label(header, textvariable=self.page_subtitle, bg=WHITE, fg=MUTED,
                 font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(7, 0))
        footer = tk.Frame(shell, bg=BG, padx=26, pady=10)
        footer.pack(side='bottom', fill='x')
        tk.Label(footer, textvariable=self.next_step, bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9), anchor='w').pack(fill='x')
        self.tabs = ttk.Notebook(shell, style='Pages.TNotebook')
        self.tabs.pack(fill='both', expand=True, padx=24, pady=(20, 5))
        self.pages = {key: ttk.Frame(self.tabs, padding=18) for key in self.PAGE_INFO}
        for key, frame in self.pages.items():
            self.tabs.add(frame, text=key)
        self.tabs.bind('<<NotebookTabChanged>>', self._page_changed)
        from .replay_panel import ReplayPanel
        self.replay_tab = self.pages['replay']
        self.replay = ReplayPanel(self.replay_tab, on_pressure=self.open_pressure)
        self.replay.pack(fill='both', expand=True)
        self.replay.set_active(False)
        self.editor_tab = self.pages['editor']
        self.playback_tab = self.pages['playback']
        self.tabs.tab(self.playback_tab, state='hidden')
        self.acoustic_tab = self.pages['field']
        field_actions = ttk.Frame(self.acoustic_tab)
        field_actions.pack(fill='x', pady=(0, 10))
        ttk.Label(field_actions, text='理论计算与多点测量', style='Heading.TLabel').pack(side='left')
        self.measurement_setup = ttk.Button(field_actions, text='准备测绘焦点…', command=self.prepare_field_measurement)
        self.measurement_setup.pack(side='right')
        self.acoustic_panel = AcousticPanel(self.acoustic_tab, self, consumer=True)
        self.acoustic_panel.pack(fill='both', expand=True)
        self._library_page(self.pages['library'])
        self.confirmation = tk.Toplevel(self.root)
        self.confirmation.withdraw()
        self.confirmation.title('呈现前确认')
        self.confirmation.transient(self.root)
        self.confirmation.configure(bg=WHITE)
        self.confirmation.protocol('WM_DELETE_WINDOW', self.cancel_confirmation)
        self.confirmation.bind('<Escape>', lambda _: self.cancel_confirmation())
        ttk.Label(self.confirmation, text='确认这次呈现', style='Section.TLabel',
                  padding=(22, 18, 22, 5)).pack(anchor='w')
        ttk.Label(self.confirmation, text='检查图形与实际呈现距离，确认后即可开始。',
                  style='Muted.TLabel', padding=(22, 0, 22, 10)).pack(anchor='w')
        self.preset_tab = ttk.Frame(self.confirmation, padding=15)
        self.preset_tab.pack(fill='both', expand=True)
        self._preview_page(self.preset_tab)
        self.editor = SketchEditor(self.editor_tab, self.preview_sketch, self.apply_custom,
                                   self.save_custom, self.load_config,
                                   on_change=self._edited, consumer=True)
        self.editor.pack(fill='both', expand=True)
        self.playback = UserPlayback(self.playback_tab, lambda: self.send('START'),
                                     lambda: self.send('PAUSE'), lambda: self.send('STOP'), self.select_shape)
        self.playback.pack(fill='both', expand=True)
        self.start_button = self.playback.play_button
        self.pause_button = self.playback.pause_button
        self.stop_button = self.playback.stop_button
        self._device_page(self.pages['device'])
        self._help_page(self.pages['help'])

    def _nav_button(self, parent, key, label):
        button = tk.Button(parent, text=label, anchor='w', command=lambda: self.show_page(key),
                           bg=NAV, fg=MUTED, activebackground=SELECTED, activeforeground=ACCENT,
                           font=('Microsoft YaHei UI', 11), bd=0, relief='flat', padx=18, pady=13,
                           cursor='hand2', highlightthickness=0, takefocus=True)
        button.pack(fill='x', padx=12, pady=3)
        self.nav_buttons[key] = button

    def toggle_connection(self):
        if self.closing or self.disconnecting:
            return
        if self.session and self.session.is_alive():
            self.disconnect()
            return
        if self.ble_scan is not None:
            self.show_page('device')
            return
        if self.mode_choice.get() == 'BLE 蓝牙' and self.ble_choice.get() not in self.ble_peers:
            self.show_page('device')
            self.scan_button.focus_set()
            return
        baud = self.baud_choice.get().strip()
        if self.mode_choice.get() == '真实串口' and (not self.port_choice.get().strip()
                or not baud.isascii() or not baud.isdecimal() or not 1 <= int(baud) <= 0xffffffff):
            self.open_quick_connection()
            return
        self.connect()

    def open_quick_connection(self):
        if self.quick_connection is not None and self.quick_connection.winfo_exists():
            self.quick_connection.lift()
            return
        dialog = self.quick_connection = tk.Toplevel(self.root)
        dialog.title('连接设备')
        dialog.transient(self.root)
        content = ttk.Frame(dialog, padding=22)
        content.pack(fill='both', expand=True)
        ttk.Label(content, text='连接 USB 串口', style='Section.TLabel').grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 16))
        ttk.Label(content, text='设备端口').grid(row=1, column=0, sticky='w', padx=(0, 15))
        port = ttk.Combobox(content, textvariable=self.port_choice, width=22, values=self.port_picker['values'])
        port.grid(row=1, column=1, sticky='ew', pady=6)
        def refresh():
            self.refresh_ports()
            port.configure(values=self.port_picker['values'])
        ttk.Button(content, text='刷新', command=refresh).grid(row=1, column=2, padx=(8, 0))
        ttk.Label(content, text='波特率').grid(row=2, column=0, sticky='w')
        ttk.Combobox(content, textvariable=self.baud_choice, values=BAUD_RATES, width=22).grid(row=2, column=1, sticky='ew', pady=6)
        ttk.Label(content, text='选择端口，波特率与设备保持一致。', style='Muted.TLabel').grid(row=3, column=0, columnspan=3, sticky='w', pady=12)
        def connect():
            self.connect()
            if self.session and self.session.is_alive():
                dialog.destroy()
        ttk.Button(content, text='连接', style='Primary.TButton', command=connect).grid(row=4, column=1, columnspan=2, sticky='ew')
        def more():
            dialog.destroy()
            self.show_page('device')
        ttk.Button(content, text='其他连接方式…', command=more).grid(row=4, column=0, sticky='w', padx=(0, 12))
        dialog.bind('<Escape>', lambda _: dialog.destroy())
        dialog.grab_set()
        port.focus_set()

    def prepare_field_measurement(self):
        if self.acoustic_panel.busy or self.pending_presentation is not None:
            return
        if not self.confirm_replace_draft('准备测绘焦点'):
            return
        try:
            config = replace(self.get_config(), shape='POINT', scan_paths='NONE', path_xy_um='NONE', mod_hz=0)
            self.set_config(config)
            self.measurement_draft = True
            self.acoustic_panel.positions[2].set(f'{config.z_um/1000:g}')
            self.apply_config()
        except (ValueError, OverflowError) as error:
            self.next_step.set(str(error))

    def show_page(self, name):
        if name != 'device':
            self.return_to_confirmation = False
        if name == 'playback' and not self.has_presented and self.pending_presentation is None:
            name = 'library'
        self.tabs.select(self.pages[name])
        self._page_changed()

    def _page_changed(self, _event=None):
        selected = self.tabs.select()
        self.page_name = next((name for name, page in self.pages.items() if str(page)==selected), 'library')
        title, subtitle = self.PAGE_INFO[self.page_name]
        self.page_title.set(title)
        self.page_subtitle.set(subtitle)
        if self.replay is not None:
            self.replay.set_active(self.page_name == 'replay')
        for name, button in self.nav_buttons.items():
            button.configure(bg=SELECTED if name==self.page_name else NAV,
                             fg=ACCENT if name==self.page_name else MUTED)

    def navigate(self, page):
        self.show_page({'drawing':'editor', 'experiment':'device'}.get(page, page))

    def open_pressure(self, sequence, index):
        from .pressure_panel import PressureWindow
        if self.pressure_window is not None and self.pressure_window.winfo_exists():
            self.pressure_window.destroy()
        self.pressure_window = PressureWindow(self, sequence, index, consumer=True)

    def pressure_send_issue(self, result):
        if self.acoustic_panel.busy or self.pending_presentation is not None:
            return '请等待当前操作完成，或先停止。'
        return super().pressure_send_issue(result)

    def _library_page(self, page):
        top = ttk.Frame(page)
        top.pack(fill='x', pady=(0, 18))
        ttk.Label(top, text='从一个简单图形开始', style='Section.TLabel').pack(side='left')
        ttk.Button(top, text='打开图形…', command=self.load_config).pack(side='right')
        grid = ttk.Frame(page)
        grid.pack(fill='x')
        for i, code in enumerate(SHAPES):
            card = ShapeCard(grid, code, lambda c=code: self.pick_shape(c),
                             lambda c=code: self.open_shape(c))
            card.grid(row=i//4, column=i%4, sticky='nsew', padx=6, pady=7)
            grid.columnconfigure(i%4, weight=1, uniform='cards')
            self.cards[code] = card
        selected = ttk.Frame(page, padding=(8, 14))
        selected.pack(fill='x')
        ttk.Label(selected, textvariable=self.selected_title, style='Heading.TLabel').pack(side='left')
        self.present_button = ttk.Button(selected, text='呈现此图形  →', style='Primary.TButton', command=self.apply_config)
        self.present_button.pack(side='right')
        ttk.Separator(page).pack(fill='x', pady=12)
        row = ttk.Frame(page)
        row.pack(fill='x', pady=(0, 8))
        ttk.Label(row, text='最近使用', style='Section.TLabel').pack(side='left')
        ttk.Button(row, text='新建草图', command=self.new_sketch).pack(side='right')
        self.recent_host = ttk.Frame(page)
        self.recent_host.pack(fill='x')
        self._render_recent()

    def _preview_page(self, page):
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.preset_preview = FigurePreview(page, self.confirm_presentation)
        self.preset_preview.grid(row=0, column=0, sticky='nsew', padx=(0, 20))
        settings = ttk.Frame(page, width=310, padding=(14, 6))
        settings.grid(row=0, column=1, sticky='ns')
        settings.pack_propagate(False)
        footer = ttk.Frame(settings)
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        self.apply_button = ttk.Button(footer, text='确认并开始', style='Primary.TButton', command=self.confirm_presentation)
        self.apply_button.pack(fill='x', pady=(4, 5))
        ttk.Label(footer, textvariable=self.config_line, style='Muted.TLabel', wraplength=280).pack(fill='x', pady=8)
        self.confirmation_stop = ttk.Button(footer, text='停止当前呈现', style='Stop.TButton', command=lambda: self.send('STOP'))
        self.confirmation_stop.pack(fill='x', pady=5)
        self.cancel_button = ttk.Button(footer, text='返回修改', command=self.cancel_confirmation)
        self.cancel_button.pack(fill='x', pady=5)
        scroll_host = ttk.Frame(settings)
        scroll_host.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(scroll_host, orient='vertical')
        scroll.pack(side='right', fill='y')
        self.adjustment_canvas = tk.Canvas(scroll_host, bg=WHITE, highlightthickness=0,
                                            yscrollcommand=scroll.set, width=250)
        self.adjustment_canvas.pack(fill='both', expand=True)
        scroll.configure(command=self.adjustment_canvas.yview)
        form = ttk.Frame(self.adjustment_canvas, padding=(0, 0, 9, 0))
        form_id = self.adjustment_canvas.create_window(0, 0, anchor='nw', window=form)
        form.bind('<Configure>', lambda _: self.adjustment_canvas.configure(scrollregion=self.adjustment_canvas.bbox('all')))
        self.adjustment_canvas.bind('<Configure>', lambda e: self.adjustment_canvas.itemconfigure(form_id, width=e.width))
        def wheel(event):
            widget = event.widget
            while widget is not None:
                if widget == scroll_host:
                    self.adjustment_canvas.yview_scroll(-1 if event.delta>0 else 1, 'units')
                    return 'break'
                widget = getattr(widget,'master',None)
        self.root.bind_all('<MouseWheel>', wheel, add='+')
        ttk.Label(form, text='呈现设置', style='Section.TLabel').pack(anchor='w', pady=(0, 15))
        self.user_entries = {}
        for name, title in [('radius_um','图形大小（mm）'), ('z_um','呈现距离（mm）'),
                            ('repeat_millihz','重复速度（次 / 秒）')]:
            ttk.Label(form, textvariable=self.size_label if name=='radius_um' else None,
                      text=title).pack(anchor='w', pady=(9, 5))
            entry = ttk.Entry(form, textvariable=self.vars[name])
            entry.pack(fill='x')
            self.user_entries[name] = entry
        ttk.Label(form, text='距离请按发射面到掌面的实际距离填写。',
                  style='Muted.TLabel', wraplength=230).pack(anchor='w', pady=10)
        self.position_details = ttk.Frame(form)
        ttk.Button(form, text='调整图形位置', command=self._toggle_position).pack(fill='x', pady=5)
        for name, title in [('cx_um','水平位置 / mm'), ('cy_um','前后位置 / mm')]:
            ttk.Label(self.position_details, text=title).pack(anchor='w', pady=3)
            ttk.Entry(self.position_details, textvariable=self.vars[name]).pack(fill='x')

    def _device_page(self, page):
        ttk.Label(page, text='选择连接方式', style='Section.TLabel').pack(anchor='w', pady=(0, 12))
        methods = ttk.Frame(page)
        methods.pack(fill='x', pady=(0, 20))
        self.mode_buttons = []
        for text, value in [('USB 串口', '真实串口'), ('蓝牙', 'BLE 蓝牙'), ('无设备演示', 'Demo 模拟设备')]:
            b = ttk.Radiobutton(methods, text=text, value=value, variable=self.mode_choice, command=self.update_controls)
            b.pack(side='left', padx=(0, 28))
            self.mode_buttons.append(b)
        self.connection_form = ttk.Frame(page, padding=18)
        self.connection_form.pack(fill='x')
        self.serial_options = ttk.Frame(self.connection_form)
        ttk.Label(self.serial_options, text='设备端口').grid(row=0, column=0, sticky='w', pady=8, padx=(0, 18))
        self.port_picker = ttk.Combobox(self.serial_options, textvariable=self.port_choice, width=24)
        self.port_picker.grid(row=0, column=1, sticky='ew')
        self.refresh_button = ttk.Button(self.serial_options, text='刷新', command=self.refresh_ports)
        self.refresh_button.grid(row=0, column=2, padx=10)
        ttk.Label(self.serial_options, text='波特率').grid(row=1, column=0, sticky='w', pady=8)
        self.baud_picker = ttk.Combobox(self.serial_options, textvariable=self.baud_choice,
                                       values=BAUD_RATES, width=24)
        self.baud_picker.grid(row=1, column=1, sticky='ew')
        self.ble_options = ttk.Frame(self.connection_form)
        self.ble_picker = ttk.Combobox(self.ble_options, textvariable=self.ble_choice, width=35, state='readonly')
        self.ble_picker.grid(row=0, column=0, padx=(0, 10))
        self.scan_button = ttk.Button(self.ble_options, text='查找设备', command=self.scan_ble)
        self.scan_button.grid(row=0, column=1)
        self.ble_settings_button = ttk.Button(self.ble_options, text='蓝牙连接参数…', command=self.configure_ble)
        self.ble_settings_button.grid(row=1, column=0, sticky='w', pady=10)
        self.demo_options = ttk.Frame(self.connection_form)
        ttk.Label(self.demo_options, text='无需连接硬件，体验选图、发送和呈现的完整流程。', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(self.demo_options, text='画面与设备反馈均为模拟，不产生实际触觉。', style='Muted.TLabel').pack(anchor='w', pady=10)
        ttk.Label(page, textvariable=self.connection_hint, style='Muted.TLabel', wraplength=730).pack(anchor='w', pady=15)
        controls = ttk.Frame(page)
        controls.pack(fill='x', pady=(0, 18))
        self.connect_button = ttk.Button(controls, text='连接设备', style='Primary.TButton', command=self.connect)
        self.connect_button.pack(side='left', padx=(0, 10))
        self.disconnect_button = ttk.Button(controls, text='断开连接', command=self.disconnect)
        self.disconnect_button.pack(side='left')
        self.source_banner = tk.Label(page, textvariable=self.source_line, bg=SOFT, fg=INK,
                                      anchor='w', padx=16, pady=16, font=('Microsoft YaHei UI', 11))
        self.source_banner.pack(fill='x', pady=10)
        self.connection_message = ttk.Label(page, textvariable=self.status_line, style='Muted.TLabel', wraplength=740)
        self.connection_message.pack(anchor='w', pady=8)
        ttk.Separator(page).pack(fill='x', pady=18)
        ttk.Label(page, text='设备操作', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(page, text='使用设备按键时切换到本地控制；在电脑中呈现图形时选择电脑控制。',
                  style='Muted.TLabel', wraplength=750).pack(anchor='w', pady=8)
        row = ttk.Frame(page)
        row.pack(fill='x')
        self.local_button = ttk.Button(row, text='使用设备按键', command=lambda: self.send('MODE', value='LOCAL'))
        self.local_button.pack(side='left', padx=(0, 10))
        self.remote_button = ttk.Button(row, text='使用电脑控制', command=lambda: self.send('MODE', value='REMOTE'))
        self.remote_button.pack(side='left')

    def _help_page(self, page):
        ttk.Label(page, text='第一次使用触见', style='Section.TLabel').pack(anchor='w', pady=(4, 20))
        for number, title, body in [
            ('01','连接设备','在“连接设备”中选择端口和波特率。没有硬件时，选择“无设备演示”。'),
            ('02','准备图形','从图形库选择图案，或在“自由创作”中绘制并保存自己的轮廓。'),
            ('03','确认并呈现','点击“呈现此图形”，在弹出的窗口中检查图形和实际距离；点击“确认并开始”。成功后可暂停、继续或停止。')]:
            row = ttk.Frame(page, padding=(0, 8))
            row.pack(fill='x', pady=6)
            ttk.Label(row, text=number, foreground=ACCENT, font=('Microsoft YaHei UI', 18, 'bold')).pack(side='left', padx=(0, 20))
            col = ttk.Frame(row)
            col.pack(fill='x')
            ttk.Label(col, text=title, style='Heading.TLabel').pack(anchor='w')
            ttk.Label(col, text=body, wraplength=710, style='Muted.TLabel').pack(anchor='w', pady=8)
        ttk.Separator(page).pack(fill='x', pady=20)
        ttk.Label(page, text='绘图与文件', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(page, text='选择工具后在掌面上绘制；双击选择整条轮廓，拖动旋转手柄即可旋转。\n'
                  '选中线条后，可以在右侧设置尺寸或几何关系。\n'
                  'Ctrl+S 保存图形，Ctrl+O 打开图形。保存的图形也会出现在“最近使用”。',
                  style='Muted.TLabel', wraplength=790).pack(anchor='w', pady=12)
        ttk.Label(page, text='关于画面', style='Heading.TLabel').pack(anchor='w', pady=(10, 0))
        ttk.Label(page, text='手掌轮廓用于帮助理解位置，不会检测或跟踪手部。呈现画面中的亮点依据设备反馈更新。\n'
                  '演示模式会明确标注；连接真实设备后显示设备返回的状态。',
                  style='Muted.TLabel', wraplength=790).pack(anchor='w', pady=12)

    def _toggle_position(self):
        if self.position_details.winfo_manager():
            self.position_details.pack_forget()
        else:
            self.position_details.pack(fill='x', pady=6)

    def _edited(self):
        self.restore_touch_modulation()
        self.vars['shape'].set(SHAPES['CUSTOM'])

    def restore_touch_modulation(self):
        if self.measurement_draft:
            self.measurement_draft = False
            self.vars['mod_hz'].set(str(BUTTON_OUTPUT_SETTINGS['mod_hz']))

    def pick_shape(self, code):
        self.restore_touch_modulation()
        if code == 'CUSTOM':
            self.show_page('editor')
            self.vars['shape'].set(SHAPES['CUSTOM'])
        else:
            self.vars['shape'].set(SHAPES[code])
        self.refresh_pattern_preview()
        self.update_controls()

    def open_shape(self, code):
        self.pick_shape(code)
        if code != 'CUSTOM':
            self.apply_config()

    def select_shape(self, _event=None):
        self.show_page('editor' if self.vars['shape'].get()==SHAPES['CUSTOM'] else 'library')
        self.refresh_pattern_preview()
        self.update_controls()

    def new_sketch(self):
        if not self.confirm_replace_draft('新建草图'):
            return
        try:
            config = replace(self.get_config(), shape='CIRCLE', scan_paths='NONE', path_xy_um='NONE', mod_hz=BUTTON_OUTPUT_SETTINGS['mod_hz'])
        except (ValueError, OverflowError):
            config = Config()
        self.set_config(config)
        self.current_file = None
        self.vars['shape'].set(SHAPES['CUSTOM'])
        self.show_page('editor')
        self.editor.tool.set('polyline')
        self.editor.choose_tool()

    def toggle_debug(self):
        self.debug_mode.set(False)
        self.editor.set_debug(False)
        self.update_controls()

    def preview(self):
        self.apply_config()

    def preview_sketch(self):
        self.apply_custom()

    def apply_custom(self):
        self.restore_touch_modulation()
        super().apply_custom()

    def set_config(self, config, document=None):
        self.measurement_draft = False
        super().set_config(replace(config, level=USER_OUTPUT_LEVEL), document)

    def apply_config(self):
        if self.pending_presentation is not None:
            return
        try:
            self.get_config()
        except (ValueError, OverflowError) as error:
            self.status_line.set(str(error))
            self.editor.message.set(str(error))
            self.next_step.set(str(error))
            return
        self.presentation_error = ''
        self.return_to_confirmation = False
        self.refresh_pattern_preview()
        if not self.confirmation_visible:
            width = min(1180, self.root.winfo_screenwidth()-80)
            height = min(870, self.root.winfo_screenheight()-120)
            x = max(0, self.root.winfo_rootx()+(self.root.winfo_width()-width)//2)
            y = max(0, self.root.winfo_rooty()+(self.root.winfo_height()-height)//2)
            self.confirmation.geometry(f'{width}x{height}+{x}+{y}')
            self.confirmation.minsize(min(width, 900), min(height, 650))
            self.confirmation.deiconify()
            self.confirmation_visible = True
            self.confirmation.grab_set()
        self.confirmation.lift()
        self.update_controls()
        self.apply_button.focus_set()

    def cancel_confirmation(self):
        self.confirmation_visible = False
        if self.confirmation.grab_current() == self.confirmation:
            self.confirmation.grab_release()
        self.confirmation.withdraw()

    def confirm_presentation(self):
        if self.pending_presentation is not None:
            return
        fresh = bool(self.ready and self.state and time.monotonic()-self.received < 1.6)
        if not fresh:
            self.cancel_confirmation()
            self.show_page('device')
            self.return_to_confirmation = True
            self.status_line.set('连接设备后，会返回这次呈现的确认窗口。')
            return
        if self.state.mode != 'REMOTE':
            self.send('MODE', value='REMOTE')
            return
        if self.busy or self.await_revision is not None or self.state.state == 'FAULT':
            return
        try:
            config = self.get_config()
            if self.workspace and self.workspace.incompatibility(config):
                raise ValueError('图形超出设备范围，请调整尺寸、位置或呈现距离。')
            if config.shape == 'CUSTOM' and 'SCAN_PATHS' not in self.capabilities:
                raise ValueError('当前设备不支持自定义轮廓，请选择基础图形。')
        except (ValueError, OverflowError) as error:
            self.presentation_error = str(error)
            self.config_line.set(str(error))
            return
        self.pending_presentation = dict(config=config, stage='stop', boot=self.state.boot,
                                         generation=self.generation, deadline=time.monotonic()+10)
        self.presentation_error = ''
        self.cancel_confirmation()
        if self.state.state != 'IDLE':
            App.send(self, 'STOP')
        else:
            self._advance_presentation()
        self.update_controls()

    def _advance_presentation(self):
        plan = self.pending_presentation
        if plan is None or self.advancing_presentation:
            return
        fresh = bool(self.ready and self.state and time.monotonic()-self.received < 1.6)
        if (not fresh or self.generation != plan['generation'] or self.state.boot != plan['boot']
                or self.state.mode != 'REMOTE' or self.state.state == 'FAULT'
                or time.monotonic() > plan['deadline']):
            self.pending_presentation = None
            self.presentation_error = '呈现未完成，请检查连接后重试。'
            return
        if self.busy or self.await_revision is not None:
            return
        try:
            if self.get_config() != plan['config']:
                raise ValueError('图形已更改，请重新点击呈现。')
            self.advancing_presentation = True
            if plan['stage'] == 'stop':
                if self.state.state != 'IDLE':
                    raise ValueError('设备尚未停止，请重试。')
                plan['stage'] = 'config'
                App.send(self, 'CONFIG', **plan['config'].wire())
                if not self.busy or self.pending_verb != 'CONFIG':
                    raise ValueError('图形未能发送，请重试。')
            elif plan['stage'] == 'config':
                if not self.config_confirmed() or self.state.config != plan['config']:
                    raise ValueError('设备没有确认这次图形，请重新呈现。')
                plan['stage'] = 'start'
                App.send(self, 'START')
                if not self.busy or self.pending_verb != 'START':
                    raise ValueError('呈现未能启动，请重试。')
            else:
                self.pending_presentation = None
                if self.state.state == 'RUNNING':
                    self.has_presented = True
                else:
                    raise ValueError('设备未能开始呈现，请重试。')
        except (ValueError, OverflowError) as error:
            self.pending_presentation = None
            self.presentation_error = str(error)
        finally:
            self.advancing_presentation = False

    def send(self, verb, **fields):
        if verb == 'STOP':
            self.pending_presentation = None
            self.presentation_error = ''
        super().send(verb, **fields)

    def disconnect(self):
        self.pending_presentation = None
        self.return_to_confirmation = False
        self.disconnecting = bool(self.session and self.session.is_alive())
        super().disconnect()

    def connect(self):
        previous = self.session
        if previous is None or not previous.is_alive():
            self.has_presented = False
            self.pending_presentation = None
        super().connect()
        if self.session is not None and self.session is not previous:
            self.disconnecting = False
            self._save_preferences()
            self.session_mirror = SessionMirror(self.session)
            self.session_mirror.start()

    def render_log(self):
        pass

    def refresh_pattern_preview(self):
        if self.syncing_geometry:
            return
        try:
            self.preset_preview.set_config(self.get_config())
        except (ValueError, OverflowError):
            self.preset_preview.set_config(None)
        code = next((k for k,v in SHAPES.items() if v==self.vars['shape'].get()), 'CIRCLE')
        self.selected_title.set('当前选择：'+SHAPES[code])
        for key, card in self.cards.items():
            card.set_active(key==code)
        self.size_label.set({'SQUARE':'正方形半边长（mm）', 'LINE_X':'线段半长（mm）',
                             'LINE_Y':'线段半长（mm）', 'TRIANGLE':'外接圆半径（mm）',
                             'ARROW':'箭头半长（mm）'}.get(code,'图形半径（mm）'))
        self.user_entries['radius_um'].configure(state='disabled' if code in ('POINT','CUSTOM') else 'normal')

    def get_config(self):
        try:
            return replace(super().get_config(), level=USER_OUTPUT_LEVEL)
        except (ValueError, OverflowError) as error:
            text = str(error)
            labels = {'cx_um':'水平位置', 'cy_um':'前后位置', 'radius_um':'图形大小',
                      'z_um':'呈现距离', 'repeat_millihz':'重复速度', 'level':'呈现强度',
                      'carrier_hz':'设备载波', 'phase_steps':'设备相位设置', 'mod_hz':'设备调制设置'}
            for name, label in labels.items():
                if name in text:
                    text = f'请检查{label}，输入有效且在设备范围内的数值。'
                    break
            if 'could not convert' in text:
                text = '请输入有效数字，完成图形后再预览或发送。'
            raise ValueError(text) from error

    def update_controls(self):
        self.update_file_line()
        alive = bool(self.session and self.session.is_alive())
        fresh = bool(self.ready and self.state and time.monotonic()-self.received < 1.6)
        remote = fresh and self.state.mode=='REMOTE'
        if not alive:
            self.disconnecting = False
        free = not self.busy and self.await_revision is None and not self.acoustic_panel.busy
        idle = fresh and self.state.state=='IDLE'
        valid = True
        try:
            config = self.get_config()
            mismatch = self.workspace.incompatibility(config) if self.workspace else ''
        except (ValueError, OverflowError):
            valid, mismatch = False, ''
        preparing = self.pending_presentation is not None
        can_prepare = valid and free and not preparing
        can_confirm = bool(can_prepare and not mismatch and
                           (not alive or fresh and self.state.state != 'FAULT'))
        for button, enabled in [(self.apply_button, can_confirm),
                                (self.present_button, can_prepare),
                                (self.editor.apply_button, can_prepare),
                                (self.start_button, self.can_play() and not preparing),
                                (self.pause_button, bool(remote and free and not preparing and self.state.state=='RUNNING')),
                                (self.stop_button, self.ready), (self.header_stop,self.ready),
                                (self.confirmation_stop,self.ready),
                                (self.connect_button,not alive and self.ble_scan is None),
                                (self.disconnect_button,alive and not self.disconnecting), (self.local_button,idle and free),
                                (self.remote_button,fresh and free and (idle or self.state.mode=='LOCAL'))]:
            button.configure(state='normal' if enabled else 'disabled')
        self.connection_shortcut.configure(state='disabled' if self.disconnecting or self.closing else 'normal')
        self.measurement_setup.configure(state='normal' if can_prepare else 'disabled')
        self.preset_preview.apply_button.configure(state=self.apply_button['state'])
        self.apply_button.configure(text='确认并开始' if remote else '切换到电脑控制' if fresh else
                                    '正在连接…' if alive else '连接设备并继续')
        if remote and self.state.state in ('RUNNING', 'PAUSED'):
            self.apply_button.configure(text='替换并开始呈现')
        if self.state and self.ready and self.state.state in ('RUNNING', 'PAUSED', 'FAULT'):
            if not self.confirmation_stop.winfo_manager():
                self.confirmation_stop.pack(fill='x',pady=5,before=self.cancel_button)
        else:
            self.confirmation_stop.pack_forget()
        scanning = self.ble_scan is not None
        for b in self.mode_buttons:
            b.configure(state='disabled' if alive or scanning else 'normal')
        serial = self.mode_choice.get()=='真实串口'
        ble = self.mode_choice.get()=='BLE 蓝牙'
        if self.connection_layout != self.mode_choice.get():
            for form in (self.serial_options, self.ble_options, self.demo_options):
                form.pack_forget()
            (self.serial_options if serial else self.ble_options if ble else self.demo_options).pack(fill='x')
            self.connection_layout = self.mode_choice.get()
        self.port_picker.configure(state='normal' if not alive else 'disabled')
        self.baud_picker.configure(state='normal' if not alive else 'disabled')
        self.refresh_button.configure(state='disabled' if alive else 'normal')
        self.ble_picker.configure(state='disabled' if alive or scanning else 'readonly')
        self.scan_button.configure(state='disabled' if alive else 'normal', text='取消查找' if scanning else '查找设备')
        self.ble_settings_button.configure(state='disabled' if alive or scanning else 'normal')
        self.connection_hint.set('选择设备端口；波特率需与设备设置一致，也可以手动输入。' if serial else
                                 '先查找附近设备，再选择要连接的触见模块。' if ble else
                                 '选择图形后点击呈现，演示同样需要设备确认。')
        confirmed = bool(fresh and self.config_confirmed() and valid and config==self.state.config)
        static_confirmed = bool(fresh and self.config_confirmed() and self.state.config.phase_table != 'NONE')
        if static_confirmed:
            self.has_presented = True
        running = bool(fresh and self.state.state=='RUNNING')
        if self.busy:
            action = self.pending_verb or self.await_verb
            feedback = {'CONFIG':'正在发送图形，请稍候…', 'START':'正在启动呈现…',
                        'PAUSE':'正在暂停…', 'STOP':'正在停止…'}.get(action,'正在等待设备确认…')
        elif not fresh:
            feedback = '连接设备后即可继续这次呈现。' if not alive else '等待设备响应，请检查连接。'
        elif self.state.state=='FAULT':
            feedback = '设备报告故障，请停止呈现并检查设备。'
        elif self.state.mode=='LOCAL':
            feedback = '设备按键控制中。请先切换到电脑控制。'
        elif not valid:
            feedback = '请完成图形并检查输入的数值。'
        elif mismatch:
            feedback = '图形超出设备支持范围，请调整尺寸、位置或呈现距离。'
        elif static_confirmed:
            feedback = '压力单帧已确认，点击开始呈现。' if idle else '已暂停压力呈现。' if self.state.state=='PAUSED' else '正在呈现静态压力分布。'
        elif not confirmed:
            feedback = '图形已准备好，确认后即可开始。' if idle else '确认后会停止当前呈现，并使用新图形。'
        else:
            feedback = '图形已确认，可以开始呈现。' if idle else '已暂停，可以继续呈现。' if self.state.state=='PAUSED' else '正在呈现设备已接收的图形。'
        if self.presentation_error and not self.busy:
            feedback = self.presentation_error
        self.config_line.set(feedback)
        self.next_step.set(feedback if self.page_name not in ('library','editor') or preparing else
                           '选择或绘制图形 → 点击呈现 → 确认并开始')
        self.playback.update_state(self.state, fresh, feedback,
                                   self.session.connection_label if self.session else '')
        self.start_button.configure(text='继续呈现' if fresh and self.state.state=='PAUSED' else '开始呈现')
        self.user_device.set('正在断开…' if self.disconnecting else '断开连接' if alive and self.ready else
                             '取消连接' if alive else '连接设备')
        self.header_source.set('演示模式' if fresh and self.state.simulated else '设备已连接' if fresh else
                               '等待反馈' if alive and self.ready else '正在连接' if alive else '未连接')
        self.source_line.set(('演示体验 · '+('正在呈现' if running else '已连接')) if fresh and self.state.simulated else
                             '设备已连接 · '+('电脑控制' if remote else '设备按键控制') if fresh else
                             '等待设备响应' if alive else '尚未连接设备')
        if fresh and self.state.state in ('RUNNING','PAUSED'):
            self.has_presented = True
        visible = self.has_presented or preparing or static_confirmed
        if visible:
            self.tabs.tab(self.playback_tab,state='normal')
            if not self.nav_buttons['playback'].winfo_manager():
                self.nav_buttons['playback'].pack(fill='x',padx=12,pady=3,after=self.nav_buttons['editor'])
        elif self.tabs.select() != str(self.playback_tab):
            self.tabs.tab(self.playback_tab,state='hidden')
            self.nav_buttons['playback'].pack_forget()
        self._advance_presentation()
        self.acoustic_panel.tick(fresh, self.page_name == 'field')
        if self.return_to_confirmation and fresh and not self.closing:
            self.return_to_confirmation = False
            self.root.after_idle(self.apply_config)

    def _load_preferences(self):
        try:
            if self.preferences_path.stat().st_size > 65536:
                return
            data = json.loads(self.preferences_path.read_text(encoding='utf-8'))
            self.recent_files = [str(p) for p in data.get('recent', []) if isinstance(p,str)][:6]
            for key, variable in [('port',self.port_choice), ('baud',self.baud_choice)]:
                value = data.get(key)
                if isinstance(value,str) and len(value)<120:
                    variable.set(value)
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        self._render_recent()

    def _save_preferences(self):
        temporary = None
        try:
            self.preferences_path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.preferences_path.parent,
                                             prefix='.touchsee-user-', suffix='.tmp',delete=False) as f:
                temporary = Path(f.name)
                json.dump({'recent':self.recent_files,'port':self.port_choice.get(),'baud':self.baud_choice.get()},f,ensure_ascii=False)
            os.replace(temporary,self.preferences_path)
        except OSError:
            pass
        finally:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _remember_file(self):
        if self.current_file:
            value = str(self.current_file.resolve())
            self.recent_files = [value]+[p for p in self.recent_files if p!=value][:5]
            self._save_preferences()
            self._render_recent()

    def _render_recent(self):
        for child in self.recent_host.winfo_children():
            child.destroy()
        if not self.recent_files:
            ttk.Label(self.recent_host, text='保存或打开图形后，可在这里快速找到它。', style='Muted.TLabel').pack(anchor='w',pady=8)
        for path in self.recent_files[:3]:
            row = ttk.Frame(self.recent_host)
            row.pack(fill='x',pady=3)
            ttk.Button(row,text=Path(path).stem,command=lambda p=path:self.open_recent(p)).pack(side='left',fill='x',expand=True)
            ttk.Label(row,text='已保存图形',style='Muted.TLabel').pack(side='right',padx=12)

    def save_config(self):
        saved = super().save_config()
        if saved:
            self._remember_file()
        return saved

    def load_config(self):
        old = self.current_file
        super().load_config()
        if self.current_file is not None and (self.current_file!=old or not self.draft_dirty()):
            self._remember_file()

    def open_recent(self, filename):
        try:
            self.read_config_file(filename)
            if not self.confirm_replace_draft('打开图形'):
                return
            config, document = self.read_config_file(filename)
            self.set_config(config, document)
            self.current_file = Path(filename)
            self.file_directory = self.current_file.parent
            self.saved_signature = self.draft_signature()
            self.confirmed_config = self.pending_config = None
            self.select_shape()
            self._remember_file()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            messagebox.showerror('无法打开图形',str(error),parent=self.root)

    def close(self):
        self.pending_presentation = None
        self.return_to_confirmation = False
        self.cancel_confirmation()
        super().close()
        if self.closing:
            self._save_preferences()
