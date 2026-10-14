"""Read-only drill/opening/edge audit; shares verify_copper CLI flags.

Checks this release only. Does not modify CAD, create routes or certify assembly.
"""
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path

import verify_copper
# verify_copper supplies --dependency-path/--output before importing dependencies.
from audit_source import entities
from verify_release import read_project
from verify_copper import project_pads, project_holes, pad_outline, copper, TOL, LAYERS
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Point, box
from shapely.strtree import STRtree

PACKAGE = Path(__file__).resolve().parent
original_shape = verify_copper.command_shape


def fabrication_shape(command, cache):
    """Existing rounded mask macros, including their dark/clear primitives."""
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


def check_drills(source, expected):
    definitions, hits = {}, []
    active = None
    for line in source.splitlines():
        tool = re.fullmatch(r'T(\d+)C([\d.]+)', line)
        select = re.fullmatch(r'T(\d+)', line)
        hit = re.fullmatch(r'X([-\d.]+)Y([-\d.]+)', line)
        if tool:
            definitions[int(tool[1])] = float(tool[2])
        elif select:
            active = int(select[1])
        elif hit:
            hits.append((float(hit[1]), float(hit[2]), definitions[active]))
    errors = []
    if len(expected) != len(hits):
        errors.append({'expected_count': len(expected), 'actual_count': len(hits)})
    for hole in expected:
        match = [h for h in hits if math.dist(h[:2], (hole['x'], hole['y'])) < .00001]
        if len(match) != 1 or abs(match[0][2]-hole['diameter']) > .00001:
            errors.append({'expected': hole, 'matched': match})
    return errors


def opening_geometry(shape, pads, layer):
    islands = list(shape.geoms) if shape.geom_type == 'MultiPolygon' else [shape]
    tree = STRtree(islands)
    labels, bridges = {}, []
    for pad in pads:
        if layer not in pad['layers']:
            continue
        for idx in tree.query(Point(pad['x'], pad['y']), predicate='intersects'):
            labels.setdefault(int(idx), []).append(pad['ref']+'.'+pad['pin'])
    minimum, nearest = math.inf, None
    for i, island in enumerate(islands):
        if len(labels.get(i, [])) > 1:
            bridges.append(labels[i])
        for j in tree.query(island.buffer(.5), predicate='intersects'):
            if j <= i:
                continue
            gap = island.distance(islands[j])
            if gap < minimum:
                minimum, nearest = gap, [labels.get(i, []), labels.get(int(j), [])]
    return {'islands': len(islands), 'minimum_web_mm': minimum if nearest else None,
            'nearest_pads': nearest}, bridges


