"""Independent mesh, datum, clearance, unchanged-receiver and native CAD checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import trimesh
import layout

P, OUT = layout.P, layout.OUT
NEW_PRINTABLES = ('08_fixed_riser_30', '09_riser_bridge', '10_film_lower_stand',
                  '11_film_upper_ring', '12_riser_fit_coupon')


def vertical_hits(mesh, x, y):
    tri = mesh.triangles
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    det = (b[:,0]-a[:,0])*(c[:,1]-a[:,1]) - (b[:,1]-a[:,1])*(c[:,0]-a[:,0])
    good = abs(det) > 1e-9
    a, b, c, det = a[good], b[good], c[good], det[good]
    u = ((x-a[:,0])*(c[:,1]-a[:,1]) - (y-a[:,1])*(c[:,0]-a[:,0])) / det
    v = ((b[:,0]-a[:,0])*(y-a[:,1]) - (b[:,1]-a[:,1])*(x-a[:,0])) / det
    inside = (u >= -1e-8) & (v >= -1e-8) & (u+v <= 1+1e-8)
    return np.unique(np.round((a[:,2] + u*(b[:,2]-a[:,2]) + v*(c[:,2]-a[:,2]))[inside], 5))


def mesh_check():
    layout.verify_source()
    results, meshes = [], {}
    for name in NEW_PRINTABLES:
        path = OUT / 'stl' / (name+'.stl')
        meta = json.loads((OUT / (name+'.json')).read_text(encoding='utf-8'))
        m = trimesh.load_mesh(path, process=True)
        assert m.is_watertight and m.is_winding_consistent and m.body_count == 1, name
        assert np.isfinite(m.vertices).all() and not (m.area_faces < 1e-8).any(), name
        assert abs(m.bounds[0,2]) < .001, name
        assert (m.extents < np.array(P['print_bed_mm']) - 10.0 + .001).all(), name
        relative = abs(m.volume-meta['volume_mm3'])/meta['volume_mm3']
        assert relative < .001, (name, relative)
        results.append(dict(name=name, watertight=True, connected_solids=1,
                            winding_consistent=True, size_mm=m.extents.tolist(),
                            cad_stl_volume_relative_error=float(relative)))
        meshes[name] = m
    # The full 10 um sheet fits; clamping screws do not require holes in it.
    fx, fy = np.array(P['film_size_mm'])/2
    wx, wy = np.array(P['film_window_size_mm'])/2
    assert fx-wx >= 5 and fy-wy >= 5
    for x, y in P['film_holes_mm']:
        dx, dy = max(abs(x)-fx, 0), max(abs(y)-fy, 0)
        assert np.hypot(dx, dy) > P['m3_clearance_mm']/2 + 4, (x, y)
    aperture_probes = 0
    contact_probes = 0
    for name in ('10_film_lower_stand', '11_film_upper_ring'):
        m = meshes[name]
        for x in np.linspace(-wx+1.1, wx-1.1, 17):
            for y in np.linspace(-wy+1.1, wy-1.1, 21):
                assert len(vertical_hits(m, x, y)) == 0, (name, x, y)
                aperture_probes += 1
        for x, y in ((wx+2, 0), (-wx-2, 0), (0, wy+2), (0, -wy-2)):
            hits = vertical_hits(m, x, y)
            assert np.allclose(hits, [0, P['film_ring_thickness_mm']], atol=.001), (name, hits)
            contact_probes += 1
    d = layout.datums()
    assert abs(d['receiver_gap_mm'] - 100) < 1e-9
    assert abs(d['film_gap_mm'] - 99.99) < 1e-9
    assert P['riser_channel_width_mm'] - layout.OLD.P['body_plate_thickness_mm'] >= .6-1e-9
    assert P['riser_lip_height_mm'] < P['source_bottom_rail_height_mm']
    # Standard M4x25: head bearing Z=43.8, tip Z=18.8, nut spans 23..26.2.
    head = P['riser_height_mm'] + P['source_bottom_rail_height_mm'] + P['riser_clamp_thickness_mm'] + .8
    nut0, nut1 = P['riser_nut_slot_z_mm']
    assert head-25 < nut0 and nut0+3.2 < nut1 and head-25 > 0
    receiver_checks = []
    for profile in ('tx4', 'tx8'):
        old = layout.OLD.placements(profile)
        old_rx = old[next(i for i,e in enumerate(old) if e['name']=='05_receiver_flat_tray'):]
        new = layout.placements(profile, 'receiver')
        new_rx = new[next(i for i,e in enumerate(new) if e['name']=='05_receiver_flat_tray'):]
        assert new_rx == old_rx, profile
        # Projected outside diameter of all 64/16 emitters fits inside the film window.
        a = layout.OLD.P[profile]
        size = (a['cols']-1)*a['pitch_mm'] + layout.OLD.P['sensor_case_diameter_mm']
        assert size < min(P['film_window_size_mm'])
        vertices = []
        for e in new_rx:
            folder = 'reference' if e['name'].startswith('REF_') else 'stl'
            m = trimesh.load_mesh(OUT/folder/(e['name']+'.stl'))
            vertices.append(m.vertices @ np.array(e['rotation']).reshape(3,3) + np.array(e['translation_mm']))
        v = np.concatenate(vertices)
        left_inner = layout.OLD.P['body_placement_x_mm'][0] + 8 + P['riser_width_mm']/2
        right_inner = layout.OLD.P['body_placement_x_mm'][1] - 8 - P['riser_width_mm']/2
        clearance = min(v[:,0].min()-20-left_inner, right_inner-v[:,0].max()-20)
        assert clearance > 5
        # The entire receiver group stays below the lifted old frame bottom.
        assert v[:,2].max() < P['riser_height_mm']
        receiver_checks.append(dict(profile=profile, receiver_all_component_placements_unchanged=True,
                                    source_electronics_and_cable_keepouts_preserved=True,
                                    array_projected_width_mm=size,
                                    receiver_group_bounds_mm=[v.min(axis=0).tolist(),v.max(axis=0).tolist()],
                                    checked_xy_motion_each_direction_mm=20,
                                    minimum_lateral_clearance_after_motion_mm=float(clearance),
                                    external_receiver_plug_and_cable_not_modeled=True))
    source_files = {}
    for folder in ('solidworks', 'step', 'stl', 'reference'):
        for path in (layout.SOURCE / folder).iterdir():
            if path.name.startswith('~') or path.suffix.lower()=='.sldasm':
                continue
            other = OUT / folder / path.name
            assert other.read_bytes() == path.read_bytes(), path
            source_files[folder+'/'+path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = dict(passed=True, revision=P['revision'], new_printable_meshes=results,
                  datums=d, film_clamping_overlap_each_edge_mm=[fx-wx, fy-wy],
                  film_hole_piercing_required=False, aperture_probes=aperture_probes,
                  clamp_contact_probes=contact_probes, receiver_checks=receiver_checks,
                  source_parts_sha256=source_files, riser_nominal_old_rail_side_clearance_mm=.3,
                  verified_real_print_fit=False, load_tested=False, acoustic_performance_verified=False)
    (OUT/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(passed=True, new_meshes=len(results), aperture_probes=aperture_probes,
                          unchanged_source_files=len(source_files), datums=d)), flush=True)


def native_check():
    import build_addons as build
    import build_solidworks as sw
    from check_solidworks import open_doc, inspect_features
    build.initialize()
    results = []
    for path in sorted((OUT/'solidworks').glob('*.SLDPRT')):
        if path.name.startswith('~'):
            continue
        doc = open_doc(path)
        doc.ForceRebuild3(False)
        errors, sketches = inspect_features(doc)
        expected = json.loads((OUT/(path.stem+'.json')).read_text(encoding='utf-8'))
        assert not errors and len(sketches)==len(expected['sketches']), path.name
        bodies = sw.typed('IPartDoc', doc).GetBodies2(0, False)
        assert len(bodies)==expected['solid_count'], path.name
        results.append(dict(file=path.name, feature_errors=errors, sketches=sketches, reopened=True))
        sw.APP.CloseDoc(doc.GetTitle())
        print('CHECKED '+path.name, flush=True)
    assemblies = json.loads((OUT/'assemblies.json').read_text(encoding='utf-8'))
    for path in sorted((OUT/'solidworks').glob('*.SLDASM')):
        doc = open_doc(path)
        doc.ForceRebuild3(False)
        assy = sw.typed('IAssemblyDoc', doc)
        components = assy.GetComponents(False)
        expected = assemblies[path.stem]
        assert len(components)==len(expected), path.name
        paths = [Path(sw.typed('IComponent2', c).GetPathName()).resolve() for c in components]
        assert all(p.parent==(OUT/'solidworks').resolve() for p in paths), path.name
        assert sorted(p.stem for p in paths)==sorted(e['name'] for e in expected)
        actual = sorted(tuple(sw.typed('IMathTransform',sw.typed('IComponent2',c).Transform2).ArrayData) for c in components)
        matrices = sorted(tuple(e['rotation'] + [v/1000 for v in e['translation_mm']] + [1.,0.,0.,0.]) for e in expected)
        assert np.allclose(actual, matrices, atol=1e-10), path.name
        manager = sw.typed('IInterferenceDetectionMgr', assy.InterferenceDetectionManager)
        manager.TreatCoincidenceAsInterference = False
        manager.IncludeMultibodyPartInterferences = False
        clashes = []
        for raw in manager.GetInterferences() or []:
            it = sw.typed('IInterference', raw)
            clashes.append(dict(components=[sw.typed('IComponent2',c).Name2 for c in it.Components or []],
                                volume_mm3=it.Volume*1e9))
        manager.Done()
        results.append(dict(file=path.name, reopened=True, component_count=len(components),
                            local_references_verified=True, transforms_verified=True,
                            modeled_interferences=clashes))
        sw.APP.CloseDoc(doc.GetTitle())
        print('CHECKED '+path.name+' clashes='+str(len(clashes)), flush=True)
    failures = [r for r in results if r.get('modeled_interferences')]
    result = dict(passed=not failures, revision=P['revision'], documents=results,
                  scope='Native sketches fully defined with dimensions and relations, no Fix relations; exact transformed solid clashes including original PCB envelopes, standard fasteners and 10um film reference.',
                  printed_or_load_tested=False, acoustic_performance_verified=False)
    (OUT/'native_validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not failures, failures
    print(json.dumps(dict(passed=True, documents=len(results), sketches=sum(len(r.get('sketches',[])) for r in results))), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    args = parser.parse_args()
    if args.native:
        native_check()
    else:
        mesh_check()
