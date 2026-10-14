"""Advanced BLE profile editing; the hardware module has not been selected."""

from dataclasses import asdict
import tkinter as tk
from tkinter import messagebox, ttk

from .ble_transport import BleProfile


class BleSettings(tk.Toplevel):
    def __init__(self, parent, profile, on_apply):
        super().__init__(parent)
        self.title("蓝牙连接设置")
        self.transient(parent)
        self.resizable(False, False)
        self.on_apply = on_apply
        self.variables = {}
        content = ttk.Frame(self, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="默认配置适用于 Nordic UART 服务。其他模块请按其资料修改。\n"
                  "这里设置蓝牙收发通道；设备指令暂用 HAP3 草案，仍需与板端对接。",
                  wraplength=570).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 15))
        names = (("service_uuid", "服务 UUID"), ("write_uuid", "电脑发送通道 UUID"),
                 ("notify_uuid", "电脑接收通道 UUID"), ("write_mode", "写入方式"),
                 ("chunk_size", "每包字节上限"))
        self.modes = {"自动（优先有应答）": "auto", "有应答写入": "response",
                      "无应答写入": "without-response"}
        for row, (key, label) in enumerate(names, start=1):
            ttk.Label(content, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            var = self.variables[key] = tk.StringVar()
            widget = (ttk.Combobox(content, textvariable=var, values=list(self.modes), state="readonly", width=45)
                      if key == "write_mode" else ttk.Entry(content, textvariable=var, width=48))
            widget.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Label(content, text="字节上限：0 为自动，也可填 20 等模块支持的值（不超过 512）。\n"
                  "有应答写入仅确认蓝牙交付；播放仍需等待设备确认图形。\n"
                  "设置在本次程序运行期间保留。", wraplength=570).grid(
                      row=6, column=0, columnspan=2, sticky="w", pady=10)
        actions = ttk.Frame(content)
        actions.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="恢复默认", command=lambda: self.set_profile(BleProfile())).pack(side="left")
        ttk.Button(actions, text="应用", command=self.apply).pack(side="right")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=8)
        self.set_profile(profile)
        self.grab_set()

    def set_profile(self, profile):
        for key, value in asdict(profile).items():
            if key == "write_mode":
                value = next(label for label, mode in self.modes.items() if mode == value)
            self.variables[key].set(str(value))

    def apply(self):
        try:
            values = {key: var.get().strip() for key, var in self.variables.items()}
            values["write_mode"] = self.modes[values["write_mode"]]
            try:
                values["chunk_size"] = int(values["chunk_size"])
            except ValueError as error:
                raise ValueError("每包字节上限请输入整数，0 表示自动。") from error
            profile = BleProfile(**values).normalized()
        except (ValueError, KeyError) as error:
            messagebox.showerror("连接设置无效", str(error), parent=self)
            return
        self.on_apply(profile)
        self.destroy()
