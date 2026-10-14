"""Read R5 front-side parts in the same XY frame as the mechanical array.

Resistor envelopes are conservative design assumptions, not vendor 3D models.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POSITIONS = ROOT.parent / 'Haptics_4x4_R5_12VDC/Positions.csv'


def top_resistors():
    with POSITIONS.open(encoding='utf-16', newline='') as stream:
        rows = list(csv.DictReader(stream, delimiter='\t'))
    parts = []
    for row in rows:
        if row['Layer'] == 'T' and row['Designator'].startswith('R'):
            assert row['Footprint'] == 'R0603', row
            assert float(row['Rotation']) % 180 == 0, row
            parts.append(dict(
                designator=row['Designator'],
                x_mm=float(row['Mid X'].removesuffix('mm'))-27.5,
                y_mm=float(row['Mid Y'].removesuffix('mm'))+27.5))
    assert {p['designator'] for p in parts} == {f'R{i}' for i in range(2, 17, 2)}
    return parts
