"""Read-only R2 project audit, independent of Gerber or export settings.

Checks source records and nominal electrical/geometric configuration only.
Never edits CAD, places components, generates routes or certifies operation.
"""
import argparse
import collections
import hashlib
import json
import math
import re
from pathlib import Path

from audit_source import entities, netlist
from verify_release import (
    ROOT, PROJECT, MM_PER_MIL, connectivity, emitter_components,
    geometry, nominal_holes, read_project,
)


def check_stack(pcb):
    layers = {v["layerId"]: v for v in entities(pcb, "LAYER").values()}
    logical_copper = {ident for ident, layer in layers.items()
                      if layer.get("use") and layer["layerType"] in ("TOP", "BOTTOM", "SIGNAL", "PLANE")}
    physical = sorted(
        [(json.loads(r["h"]["id"])[1], r["v"]) for r in pcb["items"]
         if r["h"]["type"] == "LAYER_PHYS" and r["v"]],
        key=lambda p: p[1]["zIndex"])
    copper_order = [ident for ident, _ in physical
                    if layers.get(ident, {}).get("layerType") in ("TOP", "BOTTOM", "SIGNAL", "PLANE")]
    expected = [1, 15, 16, 2]
    errors = []
    if logical_copper != set(expected):
        errors.append({"kind": "logical_copper_layers", "actual": sorted(logical_copper), "expected": expected})
    if copper_order != expected:
        errors.append({"kind": "physical_copper_order", "actual": copper_order, "expected": expected})
    positions = {ident: i for i, (ident, _) in enumerate(physical)}
    for front, back in zip(copper_order, copper_order[1:]):
        between = physical[positions[front]+1:positions[back]]
        dielectrics = [(ident, p) for ident, p in between
                       if layers.get(ident, {}).get("layerType") == "SUBSTRATE"]
        if not dielectrics or any(p.get("thickness", 0) <= 0 for _, p in dielectrics):
            errors.append({"kind": "missing_positive_dielectric", "between_copper_layers": [front, back]})
    for ident, p in physical:
        if ident in copper_order and p.get("thickness", 0) <= 0:
            errors.append({"kind": "non_positive_copper_thickness", "layer": ident})
    return {"status": "needs_correction" if errors else "passed",
            "logical_copper_layers": sorted(logical_copper),
            "physical_copper_order": copper_order,
            "physical_layer_records": [{"layer_id": ident, **p} for ident, p in physical],
            "errors": errors,
            "boundary": "Checks consistency of source layer records; does not select a factory stack or confirm actual laminate thickness"}


def check_rc_paste(docs, components):
    pcb = next(d for d in docs if d["head"]["docType"] == "PCB")
    defaults = [rule["ruleContext"] for ident, rule in entities(pcb, "RULE").items()
                if json.loads(ident)[:2] == ["RULE", "PASTE"]
                and rule.get("ruleState") == "DEFAULT"]
    assert len(defaults) == 1, "Expected one effective default paste rule"
    default_expansion = {"topPasteExpansion": defaults[0].get("padTopExpan"),
                         "bottomPasteExpansion": defaults[0].get("padBotExpan")}
    footprints = {d["head"]["uuid"]: entities(d, "PAD") for d in docs
                  if d["head"]["docType"] == "FOOTPRINT"}
    passive = [c for c in components.values() if re.fullmatch(r"[RC]\d+", c["Designator"])]
    errors, count, inherited_pads = [], 0, 0
    for comp in passive:
        for pad in footprints[comp["Footprint"]].values():
            if (pad.get("hole") or {}).get("width", 0):
                continue
            count += 1
            values = {key: pad.get(key) for key in ("topPasteExpansion", "bottomPasteExpansion")}
            # None/missing uses the project default; it does not suppress paste.
            effective = {key: default_expansion[key] if value is None else value
                         for key, value in values.items()}
            if any(value is None for value in values.values()):
                inherited_pads += 1
            if any(value != 0 for value in effective.values()):
                errors.append({"ref": comp["Designator"], "pin": pad["num"],
                               "footprint": comp["Footprint"], "component_layer": comp["layerId"],
                               "raw_expansion": values, "effective_expansion": effective})
    assert passive and count, "No R/C SMD pads found"
    return {"status": "needs_correction" if errors else "passed",
            "rc_components_checked": len(passive), "rc_smd_pads_checked": count,
            "effective_zero_expansion_pads": count-len(errors),
            "project_default_expansion_mil": default_expansion,
            "pads_inheriting_default": inherited_pads,
            "required_effective_top_and_bottom_paste_expansion_mil": 0,
            "nonzero_or_unresolved_effective_expansion_pads": errors,
            "boundary": "Resolves None/missing R/C footprint-pad expansion through the project default paste rule; does not inspect exported mask geometry or assembly process"}


