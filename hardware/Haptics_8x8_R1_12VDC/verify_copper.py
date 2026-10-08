"""Independent read-only Gerber copper graph; never chooses or edits routes."""
import argparse
import collections
import hashlib
import json
import math
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dependency-path', action='append', default=[], help='Optional directory containing audit dependencies')
parser.add_argument('--output', type=Path, help='Save the read-only check result as JSON')
args = parser.parse_args()
sys.path[:0] = args.dependency_path
from shapely import affinity, make_valid, union_all
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box
from shapely.strtree import STRtree
from pygerber.gerberx3.api.v2 import GerberFile
from audit_source import entities
from verify_release import read_project

MIL = 0.0254
TOL = 0.002
LAYERS = {1: 'Gerber_TopLayer.GTL', 2: 'Gerber_BottomLayer.GBL',
          15: 'Gerber_InnerLayer1.G1', 16: 'Gerber_InnerLayer2.G2'}


def scalar(offset):
    return float(offset.as_millimeters())


def xy(vec):
    return scalar(vec.x), scalar(vec.y)


def curve(command):
    start, end = xy(command.start_point), xy(command.end_point)
    if type(command).__name__ == 'Line2':
        return [start, end]
    assert type(command).__name__ in ('Arc2', 'CCArc2')
    cx, cy = xy(command.center_point)
    r = math.dist(start, (cx, cy))
    a = math.atan2(start[1] - cy, start[0] - cx)
    b = math.atan2(end[1] - cy, end[0] - cx)
    if type(command).__name__ == 'CCArc2':
        sweep = (b - a) % math.tau
    else:
        sweep = -((a - b) % math.tau)
    if math.dist(start, end) < 1e-7:
        sweep = math.copysign(math.tau, 1 if type(command).__name__ == 'CCArc2' else -1)
    step = 2 * math.acos(max(-1, 1 - 0.0002 / r)) if r else 0.01
    count = max(2, math.ceil(abs(sweep) / min(step, 0.04)))
    points = [(cx + r * math.cos(a + sweep * i/count),
               cy + r * math.sin(a + sweep * i/count)) for i in range(count + 1)]
    points[0], points[-1] = start, end
    return points


def polygonal(shape):
    if shape.geom_type in ('Polygon', 'MultiPolygon'):
        return shape
    return union_all([g for g in shape.geoms if g.geom_type in ('Polygon', 'MultiPolygon')])


def aperture_shape(aperture):
    kind = type(aperture).__name__
    assert aperture.hole_diameter is None
    if kind == 'Circle2':
        return Point(0, 0).buffer(scalar(aperture.diameter)/2, quad_segs=128)
    assert kind in ('Rectangle2', 'Obround2'), kind
    w, h = scalar(aperture.x_size), scalar(aperture.y_size)
    if kind == 'Rectangle2':
        shape = box(-w/2, -h/2, w/2, h/2)
    else:
        r = min(w, h)/2
        ends = [(-(w-h)/2, 0), ((w-h)/2, 0)] if w >= h else [(0, -(h-w)/2), (0, (h-w)/2)]
        shape = LineString(ends).buffer(r, quad_segs=128)
    return affinity.rotate(shape, float(aperture.rotation), origin=(0, 0))


def command_shape(command, aperture_cache):
    kind = type(command).__name__
    assert command.transform.mirroring.value == 'N'
    assert float(command.transform.scaling) == 1
    assert float(command.transform.rotation) == 0
    if kind == 'Region2':
        contours, points = [], []
        for item in command.command_buffer:
            segment = curve(item)
            if points and math.dist(points[-1], segment[0]) > 1e-6:
                contours.append(points)
                points = []
            points.extend(segment if not points else segment[1:])
        if points:
            contours.append(points)
        shape = GeometryCollection()
        for contour in contours:
            shape = shape.symmetric_difference(polygonal(make_valid(Polygon(contour))))
        return shape
    if kind == 'Flash2':
        aperture = command.aperture
        key = aperture.identifier
        if key not in aperture_cache:
            aperture_cache[key] = aperture_shape(aperture)
        return affinity.translate(aperture_cache[key], *xy(command.flash_point))
    assert kind in ('Line2', 'Arc2', 'CCArc2'), kind
    assert type(command.aperture).__name__ == 'Circle2'
    return LineString(curve(command)).buffer(scalar(command.aperture.diameter)/2, quad_segs=128)


def copper(source):
    parsed = GerberFile.from_str(source).parse()
    shape, batch, polarity, cache = GeometryCollection(), [], None, {}
    for command in parsed._command_buffer:
        current = command.transform.polarity.value
        if polarity is not None and current != polarity:
            group = union_all(batch)
            shape = shape.union(group) if polarity == 'D' else shape.difference(group)
            batch = []
        polarity = current
        batch.append(command_shape(command, cache))
    group = union_all(batch)
    return polygonal(shape.union(group) if polarity == 'D' else shape.difference(group))


