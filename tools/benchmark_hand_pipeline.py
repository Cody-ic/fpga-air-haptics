"""Repeat the offline quantization comparison. Never connects to hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_app.egopressure import load_sequence, sample_path, counts_to_kpa
from desktop_app.model import ArraySpec
from desktop_app.pressure import compile_pressure


def benchmark(path, indices):
    sequence = load_sequence(path)
    rows = []
    array = ArraySpec(8, 8, 10300)
    for index in indices:
        if not 0 <= index < len(sequence.frames) or not sequence.pressure_valid[index]:
            raise ValueError('帧索引无效或没有压力测量：'+str(index))
        row = dict(index=index, frame_id=int(sequence.frames[index]))
        for name, sweeps in (('baseline', 0), ('refined', 2)):
            started = time.perf_counter()
            result = compile_pressure(counts_to_kpa(sequence.force[index]), array,
                                      z_mm=50, span_mm=40, starts=4, maxiter=300,
                                      refinement_sweeps=sweeps)
            row[name] = dict(seconds=time.perf_counter()-started,
                             metrics=result.metrics, phase_table=result.config.phase_table)
        if row['refined']['metrics']['loss'] > row['baseline']['metrics']['loss']+1e-12:
            raise AssertionError('Discrete refinement increased its own objective')
        rows.append(row)
    source = Path(path)
    return dict(source=source.name,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None,
                hardware_connected=False, physical_improvement_claimed=False,
                array=dict(rows=8, cols=8, pitch_um=10300), z_mm=50, span_mm=40,
                starts=4, maxiter=300, refinement_sweeps=2, rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=sample_path())
    parser.add_argument('--indices', default='69,134,199,264,322,324', help='Zero-based frame indices')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    indices = [int(value) for value in args.indices.split(',')]
    if not indices or len(indices) > 24 or len(set(indices)) != len(indices):
        parser.error('Specify 1..24 unique indices')
    document = benchmark(args.input, indices)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    reductions = [(r['baseline']['metrics']['loss']-r['refined']['metrics']['loss']) /
                  r['baseline']['metrics']['loss']*100 for r in document['rows']]
    print(f'{len(indices)} frames, objective reduction {min(reductions):.4f}%..{max(reductions):.4f}%')
    print(args.output)


if __name__ == '__main__':
    main()
