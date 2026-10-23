"""Compare carrier amplitudes at identical recorded receiver coordinates."""
import csv
from dataclasses import asdict, dataclass
import json
import math

import numpy as np

from .acoustics import pressure_at
from .array_geometry import Geometry
from .model import ArraySpec, Config


@dataclass(frozen=True)
class ScanModel:
    diameter_mm: float = 10.
    phase_direction: int = 1
    model: str = "piston-free-field-1"

    def document(self):
        if (self.model != "piston-free-field-1" or type(self.diameter_mm) not in (int, float)
                or not math.isfinite(self.diameter_mm)
                or not 0 <= self.diameter_mm <= 15 or type(self.phase_direction) is not int
                or self.phase_direction not in (-1, 1)):
            raise ValueError("测绘预测模型参数无效")
        return asdict(self)

    @classmethod
    def parse(cls, data):
        if type(data) is not dict or set(data) != {"diameter_mm", "phase_direction", "model"}:
            raise ValueError("测绘预测模型参数缺失，不能猜测历史模型")
        result = cls(**data)
        result.document()
        return result


@dataclass(frozen=True)
class Comparison:
    context: dict
    model: dict
    model_source: str
    reference: tuple
    positions: np.ndarray
    counts: np.ndarray
    measured_mv: np.ndarray
    minimum_mv: np.ndarray
    maximum_mv: np.ndarray
    predicted: np.ndarray
    measured_ratio: np.ndarray
    predicted_ratio: np.ndarray
    horizontal: np.ndarray
    field_ratio: np.ndarray
    direction: str
    section_mm: float
    profile_ratio: np.ndarray
    profile_indices: np.ndarray
    rmse: float | None

    def save_csv(self, path):
        metadata = dict(context=self.context, prediction_model=self.model,
            model_source=self.model_source, reference_mm=self.reference,
            normalization="BOTH_DIVIDED_BY_VALUE_AT_SAME_REFERENCE_POSITION",
            section_direction=self.direction, section_mm=self.section_mm,
            pressure_unit="RELATIVE_MODEL_AMPLITUDE_NOT_PA",
            measurement_unit="ADC_40KHZ_PEAK_MV")
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["schema", "source", "point", "x_mm",
                "y_mm", "z_mm", "windows", "measured_peak40_mv", "minimum_mv", "maximum_mv",
                "predicted_relative_amplitude", "measured_ratio", "predicted_ratio",
                "difference", "is_reference", "metadata_json"])
            writer.writeheader()
            for i, position in enumerate(self.positions):
                writer.writerow(dict(schema="touchsee-field-comparison-1",
                    source="DEMO_SYNTHETIC" if self.context["simulated"] else "ADC_UNCALIBRATED",
                    point=i+1, x_mm=position[0], y_mm=position[1], z_mm=position[2],
                    windows=int(self.counts[i]), measured_peak40_mv=self.measured_mv[i],
                    minimum_mv=self.minimum_mv[i], maximum_mv=self.maximum_mv[i],
                    predicted_relative_amplitude=self.predicted[i], measured_ratio=self.measured_ratio[i],
                    predicted_ratio=self.predicted_ratio[i],
                    difference=self.measured_ratio[i]-self.predicted_ratio[i],
                    is_reference=int(tuple(position) == self.reference),
                    metadata_json=json.dumps(metadata, separators=(",", ":")) if i == 0 else ""))


def compare_scan(context, points, model, z_mm, reference=None, direction="x", section_mm=0.,
                 model_source="RECORDED_AT_CAPTURE"):
    """One shared reference removes unknown gain, not phase/geometry errors.

    The reference is intentionally excluded from the deviation metric because
    both curves are forced to one there. No measured spatial interpolation.
    """
    model = ScanModel.parse(model).document()
    if (direction not in ("x", "y") or not math.isfinite(z_mm) or not 10 <= z_mm <= 400
            or not math.isfinite(section_mm) or abs(section_mm) > 300):
        raise ValueError("对比平面或剖面坐标无效")
    if context is None:
        raise ValueError("请先采集或导入测点")
    geometry = Geometry.from_document(context["geometry"])
    array = ArraySpec.from_geometry(geometry)
    config = Config.from_wire(context["config"])
    phases = tuple(context["phases"])
    groups = {}
    for point in points:
        if abs(point.position_mm[2]-z_mm) <= .001:
            # Only group windows from exactly the same position. z selects a
            # plane with a small tolerance; prediction still uses each real z.
            groups.setdefault(point.position_mm, []).append(point.amplitude_mv)
    if not groups:
        raise ValueError("此 z 平面没有测点")
    positions = np.asarray(list(groups), dtype=float)
    medians = np.asarray([np.median(v) for v in groups.values()])
    minima = np.asarray([min(v) for v in groups.values()])
    maxima = np.asarray([max(v) for v in groups.values()])
    counts = np.asarray([len(v) for v in groups.values()])

    def predict(coordinates):
        return abs(pressure_at(array, config, phases, coordinates, context["channel_mask"],
                               model["diameter_mm"], model["phase_direction"]))

    predicted = predict(positions)
    if reference is None:
        target = np.array((config.cx_um/1000, config.cy_um/1000, z_mm))
        index = int(np.argmin(np.linalg.norm(positions-target, axis=1)))
    else:
        matches = [i for i, position in enumerate(positions) if tuple(position) == tuple(reference)]
        if not matches:
            raise ValueError("参考测点不在当前平面")
        index = matches[0]
    reference = tuple(positions[index])
    if medians[index] < 1e-9:
        raise ValueError("参考测点的40 kHz分量为零，请更换参考点或检查接收信号")
    if predicted[index] <= max(1e-9, float(predicted.max())*1e-6):
        raise ValueError("模型在参考测点接近相消零点，不能稳定归一化，请更换参考点")
    measured_ratio = medians/medians[index]
    predicted_ratio = predicted/predicted[index]
    others = np.arange(len(positions)) != index
    rmse = float(np.sqrt(np.mean((measured_ratio[others]-predicted_ratio[others])**2))) if others.any() else None

    span = max(30., float(abs(positions[:, :2]).max())+10,
               abs(config.cx_um/1000)+10, abs(config.cy_um/1000)+10, abs(section_mm)+10)
    horizontal = np.linspace(-span, span, 81)
    xx, yy = np.meshgrid(horizontal, horizontal)
    field = predict(np.stack((xx, yy, np.full_like(xx, z_mm)), axis=-1))/predicted[index]
    fixed = np.full_like(horizontal, section_mm)
    profile_positions = (np.stack((horizontal, fixed, np.full_like(horizontal, z_mm)), axis=-1)
                         if direction == "x" else
                         np.stack((fixed, horizontal, np.full_like(horizontal, z_mm)), axis=-1))
    profile = predict(profile_positions)/predicted[index]
    fixed_column = 1 if direction == "x" else 0
    profile_indices = np.where(abs(positions[:, fixed_column]-section_mm) <= .001)[0]
    return Comparison(context, model, model_source, reference, positions, counts, medians, minima,
        maxima, predicted, measured_ratio, predicted_ratio, horizontal, field, direction,
        section_mm, profile, profile_indices, rmse)