def project_pads(docs, pcb):
    fps = {d['head']['uuid']: entities(d, 'PAD') for d in docs if d['head']['docType'] == 'FOOTPRINT'}
    nets = {tuple(json.loads(r['h']['id'])[1:]): r['v']['padNet'] for r in pcb['items']
            if r['h']['type'] == 'PAD_NET' and r['v']}
    pads = []
    for cid, comp in entities(pcb, 'COMPONENT').items():
        for pid, pad in fps[comp['Footprint']].items():
            bottom = comp['layerId'] == 2
            x, y = pad['centerX'], pad['centerY'] * (-1 if bottom else 1)
            angle = math.radians(comp['angle'])
            x, y = (comp['x'] + x*math.cos(angle)-y*math.sin(angle),
                    comp['y'] + x*math.sin(angle)+y*math.cos(angle))
            through = pad['layerId'] == 12
            net = nets[(cid, pad['num'], pid)].upper()
            pads.append({'ref': comp['Designator'], 'pin': pad['num'], 'net': net,
                         'x': x*MIL, 'y': y*MIL,
                         'layers': list(LAYERS) if through else [comp['layerId']],
                         'through': through, 'id': cid+pid})
    return pads


def body_and_drills(docs, pcb):
    footprints = {d['head']['uuid']: d for d in docs if d['head']['docType'] == 'FOOTPRINT'}
    coords = {p['id']: p for p in project_pads(docs, pcb)}
    bodies, holes = [], []
    for cid, comp in entities(pcb, 'COMPONENT').items():
        footprint = footprints[comp['Footprint']]
        paths = []
        for record in footprint['items']:
            if record['h']['type'] in ('POLY', 'FILL') and record['v'].get('layerId') == 48:
                paths.extend(record['v']['path'] if record['h']['type'] == 'FILL' else [record['v']['path']])
        assert len(paths) == 1, ('Unsupported component body', comp['Designator'])
        path = paths[0]
        if 'ARC' in path:
            assert path.count('ARC') == 2 and path[1] == path[5]
            body = Point((path[0]+path[4])*MIL/2, path[1]*MIL).buffer(abs(path[4]-path[0])*MIL/2, quad_segs=128)
        else:
            assert all(isinstance(item, (int, float)) or item == 'L' for item in path)
            values = [p for p in path if not isinstance(p, str)]
            body = Polygon([(x*MIL, y*MIL) for x, y in zip(values[::2], values[1::2])])
        if comp['layerId'] == 2:
            body = affinity.scale(body, xfact=1, yfact=-1, origin=(0, 0))
        body = affinity.rotate(body, comp['angle'], origin=(0, 0))
        body = affinity.translate(body, comp['x']*MIL, comp['y']*MIL)
        bodies.append((comp['Designator'], comp['layerId'], body))
        for pid, pad in entities(footprint, 'PAD').items():
            hole = pad.get('hole')
            if hole and hole['width']:
                assert hole['holeType'] == 'ROUND' and hole['width'] == hole['height']
                point = coords[cid+pid]
                holes.append((comp['Designator']+'.'+pad['num'], point['x'], point['y'], hole['width']*MIL))
    for vid, via in entities(pcb, 'VIA').items():
        holes.append(('via:'+vid, via['centerX']*MIL, via['centerY']*MIL, via['holeDiameter']*MIL))
    for pid, pad in entities(pcb, 'PAD').items():
        holes.append(('mount:'+pid, pad['centerX']*MIL, pad['centerY']*MIL, pad['hole']['width']*MIL))
    errors, body_min, hole_min = [], (math.inf, None), (math.inf, None)
    for i, (ref, layer, body) in enumerate(bodies):
        if not box(0, -151, 141, 0).covers(body):
            errors.append(['outside_board', ref])
        for other_ref, other_layer, other_body in bodies[:i]:
            if layer != other_layer:
                continue
            distance = body.distance(other_body)
            if distance < body_min[0]:
                body_min = distance, [ref, other_ref]
            if body.intersection(other_body).area > 0.0001:
                errors.append(['body_overlap', ref, other_ref])
    for i, (ref, x, y, diameter) in enumerate(holes):
        for other_ref, xx, yy, other_diameter in holes[:i]:
            gap = math.hypot(x-xx, y-yy) - (diameter+other_diameter)/2
            if gap < hole_min[0]:
                hole_min = gap, [ref, other_ref]
    assert hole_min[0] >= 11.811*MIL, 'Drill wall gap violates hole clearance'
    return {'component_bodies_checked': len(bodies), 'body_overlap_or_outside_board': errors,
            'minimum_body_gap_mm': body_min[0], 'closest_bodies': body_min[1],
            'holes_checked': len(holes), 'minimum_drill_wall_gap_mm': hole_min[0],
            'closest_drills': hole_min[1],
            'boundary': 'Nominal footprint body layer 48; connector mating/cables/part tolerances are not modeled'}