def check_channels(docs, components):
    parts, _ = netlist(docs)
    pins = collections.defaultdict(list)
    for ref, part in parts.items():
        for pin, value in part["pins"].items():
            if value["net"]:
                pins[value["net"].lower()].append((ref, pin))
    channels, errors = [], []
    shift_outputs = {"15": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
    for channel, comp in enumerate(emitter_components(docs, components)):
        ref = comp["Designator"]
        output = parts[ref]["pins"]["1"]["net"].lower()
        drivers = [(r, p) for r, p in pins[output]
                   if parts[r]["attrs"].get("Manufacturer Part") == "TC4427AVOA713"]
        assert len(drivers) == 1, (ref, "Expected one driver output")
        driver, output_pin = drivers[0]
        input_pin = {"7": "2", "5": "4"}[output_pin]
        input_net = parts[driver]["pins"][input_pin]["net"].lower()
        resistors = {r for r, _ in pins[input_net]
                     if parts[r]["attrs"].get("Manufacturer Part") == "PS03W4F1000T5E"}
        assert len(resistors) == 1, (ref, "Expected one 100 ohm series resistor")
        resistor = next(iter(resistors))
        source_nets = {p["net"].lower() for p in parts[resistor]["pins"].values()
                       if p["net"].lower() != input_net}
        assert len(source_nets) == 1, (resistor, "Unexpected source net")
        source_net = next(iter(source_nets))
        shifts = [(r, p) for r, p in pins[source_net]
                  if parts[r]["attrs"].get("Manufacturer Part") == "SN74LV595ADR"]
        assert len(shifts) == 1, (ref, "Expected one shift-register output")
        shift, shift_pin = shifts[0]
        row, col = divmod(channel, 8)
        lane = parts[shift]["pins"]["14"]["net"]
        bit = shift_outputs[shift_pin]
        if lane != f"DATA{row}" or bit != col:
            errors.append({"channel": channel, "lane": lane, "bit": bit, "expected_row": row, "expected_column": col})
        if parts[ref]["pins"]["2"]["net"].upper() != "GND":
            errors.append({"channel": channel, "kind": "transducer_pin2_not_ground"})
        x, y_down = comp["x"]*MM_PER_MIL, -comp["y"]*MM_PER_MIL
        assert math.isclose(x, 47.45+10.3*col, abs_tol=.0001)
        assert math.isclose(y_down, 29.45+10.3*row, abs_tol=.0001)
        channels.append({"channel": channel, "row_top_down": row, "column_left_right": col,
                         "emitter": ref, "driver": driver, "driver_output_pin": output_pin,
                         "series_resistor": resistor, "shift_register": shift,
                         "data_lane": lane, "output_bit": bit,
                         "pcb_x_mm": round(x, 4), "pcb_y_down_mm": round(y_down, 4),
                         "array_x_mm": round(x-83.5, 4), "array_y_up_mm": round(65.5-y_down, 4)})
    return {"status": "needs_correction" if errors else "passed", "channel_count": len(channels),
            "channels": channels, "errors": errors,
            "coordinate_note": "PCB X right/Y down; acoustic X right/Y up, centre (83.5,65.5) mm. Row 0 acoustic Y=+36.05 mm."}


def run_check(function, *arguments):
    try:
        result = function(*arguments)
        return result if "status" in result else {"status": "passed", **result}
    except (AssertionError, KeyError, ValueError, TypeError, StopIteration) as error:
        return {"status": "needs_correction", "error": str(error), "exception": type(error).__name__}


def audit(path):
    path = Path(path)
    docs = read_project(path)
    pcb = next(d for d in docs if d["head"]["docType"] == "PCB")
    components = entities(pcb, "COMPONENT")
    checks = {}
    try:
        _, _, result = connectivity(docs)
        checks["schematic_pcb_pin_nets"] = {"status": "passed", **result}
    except (AssertionError, KeyError, ValueError, TypeError) as error:
        checks["schematic_pcb_pin_nets"] = {"status": "needs_correction", "error": str(error)}
    checks["nominal_array_and_pour_records"] = run_check(geometry, docs, pcb, components)
    checks["channel_mapping"] = run_check(check_channels, docs, components)
    checks["logical_physical_stack"] = run_check(check_stack, pcb)
    checks["rc_paste_expansion"] = run_check(check_rc_paste, docs, components)
    pending = [name for name, value in checks.items() if value["status"] != "passed"]
    return {"project": path.name, "project_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "status": "needs_correction" if pending else "project_source_checks_passed",
            "checks": checks, "pending_corrections": pending,
            "drill_count_from_cad": len(nominal_holes(docs, pcb)),
            "not_covered": ["Native DRC execution and real copper connectivity", "Assembled component tolerances", "FPGA 8-lane 595 output adaptation", "Measured pressure, temperature or tactile performance"],
            "boundary": "Reads epro2 only; Gerber is neither required nor consulted"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT / PROJECT, help="Project epro2 to read")
    parser.add_argument("--output", type=Path, help="Optional report JSON; never writes the project")
    options = parser.parse_args()
    result = audit(options.project)
    if options.output:
        options.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2)+"\n").encode("utf-8"))
    summary = {"project": result["project"], "project_sha256": result["project_sha256"],
               "status": result["status"], "checks": {name: check["status"] for name, check in result["checks"].items()},
               "pending_corrections": result["pending_corrections"], "drill_count_from_cad": result["drill_count_from_cad"],
               "boundary": result["boundary"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["pending_corrections"] else 0)
