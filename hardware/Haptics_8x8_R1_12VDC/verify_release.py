"""Read-only checks of the released project, connectivity and manufacturing ZIP.

This is not a router, an electrical simulator or a replacement for native DRC.
"""
import collections
import hashlib
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
    assert len(vias) == 500
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
        assert drill_counts == {"Drill_PTH_Through.DRL": 650,
                                "Drill_NPTH_Through.DRL": 4,
                                "Drill_PTH_Through_Via.DRL": 500}
        assert drill_points["Drill_PTH_Through_Via.DRL"] <= drill_points["Drill_PTH_Through.DRL"]
        assert drill_points["Drill_NPTH_Through.DRL"] == {(4, -4), (137, -4), (4, -147), (137, -147)}
        return {"copper_layers": 4, "gerber_layers_checked": 10,
                "coordinate_format": "MM 4:6", "drill_coordinate_counts": drill_counts}


def verify(root=ROOT):
    docs = read_project(root / PROJECT)
    pcb, components, nets = connectivity(docs)
    return {"connectivity": nets, "geometry": geometry(pcb, components),
            "manufacturing": manufacturing(root / GERBER)}


if __name__ == "__main__":
    manifest_path = ROOT / "SHA256SUMS.txt"
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            expected, filename = line.split("  ", 1)
            actual = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
            assert actual == expected, f"Checksum mismatch: {filename}"
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