def audit():
    docs = read_project(ROOT / 'Haptics_8x8_R1_release.epro2')
    pcb = next(d for d in docs if d['head']['docType'] == 'PCB')
    pads = project_pads(docs, pcb)
    # NPTH aperture flashes are removed by the actual drill operation.
    holes = [Point(p['centerX']*MIL, p['centerY']*MIL).buffer(p['hole']['width']*MIL/2, quad_segs=128)
             for p in entities(pcb, 'PAD').values() if not p['plated']]
    nodes, layer_nodes, layer_shapes = [], {}, {}
    with zipfile.ZipFile(ROOT / 'Haptics_8x8_R1_release_Gerber.zip') as archive:
        for layer, filename in LAYERS.items():
            shape = copper(archive.read(filename).decode('utf8')).difference(union_all(holes))
            polygons = list(shape.geoms) if shape.geom_type == 'MultiPolygon' else [shape]
            # sub-micrometre NPTH rounding slivers cannot carry a connection
            polygons = [p for p in polygons if p.area > 1e-5]
            layer_nodes[layer] = list(range(len(nodes), len(nodes)+len(polygons)))
            nodes.extend(polygons)
            layer_shapes[layer] = polygons
            print(filename, 'islands', len(polygons), 'area mm2', round(shape.area, 3), flush=True)
    parent = list(range(len(nodes)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def join(ids):
        for i in ids[1:]:
            parent[root(i)] = root(ids[0])
    trees = {l: STRtree(shapes) for l, shapes in layer_shapes.items()}
    def contacts(x, y, layers):
        point = Point(x, y)
        result = []
        for layer in layers:
            ids = trees[layer].query(point.buffer(TOL), predicate='intersects')
            result.extend(layer_nodes[layer][i] for i in ids)
        return result
    missing = []
    for pad in pads:
        ids = contacts(pad['x'], pad['y'], pad['layers'])
        pad['nodes'] = ids
        if not ids:
            missing.append([pad['ref'], pad['pin'], pad['net']])
        if pad['through']:
            assert len(ids) == 4, ('Pad copper absent on a through layer', pad['ref'], pad['pin'])
            join(ids)
    for vid, via in entities(pcb, 'VIA').items():
        ids = contacts(via['centerX']*MIL, via['centerY']*MIL, LAYERS)
        assert len(ids) == 4, ('Via copper absent on a through layer', vid)
        join(ids)
    labels = collections.defaultdict(set)
    net_roots = collections.defaultdict(set)
    for pad in pads:
        tag = pad['net'] or f"NC:{pad['ref']}.{pad['pin']}"
        for i in pad['nodes']:
            labels[root(i)].add(tag)
            if pad['net']:
                net_roots[pad['net']].add(root(i))
    via_mismatches = []
    for vid, via in entities(pcb, 'VIA').items():
        ids = contacts(via['centerX']*MIL, via['centerY']*MIL, LAYERS)
        actual = labels[root(ids[0])]
        expected = {via['netName'].upper()}
        if actual != expected:
            via_mismatches.append({'id': vid, 'expected': sorted(expected),
                                   'actual': sorted(actual)})
    shorts = {str(k): sorted(v) for k, v in labels.items() if len(v) > 1}
    opens = {k: sorted(v) for k, v in net_roots.items() if len(v) != 1}
    clearances, violations, unassigned = {}, [], []
    for layer, polys in layer_shapes.items():
        tree = trees[layer]
        minimum = (float('inf'), None)
        for i, poly in enumerate(polys):
            ni = layer_nodes[layer][i]
            if not labels[root(ni)]:
                unassigned.append({'layer': layer, 'area_mm2': poly.area, 'bounds': list(poly.bounds)})
            for j in tree.query(poly.buffer(0.4), predicate='intersects'):
                if j <= i:
                    continue
                nj = layer_nodes[layer][j]
                if root(ni) == root(nj):
                    continue
                distance = poly.distance(polys[j])
                tags = [sorted(labels[root(ni)]), sorted(labels[root(nj)])]
                if distance < minimum[0]:
                    minimum = (distance, tags)
                if distance < 5.9843*MIL - TOL:
                    violations.append({'layer': layer, 'gap_mm': distance, 'nets': tags})
        clearances[str(layer)] = {'minimum_gap_mm': minimum[0], 'nets': minimum[1]}
    return {'package_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('Haptics_8x8_R1_release.epro2', 'Haptics_8x8_R1_release_Gerber.zip')},
            'mechanical': body_and_drills(docs, pcb),
            'algorithm': 'Gerber polygon union + PTH/via layer graph, read-only',
            'arc_sagitta_mm': 0.0002, 'contact_tolerance_mm': TOL,
            'component_pads_checked': len(pads), 'nets_checked': len(net_roots),
            'via_nets_checked': len(entities(pcb, 'VIA')),
            'via_net_mismatches': via_mismatches,
            'missing_pad_copper': missing, 'shorted_groups': shorts, 'open_nets': opens,
            'clearance_violations_approx_6mil': violations,
            'layer_clearances': clearances, 'unassigned_copper': unassigned}


if __name__ == '__main__':
    result = audit()
    source = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_bytes(source.encode('utf8'))
    print(source, flush=True)
    assert not any(result[key] for key in ('missing_pad_copper', 'shorted_groups', 'open_nets', 'via_net_mismatches',
                                         'clearance_violations_approx_6mil', 'unassigned_copper'))
    assert not result['mechanical']['body_overlap_or_outside_board']
