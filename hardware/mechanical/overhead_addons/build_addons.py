"""Generate fully defined native add-ons. Existing P5 parts are copied unchanged."""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout
sys.path.insert(0, str(layout.ROOT))
import build_solidworks as sw
import assemble_solidworks as assembly
import pythoncom
import win32com.client as wc

P, OUT = layout.P, layout.OUT


def initialize():
    for filename in ('sldworks', 'swconst'):
        lib = pythoncom.LoadTypeLib(str(sw.SW_PATH / (filename + '.tlb')))
        a = lib.GetLibAttr()
        module = wc.gencache.EnsureModule(str(a[0]), a[1], a[3], a[4])
        if filename == 'sldworks':
            sw.M = module
    sw.APP = sw.typed('ISldWorks', wc.Dispatch('SldWorks.Application'))
    sw.OUT = OUT
    for folder in ('solidworks', 'step', 'stl', 'reference', 'preview'):
        (OUT / folder).mkdir(parents=True, exist_ok=True)


def riser(short=False):
    name = '12_riser_fit_coupon' if short else '08_fixed_riser_30'
    p = sw.Part(name, 'Test the unchanged old 16 mm bottom rail and M4 nut fit first' if short
                else '30 mm fixed lift; clamp old bottom rail without drilling; two required', quantity=1 if short else 2)
    half = 13.0 if short else P['riser_length_mm'] / 2
    w = P['riser_width_mm'] / 2
    z0, z1 = P['riser_nut_slot_z_mm']
    for low, high, band in ((0, z0, 0), (z0, z1, 1), (z1, P['riser_height_mm'], 2)):
        p.begin()
        if band == 1:
            a = P['riser_nut_slot_width_mm'] / 2
            b = P['riser_nut_slot_inner_mm']
            p.polygon([(-half,-w),(-a,-w),(-a,-b),(a,-b),(a,-w),(half,-w),
                       (half,w),(a,w),(a,b),(-a,b),(-a,w),(-half,w)])
        else:
            p.rect(-half, -w, half, w)
            for y in (-P['riser_bolt_offset_mm'], P['riser_bolt_offset_mm']):
                p.circle(0, y, P['m4_clearance_mm'] / 2)
        p.end('Riser_band_' + str(band + 1), low, high)
    p.begin()
    c = P['riser_channel_width_mm'] / 2
    for a, b in ((-w, -c), (c, w)):
        p.rect(-half, a, half, b)
        p.circle(0, -P['riser_bolt_offset_mm'] if a < 0 else P['riser_bolt_offset_mm'], P['m4_clearance_mm'] / 2)
    p.end('Side_retention_lips_below_old_rail_top', P['riser_height_mm'], P['riser_height_mm'] + P['riser_lip_height_mm'])
    return p.finish()


def bridge():
    p = sw.Part('09_riser_bridge', 'M4 bridge clamps the old bottom rail; two required, one spare recommended', quantity=2)
    p.begin()
    a = P['riser_clamp_length_mm'] / 2
    b = P['riser_width_mm'] / 2
    p.polygon(sw.chamfer_rectangle(-a, -b, a, b, 1))
    for y in (-P['riser_bolt_offset_mm'], P['riser_bolt_offset_mm']):
        p.circle(0, y, P['m4_clearance_mm'] / 2)
    p.end('Bridge_over_existing_8mm_rail', 0, P['riser_clamp_thickness_mm'])
    return p.finish()


def film_ring(name, lower):
    p = sw.Part(name, 'Four-foot independent film stand; print ring flat with feet upward' if lower
                else 'Removable upper film clamp; screws never pierce the 4x5 inch sheet')
    x, y = (v / 2 for v in P['film_frame_size_mm'])
    u, v = (a / 2 for a in P['film_window_size_mm'])
    p.begin()
    p.polygon(sw.chamfer_rectangle(-x, -y, x, y, 2))
    p.polygon(sw.chamfer_rectangle(-u, -v, u, v, 1))
    for a, b in P['film_holes_mm']:
        p.circle(a, b, P['m3_clearance_mm'] / 2)
    p.end('Flat_ring_8_clearance_holes_outside_film', 0, P['film_ring_thickness_mm'])
    if lower:
        p.begin()
        size = P['film_foot_size_mm']
        for sx in (-1, 1):
            for sy in (-1, 1):
                cx, cy = sx * (x - size / 2), sy * (y - size / 2)
                # Match the clipped outer ring corners; no disconnected overhangs.
                p.polygon(sw.chamfer_rectangle(cx-size/2, cy-size/2, cx+size/2, cy+size/2, 2))
        p.end('Four_fixed_feet_film_plane_matches_receiver_face', P['film_ring_thickness_mm'], layout.datums()['film_bottom_z_mm'])
    return p.finish()


