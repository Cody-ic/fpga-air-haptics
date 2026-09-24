"""Protocol configuration and a deliberately simplified relative acoustic model.

Coordinates: mm in computation, integer um on the wire. Channel order is
row-major, +x along columns, +y along rows, emitters at z=0 facing +z.
Phasor convention: p = sum(exp(j*(k*r + phase))/r). Consequently the
transmit phase at a target is -k*r modulo 2*pi (not a positive delay).
No calibrated pressure, element directivity, reflections, or haptic threshold.
"""

from dataclasses import asdict, dataclass
import math
import re

import numpy as np

SHAPES = {"POINT": "固定点", "LINE_X": "水平线", "LINE_Y": "竖直线",
          "CIRCLE": "圆", "TRIANGLE": "三角形", "ARROW": "箭头", "CUSTOM": "自定义图形"}
ARRAY_PRESETS = {"4 × 4 · 16 路": (4, 4), "6 × 6 · 36 路": (6, 6),
                 "8 × 8 · 64 路": (8, 8), "12 × 12 · 144 路": (12, 12),
                 "16 × 16 · 256 路": (16, 16), "自定义行列": None}
MAX_PATH_POINTS = 64
MODES = ("LOCAL", "REMOTE")
STATES = ("IDLE", "RUNNING", "PAUSED", "FAULT")


@dataclass(frozen=True)
class ArraySpec:
    """Installed geometry declared by firmware, independent of drawing coordinates."""

    rows: int = 4
    cols: int = 4
    pitch_um: int = 10000
    mapping: str = "ROW_MAJOR_XY"

    @property
    def count(self):
        return self.rows * self.cols

    def validate(self):
        if (type(self.rows) is not int or type(self.cols) is not int
                or not 1 <= self.rows <= 16 or not 1 <= self.cols <= 16
                or type(self.pitch_um) is not int or not 1000 <= self.pitch_um <= 30000
                or self.mapping != "ROW_MAJOR_XY"):
            raise ValueError("不支持的实际阵列尺寸、间距或通道映射")
        return self

    def wire(self):
        return dict(hw_rows=self.rows, hw_cols=self.cols, hw_pitch_um=self.pitch_um, mapping=self.mapping)

    @classmethod
    def from_wire(cls, fields):
        return cls(int(fields["hw_rows"]), int(fields["hw_cols"]),
                   int(fields["hw_pitch_um"]), fields["mapping"]).validate()


@dataclass(frozen=True)
class Workspace:
    """Firmware-declared command bounds, not a guarantee of tactile performance."""

    x_min_um: int = -100000
    x_max_um: int = 100000
    y_min_um: int = -100000
    y_max_um: int = 100000
    z_min_um: int = 20000
    z_max_um: int = 300000

    def validate(self):
        if any(type(value) is not int for value in asdict(self).values()):
            raise ValueError("工作空间边界须为整数微米")
        if not (-400000 <= self.x_min_um < self.x_max_um <= 400000
                and -400000 <= self.y_min_um < self.y_max_um <= 400000
                and 20000 <= self.z_min_um < self.z_max_um <= 300000):
            raise ValueError("工作空间范围无效")
        return self

    def wire(self):
        return asdict(self.validate())

    @classmethod
    def from_wire(cls, fields):
        return cls(**{name: int(fields[name]) for name in cls.__dataclass_fields__}).validate()

    def incompatibility(self, config):
        low, high = trajectory_bounds(config)
        minimum = np.array([self.x_min_um, self.y_min_um, self.z_min_um])
        maximum = np.array([self.x_max_um, self.y_max_um, self.z_max_um])
        if np.any(low < minimum) or np.any(high > maximum):
            return "图形的实际坐标超出设备声明的工作空间，请调整尺寸、位置或高度"
        return ""


