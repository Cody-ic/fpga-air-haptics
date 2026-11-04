"""Offline ContactPose joint annotations; contact values are never pressure.

Source: https://github.com/facebookresearch/ContactPose
Format: https://github.com/facebookresearch/ContactPose/blob/main/utilities/dataset.py
Transforms: https://github.com/facebookresearch/ContactPose/blob/main/utilities/misc.py
Documentation: https://github.com/facebookresearch/ContactPose/blob/main/docs/doc.md
License: https://github.com/facebookresearch/ContactPose/blob/main/LICENSE.txt
The official README licenses code and non-model data under MIT. No object mesh,
MANO model, contact map, camera image, or inferred pressure is loaded here.

The two ``hands`` entries mean [left, right]. Joints use OpenPose's wrist and
four joints per finger, in metres. For a moving hand, ``frames[i].hTo`` maps
object coordinates to hand coordinates: invert it to match ``hand_joints(i)``
in the official loader. Static hand joints already use object coordinates.
Quaternion order is [w, x, y, z], as in transforms3d.quaternions.quat2mat.
The returned pose stays in the object's coordinate frame, without centring or
registration to a pressure pad, array, camera, or live hand. Importing a grasp
does not imply a time-varying motion or measured force.
"""

import json
from numbers import Integral
from pathlib import Path

import numpy as np

from .egopressure import ReplaySequence


class ContactPoseHandSelectionRequired(ValueError):
    """The source contains two valid hands; the caller must select one."""


def _numeric_array(value, shape, label):
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in "iuf":
            raise ValueError
        result = np.asarray(raw, dtype=np.float64)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"ContactPose {label}须为数值数组") from exc
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"ContactPose {label}须为有限数组，形状为 {shape}")
    return result


def _object_joints(hand, frame, hand_index):
    joints = _numeric_array(hand.get("joints"), (21, 3), "关节")
    if not isinstance(hand.get("moving"), bool):
        raise ValueError("ContactPose 缺少有效的 moving 标记")
    if not hand["moving"]:
        return joints
    poses = frame.get("hTo")
    if not isinstance(poses, list) or len(poses) != 2 or not isinstance(poses[hand_index], dict):
        raise ValueError("ContactPose 移动手缺少该帧的 hTo 变换")
    pose = poses[hand_index]
    translation = _numeric_array(pose.get("translation"), (3,), "hTo 平移")
    quaternion = _numeric_array(pose.get("rotation"), (4,), "hTo 四元数")
    norm = float(np.linalg.norm(quaternion))
    if not np.isfinite(norm) or norm < np.finfo(float).eps:
        raise ValueError("ContactPose hTo 四元数无效")
    w, x, y, z = quaternion / norm
    rotation = np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])
    return (joints-translation) @ rotation


def load_contactpose(path, *, side=None, frame_index=0):
    """Load one grasp pose from ``annotations.json`` or its direct directory.

    ``side`` is ``left`` or ``right``. Omitting it selects the unique valid hand
    and rejects bimanual grasps. ``frame_index`` is the original zero-based RGB-D
    frame index; the returned one-frame sequence retains its positive frame ID.
    This is a joint-only pose: ``pressure_valid`` is false and
    ``vertices_available`` is false, regardless of any contact-map fields.
    """
    if side is not None and side not in ("left", "right"):
        raise ValueError("ContactPose side 须为 left 或 right")
    if isinstance(frame_index, bool) or not isinstance(frame_index, Integral) or frame_index < 0:
        raise ValueError("ContactPose frame_index 须为非负整数")
    path = Path(path)
    if path.is_dir():
        path = path / "annotations.json"
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("ContactPose 注释文件过大，请选择单个抓握的 annotations.json")
    try:
        with path.open(encoding="utf-8-sig") as stream:
            annotation = json.load(stream)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 ContactPose JSON 注释") from exc
    if not isinstance(annotation, dict):
        raise ValueError("ContactPose 注释须为 JSON 对象")
    hands = annotation.get("hands")
    frames = annotation.get("frames")
    if not isinstance(hands, list) or len(hands) != 2 or any(
            not isinstance(hand, dict) or not isinstance(hand.get("valid"), bool) for hand in hands):
        raise ValueError("ContactPose hands 须包含 [left, right] 及有效性标记")
    if not isinstance(frames, list) or not frames or frame_index >= len(frames):
        raise ValueError("ContactPose 帧编号超出 annotations.json 范围")
    frame = frames[frame_index]
    if not isinstance(frame, dict):
        raise ValueError("ContactPose 帧注释无效")
    valid = [index for index, hand in enumerate(hands) if hand["valid"]]
    if side is None:
        if not valid:
            raise ValueError("ContactPose 没有有效手部注释")
        if len(valid) != 1:
            raise ContactPoseHandSelectionRequired("ContactPose 包含双手，请明确指定 side='left' 或 side='right'")
        hand_index = valid[0]
        side = ("left", "right")[hand_index]
    else:
        hand_index = ("left", "right").index(side)
        if hand_index not in valid:
            raise ValueError(f"ContactPose 没有有效的 {side} 手部注释")
    joints = _object_joints(hands[hand_index], frame, hand_index)
    # JSON numbers may fit float64 yet overflow the ReplaySequence float32 dtype.
    if np.any(np.abs(joints) > np.finfo(np.float32).max):
        raise ValueError("ContactPose 关节坐标超出可导入范围")
    identity = " ".join(str(annotation.get(key, "unknown")) for key in
                        ("participant", "instruction", "object"))
    return ReplaySequence(
        name=f"ContactPose {identity} {side} frame{frame_index:03d}",
        frames=np.array([frame_index+1], dtype=np.int64),
        vertices=np.zeros((1, 778, 3), dtype=np.float32),
        joints=joints.astype(np.float32)[None],
        force=np.zeros((1, 105, 185), dtype=np.float32),
        hand_valid=np.ones(1, dtype=bool),
        pressure_valid=np.zeros(1, dtype=bool),
        hand_side=side,
        source_dataset="ContactPose",
        vertices_available=False,
    )
