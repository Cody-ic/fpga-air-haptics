"""Read-only R2 source, nominal geometry and manufacturing ZIP checks.

Never modifies CAD or chooses routes. Native DRC and actual copper audits are
separate checks. Drill counts derive from this CAD, not the older R1 release.
"""
import argparse
import collections
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path

from audit_source import documents, entities, netlist

ROOT = Path(__file__).resolve().parent
PROJECT = "Haptics_8x8_R2_10p3mm_checked.epro2"
GERBER = "Haptics_8x8_R2_10p3mm_checked_Gerber.zip"
MM_PER_MIL = 0.0254
PITCH_MM = 10.3
ARRAY_CENTRE_MM = (83.5, 65.5)


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
    sch_groups, pcb_groups = collections.defaultdict(set), collections.defaultdict(set)
    for ref, part in parts.items():
        for pin, data in part["pins"].items():
            if data["net"]:
                sch_groups[data["net"].lower()].add((ref, pin))
    pad_count = connected_pads = 0
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
    sch = {frozenset(pins) for pins in sch_groups.values()}
    board = {frozenset(pins) for pins in pcb_groups.values()}
    assert sch == board, f"Connectivity differs: {len(sch ^ board)} groups"
    assert len(board) == 214
    return pcb, components, {
        "components": len(components), "component_pads": pad_count,
        "connected_pads": connected_pads, "effective_nets": len(board),
        "differing_connectivity_groups": 0, "schematic_alias_notices": notices,
    }


def emitter_components(docs, components):
    """Identify transducers by their schematic MPN, not mounting layer."""
    parts, _ = netlist(docs)
    result = [c for c in components.values()
              if parts[c["Designator"]]["attrs"].get("Manufacturer Part", "").upper() == "MA40S4S"]
    assert len(result) == 64, "Expected exactly 64 MA40S4S transducers"
    return sorted(result, key=lambda c: (-c["y"], c["x"]))


def nominal_holes(docs, pcb):
    """CAD drill centres in Gerber XY, including rotated pad-hole offsets."""
    footprints = {d["head"]["uuid"]: entities(d, "PAD") for d in docs
                  if d["head"]["docType"] == "FOOTPRINT"}
    holes = []
    for comp in entities(pcb, "COMPONENT").values():
        for pad in footprints[comp["Footprint"]].values():
            hole = pad.get("hole")
            if not hole or not hole["width"]:
                continue
            assert hole["holeType"] == "ROUND" and hole["width"] == hole["height"]
            angle = math.radians(pad["padAngle"])
            ox, oy = pad.get("padOffsetX", 0), pad.get("padOffsetY", 0)
            x = pad["centerX"] + ox*math.cos(angle)-oy*math.sin(angle)
            y = pad["centerY"] + ox*math.sin(angle)+oy*math.cos(angle)
            if comp["layerId"] == 2:
                y = -y
            angle = math.radians(comp["angle"])
            holes.append({"ref": comp["Designator"]+"."+pad["num"],
                          "x": (comp["x"]+x*math.cos(angle)-y*math.sin(angle))*MM_PER_MIL,
                          "y": (comp["y"]+x*math.sin(angle)+y*math.cos(angle))*MM_PER_MIL,
                          "diameter": hole["width"]*MM_PER_MIL,
                          "plated": pad["plated"], "via": False})
    for ident, via in entities(pcb, "VIA").items():
        holes.append({"ref": "via:"+ident, "x": via["centerX"]*MM_PER_MIL,
                      "y": via["centerY"]*MM_PER_MIL,
                      "diameter": via["holeDiameter"]*MM_PER_MIL,
                      "plated": True, "via": True})
    for ident, pad in entities(pcb, "PAD").items():
        assert pad["hole"]["holeType"] == "ROUND"
        assert not pad.get("padOffsetX") and not pad.get("padOffsetY")
        holes.append({"ref": "mount:"+ident, "x": pad["centerX"]*MM_PER_MIL,
                      "y": pad["centerY"]*MM_PER_MIL,
                      "diameter": pad["hole"]["width"]*MM_PER_MIL,
                      "plated": pad["plated"], "via": False})
    return holes


