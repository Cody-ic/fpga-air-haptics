"""Read-only checks of the released project, connectivity and manufacturing ZIP.

This is not a router, an electrical simulator or a replacement for native DRC.
"""
import collections
import hashlib
import heapq
import json
import math
import re
import zipfile
from pathlib import Path

from audit_source import documents, entities, netlist

ROOT = Path(__file__).resolve().parent
PROJECT = "Haptics_8x8_R1_release.epro2"
GERBER = "Haptics_8x8_R1_release_Gerber.zip"
MM_PER_MIL = 0.0254


def read_project(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "Corrupt project ZIP"
        sources = [n for n in archive.namelist() if n.endswith(".epru")]
        assert len(sources) == 1, "Expected one project source"
        return documents(archive.read(sources[0]).decode("utf-8"))


def connectivity(docs):
    parts, notices = netlist(docs)
    assert not any("duplicate_designator" in n for n in notices)
    pcb = next(d for d in docs if d["head"]["docType"] == "PCB")
    components = entities(pcb, "COMPONENT")
    refs = {cid: c["Designator"] for cid, c in components.items()}
    assert len(set(refs.values())) == len(refs) == 324
    assert set(parts) == set(refs.values()), "Schematic/PCB component mismatch"
    sch_groups = collections.defaultdict(set)
    pcb_groups = collections.defaultdict(set)
    for ref, part in parts.items():
        for pin, data in part["pins"].items():
            if data["net"]:
                sch_groups[data["net"].lower()].add((ref, pin))
    connected_pads = 0
    pad_count = 0
    for record in pcb["items"]:
        if record["h"]["type"] != "PAD_NET" or not record["v"]:
            continue
        _, cid, pin, _ = json.loads(record["h"]["id"])
        assert cid in refs, "Pad belongs to an absent component"
        pad_count += 1
        net = record["v"]["padNet"]
        if net:
            pcb_groups[net.lower()].add((refs[cid], pin))
            connected_pads += 1
    assert pad_count == 970
    # Auto-generated net names differ between SCH and PCB; compare pin sets.
    sch = {frozenset(pins) for pins in sch_groups.values()}
    board = {frozenset(pins) for pins in pcb_groups.values()}
    assert sch == board, f"Connectivity differs: {len(sch ^ board)} groups"
    assert len(board) == 214
    return pcb, components, {
        "components": len(components), "component_pads": pad_count,
        "connected_pads": connected_pads, "effective_nets": len(board),
        "differing_connectivity_groups": 0,
        "schematic_alias_notices": notices,
    }


def geometry(pcb, components):
    outline = next(v for v in entities(pcb, "POLY").values()
                   if v.get("polyType") == "BOARD_OUTLINE")
    assert outline["path"][0] == "R"
    width, height = [round(x * MM_PER_MIL, 4) for x in outline["path"][3:5]]
    assert (width, height) == (141.0, 151.0)
    vias = entities(pcb, "VIA")
    assert len(vias) == 495
    pours = entities(pcb, "POUR")
    # Project exports can retain caches for removed pours. Check the cache
    # belonging to each current POUR, rather than counting historical records.
    filled = {json.loads(r['h']['id'])[1]: r['v'].get('pourFill')
              for r in pcb['items'] if r['h']['type'] == 'POURED' and r['v']}
    assert len(pours) == 5 and all(filled.get(pid) for pid in pours), "Rebuild all copper pours before release"
    assert all(v["viaType"] == "NORMAL" for v in vias.values()), "Non-through via"
    holes = entities(pcb, "PAD")
    assert len(holes) == 4 and all(not h["plated"] for h in holes.values())
    emitters = sorted((round(c["x"] * MM_PER_MIL, 3),
                       round(-c["y"] * MM_PER_MIL, 3))
                      for c in components.values() if c["layerId"] == 1)
    expected = sorted((38 + 13 * col, 20 + 13 * row)
                      for row in range(8) for col in range(8))
    assert emitters == expected, "Emitter grid changed"
    lengths = collections.defaultdict(float)
    maximum = 0
    lines = entities(pcb, "LINE")
    for line in lines.values():
        if line["layerId"] not in (1, 2, 15, 16):
            continue
        length = math.hypot(line["endX"] - line["startX"],
                            line["endY"] - line["startY"]) * MM_PER_MIL
        lengths[line["netName"]] += length
        maximum = max(maximum, length)
    labels = [r["v"] for r in pcb["items"]
              if r["h"]["type"] in ("STRING", "ATTR") and r["v"]
              and r["v"].get("layerId") == 4
              and (r["h"]["type"] == "STRING" or r["v"].get("valueVisible"))]
    assert len(labels) == 265 and all(not t["mirror"] for t in labels)
    return {
        "board_mm": [width, height], "emitter_pitch_mm": 13,
        "vias_through": len(vias), "npth_mounting_holes": len(holes),
        "line_primitives": len(lines), "bottom_readable_labels": len(labels),
        "current_pours_with_fill": len(pours),
        "maximum_single_segment_mm": round(maximum, 3),
        "net_total_trace_lengths_mm": {n: round(v, 3) for n, v in sorted(lengths.items())},
    }


def manufacturing(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "Corrupt Gerber ZIP"
        names = archive.namelist()
        copper = [n for n in names if Path(n).suffix.upper() in (".GTL", ".GBL", ".G1", ".G2")]
        assert len(copper) == 4
        gerbers = [n for n in names if Path(n).suffix.upper() in
                   (".GTL", ".GBL", ".G1", ".G2", ".GTO", ".GBO", ".GTS", ".GBS", ".GBP", ".GKO")]
        assert len(gerbers) == 10
        for name in gerbers:
            content = archive.read(name).decode("utf-8")
            assert "%FSLAX46Y46*%" in content and "%MOMM*%" in content, name
            assert content.rstrip().endswith("M02*"), name
        drill_counts = {}
        drill_points = {}
        for name in names:
            if name.upper().endswith(".DRL"):
                source = archive.read(name).decode("utf-8")
                assert "METRIC,LZ,0000.000000" in source
                points = re.findall(r"^X([-\d.]+)Y([-\d.]+)", source, re.M)
                drill_counts[name] = len(points)
                drill_points[name] = {(round(float(x), 4), round(float(y), 4)) for x, y in points}
                assert len(points) == len(drill_points[name]), "Duplicate drill position"
                assert all(0 < x < 141 and -151 < y < 0 for x, y in drill_points[name])
        assert drill_counts == {"Drill_PTH_Through.DRL": 645,
                                "Drill_NPTH_Through.DRL": 4,
                                "Drill_PTH_Through_Via.DRL": 495}
        assert drill_points["Drill_PTH_Through_Via.DRL"] <= drill_points["Drill_PTH_Through.DRL"]
        assert drill_points["Drill_NPTH_Through.DRL"] == {(4, -4), (137, -4), (4, -147), (137, -147)}
        return {"copper_layers": 4, "gerber_layers_checked": 10,
                "coordinate_format": "MM 4:6", "drill_coordinate_counts": drill_counts}


def reviewed_layout(docs, pcb, components):
    """Measure existing traces only; graph search never chooses or changes routes."""
    footprints = {d['head']['uuid']: entities(d, 'PAD') for d in docs
                  if d['head']['docType'] == 'FOOTPRINT'}
    pads = {}
    for c in components.values():
        angle = math.radians(c['angle'])
        for p in footprints[c['Footprint']].values():
            x, y = p['centerX'], p['centerY'] * (-1 if c['layerId'] == 2 else 1)
            pads[c['Designator'], p['num']] = (
                (c['x'] + x*math.cos(angle) - y*math.sin(angle)) * MM_PER_MIL,
                -(c['y'] + x*math.sin(angle) + y*math.cos(angle)) * MM_PER_MIL,
                c['layerId'])
    lines, vias = entities(pcb, 'LINE'), entities(pcb, 'VIA')
    def node(x, y, layer): return (round(x, 3), round(y, 3), layer)
    def length(net, start, end):
        graph = collections.defaultdict(list)
        def edge(a, b, weight):
            graph[a].append((b, weight)); graph[b].append((a, weight))
        for v in lines.values():
            if v['netName'] != net: continue
            a = (v['startX']*MM_PER_MIL, -v['startY']*MM_PER_MIL)
            b = (v['endX']*MM_PER_MIL, -v['endY']*MM_PER_MIL)
            edge(node(*a, v['layerId']), node(*b, v['layerId']), math.dist(a, b))
        for v in vias.values():
            if v['netName'] != net: continue
            x, y = v['centerX']*MM_PER_MIL, -v['centerY']*MM_PER_MIL
            for layer in (2, 15, 16): edge(node(x,y,1), node(x,y,layer), 0)
        start, end = node(*pads[start]), node(*pads[end])
        pending, best = [(0, start)], {start: 0}
        while pending:
            distance, point = heapq.heappop(pending)
            if distance != best[point]: continue
            if point == end: return round(distance, 4)
            for q, weight in graph[point]:
                if distance + weight < best.get(q, float('inf')):
                    best[q] = distance + weight
                    heapq.heappush(pending, (distance + weight, q))
        raise AssertionError(f'No explicit trace path: {net}, {start}, {end}')
    gnd = [(k, (v['centerX']*MM_PER_MIL, -v['centerY']*MM_PER_MIL))
           for k,v in vias.items() if v['netName'] == 'GND']
    clocks = {}
    for net, resistor, pin in [('SRCLK','R135','11'), ('RCLK','R136','12')]:
        signal = [(k,v) for k,v in vias.items() if v['netName'] == net]
        nearest = []
        for k,v in signal:
            pt = (v['centerX']*MM_PER_MIL, -v['centerY']*MM_PER_MIL)
            gid, ground = min(gnd, key=lambda q: math.dist(pt, q[1]))
            nearest.append({'signal_via': k, 'ground_via': gid,
                            'centre_distance_mm': round(math.dist(pt,ground),4)})
        assert len(signal) == {'SRCLK':9,'RCLK':11}[net]
        assert all(.5 <= q['centre_distance_mm'] <= 2 for q in nearest)
        clocks[net] = {'signal_vias':len(signal), 'nearest_ground_vias':nearest,
                      'source_resistor_to_U1_mm':length(net,(resistor,'1'),('U1',pin)),
                      'source_resistor_to_U94_mm':length(net,(resistor,'1'),('U94',pin))}
    buck = {'C19_power_to_VIN_straight_mm':round(math.dist(pads['C19','2'][:2],pads['U27','5'][:2]),4),
            'C20_power_to_VIN_straight_mm':round(math.dist(pads['C20','2'][:2],pads['U27','5'][:2]),4),
            'C19_power_to_VIN_trace_mm':length('12V',('C19','2'),('U27','5')),
            'C19_ground_to_U27_trace_mm':length('GND',('C19','1'),('U27','2')),
            'SW_to_inductor_straight_mm':round(math.dist(pads['U27','6'][:2],pads['L1','1'][:2]),4),
            'SW_to_inductor_trace_mm':length('$2N10',('U27','6'),('L1','1'))}
    return {'clocks':clocks, 'buck':buck,
            'boundary':'Copper centreline lengths exclude cables, packages, via length and electrical delays; nearest-via distances do not prove high-frequency return impedance.'}


def verify(root=ROOT):
    docs = read_project(root / PROJECT)
    pcb, components, nets = connectivity(docs)
    return {"connectivity": nets, "geometry": geometry(pcb, components),
            "reviewed_layout": reviewed_layout(docs, pcb, components),
            "manufacturing": manufacturing(root / GERBER)}


if __name__ == "__main__":
    manifest_path = ROOT / "SHA256SUMS.txt"
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            expected, filename = line.split("  ", 1)
            actual = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
            assert actual == expected, f"Checksum mismatch: {filename}"
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
