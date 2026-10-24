"""P5 add-ons, preserving every previously released part and receiver fixture."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Load the legacy layout under a distinct name; both modules are named layout.py.
import importlib.util
_spec = importlib.util.spec_from_file_location('p5_layout_source', ROOT / 'overhead_setup/layout.py')
OLD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OLD)
P = json.loads((HERE / 'parameters.json').read_text(encoding='utf-8'))
OUT = ROOT / 'dist' / P['revision']
SOURCE = OLD.OUT
ROTATE_Z = [0., 1., 0., -1., 0., 0., 0., 0., 1.]


def datums():
    old = OLD.datums()
    return dict(receiver_face_z_mm=old['receiver_face_z_mm'],
                film_bottom_z_mm=old['receiver_face_z_mm'],
                film_top_z_mm=old['receiver_face_z_mm'] + P['film_thickness_mm'],
                tx_face_z_mm=old['tx_face_z_mm'] + P['riser_height_mm'],
                receiver_gap_mm=old['acoustic_gap_mm'] + P['riser_height_mm'],
                film_gap_mm=old['acoustic_gap_mm'] + P['riser_height_mm'] - P['film_thickness_mm'])


def reference_item(name, xyz, rotation=OLD.IDENTITY, color='#abb6c0'):
    return OLD.item(name, xyz, rotation, color)


def placements(profile, target):
    assert profile in ('tx4', 'tx8') and target in ('receiver', 'film')
    source = OLD.placements(profile)
    split = next(i for i, e in enumerate(source) if e['name'] == '05_receiver_flat_tray')
    tx, rx = source[:split], source[split:]
    for e in tx:
        e['translation_mm'][2] += P['riser_height_mm']
    entries = tx
    for x in (OLD.P['body_placement_x_mm'][0] + 8, OLD.P['body_placement_x_mm'][1] - 8):
        entries.append(reference_item('08_fixed_riser_30', [x, 0, 0], ROTATE_Z, '#315c77'))
        top = P['riser_height_mm'] + P['source_bottom_rail_height_mm']
        entries.append(reference_item('09_riser_bridge', [x, 0, top], ROTATE_Z, '#dbad67'))
        for across in (-P['riser_bolt_offset_mm'], P['riser_bolt_offset_mm']):
            bx = x - across
            entries += [reference_item('REF_M4x25_screw', [bx, 0, top + 5.8], OLD.FLIP_X),
                        reference_item('REF_M4_washer', [bx, 0, top + 5]),
                        reference_item('REF_M4_plain_nut', [bx, 0, 23.0], ROTATE_Z)]
    if target == 'receiver':
        # Receiver PCB, ports, test pins, standard fasteners and base are unchanged.
        entries += rx
    else:
        z = datums()['film_bottom_z_mm']
        entries += [reference_item('10_film_lower_stand', [0, 0, z], OLD.FLIP_X, '#ac7945'),
                    reference_item('REF_film_4x5in_10um', [0, 0, z], color='#b5e4ee'),
                    reference_item('11_film_upper_ring', [0, 0, z + P['film_thickness_mm']], color='#d7ac70')]
        for x, y in P['film_holes_mm']:
            # Lower stand flips Y, but its hole pattern is symmetric about both axes.
            entries += [reference_item('REF_M3x16_screw', [x, y, z + P['film_thickness_mm'] + 4.5], OLD.FLIP_X),
                        reference_item('REF_M3_washer', [x, y, z + P['film_thickness_mm'] + 4]),
                        reference_item('REF_M3_washer', [x, y, z - 4.5]),
                        reference_item('REF_M3_plain_nut', [x, y, z - 6.9])]
    return entries


def verify_source():
    expected = json.loads((ROOT / 'P5_20261015/file_manifest.json').read_text(encoding='utf-8'))
    for folder in ('solidworks', 'step', 'stl', 'reference'):
        for path in (SOURCE / folder).iterdir():
            if path.name.startswith('~'):
                continue
            name = path.name if folder == 'solidworks' else path.stem + path.suffix.lower()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[folder + '/' + name], path
    assert OLD.P['revision'] == P['source_revision']
    assert OLD.P['body_plate_thickness_mm'] == 16.0
