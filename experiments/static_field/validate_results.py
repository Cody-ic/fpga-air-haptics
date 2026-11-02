"""Evaluate saved phases on a finer grid and under the OTHER propagation model."""
import argparse
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from experiments.static_field.reference import (
    circle_points, metrics, plane_points, propagator, target_distance,
)


def validate(directory):
    directory = Path(directory)
    document = json.loads((directory/'results.json').read_text(encoding='utf-8'))
    _, points = plane_points(45, .25, 50)
    checks = []
    with threadpool_limits(limits=1):
        for row in document['results']:
            emitters = np.asarray(row['source_coordinates_mm'])
            targets = np.asarray(row['target_coordinates_mm'])
            weights = np.exp(1j*np.asarray(row['phase_codes_64'])*2*np.pi/64)
            ring = row['case'] == 'ring'
            own_radius = 0 if row['model']=='isotropic' else 5
            for radius in [own_radius, 5-own_radius]:
                def h(p):
                    return propagator(p, emitters, piston_radius_mm=radius)
                centre_h = h([[0,0,50]])
                reference = np.abs(centre_h).sum()
                values = np.concatenate([np.abs(h(chunk) @ weights)/reference
                                         for chunk in np.array_split(points, 64)])
                sample_targets = circle_points(1440, 20, 50, np.pi/1440) if ring else targets
                measured = metrics(np.abs(h(sample_targets)@weights)/reference, values,
                                   target_distance(points, targets, ring) >= 6,
                                   np.abs((centre_h@weights)[0])/reference)
                checks.append(dict(case=row['case'], design_model=row['model'],
                                   evaluation_model='isotropic' if radius==0 else 'piston_assumed_10mm',
                                   same_model=radius==own_radius, metrics=measured))
    (directory/'validation.json').write_text(json.dumps(dict(
        simulated=True, grid_step_mm=.25, ring_samples=1440,
        note='Cross-model rows reuse the same phase codes WITHOUT re-optimisation',
        checks=checks), indent=2), encoding='utf-8')
    same = [r for r in checks if r['same_model']]
    delta = max(abs(r['metrics']['off_target_peak']-old['metrics']['quantized_64']['off_target_peak'])
                for r, old in zip(same, document['results']))
    print(f'Max off-target peak change, 0.5 -> 0.25 mm grid: {delta:.6f}')
    for row in checks:
        if row['case']=='ring':
            print(json.dumps(row))
    if delta > .02:
        raise RuntimeError('Grid refinement changed peak by > 0.02 of reference; inspect before reporting')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    validate(parser.parse_args().directory)
