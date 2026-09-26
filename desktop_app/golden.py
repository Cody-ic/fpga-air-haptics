"""导出相位求解的黄金向量，供 FPGA 侧逐通道比对。

参考模型见 `desktop_app/model.py`。本工具只做批量导出与格式固化，不引入新的
物理假设；输出的相位码是「电脑认为设备应该算出的值」，不是实测数据。

用法（仓库根目录）：

    python -m desktop_app.golden
    python -m desktop_app.golden --array 8x8 --rate-hz 2000
    python -m desktop_app.golden --file desktop_app/exports/haptics-config.json

输出目录（默认 `desktop_app/exports/golden-*/`）：

- `*.csv`：人看的对照表，每帧的焦点坐标（单位微米）与全部相位码
- `*_phases.mem`：`$readmemh` 用的相位码，每行一帧，每通道两位十六进制
- `*_focus.mem`：同一帧的焦点坐标，x/y/z 各 32 位补码十六进制
- `*.json`：本次导出的参数、帧数与文件校验和，便于回溯
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__
from .model import (ArraySpec, Config, SHAPES, config_from_document,
                    focus_phases, trajectory_point)

MAX_FRAMES = 200000
NOTES = [
    "相位码由 desktop_app/model.py 的参考模型生成，是设备的期望值，不是实测数据。",
    "RTL 定点实现应与 phases.mem 逐通道一致，或误差不超过 1 个档位并说明理由。",
    "phases.mem：每行一帧，每通道两位十六进制，$readmemh 可直接读取。",
    "focus.mem：每行三个 32 位补码十六进制，依次为 x_um、y_um、z_um。",
    "改阵列尺寸不改变图形坐标，但会改变相位码数量与数值，必须重新导出。",
]


def parse_array(text):
    """把 `4x4`、`4,4` 这类写法解析成 (rows, cols)。"""
    parts = text.lower().replace(",", "x").split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("阵列写法应为 4x4、8x8 这样的“行x列”")
    try:
        rows, cols = (int(part) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("阵列行列必须是整数") from None
    if not 1 <= rows <= 16 or not 1 <= cols <= 16:
        raise argparse.ArgumentTypeError("阵列行列须在 1～16 之间")
    return rows, cols


def frames_per_lap(config, rate_hz):
    """按给定更新率走完一圈轨迹需要多少帧。"""
    return max(1, int(round(rate_hz * 1000.0 / config.repeat_millihz)))


def build_config(args):
    if args.file:
        document = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
        return config_from_document(document)
    return Config(carrier_hz=args.carrier_hz, phase_steps=args.phase_steps,
                  cx_um=args.cx_um, cy_um=args.cy_um, z_um=args.z_um,
                  radius_um=args.radius_um, repeat_millihz=args.repeat_millihz,
                  mod_hz=args.mod_hz, level=args.level, shape=args.shape,
                  path_xy_um=args.path_xy_um, path_closed=args.path_closed).validate()


def generate(config, array, rate_hz, frames):
    """按固定更新率采样轨迹，返回时间、焦点坐标（mm）与相位码矩阵。"""
    if not rate_hz > 0:
        raise ValueError("相位更新率必须为正数")
    if not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"帧数须在 1～{MAX_FRAMES} 之间")
    times = np.arange(frames, dtype=float) / rate_hz
    focus_mm = np.array([trajectory_point(config, float(t)) for t in times])
    codes = np.array([focus_phases(config, point, array) for point in focus_mm], dtype=int)
    if codes.shape != (frames, array.count):
        raise AssertionError("相位码维度与阵列不符")
    if codes.min() < 0 or codes.max() >= config.phase_steps:
        raise AssertionError("相位码超出档位范围")
    return times, focus_mm, codes


def _hex32(value):
    return "%08X" % (int(value) & 0xFFFFFFFF)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tolerant_console():
    """控制台编码放不下的字符应降级为替代符号，而不是让命令崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def write_outputs(out_dir, config, array, times, focus_mm, codes, rate_hz):
    """写出 CSV、两个 .mem 文件和一份元数据 JSON，返回元数据字典。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = out_dir.name or "golden"
    focus_um = [[int(round(value * 1000.0)) for value in row] for row in focus_mm]
    width = 2 if config.phase_steps <= 256 else 3
    frames = len(times)

    csv_path = out_dir / f"{name}.csv"
    header = ["frame", "time_us", "fx_um", "fy_um", "fz_um"]
    header += [f"phase_{index}" for index in range(array.count)]
    lines = [",".join(header)]
    for index in range(frames):
        row = [str(index), f"{times[index] * 1e6:.3f}"]
        row += [str(value) for value in focus_um[index]]
        row += [str(int(value)) for value in codes[index]]
        lines.append(",".join(row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    phases_path = out_dir / f"{name}_phases.mem"
    phases_path.write_text(
        "".join(" ".join(f"{int(value):0{width}X}" for value in row) + "\n" for row in codes),
        encoding="ascii")

    focus_path = out_dir / f"{name}_focus.mem"
    focus_path.write_text(
        "".join(" ".join(_hex32(value) for value in row) + "\n" for row in focus_um),
        encoding="ascii")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": __version__,
        "frames": frames,
        "rate_hz": rate_hz,
        "frames_per_lap": frames_per_lap(config, rate_hz),
        "laps": frames / frames_per_lap(config, rate_hz),
        "array": array.wire(),
        "config": config.wire(),
        "phase_code_range": [int(codes.min()), int(codes.max())],
        "focus_um_range": [int(min(row[i] for row in focus_um)) for i in range(3)]
                         + [int(max(row[i] for row in focus_um)) for i in range(3)],
        "files": {"csv": csv_path.name, "phases": phases_path.name, "focus": focus_path.name},
        "sha256": {"csv": _sha256(csv_path), "phases": _sha256(phases_path),
                   "focus": _sha256(focus_path)},
        "notes": NOTES,
    }
    meta_path = out_dir / f"{name}.json"
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["paths"] = {"csv": csv_path, "phases": phases_path, "focus": focus_path, "meta": meta_path}
    return payload


def main(argv=None):
    _tolerant_console()
    parser = argparse.ArgumentParser(description="导出 FPGA 相位求解的黄金向量")
    parser.add_argument("--array", type=parse_array, default=(4, 4), metavar="行x列",
                        help="实际阵列尺寸，默认 4x4")
    parser.add_argument("--pitch-um", type=int, default=10000, help="阵元间距，单位微米")
    parser.add_argument("--file", help="图形 JSON（界面“保存图形”的文件），与图形参数互斥")
    parser.add_argument("--shape", choices=sorted(SHAPES), default="CIRCLE", help="预设图形")
    parser.add_argument("--radius-um", type=int, default=20000, help="预设图形半径／半长，单位微米")
    parser.add_argument("--path-xy-um", default="NONE", help="自定义顶点，形如 x:y,x:y")
    parser.add_argument("--path-closed", type=int, choices=(0, 1), default=1, help="1 闭合，0 往返")
    parser.add_argument("--cx-um", type=int, default=0, help="图形中心 x，单位微米")
    parser.add_argument("--cy-um", type=int, default=0, help="图形中心 y，单位微米")
    parser.add_argument("--z-um", type=int, default=150000, help="焦点平面高度，单位微米")
    parser.add_argument("--carrier-hz", type=int, default=40000, help="载波频率，Hz")
    parser.add_argument("--phase-steps", type=int, default=64, help="相位档数")
    parser.add_argument("--repeat-millihz", type=int, default=500, help="轨迹重复率乘 1000")
    parser.add_argument("--mod-hz", type=int, default=200, help="调制频率，Hz")
    parser.add_argument("--level", type=int, default=30, help="归一化驱动等级 0～100")
    parser.add_argument("--rate-hz", type=float, default=1000.0, help="相位更新率，默认 1000 Hz")
    parser.add_argument("--frames", type=int, help="导出帧数，默认为完整一圈")
    parser.add_argument("--out", help="输出目录，默认 desktop_app/exports/golden-行x列-图形")
    args = parser.parse_args(argv)

    if args.file and (args.shape != "CIRCLE" or args.path_xy_um != "NONE"):
        parser.error("--file 已经包含图形参数，不能再指定 --shape 或 --path-xy-um")

    config = build_config(args)
    array = ArraySpec(*args.array, args.pitch_um, "ROW_MAJOR_XY").validate()
    frames = args.frames or frames_per_lap(config, args.rate_hz)
    out_dir = Path(args.out) if args.out else (
        Path("desktop_app/exports") / f"golden-{array.rows}x{array.cols}-{config.shape.lower()}")

    times, focus_mm, codes = generate(config, array, args.rate_hz, frames)
    meta = write_outputs(out_dir, config, array, times, focus_mm, codes, args.rate_hz)

    print(f"阵列 {array.rows}×{array.cols} = {array.count} 路，相位 {config.phase_steps} 档，"
          f"载波 {config.carrier_hz} Hz")
    print(f"图形 {config.shape}，焦点平面 z = {config.z_um / 1000:.1f} mm，"
          f"轨迹重复率 {config.repeat_millihz / 1000:.3f} 圈/秒")
    print(f"导出 {meta['frames']} 帧，更新率 {args.rate_hz:g} Hz，"
          f"覆盖 {meta['laps']:.3f} 圈（一圈 {meta['frames_per_lap']} 帧）")
    print(f"相位码范围 {meta['phase_code_range'][0]}～{meta['phase_code_range'][1]}")
    print("输出：")
    for key in ("csv", "phases", "focus", "meta"):
        path = meta["paths"][key]
        print(f"  {path}  {path.stat().st_size} 字节")
    print(f"  phases.mem sha256 {meta['sha256']['phases'][:16]}…")
    print("提示：RTL 测试台读 *_phases.mem 逐通道比对；换阵列后必须重新导出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
