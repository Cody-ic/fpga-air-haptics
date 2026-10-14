"""Nominal acoustic-face geometry shared by CAD, host and generated firmware.

Positions are integer nm and normals integer ppm on the wire. These are storage
units, not claims about mechanical accuracy. Channel IDs follow electrical wiring.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Geometry:
    rows: int
    cols: int
    pitch_um: int
    elements: tuple  # channel order: (x_nm, y_nm, z_nm, nx_ppm, ny_ppm, nz_ppm)
    sound_speed_mm_s: int = 343000

    def validate(self):
        if (type(self.rows) is not int or type(self.cols) is not int
                or not 1 <= self.rows <= 16 or not 1 <= self.cols <= 16
                or type(self.pitch_um) is not int or not 1000 <= self.pitch_um <= 30000
                or type(self.sound_speed_mm_s) is not int
                or not 300000 <= self.sound_speed_mm_s <= 380000
                or len(self.elements) != self.rows * self.cols):
            raise ValueError("阵列参数或通道数无效")
        positions = set()
        for e in self.elements:
            if (len(e) != 6 or any(type(v) is not int for v in e)
                    or any(abs(v) > 1000000000 for v in e[:3])
                    or abs(math.sqrt(sum(v*v for v in e[3:])) - 1000000) > 2):
                raise ValueError("阵元位置或单位朝向无效")
            if e[:3] in positions:
                raise ValueError("两个通道不能占用同一阵元位置")
            positions.add(e[:3])
        return self

    @property
    def identity(self):
        canonical = [self.rows, self.cols, self.pitch_um, self.sound_speed_mm_s, self.elements]
        return hashlib.sha256(json.dumps(canonical, separators=(',', ':')).encode()).hexdigest()[:24]

    @property
    def positions_mm(self):
        return tuple(tuple(v/1000000 for v in e[:3]) for e in self.elements)

    @property
    def normals(self):
        return tuple(tuple(v/1000000 for v in e[3:]) for e in self.elements)

    def document(self):
        return dict(schema='haptics-array-1', geometry_id=self.identity,
                    rows=self.rows, cols=self.cols, pitch_um=self.pitch_um,
                    sound_speed_mm_s=self.sound_speed_mm_s,
                    origin='acoustic_face_center', units='mm', nominal=True,
                    elements=[dict(channel=i, position_mm=p, normal=n)
                              for i, (p, n) in enumerate(zip(self.positions_mm, self.normals))])

    def save(self, path):
        Path(path).write_text(json.dumps(self.document(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    @classmethod
    def load(cls, path):
        return cls.from_document(json.loads(Path(path).read_text(encoding='utf-8-sig')))

    @classmethod
    def from_document(cls, d):
        if not isinstance(d, dict) or d.get('schema') != 'haptics-array-1' or d.get('units') != 'mm':
            raise ValueError('不支持的阵列文件或坐标单位')
        rows = []
        for i, e in enumerate(d['elements']):
            if type(e['channel']) is not int or e['channel'] != i:
                raise ValueError('通道必须从 0 开始连续排列，不能按位置重排')
            p, n = e['position_mm'], e['normal']
            if len(p) != 3 or len(n) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p+n):
                raise ValueError('阵元需要有限的三维坐标和朝向')
            rows.append(tuple(round(v*1000000) for v in p+n))
        obj = cls(d['rows'], d['cols'], d['pitch_um'], tuple(rows), d.get('sound_speed_mm_s', 343000)).validate()
        if d.get('geometry_id', obj.identity) != obj.identity:
            raise ValueError('阵列文件内容与标识不一致')
        return obj

    def chunk(self, start, count):
        if type(start) is not int or type(count) is not int or not 1 <= count <= 16 or not 0 <= start < len(self.elements):
            raise ValueError('BAD_GEOMETRY_RANGE')
        data = self.elements[start:start+count]
        return dict(geometry_id=self.identity, start=start, count=len(data),
                    sound_speed_mm_s=self.sound_speed_mm_s,
                    elements='|'.join(','.join(map(str, e)) for e in data))


def grid_geometry(rows=4, cols=4, pitch_mm=11., radius_mm=None, sound_speed_m_s=343.):
    """Sphere vertex z=0, centre (0,0,R); grid spacing is XY projection."""
    if type(rows) is not int or type(cols) is not int or not 1 <= rows <= 16 or not 1 <= cols <= 16:
        raise ValueError('行列范围为 1–16')
    if not math.isfinite(pitch_mm) or not 1 <= pitch_mm <= 30:
        raise ValueError('间距范围为 1–30 mm')
    if radius_mm is not None and (not math.isfinite(radius_mm) or radius_mm <= 0):
        raise ValueError('球冠半径须为正数')
    elements = []
    for row in range(rows):
        for col in range(cols):
            x, y = (col-(cols-1)/2)*pitch_mm, (row-(rows-1)/2)*pitch_mm
            z, n = 0., (0., 0., 1.)
            if radius_mm is not None:
                if x*x+y*y >= radius_mm*radius_mm:
                    raise ValueError('球冠半径必须大于阵列的投影半径')
                height = math.sqrt(radius_mm*radius_mm-x*x-y*y)
                z, n = radius_mm-height, (-x/radius_mm, -y/radius_mm, height/radius_mm)
            elements.append(tuple(round(v*1000000) for v in (x, y, z, *n)))
    return Geometry(rows, cols, round(pitch_mm*1000), tuple(elements), round(sound_speed_m_s*1000)).validate()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='生成名义阵列坐标；不生成或验证打印件')
    parser.add_argument('--rows', type=int, default=4)
    parser.add_argument('--cols', type=int, default=4)
    parser.add_argument('--pitch-mm', type=float, default=11.)
    parser.add_argument('--radius-mm', type=float)
    parser.add_argument('--sound-speed-m-s', type=float, default=343.)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    g = grid_geometry(args.rows, args.cols, args.pitch_mm, args.radius_mm, args.sound_speed_m_s)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    g.save(args.output)
    print(args.output, g.identity)


if __name__ == '__main__':
    main()
