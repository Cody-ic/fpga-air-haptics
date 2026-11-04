"""Geometric features of an annotated 21-joint hand, independent of devices.

Joint order is wrist, then four joints per thumb/index/middle/ring/little.
These are descriptive model features, not clinical range-of-motion measures.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class PalmFrame:
    origin_m: np.ndarray
    axes: np.ndarray
    hand_side: str
    width_mm: float
    length_mm: float

    def local_mm(self, points_m):
        points = np.asarray(points_m, dtype=float)
        if points.shape[-1:] != (3,) or not np.isfinite(points).all():
            raise ValueError('手部坐标须为有限三维坐标')
        return (points-self.origin_m) @ self.axes * 1000

    def metadata(self):
        return dict(origin_m=self.origin_m.tolist(), axes_columns=self.axes.tolist(),
                    hand_side=self.hand_side, width_mm=self.width_mm,
                    length_mm=self.length_mm, convention='radial_longitudinal_cross_product',
                    source='annotated_21_joints', live_tracking=False)


def palm_frame_from_joints(joints_m, hand_side='right'):
    joints = np.asarray(joints_m, dtype=float)
    if joints.shape != (21, 3) or not np.isfinite(joints).all():
        raise ValueError('需要21个有效关节，坐标单位为米')
    if hand_side not in ('left', 'right'):
        raise ValueError('左右手标记无效')
    radial = joints[5]-joints[17]
    longitudinal = joints[9]-joints[0]
    width, length = float(np.linalg.norm(radial)), float(np.linalg.norm(longitudinal))
    if min(width, length) < 1e-5:
        raise ValueError('关节重合，无法建立掌平面')
    radial /= width
    longitudinal -= np.dot(longitudinal, radial)*radial
    norm = float(np.linalg.norm(longitudinal))
    if norm < 1e-5 or norm/length < .05:
        raise ValueError('掌部关节近共线，无法建立稳定掌平面')
    longitudinal /= norm
    axes = np.column_stack((radial, longitudinal, np.cross(radial, longitudinal)))
    origin = joints[[0, 5, 9, 13, 17]].mean(0)
    axes.setflags(write=False)
    origin.setflags(write=False)
    return PalmFrame(origin, axes, hand_side, width*1000, length*1000)


def geometric_bends_deg(joints_m):
    joints = np.asarray(joints_m, dtype=float)
    if joints.shape != (21, 3) or not np.isfinite(joints).all():
        raise ValueError('需要21个有效关节')
    result = {}
    for name, first in zip(('thumb', 'index', 'middle', 'ring', 'little'), (1, 5, 9, 13, 17)):
        bones = np.diff(joints[first:first+4], axis=0)
        lengths = np.linalg.norm(bones, axis=1)
        if np.any(lengths < 1e-6):
            result[name] = None
            continue
        unit = bones/lengths[:, None]
        angles = np.degrees(np.arccos(np.clip(np.sum(unit[:-1]*unit[1:], axis=1), -1, 1)))
        result[name] = float(angles.sum())
    return result
