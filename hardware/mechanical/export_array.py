"""Export nominal face centres from the same parameters as the P1 CAD."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop_app.array_geometry import grid_geometry


def geometry_from_parameters(parameters):
    return grid_geometry(parameters['array_rows'], parameters['array_cols'], parameters['pitch_mm'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', type=Path, default=Path(__file__).with_name('parameters.json'))
    parser.add_argument('--output', type=Path, default=ROOT/'hardware/arrays/p1_flat_4x4.json')
    args = parser.parse_args()
    p = json.loads(args.parameters.read_text(encoding='utf-8-sig'))
    g = geometry_from_parameters(p)
    document = g.document()
    document['cad_frame'] = dict(translation_mm=[0, 0, p['base_thickness_mm']+p['pcb_standoff_mm']
                                               +p['pcb_thickness_assumed_mm']+p['datum_to_pcb_front_mm']],
                                 rotation='identity', source=str(args.parameters.name))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(args.output, g.identity)


if __name__ == '__main__':
    main()
