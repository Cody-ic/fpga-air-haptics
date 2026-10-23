"""Physical P5 tabletop frame; firmware/channel coordinates stay unchanged."""
from dataclasses import asdict, dataclass
import math


def point3(values):
    point = tuple(float(v) for v in values)
    if len(point) != 3 or not all(math.isfinite(v) for v in point):
        raise ValueError("位置须为三个有限数值，单位 mm")
    return point


@dataclass(frozen=True)
class TableFrame:
    # Nominal assembled dimensions, not measured/calibrated positions.
    center_x_mm: float = 132.
    center_y_mm: float = 94.
    face_height_mm: float = 124.7
    receiver_height_mm: float = 24.7
    tray_offset_x_mm: float = 33.273
    tray_offset_y_mm: float = 20.128

    def __post_init__(self):
        if not all(type(v) in (int, float) and math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("坐标基准必须是有限数值")
        if not 0 <= self.receiver_height_mm < self.face_height_mm <= 1000:
            raise ValueError("发射面高度须高于接收面高度")

    def to_array(self, table_position):
        x, y, z = point3(table_position)
        # Overhead fixture rotates the array 180 degrees about its X axis.
        return x-self.center_x_mm, self.center_y_mm-y, self.face_height_mm-z

    def to_table(self, array_position):
        x, y, z = point3(array_position)
        return x+self.center_x_mm, self.center_y_mm-y, self.face_height_mm-z

    def receiver_from_tray(self, tray_position):
        x, y, z = point3(tray_position)
        # Input Z is always the acoustic face height, not the tray bottom.
        return x+self.tray_offset_x_mm, y+self.tray_offset_y_mm, z

    def document(self):
        return dict(schema="touchsee-table-frame-1", fixture="P5_RISER_30MM",
                    origin="LEFT_RISER_FRONT_LEFT_TABLE_CORNER", nominal=True, **asdict(self))

    @classmethod
    def parse(cls, data):
        if (type(data) is not dict or data.get("schema") != "touchsee-table-frame-1"
                or data.get("fixture") != "P5_RISER_30MM"
                or data.get("origin") != "LEFT_RISER_FRONT_LEFT_TABLE_CORNER"
                or data.get("nominal") is not True):
            raise ValueError("不支持的底面坐标定义")
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


def placement_record(frame, input_position, reference="RECEIVER_R"):
    original = point3(input_position)
    if reference not in ("RECEIVER_R", "TRAY_A"):
        raise ValueError("未知的接收器定位点")
    receiver = frame.receiver_from_tray(original) if reference == "TRAY_A" else original
    return dict(frame=frame.document(), reference=reference, input_mm=list(original),
                receiver_table_mm=list(receiver))


def validate_placement(record, array_position):
    frame = TableFrame.parse(record["frame"])
    expected = placement_record(frame, record["input_mm"], record["reference"])
    if (point3(record["receiver_table_mm"]) != tuple(expected["receiver_table_mm"])
            or any(abs(a-b) > 1e-6 for a, b in zip(frame.to_array(expected["receiver_table_mm"]),
                                                  point3(array_position)))):
        raise ValueError("底面坐标与阵列坐标不一致")
    return expected
