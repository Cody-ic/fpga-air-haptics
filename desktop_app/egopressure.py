"""Offline EgoPressure data. No device session or transport is used here.

Conventions: https://github.com/eth-siplab/EgoPressure/blob/master/docs/DATA.md
"""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
import json

import numpy as np

FPS = 30.0
COUNTS_PER_NEWTON = 1736.0
CELL_PITCH_M = .00125
SAMPLE_NAME = "p_001_press_palm_low_x5_right"


def display_points(points):
    """Rotate 180 degrees about x: metres remain metres; z is now up."""
    return np.asarray(points, dtype=np.float32) * np.array([1, -1, -1], np.float32)


def counts_to_kpa(counts):
    return np.asarray(counts, dtype=np.float32) / (COUNTS_PER_NEWTON * CELL_PITCH_M**2 * 1000)


def pressure_rgb(counts, limit_kpa=20):
    """Fixed physical scale across frames; zero has the pad's dark surface color."""
    stops = np.array([[.16, .20, .27], [.12, .40, .84], [.05, .76, .84],
                      [.98, .77, .22], [.96, .26, .20]], dtype=np.float32)
    values = np.clip(counts_to_kpa(counts) / limit_kpa, 0, 1)
    colors = np.stack([np.interp(values, [0, .1, .3, .65, 1], stops[:, c]) for c in range(3)], -1)
    return np.ascontiguousarray(colors * 255, dtype=np.uint8)


@dataclass
class ReplaySequence:
    name: str
    frames: np.ndarray
    vertices: np.ndarray
    joints: np.ndarray
    force: np.ndarray
    hand_valid: np.ndarray
    pressure_valid: np.ndarray
    hand_side: str = "right"
    source_dataset: str = "EgoPressure"
    vertices_available: bool = True

    def __post_init__(self):
        n = len(self.frames)
        if not n or n > 30000 or self.frames.shape != (n,):
            raise ValueError("序列帧数无效")
        if np.any(self.frames < 1) or np.any(np.diff(self.frames) <= 0):
            raise ValueError("帧编号必须为递增正整数")
        for value, shape in ((self.vertices, (n, 778, 3)), (self.joints, (n, 21, 3)),
                             (self.force, (n, 105, 185)), (self.hand_valid, (n,)),
                             (self.pressure_valid, (n,))):
            if value.shape != shape:
                raise ValueError(f"数据形状错误：需要 {shape}，得到 {value.shape}")
        if not np.isfinite(self.vertices[self.hand_valid]).all() or not np.isfinite(self.joints[self.hand_valid]).all():
            raise ValueError("有效手部帧包含非有限坐标")
        if not np.isfinite(self.force[self.pressure_valid]).all() or np.any(self.force[self.pressure_valid] < 0):
            raise ValueError("压力值必须是有限非负数")
        if self.hand_side not in ("left", "right"):
            raise ValueError("未知的左右手标记")
        if self.source_dataset not in ('EgoPressure', 'ContactPose') or type(self.vertices_available) is not bool:
            raise ValueError('不支持的数据源或网格可用标记')
        if self.source_dataset == 'ContactPose' and self.pressure_valid.any():
            raise ValueError('ContactPose没有压力测量，不能声明压力有效')

    @property
    def seconds(self):
        return (self.frames - self.frames[0]) / FPS

    @cached_property
    def total_force_n(self):
        return np.where(self.pressure_valid, self.force.sum(axis=(1, 2)) / COUNTS_PER_NEWTON, np.nan)

    def index_at(self, seconds):
        return max(0, min(len(self.frames)-1, int(np.searchsorted(self.seconds, seconds, side="right")-1)))

    def save(self, path):
        metadata = json.dumps({"format": "touchsee-egopressure-1", "name": self.name,
                               "hand_side": self.hand_side, "fps": FPS,
                               "source_dataset": self.source_dataset, "vertices_available": self.vertices_available})
        np.savez_compressed(path, metadata=metadata, frames=self.frames, vertices=self.vertices,
                            joints=self.joints, force=self.force, hand_valid=self.hand_valid,
                            pressure_valid=self.pressure_valid)


def load_sequence(path):
    path = Path(path)
    if path.is_dir() and (path / 'annotations.json').is_file():
        from .hand_datasets import load_contactpose
        return load_contactpose(path)
    if path.suffix.lower() == '.json':
        from .hand_datasets import load_contactpose
        return load_contactpose(path)
    if path.is_dir() or path.suffix == ".parquet":
        return load_parquet(path if path.is_dir() else path.parent)
    with np.load(path, allow_pickle=False) as data:
        metadata = json.loads(str(data["metadata"]))
        if metadata.get("format") != "touchsee-egopressure-1" or metadata.get("fps") != FPS:
            raise ValueError("不支持的回放格式或帧率")
        return ReplaySequence(metadata["name"], data["frames"].astype(np.int64),
                              data["vertices"].astype(np.float32), data["joints"].astype(np.float32),
                              data["force"].astype(np.float32), data["hand_valid"].astype(bool),
                              data["pressure_valid"].astype(bool), metadata["hand_side"],
                              metadata.get('source_dataset', 'EgoPressure'), metadata.get('vertices_available', True))


def load_parquet(directory):
    """Join by original frame IDs, never by row number; absent data stays absent."""
    import pyarrow.parquet as pq
    directory = Path(directory)
    annotations = pq.read_table(directory / "annotation.parquet", columns=[
        "frame", "has_annotation", "hand_side", "vertices", "joint_position"]).to_pylist()
    pressures = pq.read_table(directory / "pressure.parquet", columns=["frame", "force"]).to_pylist()
    def keyed(rows):
        result = {int(row["frame"]): row for row in rows}
        if len(result) != len(rows):
            raise ValueError("同一数据流中存在重复帧编号")
        return result
    a, p = keyed(annotations), keyed(pressures)
    sides = {row["hand_side"] for row in annotations}
    if len(sides) != 1:
        raise ValueError("序列中左右手标记不一致")
    ids = sorted(a.keys() | p.keys())
    if not ids or ids[-1]-ids[0]+1 > 30000:
        raise ValueError("请导入单个手势序列")
    frames = np.arange(ids[0], ids[-1]+1, dtype=np.int64)
    n = len(frames)
    vertices, joints = np.zeros((n, 778, 3), np.float32), np.zeros((n, 21, 3), np.float32)
    force = np.zeros((n, 105, 185), np.float32)
    hand_valid, pressure_valid = np.zeros(n, bool), np.zeros(n, bool)
    for index, frame in enumerate(frames):
        row = a.get(frame)
        if row and row["has_annotation"]:
            vertices[index] = np.asarray(row["vertices"]).reshape(778, 3)
            joints[index] = np.asarray(row["joint_position"]).reshape(21, 3)
            hand_valid[index] = True
        row = p.get(frame)
        if row and row["force"] is not None:
            force[index] = np.asarray(row["force"]).reshape(105, 185)
            pressure_valid[index] = True
    return ReplaySequence(directory.name, frames, vertices, joints, force, hand_valid, pressure_valid, sides.pop())


def sample_path():
    return Path(__file__).resolve().parent / "assets" / "egopressure" / (SAMPLE_NAME + ".npz")
