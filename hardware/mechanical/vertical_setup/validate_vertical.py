"""Independent STL checks, source mounting pattern and common-reference audit."""
import json
from pathlib import Path
import sys

import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
import validate_models as mesh

P=json.loads((HERE/'parameters.json').read_text(encoding='utf-8'))
OUT=ROOT/'dist'/P['revision']
mesh.P=P; mesh.OUT=OUT


def transform_point(entry, point):
    rotation=np.array(entry['rotation']).reshape(3,3).T
    return rotation@np.array(point)+entry['translation_mm']


def main():
    results=[mesh.check_mesh(f) for folder in ['stl','reference'] for f in sorted((OUT/folder).glob('*.stl'))]
    assert len(results)==28
    assemblies=json.loads((OUT/'assemblies.json').read_text(encoding='utf-8'))
    l=json.loads((OUT/'vertical_layout.json').read_text(encoding='utf-8'))
    r=assemblies['Vertical_receiver_assembly']
    sensor=next(e for e in r if e['name']=='REF_receiver_U1')
    actual=transform_point(sensor,[0,0,7.1])
    assert np.allclose(actual,l['palm_reference_mm']),actual
    wrist=np.array(l['wrist_pad_contact_reference_mm'])
    assert np.allclose(actual-wrist,[0,P['palm_forward_from_wrist_mm'],0])
    arm=next(e for e in r if e['name']=='09_fixed_receiver_arm')
    tray=next(e for e in r if e['name']=='10_receiver_edge_tray')
    tray_mesh=mesh.triangles(OUT/'stl/10_receiver_edge_tray.stl')
    rx0,ry0,rx1,ry1=P['receiver']['pcb_bounds_from_U1_mm']
    clip_entries=[e for e in r if e['name']=='11_receiver_M3_edge_clip']
    assert len(clip_entries)==3
    clip_points=[(-10,ry1+4),(10,ry1+4),(-15,ry0-4)]
    for x,y in clip_points:
        assert not len(mesh.vertical_hits(tray_mesh,x,y)),(x,y)
        assert any(np.allclose(transform_point(e,[0,0,0]),
                               transform_point(tray,[x,y,P['receiver']['pcb_back_offset_mm']+P['pcb_thickness_assumed_mm']]))
                   for e in clip_entries),(x,y)
    for x in [rx0-1.5,rx1+1.5]:
        assert np.allclose(mesh.vertical_hits(tray_mesh,x,-3),
                           [0,P['receiver']['pcb_back_offset_mm']+P['pcb_thickness_assumed_mm']+.8])
    for x in P['receiver']['arm_holes_x_mm']:
        assert np.allclose(transform_point(arm,[x,4-7-P['wrist_pad_assumed_mm'],l['receiver_arm_length_mm']]),
                           transform_point(tray,[x,4,0]))
    # Dimensions are distinct: wrist contact does not serve as the receiver datum.
    assert np.linalg.norm(actual-wrist)>50
    frame_top=P['tx_frame_origin_mm'][2]+46
    assert actual[2]-frame_top>=8, 'Vertical frame must stay below nominal horizontal palm'
    # Explicit holes: M4 mount pitch matches both swappable cartridges.
    for assembly_name,part_name in [('Vertical_receiver_assembly','09_fixed_receiver_arm'),
                                   ('Vertical_wrist_assembly','04_wrist_cradle')]:
        e=next(e for e in assemblies[assembly_name] if e['name']==part_name)
        for x in [-55,55]:
            assert np.allclose(transform_point(e,[x,8,0]),[x,-95,P['wrist_bolt_height_mm']])
    tx_frame=mesh.triangles(OUT/'stl/07_vertical_tx_frame.stl')
    base_mesh=mesh.triangles(OUT/'stl/03_fixed_base.stl')
    shoe_mesh=mesh.triangles(OUT/'stl/08_tx_fixed_shoe.stl')
    for x in [-40,40]:
        shoe_entry=next(e for e in r if e['name']=='08_tx_fixed_shoe' and e['translation_mm'][0]==x)
        for y in P['tx_shoe_bolt_y_mm']:
            world=transform_point(shoe_entry,[0,y,0])
            assert not len(mesh.vertical_hits(base_mesh,*world[:2]))
            assert not len(mesh.vertical_hits(shoe_mesh,0,y))
            assert np.allclose(mesh.vertical_hits(base_mesh,world[0]+3.2,world[1]),[0,6])
            assert np.allclose(mesh.vertical_hits(shoe_mesh,3.2,y),[0,6])
        assert not len(mesh.vertical_hits(tx_frame,x,17-P['tx_frame_origin_mm'][2]))
    assert not len(mesh.vertical_hits(shoe_mesh[:,:,[0,2,1]],0,11))
    for x,y in P['mount_centers_mm']:
        assert not len(mesh.vertical_hits(tx_frame,x,y)),(x,y)
        assert np.allclose(mesh.vertical_hits(tx_frame,x,y-2.5),[0,8]),(x,y)
    window=P['tx_rear_window_mm']
    keepouts=[[-35.274,-27.01,-22.774,-14.51],P['tx_wired_connector_keepout_mm']]
    for x0,y0,x1,y1 in keepouts:
        assert window[0]<x0<x1<window[2] and y1<window[3]
        for x in np.linspace(x0,x1,5):
            for y in np.linspace(y0,y1,5): assert not len(mesh.vertical_hits(tx_frame,x,y))
    # Use the actual component transforms and head vertices to audit desk clearance.
    for assembly_name in ['Vertical_wrist_assembly','Vertical_receiver_assembly']:
        screws=[e for e in assemblies[assembly_name] if e['name']=='REF_M4x20_screw']
        assert len(screws)==4
        points=mesh.triangles(OUT/'reference/REF_M4x20_screw.stl').reshape(-1,3)
        lowest=min(transform_point(e,p)[2] for e in screws for p in points)
        assert lowest>=-4.8001
        feet=[e for e in assemblies[assembly_name] if e['name']=='REF_rubber_foot_16x6']
        assert len(feet)==4 and all(e['translation_mm'][2]==0 for e in feet)
        assert lowest-(-6)>=1.1999
    # M4x20 through base+shoe, two 0.8 washers and a 5 mm DIN 985 nut.
    assert 20-(12+2*.8+5)>=2*.7-1e-8
    # Straight rear entry: conservative Ø4 mm envelope for a 3 mm hex key.
    # Entry Z=17 (+/-2) stays below the wired keepout; drive faces towards +Y.
    wired_min_z=P['tx_frame_origin_mm'][2]+P['tx_wired_connector_keepout_mm'][1]
    assert wired_min_z-(17+2)>5
    cross_screws=[e for e in r if e['name']=='REF_M4x25_screw']
    assert len(cross_screws)==2 and all(e['rotation'][6:9]==[0.,-1.,0.] for e in cross_screws)
    assert min(e['translation_mm'][1]+4 for e in cross_screws)>P['tx_frame_origin_mm'][1]+8
    report=dict(passed=True,meshes=results,pcb_mount_pattern_verified=mesh.source_pattern(),
                common_datum=mesh.check_datum_and_relief(),
                receiver_front_matches_palm_reference=True,
                wrist_to_palm_reference_mm=(actual-wrist).tolist(),
                common_M4_cartridge_mount_verified=True,arm_tray_M3_axes_verified=True,
                base_shoe_M4_axes_verified=True,frame_shoe_M4_axes_verified=True,
                rear_cross_screw_drive_entry_verified=True,
                receiver_three_clips_verified=True,receiver_side_stops_verified=True,
                base_M4x20_thread_protrusion_mm=1.4,
                nominal_screw_head_to_table_gap_mm=1.2,
                tx_rear_open_window_verified=True,vertical_layout=l,
                wired_connector_keepout_mm=P['tx_wired_connector_keepout_mm'],
                wired_rear_depth_reserved_mm=P['tx_wired_rear_depth_mm'],
                actual_wiring_measured=False,
                nominal_palm_to_frame_top_gap_mm=float(actual[2]-frame_top),
                fixed_mounts_only=True,printed_or_load_tested=False,
                unknowns=['receiver case soldering gap','PCB thickness','remaining component envelopes','actual fastener tolerances','foot compression','human palm pose','acoustic response'])
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,mesh_count=len(results),printable_kinds=sum(r['printable'] for r in results),
                          receiver_front_mm=actual.tolist(),max_part_size_mm=max(max(r['size_mm']) for r in results)),indent=2))


if __name__=='__main__': main()