@dataclass(frozen=True)
class Config:
    carrier_hz: int = 40000
    phase_steps: int = 64
    cx_um: int = 0
    cy_um: int = 0
    z_um: int = 150000
    radius_um: int = 20000
    repeat_millihz: int = 500
    mod_hz: int = 200
    level: int = 30
    shape: str = "CIRCLE"
    path_xy_um: str = "NONE"
    path_closed: int = 1

    def validate(self):
        limits = {"carrier_hz": (20000, 80000), "phase_steps": (8, 256),
                  "cx_um": (-100000, 100000), "cy_um": (-100000, 100000),
                  "z_um": (20000, 300000), "radius_um": (0, 80000),
                  "repeat_millihz": (10, 200000), "mod_hz": (0, 1000), "level": (0, 100),
                  "path_closed": (0, 1)}
        for name, (lo, hi) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{name} 必须是 {lo}～{hi} 的整数")
        if self.phase_steps not in (8, 16, 32, 64, 128, 256):
            raise ValueError("phase_steps 必须是 8、16、32、64、128 或 256")
        if self.shape not in SHAPES:
            raise ValueError("未知图形")
        points = self.points_um()
        if self.shape == "CUSTOM" and not points:
            raise ValueError("请先在画布上添加一个节点")
        return self

    def points_um(self):
        """Signed local x:y coordinate pairs, in integer micrometres."""
        if self.path_xy_um == "NONE":
            return ()
        if not isinstance(self.path_xy_um, str) or len(self.path_xy_um) > MAX_PATH_POINTS * 16:
            raise ValueError("自定义路径格式错误")
        tokens = self.path_xy_um.split(",")
        if not 1 <= len(tokens) <= MAX_PATH_POINTS:
            raise ValueError(f"最多支持 {MAX_PATH_POINTS} 个节点")
        if any(not re.fullmatch(r"-?[0-9]{1,6}:-?[0-9]{1,6}", token) for token in tokens):
            raise ValueError("路径应为整数微米坐标 x:y")
        points = tuple(tuple(map(int, token.split(":"))) for token in tokens)
        if any(abs(value) > 300000 for point in points for value in point):
            raise ValueError("节点距离图形中心不能超过 300 mm")
        if any(a == b for a, b in zip(points, points[1:])):
            raise ValueError("相邻路径节点不能重复")
        if self.path_closed and len(points) > 1 and points[0] == points[-1]:
            raise ValueError("闭合路径不必重复首节点")
        return points

    @classmethod
    def from_wire(cls, fields):
        values = {}
        for name in cls.__dataclass_fields__:
            if name not in fields:
                raise ValueError(f"缺少配置字段 {name}")
            value = fields[name]
            if name not in ("shape", "path_xy_um"):
                # JSON booleans/floats must not silently become valid integers.
                if not (type(value) is int or isinstance(value, str) and value.lstrip("-+").isdigit()):
                    raise ValueError(f"{name} 必须是整数")
                value = int(value)
            values[name] = value
        return cls(**values).validate()

    def wire(self):
        self.validate()
        return asdict(self)


def encode_points(points):
    points = tuple(points)
    if any(len(point) != 2 or any(type(value) is not int for value in point) for point in points):
        raise ValueError("节点坐标须为整数微米")
    return ",".join(f"{x}:{y}" for x, y in points) if points else "NONE"


def config_from_document(data):
    """Read coordinate files or migrate existing grid files without moving points."""
    if data.get("schema") == "haptics-config-2":
        return Config.from_wire(data["config"])
    if data.get("schema") != "haptics-config-1":
        raise ValueError("配置版本不支持")
    fields = dict(data["config"])
    rows, cols, pitch = (fields.pop(key) for key in ("rows", "cols", "pitch_um"))
    if (any(type(v) is not int for v in (rows, cols, pitch))
            or not 1 <= rows <= 65 or not 1 <= cols <= 65 or not 100 <= pitch <= 30000):
        raise ValueError("旧版图形的网格参数无效")
    nodes = fields.pop("path_nodes")
    points = []
    if nodes != "NONE":
        if not isinstance(nodes, str) or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", nodes):
            raise ValueError("旧版图形的节点编号无效")
        for node in map(int, nodes.split(",")):
            if not 0 <= node < rows * cols:
                raise ValueError("旧版图形的节点编号越界")
            r, c = divmod(node, cols)
            points.append((round((c - (cols-1)/2)*pitch), round((r - (rows-1)/2)*pitch)))
    fields["path_xy_um"] = encode_points(points)
    return Config.from_wire(fields)


def array_coordinates(config):
    if not isinstance(config, ArraySpec):
        raise TypeError("阵元坐标必须来自实际 ArraySpec")
    return _rectangular_coordinates(config)


def _rectangular_coordinates(config):
    config.validate()
    pitch = config.pitch_um / 1000
    x = (np.arange(config.cols) - (config.cols - 1) / 2) * pitch
    y = (np.arange(config.rows) - (config.rows - 1) / 2) * pitch
    xx, yy = np.meshgrid(x, y)
    return np.column_stack((xx.ravel(), yy.ravel(), np.zeros(config.count)))


def _polyline(points, fraction):
    points = np.asarray(points, dtype=float)
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    if not len(lengths) or lengths.sum() == 0:
        return points[0]
    distance = (fraction % 1) * lengths.sum()
    accum = 0.0
    for i, length in enumerate(lengths):
        if length > 0 and distance <= accum + length:
            return points[i] + (points[i + 1] - points[i]) * ((distance - accum) / length)
        accum += length
    return points[-1]


