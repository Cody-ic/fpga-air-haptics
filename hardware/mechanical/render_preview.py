"""Render the actual exported meshes with Blender (run with blender -b -P)."""
import bpy
import json
import math
from pathlib import Path
import struct
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parent
P = json.loads((ROOT/'parameters.json').read_text(encoding='utf-8'))
OUT = ROOT/'dist'/P['revision']
ASSEMBLIES = json.loads((OUT/'assemblies.json').read_text(encoding='utf-8'))


def material(name, color, metallic=0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = tuple(int(color[i:i+2], 16)/255 for i in (1,3,5))+(1.,)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = m.diffuse_color
    bsdf.inputs['Roughness'].default_value = .4
    bsdf.inputs['Metallic'].default_value = metallic
    return m


def part(entry, extra_z=0):
    folder = 'reference' if entry['name'].startswith('REF_') else 'stl'
    raw = (OUT/folder/(entry['name']+'.stl')).read_bytes()
    count = struct.unpack_from('<I', raw, 80)[0]
    r = entry['rotation']
    rotation = Matrix([r[0:3],r[3:6],r[6:9]]).transposed()
    location = Vector(entry['translation_mm'])+Vector((0,0,extra_z))
    vertices = []
    for i in range(count):
        v = struct.unpack_from('<9f', raw, 84+50*i+12)
        vertices.extend(tuple((rotation @ Vector(v[j:j+3])+location)*.001) for j in (0,3,6))
    mesh = bpy.data.meshes.new(entry['name'])
    mesh.from_pydata(vertices, [], [(i,i+1,i+2) for i in range(0,len(vertices),3)])
    mesh.update()
    obj = bpy.data.objects.new(entry['name'], mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material(entry['name'], entry['color'], .15 if 'emitters' in entry['name'] else 0))


def cube(name, location, scale, color):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj=bpy.context.object
    obj.name=name
    obj.scale=scale
    obj.data.materials.append(material(name,color))


def render(name, entries, camera, target, scale, exploded=False):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for entry in entries:
        extra = 18 if exploded and 'emitters' in entry['name'] else 50 if exploded and any(
            tag in entry['name'] for tag in ('pcb','resistor')) else 0
        part(entry, extra)
    has_datum=any('datum' in entry['name'] for entry in entries)
    has_feet=any('rubber_foot' in entry['name'] for entry in entries)
    floor_z=-.008 if has_feet else -.006 if has_datum else -.002
    cube('Floor', (0,0,floor_z), (2,2,.002), '#edf1f5')
    bpy.ops.object.camera_add(location=camera)
    cam=bpy.context.object
    cam.rotation_euler=(Vector(target)-cam.location).to_track_quat('-Z','Y').to_euler()
    cam.data.type='ORTHO'
    cam.data.ortho_scale=scale
    scene=bpy.context.scene
    scene.camera=cam
    for i,(location,power,size) in enumerate([((.2,.2,.45),80,.3),((-.25,.1,.2),45,.25),((.1,-.25,.4),70,.25)]):
        bpy.ops.object.light_add(type='AREA', location=location)
        lamp=bpy.context.object
        lamp.data.energy=power/60
        lamp.data.shape='DISK';lamp.data.size=size
        lamp.rotation_euler=(Vector(target)-lamp.location).to_track_quat('-Z','Y').to_euler()
    scene.render.engine='CYCLES'
    scene.cycles.samples=24
    scene.cycles.use_denoising=True
    scene.world.color=(.35,.35,.35)
    scene.render.resolution_x=1100;scene.render.resolution_y=1100
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    scene.view_settings.view_transform='AgX'
    scene.render.filepath=str(OUT/'preview'/(name+'.png'))
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'.runtime'/(name+'.blend')))
    bpy.ops.render.render(write_still=True)


if __name__ == '__main__':
    render('wrist_support', ASSEMBLIES['Wrist_support_assembly'], (.27,.30,.26), (0,-.036,.078), .285)
    render('alignment_exploded', ASSEMBLIES['Array_jig_assembly'], (.17,-.23,.13), (0,-.003,.030), .175, True)
    render('alignment_jig', [entry for entry in ASSEMBLIES['Array_jig_assembly']
                            if 'jig' in entry['name'] or 'datum' in entry['name']],
           (.13,-.18,.18), (0,-.006,.003), .150)
