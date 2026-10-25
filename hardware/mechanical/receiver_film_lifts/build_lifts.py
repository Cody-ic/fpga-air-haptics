"""Two fixed 20 mm lifts under existing receiver tray / four-foot film stand."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import pythoncom
import win32com.client as wc

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import build_solidworks as sw
import assemble_solidworks as assembly
import check_solidworks as native


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


layout = module("lift_source_layout", ROOT/"overhead_addons/layout.py")
P = json.loads((HERE/"parameters.json").read_text(encoding="utf-8"))
OUT = ROOT/"dist"/P["revision"]
RELEASE = ROOT/"P5_lifts20_20261025"
NAMES = ("13_receiver_tray_lift20", "14_film_stand_lift20")


def dimensions():
    # Read actual legacy parts' dimensions, keeping the receiver on its tray.
    h = round(layout.OLD.BOARDS["receiver"]["board_size_mm"][1], 3)
    c, wall = P["fit_clearance_per_side_mm"], P["locating_wall_mm"]
    rx = (76., h+20.)
    film = tuple(layout.P["film_frame_size_mm"])
    return dict(receiver_tray_mm=rx, film_stand_mm=film,
                receiver_outer_mm=tuple(v+2*c+2*wall for v in rx),
                film_outer_mm=tuple(v+2*c+2*wall for v in film),
                receiver_pocket_mm=tuple(v+2*c for v in rx),
                film_pocket_mm=tuple(v+2*c for v in film),
                film_opening_mm=tuple(v+2*P["film_aperture_extra_per_side_mm"]
                                      for v in layout.P["film_window_size_mm"]))


def initialize():
    for filename in ("sldworks", "swconst"):
        library = pythoncom.LoadTypeLib(str(sw.SW_PATH/(filename+".tlb")))
        attrs = library.GetLibAttr()
        generated = wc.gencache.EnsureModule(str(attrs[0]), attrs[1], attrs[3], attrs[4])
        if filename == "sldworks":
            sw.M = generated
    sw.APP = sw.typed("ISldWorks", wc.Dispatch("SldWorks.Application"))
    sw.OUT = OUT
    for folder in ("solidworks", "step", "stl", "reference", "preview"):
        (OUT/folder).mkdir(parents=True, exist_ok=True)


def outline(part, size, chamfer):
    x, y = (value/2 for value in size)
    part.polygon(sw.chamfer_rectangle(-x, -y, x, y, chamfer))


def build(film=False):
    d = dimensions()
    name = NAMES[int(film)]
    outer = d["film_outer_mm" if film else "receiver_outer_mm"]
    pocket = d["film_pocket_mm" if film else "receiver_pocket_mm"]
    height = P["lift_mm"]
    part = sw.Part(name, "Fixed 20 mm bearing plane under existing " +
                   ("film stand; open center, low outer lip" if film else
                    "receiver tray; PCB and wiring remain on the old tray"))
    part.begin()
    outline(part, outer, 3.)
    if film:
        outline(part, d["film_opening_mm"], 1.)
    part.end("Support_plane_exactly_20mm_above_table", 0, height)
    part.begin()
    outline(part, outer, 3.)
    outline(part, pocket, 2. if film else 3.)
    part.end("Low_locating_lip_0p4mm_clearance_each_side", height, height+P["locating_lip_height_mm"])
    return part.finish()


def assemblies():
    source = layout.SOURCE
    addon = layout.OUT
    refs = {"05_receiver_flat_tray": source, "10_film_lower_stand": addon}
    sources = {}
    for name, directory in refs.items():
        for folder, suffix in (("solidworks", ".SLDPRT"), ("stl", ".stl")):
            path = directory/folder/(name+suffix)
            destination = OUT/folder/path.name
            shutil.copy2(path, destination)
            sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    h = dimensions()["receiver_tray_mm"][1]-20
    ox, oy, _ = layout.OLD.receiver_origin()
    item = assembly.item
    group = {
        "Receiver_tray_on_20mm_lift": [
            item(NAMES[0], [ox+30, oy-h/2, 0], color="#66849a"),
            item("05_receiver_flat_tray", [ox, oy, P["lift_mm"]], color="#d2a16a")],
        "Film_stand_on_20mm_lift": [
            item(NAMES[1], [0, 0, 0], color="#66849a"),
            item("10_film_lower_stand", [0, 0, layout.datums()["film_bottom_z_mm"]+P["lift_mm"]],
                 layout.OLD.FLIP_X, color="#d2a16a")]
    }
    for name, items in group.items():
        assembly.make_assembly(name, items)
    (OUT/"assemblies.json").write_text(json.dumps(group, indent=2), encoding="utf-8")
    return sources


def check_native():
    results = []
    for name in NAMES:
        doc = native.open_doc(OUT/"solidworks"/(name+".SLDPRT"))
        try:
            errors, sketches = native.inspect_features(doc)
            assert not errors and len(sketches) == 2
            results.append(dict(name=name, errors=errors, sketches=sketches))
        finally:
            sw.APP.CloseDoc(doc.GetTitle())
    checks = []
    for name in ("Receiver_tray_on_20mm_lift", "Film_stand_on_20mm_lift"):
        doc = native.open_doc(OUT/"solidworks"/(name+".SLDASM"))
        try:
            model = sw.typed("IAssemblyDoc", doc)
            manager = sw.typed("IInterferenceDetectionMgr", model.InterferenceDetectionManager)
            manager.TreatCoincidenceAsInterference = False
            manager.IncludeMultibodyPartInterferences = False
            count = len(manager.GetInterferences() or [])
            manager.Done()
            assert count == 0, (name, count)
            checks.append(dict(name=name, modeled_interferences=count))
        finally:
            sw.APP.CloseDoc(doc.GetTitle())
    result = dict(parts=results, assemblies=checks, all_sketches_fully_defined=True,
                  no_fixed_sketch_relations=True, passed=True)
    (OUT/"native_validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    initialize()
    saved = []
    try:
        for kind, values in (("IntegerValue", {211:0, 78:3}),
                             ("DoubleValue", {2:.000005, 3:math.radians(2)}),
                             ("Toggle", {69:True, 70:False, 71:True, 191:False})):
            for key, value in values.items():
                saved.append((kind, key, getattr(sw.APP, "GetUserPreference"+kind)(key)))
                getattr(sw.APP, "SetUserPreference"+kind)(key, value)
        build(False)
        build(True)
        sources = assemblies()
        check_native()
        for path, checksum in sources.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == checksum
        manifest = dict(revision=P["revision"], dimensions=dimensions(),
                        source_parts_unchanged={str(Path(path).relative_to(ROOT/"dist")): checksum
                                                for path, checksum in sources.items()},
                        support_lift_mm=P["lift_mm"], total_height_mm=P["lift_mm"]+P["locating_lip_height_mm"],
                        receiver_face_z_mm=layout.datums()["receiver_face_z_mm"]+P["lift_mm"],
                        film_bottom_z_mm=layout.datums()["film_bottom_z_mm"]+P["lift_mm"],
                        transmitter_face_z_mm=layout.datums()["tx_face_z_mm"],
                        acoustic_gap_with_tx_risers_mm=layout.datums()["receiver_gap_mm"]-P["lift_mm"],
                        physical_fit_not_yet_tested=True)
        (OUT/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for kind, key, value in saved:
            getattr(sw.APP, "SetUserPreference"+kind)(key, value)


if __name__ == "__main__":
    main()
