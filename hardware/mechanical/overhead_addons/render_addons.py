"""Preview actual exported meshes, keeping the receiver and film interchangeable."""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import bpy
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import layout
spec = importlib.util.spec_from_file_location('p5_renderer', layout.ROOT/'overhead_setup/render_overhead.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
r.OUT = layout.OUT
r.layout = layout.OLD


def render(name, profile='tx8', target='receiver'):
    entries = layout.placements(profile, target)
    exploded = name=='film_frame_exploded'
    if name.startswith('film_frame'):
        entries = entries[next(i for i,e in enumerate(entries) if e['name']=='10_film_lower_stand'):]
        scene = r.setup_scene((.20,-.24,.19), (0,0,.03 if not exploded else .045), .215)
    elif name=='riser_attachment':
        x = -110
        entries = [e for e in entries if e['name'] in ('01_side_trestle','08_fixed_riser_30','09_riser_bridge','REF_M4x25_screw','REF_M4_plain_nut','REF_M4_washer')
                   and e['translation_mm'][0]<-65
                   and (e['name']!='REF_M4_washer' or e['translation_mm'][2]<50)]
        scene = r.setup_scene((-.30,-.25,.18), (x/1000,0,.060), .235)
    else:
        scene = r.setup_scene((.37,-.31,.27), (-.01,0,.080), .355)
    sources = []
    for entry in entries:
        e = dict(entry, translation_mm=list(entry['translation_mm']))
        dz = 0
        if exploded:
            if e['name']=='REF_film_4x5in_10um': dz=18
            elif e['name']!='10_film_lower_stand': dz=36
        obj = r.add_part(e, dz)
        if e['name']=='REF_film_4x5in_10um':
            obj.data.materials.clear()
            obj.data.materials.append(r.material('Thin film reference, not predicted deformation', '#8ed6e8', alpha=.30))
        folder = 'reference' if e['name'].startswith('REF_') else 'stl'
        path = layout.OUT/folder/(e['name']+'.stl')
        sources.append(dict(e, exploded_offset_z_mm=dz, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    r.add_floor()
    scene.render.filepath = str(layout.OUT/'preview'/(name+'.png'))
    bpy.ops.render.render(write_still=True)
    (layout.OUT/'preview'/(name+'.json')).write_text(json.dumps(dict(
        renderer='Blender '+bpy.app.version_string, sources=sources,
        notice='Actual CAD meshes; electronics and wires remain nominal reference envelopes. Flat sheet is not a simulated deformation.'),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    scenes = dict(overhead_receiver_100mm=('tx8','receiver'), overhead_film_100mm=('tx8','film'),
                  overhead_4x4_film_100mm=('tx4','film'), film_frame_detail=('tx8','film'),
                  film_frame_exploded=('tx8','film'), riser_attachment=('tx8','receiver'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=scenes)
    a = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    for name, (profile,target) in scenes.items():
        if not a.only or a.only==name:
            render(name, profile, target)
