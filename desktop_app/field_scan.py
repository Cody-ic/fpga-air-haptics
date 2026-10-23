"""Manually positioned receiver scan: preserve raw windows and their context."""
import csv
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics

from .receiver import Capture
from .measurement_frame import validate_placement


@dataclass(frozen=True)
class ScanPoint:
    position_mm: tuple
    capture: Capture
    vref_mv: float
    divider: float
    placement: dict | None = None

    @property
    def amplitude_mv(self):
        return self.capture.analyze(self.vref_mv, self.divider)["peak40_mv"]


class ScanBook:
    """Never merge different boots/configurations, Demo and actual measurements."""
    def __init__(self):
        self.context = None
        self.points = []
        self.imported = False
        self.prediction_model = None

    @staticmethod
    def context_for(state):
        if (state.config.shape != "POINT" or state.config.carrier_hz != 40000 or state.config.mod_hz
                or state.config.level != 100 or not state.output or not state.channel_mask):
            raise ValueError("测绘需固定点、满等级、关闭调制并保持输出；本页不会自动启动设备")
        context = dict(boot=state.boot, revision=state.revision, simulated=state.simulated,
                    geometry=state.array.resolved_geometry().document(),
                    config=state.config.wire(), phases=list(state.phases),
                    channel_mask=state.channel_mask,
                    position_source="MANUAL_RECEIVER_POSITION", unit="ADC_40KHZ_PEAK_MV")
        # JSON uses lists; canonicalize live context too so imported records can
        # be compared to the same live geometry without tuple/list mismatches.
        return json.loads(json.dumps(context))

    def add_windows(self, state, captures, position_mm, vref_mv=None, divider=3.4, prediction_model=None, placement=None):
        context = self.context_for(state)
        if self.context is not None and context != self.context:
            raise ValueError("设备或输出配置已改变，请导出旧记录并新建测绘")
        if prediction_model is not None:
            from .field_compare import ScanModel
            prediction_model = ScanModel.parse(prediction_model).document()
            if self.prediction_model is not None and prediction_model != self.prediction_model:
                raise ValueError("测绘预测模型已改变，请新建测绘")
        position = tuple(position_mm)
        if (len(position) != 3 or any(not math.isfinite(v) for v in position)
                or any(abs(v) > 300 for v in position[:2]) or not 10 <= position[2] <= 400):
            raise ValueError("测点坐标无效：x/y ±300 mm，z 10～400 mm")
        if not captures or len(self.points)+len(captures) > 2000:
            raise ValueError("采样为空或测绘超过 2000 个窗口，请新建记录")
        if placement is not None:
            placement = validate_placement(placement, position)
        pending = []
        for capture in captures:
            if (capture.boot != state.boot or capture.revision != state.revision
                    or capture.simulated != state.simulated or not capture.tx_running
                    or capture.uptime_ms < state.uptime_ms):
                raise ValueError("采样与请求时设备输出不一致，未记录该测点")
            result = capture.analyze(vref_mv, divider)
            if result["adc_rail"]:
                raise ValueError("ADC 已触及量程边界，不能作为有效测点")
            scale = capture.full_scale_mv if vref_mv is None else vref_mv
            pending.append(ScanPoint(position, capture, scale, divider, placement))
        self.context = context
        if prediction_model is not None:
            self.prediction_model = prediction_model
        self.points.extend(pending)

    def plane_points(self, z_mm):
        groups = {}
        for point in self.points:
            if abs(point.position_mm[2]-z_mm) > .001:
                continue
            groups.setdefault(point.position_mm[:2], []).append(point.amplitude_mv)
        return [(x, y, statistics.median(values), len(values))
                for (x, y), values in groups.items()]

    def save_csv(self, path):
        if not self.points:
            raise ValueError("尚无测点")
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[
                "schema", "source", "x_mm", "y_mm", "z_mm", "adc_peak40_mv",
                "vref_mv", "divider", "context_json", "capture_json", "prediction_model_json", "placement_json",
                "table_x_mm", "table_y_mm", "table_z_mm"])
            writer.writeheader()
            for index, point in enumerate(self.points):
                writer.writerow(dict(schema="touchsee-field-scan-2",
                    source="DEMO_SYNTHETIC" if point.capture.simulated else "ADC_UNCALIBRATED",
                    x_mm=point.position_mm[0], y_mm=point.position_mm[1], z_mm=point.position_mm[2],
                    adc_peak40_mv=point.amplitude_mv, vref_mv=point.vref_mv, divider=point.divider,
                    context_json=json.dumps(self.context, separators=(",", ":")) if index == 0 else "",
                    capture_json=json.dumps(asdict(point.capture), separators=(",", ":")),
                    placement_json=json.dumps(point.placement, separators=(",", ":")) if point.placement else "",
                    table_x_mm=point.placement["receiver_table_mm"][0] if point.placement else "",
                    table_y_mm=point.placement["receiver_table_mm"][1] if point.placement else "",
                    table_z_mm=point.placement["receiver_table_mm"][2] if point.placement else "",
                    prediction_model_json=json.dumps(self.prediction_model, separators=(",", ":")) if index == 0 else ""))

    @classmethod
    def load_csv(cls, path):
        if Path(path).stat().st_size > 8_000_000:
            raise ValueError("测绘文件超过 8 MB")
        from .array_geometry import Geometry
        from .model import Config
        book = cls()
        with open(path, encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["schema"] not in ("touchsee-field-scan-1", "touchsee-field-scan-2") or len(book.points) >= 2000:
                    raise ValueError("测绘格式或数据量无效")
                context = json.loads(row["context_json"]) if row["context_json"] else book.context
                if row.get("prediction_model_json"):
                    from .field_compare import ScanModel
                    model = json.loads(row["prediction_model_json"])
                    model = ScanModel.parse(model).document() if model is not None else None
                    if book.context is not None and model != book.prediction_model:
                        raise ValueError("文件混入不同预测模型")
                    book.prediction_model = model
                if context is None:
                    raise ValueError("测绘缺少首行设备上下文")
                geometry = Geometry.from_document(context["geometry"])
                config = Config.from_wire(context["config"])
                phases = context["phases"]
                if (context["unit"] != "ADC_40KHZ_PEAK_MV"
                        or context["position_source"] != "MANUAL_RECEIVER_POSITION"
                        or type(context["simulated"]) is not bool
                        or type(context["revision"]) is not int or context["revision"] < 0
                        or not context["boot"] or len(phases) != len(geometry.elements)
                        or any(type(v) is not int or not 0 <= v < config.phase_steps for v in phases)
                        or type(context["channel_mask"]) is not int
                        or not 0 < context["channel_mask"] < (1 << len(phases))
                        or config.shape != "POINT" or config.carrier_hz != 40000 or config.mod_hz or config.level != 100):
                    raise ValueError("测绘设备上下文无效")
                if book.context is not None and context != book.context:
                    raise ValueError("文件混入不同设备或输出配置")
                data = json.loads(row["capture_json"])
                if any(type(data[k]) is not bool for k in ("simulated", "tx_running")):
                    raise ValueError("采样来源无效")
                fields = {k: str(data[k]) for k in ("boot", "uptime_ms", "pin", "fs_hz", "bits")}
                fields.update(rev=str(data["revision"]), simulated=str(int(data["simulated"])),
                              tx_running=str(int(data["tx_running"])), n=str(len(data["raw"])),
                              raw=",".join(map(str, data["raw"])))
                if data.get("probe") is not None:
                    probe = data["probe"]
                    if type(probe["valid"]) is not bool:
                        raise ValueError("ADC 对照来源无效")
                    fields.update(probe_ok=str(int(probe["valid"])), direct_n=str(probe["count"]),
                                  direct_min=str(probe["minimum"]), direct_max=str(probe["maximum"]),
                                  direct_mean=str(probe["mean"]), vrefint_raw=str(probe["vrefint"]))
                fields["full_scale_mv"] = str(data.get("full_scale_mv", 3300))
                capture = Capture.parse(fields)
                expected = "DEMO_SYNTHETIC" if capture.simulated else "ADC_UNCALIBRATED"
                if (row["source"] != expected or capture.simulated != context["simulated"]
                        or capture.boot != context["boot"] or capture.revision != context["revision"]
                        or not capture.tx_running):
                    raise ValueError("采样与文件声明不一致")
                position = tuple(float(row[k]) for k in ("x_mm", "y_mm", "z_mm"))
                if (any(not math.isfinite(v) for v in position)
                        or any(abs(v) > 300 for v in position[:2]) or not 10 <= position[2] <= 400):
                    raise ValueError("测点坐标无效")
                placement = json.loads(row["placement_json"]) if row.get("placement_json") else None
                if placement is not None:
                    placement = validate_placement(placement, position)
                    for key, value in zip(("table_x_mm", "table_y_mm", "table_z_mm"), placement["receiver_table_mm"]):
                        if not math.isclose(float(row[key]), value, abs_tol=1e-6):
                            raise ValueError("文件底面坐标与定位记录不一致")
                point = ScanPoint(position, capture, float(row["vref_mv"]), float(row["divider"]), placement)
                analysis = capture.analyze(point.vref_mv, point.divider)
                if analysis["adc_rail"] or not math.isclose(point.amplitude_mv, float(row["adc_peak40_mv"]), rel_tol=1e-6, abs_tol=1e-6):
                    raise ValueError("采样削顶或分析值与原码不一致")
                book.context = context
                book.points.append(point)
        if not book.points:
            raise ValueError("文件没有测点")
        book.imported = True
        return book