def screw4():
    p = sw.Part('REF_M4x25_screw', 'Purchased M4x25 socket head screw; reference without thread', False)
    p.begin(); p.circle(0, 0, 3.5); p.end('Socket_head', -4, 0)
    p.begin(); p.circle(0, 0, 2); p.end('Nominal_shank', 0, 25)
    return p.finish()


def nut4():
    p = sw.Part('REF_M4_plain_nut', 'Purchased M4 DIN934 nut, 7 mm across flats, 3.2 mm high', False)
    r = 7 / math.sqrt(3)
    p.begin()
    # Flats parallel to slot walls. Local X is the old rail length after placement.
    p.polygon([(r*math.cos(math.radians(30+60*i)), r*math.sin(math.radians(30+60*i))) for i in range(6)])
    p.circle(0, 0, 2)
    p.end('Plain_M4_AF7_height3p2', 0, 3.2)
    return p.finish()


def film():
    p = sw.Part('REF_film_4x5in_10um', 'Unframed 101.6 x 127 x 0.01 mm sheet; material and prestress unspecified; do not print', False)
    x, y = (v / 2 for v in P['film_size_mm'])
    p.begin(); p.rect(-x, -y, x, y); p.end('Sheet_thickness_10um_reference', 0, P['film_thickness_mm'])
    return p.finish()


BUILDERS = dict(riser=lambda:riser(False), bridge=bridge,
                lower=lambda:film_ring('10_film_lower_stand', True),
                upper=lambda:film_ring('11_film_upper_ring', False),
                coupon=lambda:riser(True), m4screw=screw4, m4nut=nut4, film=film)


def copy_source():
    layout.verify_source()
    for folder in ('solidworks', 'step', 'stl', 'reference'):
        for p in (layout.SOURCE / folder).iterdir():
            if p.name.startswith('~') or p.suffix.lower() == '.sldasm':
                continue
            shutil.copy2(p, OUT / folder / p.name)
    for p in layout.SOURCE.glob('*.json'):
        if p.stem.startswith(('0', 'REF_')):
            shutil.copy2(p, OUT / p.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', choices=BUILDERS)
    parser.add_argument('--assemble', action='store_true')
    args = parser.parse_args()
    initialize()
    copy_source()
    prefs = [('IntegerValue', {211:0, 78:3}), ('DoubleValue', {2:.000005, 3:math.radians(2)}),
             ('Toggle', {69:True, 70:False, 71:True, 191:False})]
    saved = []
    try:
        for kind, values in prefs:
            for key, value in values.items():
                saved.append((kind, key, getattr(sw.APP, 'GetUserPreference'+kind)(key)))
                getattr(sw.APP, 'SetUserPreference'+kind)(key, value)
        if not args.assemble:
            for name, fn in BUILDERS.items():
                if not args.only or name in args.only:
                    print('BUILD ' + name, flush=True)
                    fn()
        else:
            records = {}
            for profile in ('tx4', 'tx8'):
                for target in ('receiver', 'film'):
                    name = ('Addons_4x4_' if profile == 'tx4' else 'Addons_8x8_') + target + '_100mm'
                    records[name] = layout.placements(profile, target)
                    assembly.make_assembly(name, records[name])
            (OUT / 'assemblies.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        for kind, key, value in saved:
            getattr(sw.APP, 'SetUserPreference'+kind)(key, value)
    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(OUT.glob('*.json'))
               if p.stem.startswith(('0', '1', 'REF_'))]
    (OUT / 'manifest.json').write_text(json.dumps(dict(revision=P['revision'], datums=layout.datums(), parts=records), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