def geometry(docs, pcb, components):
    outline = next(v for v in entities(pcb, "POLY").values()
                   if v.get("polyType") == "BOARD_OUTLINE")
    assert outline["path"][0] == "R"
    assert all(abs(v) < 1e-6 for v in outline["path"][1:3]), "Unexpected board origin"
    width, height = [round(x*MM_PER_MIL, 4) for x in outline["path"][3:5]]
    assert (width, height) == (141.0, 151.0)
    vias, pours = entities(pcb, "VIA"), entities(pcb, "POUR")
    assert all(v["viaType"] == "NORMAL" for v in vias.values()), "Non-through via"
    filled = {json.loads(r["h"]["id"])[1]: r["v"].get("pourFill")
              for r in pcb["items"] if r["h"]["type"] == "POURED" and r["v"]}
    assert len(pours) == 5 and all(filled.get(pid) for pid in pours), "Rebuild every current copper pour"
    mounting = entities(pcb, "PAD")
    assert len(mounting) == 4 and all(not h["plated"] for h in mounting.values())
    assert {(round(h["centerX"]*MM_PER_MIL, 4), round(h["centerY"]*MM_PER_MIL, 4))
            for h in mounting.values()} == {(4, -4), (137, -4), (4, -147), (137, -147)}
    emitters = emitter_components(docs, components)
    actual = [(round(c["x"]*MM_PER_MIL, 4), round(-c["y"]*MM_PER_MIL, 4)) for c in emitters]
    expected = [(round(47.45+PITCH_MM*col, 4), round(29.45+PITCH_MM*row, 4))
                for row in range(8) for col in range(8)]
    assert actual == expected, "MA40S4S grid differs from the R2 coordinates"
    lengths = collections.defaultdict(float)
    maximum = 0
    lines = entities(pcb, "LINE")
    for line in lines.values():
        if line["layerId"] not in (1, 2, 15, 16):
            continue
        length = math.hypot(line["endX"]-line["startX"], line["endY"]-line["startY"])*MM_PER_MIL
        lengths[line["netName"]] += length
        maximum = max(maximum, length)
    holes = nominal_holes(docs, pcb)
    return {"board_mm": [width, height], "emitter_count": len(emitters),
            "emitter_pitch_mm": PITCH_MM, "array_centre_pcb_mm": list(ARRAY_CENTRE_MM),
            "outer_centre_span_mm": [72.1, 72.1], "vias_through": len(vias),
            "npth_mounting_holes": len(mounting), "nominal_drill_count": len(holes),
            "line_primitives": len(lines), "current_pours_with_fill": len(pours),
            "maximum_single_segment_mm": round(maximum, 3),
            "net_total_trace_lengths_mm": {n: round(v, 3) for n, v in sorted(lengths.items())}}


def drill_hits(source):
    assert "METRIC,LZ,0000.000000" in source
    tools, hits, active = {}, [], None
    for line in source.splitlines():
        tool = re.fullmatch(r"T(\d+)C([\d.]+)", line)
        select = re.fullmatch(r"T(\d+)", line)
        hit = re.fullmatch(r"X([-\d.]+)Y([-\d.]+)", line)
        if tool:
            tools[int(tool[1])] = float(tool[2])
        elif select:
            active = int(select[1])
        elif hit:
            assert active in tools, "Drill hit without a defined tool"
            hits.append((float(hit[1]), float(hit[2]), tools[active]))
    return hits


def manufacturing(path, docs, pcb):
    holes = nominal_holes(docs, pcb)
    expected = {"Drill_PTH_Through.DRL": [h for h in holes if h["plated"]],
                "Drill_NPTH_Through.DRL": [h for h in holes if not h["plated"]],
                "Drill_PTH_Through_Via.DRL": [h for h in holes if h["via"]]}
    copper = {".GTL", ".GBL", ".G1", ".G2"}
    required = copper | {".GTO", ".GBO", ".GTS", ".GBS", ".GTP", ".GBP", ".GKO"}
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "Corrupt Gerber ZIP"
        names = archive.namelist()
        suffixes = [Path(n).suffix.upper() for n in names]
        assert all(suffixes.count(ext) == 1 for ext in required), "Missing or duplicate Gerber layer"
        gerbers = [n for n in names if Path(n).suffix.upper() in required]
        for name in gerbers:
            source = archive.read(name).decode("utf-8")
            assert "%FSLAX46Y46*%" in source and "%MOMM*%" in source, name
            assert source.rstrip().endswith("M02*"), name
        counts = {}
        for name, expected_holes in expected.items():
            hits = drill_hits(archive.read(name).decode("utf-8"))
            assert len(hits) == len(expected_holes), (name, "CAD/Gerber drill count differs")
            positions = {(round(x, 5), round(y, 5)) for x, y, _ in hits}
            assert len(positions) == len(hits), (name, "Duplicate drill position")
            assert all(0 < x < 141 and -151 < y < 0 for x, y in positions)
            for h in expected_holes:
                found = [p for p in hits if math.dist(p[:2], (h["x"], h["y"])) < .00001]
                assert len(found) == 1 and abs(found[0][2]-h["diameter"]) < .00001, (name, h)
            counts[name] = len(hits)
    return {"copper_layers": 4, "gerber_layers_checked": len(gerbers),
            "coordinate_format": "MM 4:6", "drill_coordinate_counts": counts,
            "drill_count_basis": "Derived from this project, including rotated drill offsets"}


def verify(root=ROOT):
    docs = read_project(root / PROJECT)
    pcb, components, nets = connectivity(docs)
    return {"connectivity": nets, "geometry": geometry(docs, pcb, components),
            "manufacturing": manufacturing(root / GERBER, docs, pcb),
            "boundary": "Source/netlist and nominal-coordinate checks only; native DRC and copper-shape checks are separate"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Save the read-only result as JSON')
    options = parser.parse_args()
    manifest_path = ROOT / "SHA256SUMS.txt"
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            expected, filename = line.split("  ", 1)
            assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == expected, f"Checksum mismatch: {filename}"
    report = json.dumps(verify(), ensure_ascii=False, indent=2) + '\n'
    if options.output:
        options.output.write_text(report, encoding='utf-8')
    print(report, end='')
