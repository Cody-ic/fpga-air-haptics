"""Independent mesh/manufacturing checks and source PCB mounting-pattern audit."""
from collections import Counter, defaultdict, deque
import json
from pathlib import Path
import re
import struct
import zipfile

import numpy as np
from pcb_layout import top_resistors

ROOT=Path(__file__).resolve().parent
P=json.loads((ROOT/'parameters.json').read_text(encoding='utf-8'))
OUT=ROOT/'dist'/P['revision']


def triangles(path):
    data=path.read_bytes()
    count=struct.unpack_from('<I',data,80)[0]
    assert len(data)==84+50*count, ('Unexpected STL format',path)
    dtype=np.dtype([('normal','<f4',3),('vertices','<f4',(3,3)),('attr','<u2')])
    return np.frombuffer(data,offset=84,count=count,dtype=dtype)['vertices'].astype(float)


def check_mesh(path):
    a=triangles(path)
    assert np.isfinite(a).all()
    vertices,indices=np.unique(np.round(a.reshape(-1,3),4),axis=0,return_inverse=True)
    faces=indices.reshape(-1,3)
    edges=defaultdict(list)
    for i,face in enumerate(faces):
        for u,v in zip(face,np.roll(face,-1)):
            edges[tuple(sorted((u,v)))].append((i,u<v))
    assert all(len(e)==2 and e[0][1]!=e[1][1] for e in edges.values()), ('Open/nonmanifold mesh',path)
    neighbors=defaultdict(list)
    for e in edges.values():
        neighbors[e[0][0]].append(e[1][0]);neighbors[e[1][0]].append(e[0][0])
    unseen=set(range(len(faces)));components=0
    while unseen:
        q=[unseen.pop()];components+=1
        while q:
            for n in neighbors[q.pop()]:
                if n in unseen:unseen.remove(n);q.append(n)
    cross=np.cross(a[:,1]-a[:,0],a[:,2]-a[:,0])
    assert (np.linalg.norm(cross,axis=1)>1e-8).all()
    volume=float(np.einsum('ij,ij->i',a[:,0],np.cross(a[:,1],a[:,2])).sum()/6)
    assert volume>0
    lo=a.min(axis=(0,1));hi=a.max(axis=(0,1));size=hi-lo
    metadata=json.loads((OUT/(path.stem+'.json')).read_text(encoding='utf-8'))
    assert components==metadata['solid_count']
    error=abs(volume-metadata['volume_mm3'])/metadata['volume_mm3']
    assert error<.001, ('Tessellation error',path,error)
    if metadata['printable']:
        assert components==1 and lo[2]>-1e-4
        assert (size<=np.array(P['assumed_print_bed_mm'])).all()
    return dict(name=path.name,triangles=len(faces),watertight=True,oriented=True,
                connected_solids=components,size_mm=size.tolist(),volume_mm3=volume,
                cad_volume_relative_error=error,printable=metadata['printable'])


def source_pattern():
    path=ROOT.parent/'Haptics_4x4_R5_12VDC/Haptics_4x4_R5_12VDC_Gerber.zip'
    with zipfile.ZipFile(path) as z:
        text=z.read('Drill_NPTH_Through.DRL').decode()
    actual=sorted((round(float(x)-27.5,4),round(float(y)+27.5,4))
                  for x,y in re.findall(r'X(-?[\d.]+)Y(-?[\d.]+)',text))
    assert actual==sorted(tuple(v) for v in P['mount_centers_mm'])
    return actual


def vertical_hits(mesh, x, y):
    """Intersect a vertical probe with exported triangles, excluding vertical faces."""
    a,b,c=mesh[:,0],mesh[:,1],mesh[:,2]
    det=(b[:,0]-a[:,0])*(c[:,1]-a[:,1])-(b[:,1]-a[:,1])*(c[:,0]-a[:,0])
    usable=np.abs(det)>1e-10
    a,b,c,det=a[usable],b[usable],c[usable],det[usable]
    u=((x-a[:,0])*(c[:,1]-a[:,1])-(y-a[:,1])*(c[:,0]-a[:,0]))/det
    v=((b[:,0]-a[:,0])*(y-a[:,1])-(b[:,1]-a[:,1])*(x-a[:,0]))/det
    inside=(u>=-1e-7)&(v>=-1e-7)&(u+v<=1+1e-7)
    z=a[:,2]+u*(b[:,2]-a[:,2])+v*(c[:,2]-a[:,2])
    return np.unique(np.round(z[inside],5))


