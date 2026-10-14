"""Extract assembly and channel tables from the released CAD; no CAD edits."""
import collections
import csv
import json
import re

from audit_source import netlist
from verify_release import ROOT, PROJECT, connectivity, read_project


def ref_order(ref):
    prefix, number = re.fullmatch(r"([A-Z]+)(\d+)", ref).groups()
    return prefix, int(number)


def main():
    docs = read_project(ROOT / PROJECT)
    _, components, _ = connectivity(docs)
    parts, _ = netlist(docs)
    footprints = {d["head"]["uuid"]: next(r["v"]["title"] for r in d["items"]
                  if r["h"]["type"] == "META")
                  for d in docs if d["head"]["docType"] == "FOOTPRINT"}
    bom = collections.defaultdict(list)
    with (ROOT / "Positions.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Designator", "X_mm", "Y_down_mm", "CAD_angle_deg", "Side", "Footprint"])
        for c in sorted(components.values(), key=lambda c: ref_order(c["Designator"])):
            ref = c["Designator"]
            a = parts[ref]["attrs"]
            footprint = footprints[c["Footprint"]]
            bom[a.get("Manufacturer Part", ""), a.get("Value", ""),
                a.get("Supplier Part", ""), footprint].append(ref)
            writer.writerow([ref, round(c["x"] * .0254, 4), round(-c["y"] * .0254, 4),
                             c["angle"], "Top" if c["layerId"] == 1 else "Bottom", footprint])
    with (ROOT / "BOM.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Manufacturer_Part", "Value", "LCSC_Part", "Footprint", "Quantity", "Designators"])
        for (mpn, value, supplier, footprint), refs in sorted(bom.items()):
            writer.writerow([mpn, value, supplier, footprint, len(refs), ",".join(refs)])
    pins = collections.defaultdict(list)
    for ref, part in parts.items():
        for number, p in part["pins"].items():
            if p["net"]:
                pins[p["net"].lower()].append((ref, number))
    emitters = sorted((c for c in components.values() if c["layerId"] == 1),
                      key=lambda c: (-c["y"], c["x"]))
    channels = []
    for index, c in enumerate(emitters):
        ref = c["Designator"]
        output = parts[ref]["pins"]["1"]["net"].lower()
        driver, outpin = next((r, p) for r, p in pins[output]
                              if parts[r]["attrs"].get("Manufacturer Part") == "TC4427AVOA713")
        inpin = {"7": "2", "5": "4"}[outpin]
        innet = parts[driver]["pins"][inpin]["net"].lower()
        resistor = next(r for r, _ in pins[innet]
                        if parts[r]["attrs"].get("Manufacturer Part") == "PS03W4F1000T5E")
        channel_net = next(p["net"] for p in parts[resistor]["pins"].values()
                           if p["net"].lower() != innet)
        shift, shiftpin = next((r, p) for r, p in pins[channel_net.lower()]
                               if parts[r]["attrs"].get("Manufacturer Part") == "SN74LV595ADR")
        bit = {"15": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}[shiftpin]
        channels.append({"channel": index, "row_top_down": index // 8, "column_left_right": index % 8,
                         "emitter": ref, "pcb_x_mm": round(c["x"] * .0254, 3),
                         "pcb_y_down_mm": round(-c["y"] * .0254, 3),
                         "array_x_mm": round(c["x"] * .0254 - 83.5, 3),
                         "array_y_up_mm": round(c["y"] * .0254 + 65.5, 3),
                         "array_z_mm": 0, "driver": driver, "driver_output_pin": outpin,
                         "series_resistor": resistor, "shift_register": shift,
                         "data_lane": parts[shift]["pins"]["14"]["net"], "output_bit": bit})
    assert all(c["data_lane"] == f'DATA{c["row_top_down"]}' and c["output_bit"] == c["column_left_right"]
               for c in channels)
    mapping = {"version": 1, "units": "mm", "rows": 8, "columns": 8, "pitch_mm": 13,
               "coordinate_note": "Top view; PCB origin top-left; array origin centre. Nominal emission plane z=0, no height calibration.",
               "serial_order": "Send bit7 first, bit0 last on each DATA lane; then latch all lanes together.",
               "channels": channels}
    (ROOT / "transducer_map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(parts)} positions, {len(bom)} BOM groups and {len(channels)} channels")


if __name__ == "__main__":
    main()
