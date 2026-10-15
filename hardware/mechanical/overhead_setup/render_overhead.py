"""Render the exported P5 STL files with Blender; no replacement CAD geometry.

Example: blender --background -t 4 --python render_overhead.py -- --only overhead_8x8
Electronics are the CAD's nominal/assumed reference envelopes. Amber transparent
geometry is reserved cable space, not a printed cover or a detailed cable model.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import layout

OUT = layout.OUT


def material(name, color, metallic=0.0, alpha=1.0):
    existing = bpy.data.materials.get(name)
    if existing:
        return existing
    mat = bpy.data.materials.new(name)
    srgb = tuple(int(color[i:i+2], 16) / 255 for i in (1, 3, 5))
    rgb = tuple(c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in srgb)
    mat.diffuse_color = (*rgb, alpha)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*rgb, 1.0)
    bsdf.inputs['Roughness'].default_value = .38
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['Alpha'].default_value = alpha
    return mat


def read_stl(path):
    raw = path.read_bytes()
    count = struct.unpack_from('<I', raw, 80)[0]
    if len(raw) != 84 + 50*count:
        raise ValueError('Expected binary STL: ' + str(path))
    vertices, triangles, lookup = [], [], {}
    for i in range(count):
        coords = struct.unpack_from('<9f', raw, 96 + 50*i)
        face = []
        for k in (0, 3, 6):
            key = tuple(round(c, 5) for c in coords[k:k+3])
            if key not in lookup:
                lookup[key] = len(vertices)
                vertices.append(coords[k:k+3])
            face.append(lookup[key])
        triangles.append(tuple(face))
    return vertices, triangles


def components(vertices, triangles):
    """Separate exported reference bodies without altering their triangle mesh."""
    parent = list(range(len(vertices)))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, c in triangles:
        a = find(a)
        parent[find(b)] = a
        parent[find(c)] = a
    groups = defaultdict(list)
    for face in triangles:
        groups[find(face[0])].append(face)
    return groups.values()


def reference_material(points, profile):
    low = [min(v[i] for v in points) for i in range(3)]
    high = [max(v[i] for v in points) for i in range(3)]
    span = [high[i]-low[i] for i in range(3)]
    if span[2] > 35:
        return material('Reserved cable volume', '#dba14c', alpha=.10)
    if span[0] > 45 and span[1] > 25 and span[2] < 1.7:
        return material('PCB reference', '#247c5c')
    if high[2] > 6 and abs(span[2] - 7.1) < .05:
        return material('Nominal acoustic case', '#b7bcc1', .65)
    # The terminal's exact plastic shape is not modelled: color its envelope.
    center = [(low[i]+high[i])/2 for i in range(3)]
    cx, cy = ((0, 0) if profile == 'receiver'
              else layout.P[profile]['array_center_top_left_mm'])
    for part in layout.BOARDS[profile]['parts']:
        if 'XT30' in part.get('model_title', ''):
            x, y = part['center_xy_mm']
            if abs(center[0]-(x-cx)) < 1 and abs(center[1]-(cy-y)) < 1:
                return material('Power connector envelope', '#e3b92f')
    return material('Component and test-pin envelopes', '#454b51', .1)


def add_part(entry, offset_z=0):
    stem = entry['name']
    folder = 'reference' if stem.startswith('REF_') else 'stl'
    path = OUT / folder / (stem+'.STL')
    if not path.exists():
        path = path.with_suffix('.stl')
    if not path.exists():
        raise FileNotFoundError(path)
    vertices, faces = read_stl(path)
    r = entry['rotation']
    rotation = Matrix([r[:3], r[3:6], r[6:9]]).transposed()
    location = Vector(entry['translation_mm']) + Vector((0, 0, offset_z))
    transformed = [tuple((rotation @ Vector(v)+location)*.001) for v in vertices]
    mesh = bpy.data.meshes.new(stem)
    mesh.from_pydata(transformed, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(stem, mesh)
    bpy.context.collection.objects.link(obj)
    if stem.endswith('_electronics'):
        profile = stem[4:-12]
        face_indices = {face: index for index, face in enumerate(faces)}
        for group in components(vertices, faces):
            points = [vertices[i] for i in {v for f in group for v in f}]
            mat = reference_material(points, profile)
            if mat.name not in mesh.materials:
                mesh.materials.append(mat)
            index = list(mesh.materials).index(mat)
            for face in group:
                mesh.polygons[face_indices[face]].material_index = index
    else:
        metal = stem.startswith('REF_M')
        mat = material('Standard metal hardware' if metal else stem,
                       '#b1b8bf' if metal else entry['color'], .7 if metal else 0)
        mesh.materials.append(mat)
    return obj


def add_floor():
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -.0005))
    bpy.context.object.name = 'Unconnected table background'
    bpy.context.object.data.materials.append(material('Table background', '#e7ebef'))


def setup_scene(camera, target, scale):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    # Leave CPU capacity for SOLIDWORKS and the interactive desktop.
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 4
    scene.render.resolution_x = 1500
    scene.render.resolution_y = 1250
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (.70, .73, .78, 1)
    scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = .35
    scene.view_settings.view_transform = 'AgX'
    bpy.ops.object.camera_add(location=camera)
    cam = bpy.context.object
    cam.rotation_euler = (Vector(target)-cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.type = 'ORTHO'
    cam.data.ortho_scale = scale
    cam.data.lens = 50
    scene.camera = cam
    for position, power, size in [((.15, -.2, .42), 1.5, .3),
                                  ((-.25, -.1, .23), .75, .25),
                                  ((.1, .3, .3), 1.2, .25)]:
        bpy.ops.object.light_add(type='AREA', location=position)
        lamp = bpy.context.object
        lamp.data.energy = power
        lamp.data.shape = 'DISK'
        lamp.data.size = size
        lamp.rotation_euler = (Vector(target)-lamp.location).to_track_quat('-Z', 'Y').to_euler()
    return scene


def render(name, profile, receiver_only=False, exploded=False, underside=False):
    entries = layout.placements(profile)
    if receiver_only:
        start = next(i for i, e in enumerate(entries) if e['name'] == '05_receiver_flat_tray')
        entries = entries[start:]
        camera, target, scale = ((.13, -.17, .09), (.005, .008, .018), .105)
    else:
        camera, target, scale = ((.33, -.25, .21), (-.01, 0, .067), .315)
    if exploded:
        camera, target, scale = ((.15, -.19, .095), (.005, .008, .027), .12)
    if underside:
        camera, target, scale = ((.42, -.12, -.10), (-.01, 0, .067), .315)
    scene = setup_scene(camera, target, scale)
    sources = []
    for entry in entries:
        z = 0
        if exploded:
            if entry['name'] == 'REF_receiver_electronics':
                z = 20
            elif entry['name'] != '05_receiver_flat_tray':
                z = 32
        add_part(entry, z)
        folder = 'reference' if entry['name'].startswith('REF_') else 'stl'
        source = OUT / folder / (entry['name']+'.STL')
        if not source.exists():
            source = source.with_suffix('.stl')
        sources.append(dict(entry, exploded_offset_z_mm=z,
                            source=str(source.relative_to(OUT)),
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    if not underside:
        add_floor()
    else:
        # Looking upward through the stand: an infinite table plane would
        # obscure the lower half of the orthographic view. Use the world
        # background and a soft inspection light below the acoustic cases.
        bpy.ops.object.light_add(type='AREA', location=(.15, 0, .03))
        lamp = bpy.context.object
        lamp.data.energy = .4
        lamp.data.size = .15
        lamp.rotation_euler = (Vector((0, 0, .095))-lamp.location).to_track_quat('-Z', 'Y').to_euler()
    preview = OUT / 'preview'
    preview.mkdir(exist_ok=True, parents=True)
    scene.render.filepath = str(preview / (name+'.png'))
    bpy.ops.render.render(write_still=True)
    (preview / (name+'.json')).write_text(json.dumps({
        'renderer': 'Blender '+bpy.app.version_string,
        'engine': scene.render.engine,
        'profile': profile,
        'exploded_view': exploded,
        'notice': 'Actual exported STL geometry; electronic body heights and cable volumes are nominal/assumed CAD references. Colors are illustrative.',
        'sources': sources,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RENDERED', scene.render.filepath)


def main():
    scenes = {
        'overhead_8x8': ('tx8', False, False),
        'overhead_4x4': ('tx4', False, False),
        'overhead_8x8_underside': ('tx8', False, False, True),
        'receiver_tray_detail': ('tx8', True, False),
        'receiver_tray_exploded': ('tx8', True, True),
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=scenes)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    for name, spec in scenes.items():
        if args.only is None or args.only == name:
            render(name, *spec)


if __name__ == '__main__':
    main()
