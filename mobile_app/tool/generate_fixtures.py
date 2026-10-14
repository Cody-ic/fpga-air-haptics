"""Generate cross-language vectors from the existing Python reference."""
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop_app.demo import DemoDevice
from desktop_app.model import ArraySpec, Config, focus_phases, trajectory_sample
from desktop_app.protocol import encode


def main():
    array = ArraySpec(8, 8)
    configs = [Config(shape=shape) for shape in ('POINT', 'LINE_X', 'CIRCLE', 'SQUARE', 'TRIANGLE', 'ARROW')]
    configs += [Config(shape='CUSTOM', path_xy_um='-15123:-8123,12345:9843,20111:18000', path_closed=0),
                Config(shape='CUSTOM', scan_paths='0:0,10000:0,10000:10000,0:0|30000:0,40000:0')]
    vectors = []
    for config in configs:
        for seconds in (0, .231, 1.0, 1.999):
            focus, gate, stroke = trajectory_sample(config, seconds)
            vectors.append(dict(config=config.wire(), seconds=seconds, focus_mm=focus.tolist(),
                                scan_on=bool(gate), stroke_index=stroke,
                                phases=focus_phases(config, focus, array).tolist()))
    demo = DemoDevice(clock=lambda: 12.0, array=array)
    demo.boot = 'fixture01'
    hello = demo.handle(encode('CMD', 1, 'HELLO'))
    config = replace(configs[-1], cx_um=1000, cy_um=-1000)
    applied = demo.handle(encode('CMD', 2, 'CONFIG', **config.wire(), **array.wire()))
    data = dict(array=array.wire(), frames=[raw.decode('ascii') for raw in hello+applied],
                vectors=vectors, document=dict(schema='haptics-config-3', config=config.wire()))
    target = ROOT/'mobile_app/test/fixtures/python_reference.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Generated {len(vectors)} trajectory/phase vectors and {len(hello+applied)} frames')


if __name__ == '__main__':
    main()
