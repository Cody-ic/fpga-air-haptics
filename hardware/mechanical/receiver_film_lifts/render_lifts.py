"""Render actual exported meshes with depth testing; no substitute geometry."""
import importlib.util
from pathlib import Path
import sys

import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT/"dist/P5-LIFTS20-20261025"
sys.path.insert(0, str(ROOT/"overhead_setup"))
spec = importlib.util.spec_from_file_location("lift_renderer", ROOT/"overhead_setup/render_overhead.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
OLD = ROOT/"dist/P5-20261015"
ADDON = ROOT/"dist/P5-ADDON-20261022"
IDENTITY = [1.,0.,0.,0.,1.,0.,0.,0.,1.]
FLIP = [1.,0.,0.,0.,-1.,0.,0.,0.,-1.]


def add(directory, name, xyz, color, rotation=IDENTITY):
    r.OUT = directory
    return r.add_part(dict(name=name, translation_mm=xyz, rotation=rotation, color=color))


def render(film, assembled):
    camera = (.17,-.20,.17) if film else (.11,-.14,.13)
    scene = r.setup_scene(camera, (0,0,.025 if assembled else .01), .215 if film else .128)
    scene.render.resolution_x = 1100
    scene.render.resolution_y = 850
    scene.render.threads = 4
    name = "14_film_stand_lift20" if film else "13_receiver_tray_lift20"
    add(OUT, name, [0,0,0], "#6087a4")
    if assembled and film:
        add(ADDON, "10_film_lower_stand", [0,0,44.7], "#c49458", FLIP)
        add(ADDON, "11_film_upper_ring", [0,0,44.71], "#dfbb82")
        obj = add(ADDON, "REF_film_4x5in_10um", [0,0,44.7], "#83cfda")
        obj.data.materials.clear()
        obj.data.materials.append(r.material("Film, not simulated deformation", "#83cfda", alpha=.30))
    elif assembled:
        # Reuse every receiver component's original placement, translating the
        # whole receiver assembly by Z=20 and recentering only for this preview.
        original = r.layout.placements("tx8")
        original = original[next(i for i,v in enumerate(original) if v["name"] == "05_receiver_flat_tray"):]
        ox, oy, _ = r.layout.receiver_origin()
        height = round(r.layout.BOARDS["receiver"]["board_size_mm"][1],3)
        for entry in original:
            x,y,z = entry["translation_mm"]
            add(OLD, entry["name"], [x-ox-30,y-oy+height/2,z+20], entry["color"], entry["rotation"])
    r.add_floor()
    scene.render.filepath = str(OUT/"preview"/(name+("_assembled" if assembled else "_bare")+".png"))
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    for assembled in (False, True):
        for film in (False, True):
            render(film, assembled)