def check_datum_and_relief():
    guide=triangles(OUT/'stl/01_array_alignment_jig.stl')
    datum=triangles(OUT/'reference/REF_common_flat_datum.stl')
    # Positive control: a solid region must be detected by the same probe code.
    assert np.allclose(vertical_hits(guide,-30,0),[0,P['guide_depth_mm']])
    bodies=json.loads((OUT/'REF_16_emitters.json').read_text(encoding='utf-8'))['boxes_mm']
    assert len(bodies)==16
    bottoms=[]
    probes=0
    for x0,y0,z0,x1,y1,z1 in bodies:
        x,y=(x0+x1)/2,(y0+y1)/2
        bottoms.append(z0)
        assert abs(z0)<1e-5
        assert abs(max(vertical_hits(datum,x,y))-z0)<1e-5
        # Check the centre and 48 probes over the nominal front-rim footprint.
        # This detects a hidden bottom or ledge that could lift the emitting face.
        assert not len(vertical_hits(guide,x,y))
        probes+=1
        for radius in (P['body_nominal_diameter_mm']/4,P['body_nominal_diameter_mm']/2):
            for angle in np.linspace(0,2*np.pi,24,endpoint=False):
                assert not len(vertical_hits(guide,x+radius*np.cos(angle),y+radius*np.sin(angle)))
                probes+=1
    x0,y0,x1,y1=P['resistor_window_mm']
    width,length,height=P['resistor_envelope_mm']
    resistor_checks=[]
    for r in top_resistors():
        x,y=r['x_mm'],r['y_mm']
        assert x0+1.5 < x-width/2 and x+width/2 < x1-1.5
        assert y0+1.5 < y-length/2 and y+length/2 < y1-1.5
        for dx in (-width/2,0,width/2):
            for dy in (-length/2,0,length/2):
                assert not len(vertical_hits(guide,x+dx,y+dy)), r
        resistor_checks.append(dict(**r,window_clear=True))
    return dict(reference='one external unprinted flat plate, top Z=0',
                modeled_face_height_spread_mm=max(bottoms)-min(bottoms),
                through_bore_vertical_probes=probes,resistor_checks=resistor_checks,
                resistor_envelope_assumed_mm=P['resistor_envelope_mm'],
                nominal_resistor_to_guide_top_gap_mm=P['datum_to_pcb_front_mm']-height-P['guide_depth_mm'],
                physical_coplanarity_measured=False)


if __name__=='__main__':
    meshes=[check_mesh(p) for folder in ['stl','reference'] for p in (OUT/folder).glob('*.stl')]
    assert len(meshes)==10
    # Mechanical dimensions only; no claim of a measured acoustic working height.
    array_face=P['base_thickness_mm']+P['pcb_standoff_mm']+P['pcb_thickness_assumed_mm']+P['datum_to_pcb_front_mm']
    report=dict(passed=True,pcb_mount_pattern_verified=source_pattern(),meshes=meshes,
                common_datum=check_datum_and_relief(),
                array_face_above_base_bottom_mm=array_face,
                bare_cradle_center_clearance_mm=[82+15-array_face,142+15-array_face],
                default_bare_clearance_mm=P['wrist_bolt_height_mm']+15-array_face,
                minimum_guide_web_mm=P['pitch_mm']-P['guide_bore_mm'],
                minimum_case_to_pcb_gap_mm=P['datum_to_pcb_front_mm']-(P['body_nominal_height_mm']+.3),
                printed_or_load_tested=False)
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