def audit(package=PACKAGE):
    docs = read_project(package / 'Haptics_8x8_R2_10p3mm_checked.epro2')
    pcb = next(d for d in docs if d['head']['docType'] == 'PCB')
    footprints = {d['head']['uuid']: entities(d, 'PAD') for d in docs if d['head']['docType'] == 'FOOTPRINT'}
    pads = project_pads(docs, pcb)
    by_id = {p['id']: p for p in pads}
    holes = project_holes(docs, pcb)
    holes_by_ref = {h['ref']: h for h in holes}
    rings, disabled_paste, outlines = [], [], {}
    for cid, comp in entities(pcb, 'COMPONENT').items():
        for pid, pad in footprints[comp['Footprint']].items():
            point = by_id[cid+pid]
            outlines[cid+pid] = pad_outline(comp, pad)
            hole = pad.get('hole')
            ref = comp['Designator']+'.'+pad['num']
            if hole and hole['width']:
                actual = holes_by_ref[ref]
                centre = Point(actual['x'], actual['y'])
                rings.append({'ref': ref, 'minimum_nominal_ring_mm':
                              centre.distance(outlines[cid+pid].boundary)-actual['diameter']/2})
            if not point['through']:
                expansion = pad.get('bottomPasteExpansion' if comp['layerId'] == 2 else 'topPasteExpansion')
                if expansion is not None and expansion < -50:
                    disabled_paste.append([comp['Designator'], pad['num'], expansion])
    for ident, via in entities(pcb, 'VIA').items():
        rings.append({'ref': 'via:'+ident, 'minimum_nominal_ring_mm':
                      (via['viaDiameter']-via['holeDiameter'])*.0254/2})
    drill_errors, missing_mask, missing_paste = [], [], []
    mask_errors, paste_errors, bridges, annular_errors = [], [], [], []
    opening_stats, edge_stats, actual_rings = {}, {}, []
    board = box(0, -151, 141, 0)
    hole_min_edge = min(({'ref': h['ref'], 'gap_mm':
                         Point(h['x'], h['y']).distance(board.boundary)-h['diameter']/2}
                        for h in holes), key=lambda h:h['gap_mm'])
    smd = [p for p in pads if not p['through']]
    with zipfile.ZipFile(package / 'Haptics_8x8_R2_10p3mm_checked_Gerber.zip') as archive:
        for plated, name in ((True, 'Drill_PTH_Through.DRL'), (False, 'Drill_NPTH_Through.DRL')):
            drill_errors.extend(check_drills(archive.read(name).decode(), [h for h in holes if h['plated'] == plated]))
        print('Drill positions and diameters checked', flush=True)
        for layer, mask_file, paste_file in ((1,'Gerber_TopSolderMaskLayer.GTS','Gerber_TopPasteMaskLayer.GTP'),
                                           (2,'Gerber_BottomSolderMaskLayer.GBS','Gerber_BottomPasteMaskLayer.GBP')):
            mask = openings(archive.read(mask_file).decode())
            paste = openings(archive.read(paste_file).decode()) if paste_file else None
            for name, shape in (('mask', mask), ('paste', paste)):
                if shape is None:
                    continue
                stats, joined = opening_geometry(shape, pads, layer)
                opening_stats[f'{layer}_{name}'] = stats
                bridges.extend({'layer': layer, 'type': name, 'pads': group} for group in joined)
            expanded_mask = mask.buffer(TOL)
            expanded_paste = paste.buffer(TOL) if paste is not None else None
            for pad in smd:
                if pad['layers'] != [layer]:
                    continue
                point = Point(pad['x'], pad['y'])
                if not mask.covers(point):
                    missing_mask.append([pad['ref'], pad['pin']])
                if paste is None or not paste.covers(point):
                    missing_paste.append([pad['ref'], pad['pin']])
                outline = outlines[pad['id']]
                if not expanded_mask.covers(outline):
                    mask_errors.append([pad['ref'], pad['pin']])
                # Release apertures are full-size, zero-reduction. Check both
                # directions so tiny, enlarged or merged windows cannot pass.
                if paste is not None:
                    local = paste.intersection(outline.buffer(.08))
                    if not expanded_paste.covers(outline) or not outline.buffer(TOL).covers(local):
                        paste_errors.append([pad['ref'], pad['pin']])
            silk_file = 'Gerber_TopSilkscreenLayer.GTO' if layer == 1 else 'Gerber_BottomSilkscreenLayer.GBO'
            silk = openings(archive.read(silk_file).decode())
            opening_stats[f'{layer}_silk'] = {'mask_overlap_area_mm2': silk.intersection(mask.buffer(-TOL)).area}
            print(f'Layer {layer} mask, paste and silkscreen checked', flush=True)
        for layer, filename in LAYERS.items():
            shape = copper(archive.read(filename).decode())
            edge_stats[str(layer)] = {'copper_to_board_edge_mm': shape.distance(board.boundary),
                                     'copper_outside_area_mm2': shape.difference(board).area}
            islands = list(shape.geoms) if shape.geom_type == 'MultiPolygon' else [shape]
            tree = STRtree(islands)
            for hole in holes:
                if not hole['plated']:
                    continue
                centre = Point(hole['x'], hole['y'])
                ids = tree.query(centre, predicate='intersects')
                if len(ids) != 1:
                    annular_errors.append({'layer': layer, 'hole': hole['ref'], 'copper_islands': len(ids)})
                    continue
                ring = centre.distance(islands[ids[0]].boundary)-hole['diameter']/2
                actual_rings.append({'layer': layer, 'ref': hole['ref'], 'minimum_copper_ring_mm': ring})
                if ring < .15-TOL:
                    annular_errors.append(actual_rings[-1])
            print(f'Layer {layer} copper edge and annular rings checked', flush=True)
    return {'checked_date': '2026-10-14',
            'package_sha256': {n: hashlib.sha256((package/n).read_bytes()).hexdigest() for n in
                              ('Haptics_8x8_R2_10p3mm_checked.epro2','Haptics_8x8_R2_10p3mm_checked_Gerber.zip')},
            'boundary': 'Nominal drill and complete default SMD pad/opening outlines, mask webs, silk overlap, copper edge and PTH annular land checks at 0.002 mm geometry tolerance; no assembly, stencil thickness, paste volume or fabrication tolerance validation.',
            'holes_checked': len(holes), 'expected_holes_from_cad': len(holes),
            'expected_smd_pads_from_cad': len(smd),
            'expected_plated_lands_from_cad': sum(h['plated'] for h in holes)*len(LAYERS),
            'drill_position_size_errors': drill_errors,
            'minimum_nominal_ring': min(rings, key=lambda p:p['minimum_nominal_ring_mm']),
            'smd_pads_checked': len(smd), 'smd_without_mask_opening': missing_mask,
            'smd_without_paste_opening': missing_paste, 'footprint_paste_suppressed': disabled_paste,
            'smd_mask_boundary_errors': mask_errors, 'smd_paste_boundary_errors': paste_errors,
            'opening_bridges': bridges, 'opening_geometry': opening_stats,
            'minimum_hole_to_board_edge': hole_min_edge, 'copper_edges': edge_stats,
            'plated_hole_layer_lands_checked': len(actual_rings),
            'minimum_actual_copper_ring': min(actual_rings, key=lambda r:r['minimum_copper_ring_mm']),
            'annular_land_errors': annular_errors}


def validate(result):
    assert result['holes_checked'] == result['expected_holes_from_cad']
    assert result['smd_pads_checked'] == result['expected_smd_pads_from_cad']
    for key in ('drill_position_size_errors', 'smd_without_mask_opening', 'smd_without_paste_opening',
                'footprint_paste_suppressed', 'smd_mask_boundary_errors', 'smd_paste_boundary_errors',
                'opening_bridges', 'annular_land_errors'):
        assert not result[key], key
    assert result['plated_hole_layer_lands_checked'] == result['expected_plated_lands_from_cad']
    assert result['minimum_hole_to_board_edge']['gap_mm'] >= .5-TOL
    assert all(e['copper_to_board_edge_mm'] >= .5-TOL and e['copper_outside_area_mm2'] < 1e-5
               for e in result['copper_edges'].values())


if __name__ == '__main__':
    result = audit()
    if verify_copper.args.output:
        verify_copper.args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2)+'\n').encode('utf8'))
    print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in result.items()}, ensure_ascii=False, indent=2))
    validate(result)
