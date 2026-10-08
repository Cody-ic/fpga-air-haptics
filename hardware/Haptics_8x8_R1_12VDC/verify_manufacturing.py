"""Read-only drill and SMD opening audit; shares verify_copper CLI flags.

Checks this release only. Does not modify CAD, create routes or certify a stencil process.
"""
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path

package = Path(__file__).resolve().parent
import verify_copper
# verify_copper supplies --dependency-path/--output before importing dependencies.
from audit_source import entities
from verify_release import read_project
from verify_copper import project_pads, copper
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Point

# Solder-mask rounded rectangles are decomposed aperture macros. This adapter
# handles their existing primitives; it does not invent or change any opening.
original_shape = verify_copper.command_shape
def fabrication_shape(command, cache):
    aperture = getattr(command, 'aperture', None)
    kind = type(aperture).__name__
    if type(command).__name__ == 'Line2' and kind == 'NoCircle2':
        return LineString(verify_copper.curve(command)).buffer(
            verify_copper.scalar(aperture.diameter)/2, cap_style='flat', quad_segs=128)
    if type(command).__name__ == 'Flash2' and kind == 'Macro2':
        key = ('macro', aperture.identifier)
        if key not in cache:
            shape = GeometryCollection()
            local = {}
            for child in aperture.command_buffer:
                part = fabrication_shape(child, local)
                shape = shape.union(part) if child.transform.polarity.value == 'D' else shape.difference(part)
            cache[key] = shape
        return affinity.translate(cache[key], *verify_copper.xy(command.flash_point))
    return original_shape(command, cache)
def openings(source):
    return copper(source, shape_fn=fabrication_shape)

docs = read_project(package / 'Haptics_8x8_R1_release.epro2')
pcb = next(d for d in docs if d['head']['docType'] == 'PCB')
components = entities(pcb, 'COMPONENT')
footprints = {d['head']['uuid']: entities(d, 'PAD') for d in docs if d['head']['docType'] == 'FOOTPRINT'}
pads = project_pads(docs, pcb)
by_id = {p['id']: p for p in pads}
holes, rings, disabled_paste = [], [], []
for cid, comp in components.items():
    for pid, pad in footprints[comp['Footprint']].items():
        point = by_id[cid + pid]
        hole = pad.get('hole')
        if hole and hole['width']:
            assert hole['holeType'] == 'ROUND'
            pa = math.radians(pad['padAngle'])
            # Apply the pad rotation before mirroring/rotating the component.
            # Several through holes are intentionally offset from pad centres.
            ox, oy = pad.get('padOffsetX', 0), pad.get('padOffsetY', 0)
            ox, oy = ox*math.cos(pa)-oy*math.sin(pa), ox*math.sin(pa)+oy*math.cos(pa)
            if comp['layerId'] == 2:
                oy = -oy
            ca = math.radians(comp['angle'])
            hx, hy = point['x']+(ox*math.cos(ca)-oy*math.sin(ca))*.0254, point['y']+(ox*math.sin(ca)+oy*math.cos(ca))*.0254
            holes.append({'ref': comp['Designator'] + '.' + pad['num'],
                          'x': hx, 'y': hy, 'diameter': hole['width'] * .0254,
                          'plated': pad['plated']})
            size = pad['defaultPad']
            rings.append({'ref': comp['Designator'] + '.' + pad['num'],
                          'minimum_nominal_ring_mm': (min(size['width'], size['height']) - hole['width']) * .0254 / 2})
        if not point['through']:
            expansion = pad.get('bottomPasteExpansion' if comp['layerId'] == 2 else 'topPasteExpansion')
            if expansion is not None and expansion < -50:
                disabled_paste.append([comp['Designator'], pad['num'], expansion])
for ident, via in entities(pcb, 'VIA').items():
    holes.append({'ref': 'via:' + ident, 'x': via['centerX'] * .0254, 'y': via['centerY'] * .0254,
                  'diameter': via['holeDiameter'] * .0254, 'plated': True})
    rings.append({'ref': 'via:' + ident,
                  'minimum_nominal_ring_mm': (via['viaDiameter'] - via['holeDiameter']) * .0254 / 2})
for ident, pad in entities(pcb, 'PAD').items():
    holes.append({'ref': 'mount:' + ident, 'x': pad['centerX'] * .0254, 'y': pad['centerY'] * .0254,
                  'diameter': pad['hole']['width'] * .0254, 'plated': pad['plated']})
drill_errors, missing_mask, missing_paste = [], [], []
with zipfile.ZipFile(package / 'Haptics_8x8_R1_release_Gerber.zip') as archive:
    for plated, filename in ((True, 'Drill_PTH_Through.DRL'), (False, 'Drill_NPTH_Through.DRL')):
        definitions, hits = {}, []
        active = None
        for line in archive.read(filename).decode().splitlines():
            tool = re.fullmatch(r'T(\d+)C([\d.]+)', line)
            select = re.fullmatch(r'T(\d+)', line)
            hit = re.fullmatch(r'X([-\d.]+)Y([-\d.]+)', line)
            if tool:
                definitions[int(tool[1])] = float(tool[2])
            elif select:
                active = int(select[1])
            elif hit:
                hits.append((float(hit[1]), float(hit[2]), definitions[active]))
        expected = [h for h in holes if h['plated'] == plated]
        assert len(expected) == len(hits)
        for hole in expected:
            match = [h for h in hits if math.dist((h[0],h[1]), (hole['x'],hole['y'])) < .00001]
            if len(match) != 1 or abs(match[0][2] - hole['diameter']) > .00001:
                drill_errors.append({'expected': hole, 'matched': match})
    smd = [p for p in pads if not p['through']]
    for layer, mask_file, paste_file in ((1,'Gerber_TopSolderMaskLayer.GTS',None),
                                       (2,'Gerber_BottomSolderMaskLayer.GBS','Gerber_BottomPasteMaskLayer.GBP')):
        mask = openings(archive.read(mask_file).decode())
        paste = openings(archive.read(paste_file).decode()) if paste_file else None
        for pad in smd:
            if pad['layers'] != [layer]:
                continue
            point = Point(pad['x'], pad['y'])
            if not mask.covers(point):
                missing_mask.append([pad['ref'], pad['pin']])
            if paste is None or not paste.covers(point):
                missing_paste.append([pad['ref'], pad['pin']])
result = {'checked_date': '2026-10-08',
          'package_sha256': {n: hashlib.sha256((package / n).read_bytes()).hexdigest() for n in ('Haptics_8x8_R1_release.epro2','Haptics_8x8_R1_release_Gerber.zip')},
          'boundary': 'Nominal round drill position/diameter and SMD opening centre coverage; no assembly, stencil thickness, paste volume or fabrication tolerance validation.',
          'holes_checked': len(holes), 'drill_position_size_errors': drill_errors,
          'minimum_nominal_ring': min(rings, key=lambda p: p['minimum_nominal_ring_mm']),
          'smd_pads_checked': len(smd), 'smd_without_mask_opening': missing_mask,
          'smd_without_paste_opening': missing_paste, 'footprint_paste_suppressed': disabled_paste}
if verify_copper.args.output:
    verify_copper.args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf8'))
summary = {k:(len(v) if isinstance(v,list) else v) for k,v in result.items()}
print(json.dumps(summary, ensure_ascii=False, indent=2))
assert len(holes) == 649 and len(smd) == 820
assert not drill_errors, 'Drill positions or diameters differ from the project'
assert not missing_mask, 'SMD pad has no solder-mask opening'
assert not missing_paste, 'SMD pad has no paste-mask opening'
assert not disabled_paste, 'Embedded footprint suppresses paste'