def trajectory_point(config, seconds):
    fraction = seconds * config.repeat_millihz / 1000 % 1
    r = config.radius_um / 1000
    if config.shape == "CUSTOM":
        points = config.points_um()
        if not points:
            raise ValueError("自定义路径尚未定义")
        points = np.asarray(points, dtype=float) / 1000
        if config.path_closed:
            points = np.vstack((points, points[0]))
        else:
            # Open strokes retrace their route; no unexplained jump or closing edge.
            points = np.vstack((points, points[-2::-1]))
        xy = _polyline(points, fraction)
        return np.array([config.cx_um / 1000 + xy[0], config.cy_um / 1000 + xy[1], config.z_um / 1000])
    if config.shape == "POINT":
        xy = np.array([0.0, 0.0])
    elif config.shape == "CIRCLE":
        xy = np.array([math.cos(2 * math.pi * fraction), math.sin(2 * math.pi * fraction)])
    elif config.shape in ("LINE_X", "LINE_Y"):
        position = 1 - 4 * abs(fraction - 0.5)
        xy = np.array([position, 0.0] if config.shape == "LINE_X" else [0.0, position])
    elif config.shape == "TRIANGLE":
        xy = _polyline([(0, 1), (-0.866, -0.5), (0.866, -0.5), (0, 1)], fraction)
    else:
        xy = _polyline([(-1, 0), (1, 0), (0.3, 0.7), (1, 0), (0.3, -0.7),
                        (1, 0), (-1, 0)], fraction)
    return np.array([config.cx_um / 1000 + r * xy[0],
                     config.cy_um / 1000 + r * xy[1], config.z_um / 1000])


def trajectory_path(config, samples=121):
    period = 1000 / config.repeat_millihz
    return np.array([trajectory_point(config, t) for t in np.linspace(0, period, samples)])


def trajectory_bounds(config):
    """Exact conservative envelope in um; do not miss extrema by sampling."""
    config.validate()
    if config.shape == "CUSTOM":
        xy = np.asarray(config.points_um())
        low, high = xy.min(axis=0), xy.max(axis=0)
    else:
        r = config.radius_um
        spans = {"POINT": (0, 0), "LINE_X": (r, 0), "LINE_Y": (0, r),
                 "CIRCLE": (r, r), "TRIANGLE": (.866*r, r), "ARROW": (r, .7*r)}
        dx, dy = spans[config.shape]
        low = np.array([-dx, -dy if config.shape != "TRIANGLE" else -.5*r])
        high = np.array([dx, dy])
    center = np.array([config.cx_um, config.cy_um])
    return np.r_[low + center, config.z_um], np.r_[high + center, config.z_um]


def focus_phases(config, focus_mm, array):
    """Quantized phase *advance* codes for the documented phasor convention."""
    r = np.linalg.norm(array_coordinates(array) - np.asarray(focus_mm), axis=1) / 1000
    cycles = np.remainder(-r * config.carrier_hz / 343.0, 1.0)
    return (np.floor(cycles * config.phase_steps + 0.5).astype(int) % config.phase_steps)


def field_slice(config, phases, focus_mm, *, array, enabled=True, resolution=71, plane="XY"):
    """Return normalized |p|, NOT Pa, SPL, tactile intensity or modulation envelope.

    The normalization uses ideal coherent pressure at the specified target for
    unit drive. Thus disabled/zero-level output remains zero, not re-normalized.
    'level' is a hypothetical linear source scale, not a voltage calibration.
    """
    phases = np.asarray(phases, dtype=float)
    if phases.shape != (array.count,) or not np.isfinite(phases).all():
        raise ValueError("相位表长度或数值无效")
    if np.any(phases < 0) or np.any(phases >= config.phase_steps):
        raise ValueError("相位值越界")
    coords = array_coordinates(array)
    focus = np.asarray(focus_mm, dtype=float)
    if focus.shape != (3,) or not np.isfinite(focus).all() or focus[2] <= 0:
        raise ValueError("焦点坐标无效")
    low, high = trajectory_bounds(config)
    span = max(45.0, max(array.rows, array.cols) * array.pitch_um / 2000 + 15,
               abs(focus[0]) + 25, abs(focus[1]) + 25,
               np.abs(np.r_[low[:2], high[:2]]).max() / 1000 + 15)
    horizontal = np.linspace(-span, span, resolution)
    vertical = (np.linspace(-span, span, resolution) if plane == "XY" else
                np.linspace(10, max(240, focus[2] + 50), resolution))
    xx, vv = np.meshgrid(horizontal, vertical)
    yy, zz = (vv, np.full_like(xx, focus[2])) if plane == "XY" else (np.full_like(xx, focus[1]), vv)
    pressure = np.zeros_like(xx, dtype=np.complex128)
    k = 2 * math.pi * config.carrier_hz / 343.0
    if enabled and config.level:
        for (x, y, _), phase in zip(coords, phases):
            distance = np.sqrt((xx - x) ** 2 + (yy - y) ** 2 + zz ** 2) / 1000
            pressure += np.exp(1j * (k * distance + phase * 2 * math.pi / config.phase_steps)) / distance
    reference = np.sum(1000 / np.linalg.norm(coords - focus, axis=1))
    values = np.abs(pressure) / reference * config.level / 100
    return horizontal, vertical, values
