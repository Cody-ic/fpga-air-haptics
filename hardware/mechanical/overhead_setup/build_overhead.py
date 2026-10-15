"""P5: editable, fully dimensioned SOLIDWORKS features; no PCB modification.

Run with the installed SOLIDWORKS/pywin32 Python. Coordinates are millimetres.
Electronics are conservative reference envelopes, never printable parts.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_solidworks as sw
import pythoncom
import win32com.client as wc
import layout

P, BOARDS, OUT = layout.P, layout.BOARDS, layout.OUT


def initialize():
    for filename in ('sldworks', 'swconst'):
        lib = pythoncom.LoadTypeLib(str(sw.SW_PATH / (filename + '.tlb')))
        attrs = lib.GetLibAttr()
        module = wc.gencache.EnsureModule(str(attrs[0]), attrs[1], attrs[3], attrs[4])
        if filename == 'sldworks':
            sw.M = module
    sw.APP = sw.typed('ISldWorks', wc.Dispatch('SldWorks.Application'))
    sw.OUT = OUT
    for folder in ('solidworks', 'step', 'stl', 'reference', 'preview'):
        (OUT / folder).mkdir(parents=True, exist_ok=True)


def rounded_rectangle(part, x0, y0, x1, y1, radius):
    if radius <= 1e-6:
        part.rect(x0, y0, x1, y1)
        return
    r = radius
    points = [(x0+r,y0),(x1-r,y0),(x1,y0+r),(x1,y1-r),
              (x1-r,y1),(x0+r,y1),(x0,y1-r),(x0,y0+r)]
    mids = [(x1-r+r/math.sqrt(2),y0+r-r/math.sqrt(2)),
            (x1-r+r/math.sqrt(2),y1-r+r/math.sqrt(2)),
            (x0+r-r/math.sqrt(2),y1-r+r/math.sqrt(2)),
            (x0+r-r/math.sqrt(2),y0+r-r/math.sqrt(2))]
    for i in range(4):
        a, b, c = points[2*i], points[2*i+1], points[(2*i+2)%8]
        part.sk.CreateLine(*part.point(*a), *part.point(*b))
        part.sk.Create3PointArc(*part.point(*b), *part.point(*c), *part.point(*mids[i]))


def side_trestle():
    d = layout.datums()
    top = d['carrier_bottom_z_mm']
    ledge = top-P['body_ledge_vertical_mm']
    p = sw.Part('01_side_trestle', 'Common fixed side trestle; print broad side flat; two required', quantity=2)
    p.begin()
    # Bottom corners remain square so both full-length feet share table Z=0.
    p.polygon([(-94,0),(94,0),(94,top-3),(91,top),(-91,top),(-94,top-3)])
    p.polygon(sw.chamfer_rectangle(-76,8,76,ledge,3))
    p.end('Fixed_side_frame_flat_print',0,P['body_plate_thickness_mm'])
    p.begin(1)
    p.rect(-92,15.9,92,16+P['body_ledge_inner_depth_mm'])
    for u in (-55,55):
        p.circle(u,24,P['m4_clearance_diameter_mm']/2)
    p.end('Inward_ledge_two_vertical_M4_through_holes',ledge,top)
    return p.finish()


def carrier(profile):
    stem = '02_tx_carrier_4x4' if profile == 'tx4' else '03_tx_carrier_8x8'
    p = sw.Part(stem, 'Down-facing PCB carrier; true source mounting holes and open wired back')
    p.begin()
    p.polygon(sw.chamfer_rectangle(*P['carrier_bounds_mm'],3))
    p.rect(*P['carrier_aperture_mm'])
    for x,y in P['body_carrier_holes_mm']:
        p.circle(x,y,P['m4_clearance_diameter_mm']/2)
    p.end('Common_open_frame_M4_holes',0,P['carrier_thickness_mm'])
    # Each tab joins the outer ring; no floating support islands in the aperture.
    for index,(x,y) in enumerate(layout.tx_mounts(profile)):
        p.begin()
        p.rect(x-5,-92 if y<0 else y-4.5,x+5,y+4.5 if y<0 else 92)
        p.circle(x,y,P['m3_clearance_diameter_mm']/2)
        p.end(f'Joined_PCB_mount_tab_{index+1}',0,P['carrier_thickness_mm'])
    p.begin()
    for x,y in layout.tx_mounts(profile):
        p.circle(x,y,P['tx_boss_od_mm']/2)
        p.circle(x,y,P['m3_clearance_diameter_mm']/2)
    p.end('Four_front_contact_bosses_OD6',P['carrier_thickness_mm'],
          P['carrier_thickness_mm']+P['tx_front_boss_height_mm'])
    return p.finish()


def sleeve():
    p = sw.Part('04_m3_rear_sleeve_6x5', 'OD6 rear contact sleeve; raises metal washer above nearby pads', quantity=4)
    p.begin();p.circle(0,0,3);p.circle(0,0,1.7)
    p.end('M3_clearance_sleeve_5mm',0,5)
    return p.finish()


def receiver_tray():
    h = round(BOARDS['receiver']['board_size_mm'][1],3)
    p = sw.Part('05_receiver_flat_tray', 'Independent flat bottom receiver base; edge supports, open ports, recessed test pins')
    p.begin();p.polygon(sw.chamfer_rectangle(-8,-h-10,68,10,3))
    p.end('Flat_floor_4mm',0,P['receiver_floor_mm'])
    z0,z1 = P['receiver_nut_slot_z_mm']
    front = layout.datums()['receiver_front_z_mm']
    def mirrored(points,direction):
        return points if direction == 1 else [(x,-h-y) for x,y in points]
    for index,(x,_,direction) in enumerate(P['receiver_anchors_local_mm']):
        base=[(x-5,.3),(x+5,.3),(x+5,8),(x-5,8)]
        pocket=[(x-5,.3),(x+5,.3),(x+5,8),(x+2.95,8),
                (x+2.95,1),(x-2.95,1),(x-2.95,8),(x-5,8)]
        ay=4.5 if direction==1 else -h-4.5
        for band,(low,high,profile) in enumerate(((4,z0,base),(z0,z1,pocket),(z1,front,base))):
            p.begin();p.polygon(mirrored(profile,direction))
            if band != 1:p.circle(x,ay,1.7)
            p.end(f'Edge_pedestal_{index+1}_band_{band+1}',low,high)
    p.begin()
    for x0,x1 in ((27,33),(45,51)):p.rect(x0,-1.8,x1,.6)
    for x0,x1 in ((9,15),(47,53)):p.rect(x0,-h-.6,x1,-h+1.8)
    p.end('Four_component_free_edge_supports',4,layout.datums()['receiver_back_z_mm'])
    p.begin();p.rect(-2.5,-27,-.3,-21);p.rect(60.3,-8,62.5,-3)
    p.end('Low_side_stops_outside_PCB',4,17)
    return p.finish()


def receiver_clip():
    p = sw.Part('06_receiver_edge_clip','Narrow M3 edge clamp; only contact the four verified blank edge strips',quantity=4)
    p.begin();p.polygon(sw.chamfer_rectangle(-3,-6.3,3,3,.4));p.circle(0,0,1.7)
    p.end('Edge_clip_M3_clearance',0,P['receiver_clip_thickness_mm'])
    return p.finish()


def coupon():
    p = sw.Part('07_fastener_fit_coupon','From clipped corner: M3 holes 3.4/3.6 and M4 holes 4.5/4.7 mm')
    p.begin();p.polygon([(-30,-9),(30,-9),(30,9),(-27,9),(-30,6)])
    for x,d in zip((-22.5,-7.5,7.5,22.5),(3.4,3.6,4.5,4.7)):p.circle(x,0,d/2)
    p.end('Four_clearance_trials',0,5)
    return p.finish()


def overlaps(a,b):
    return min(a[2],b[2])-max(a[0],b[0]) >= -.01 and min(a[3],b[3])-max(a[1],b[1]) >= -.01


def reference_electronics(profile):
    board=BOARDS[profile]
    is_rx=profile=='receiver'
    cx,cy=(0,0) if is_rx else P[profile]['array_center_top_left_mm']
    def xy(x,y):return x-cx,cy-y
    def rectbox(box):
        x0,y0,x1,y1=box
        return x0-cx,cy-y1,x1-cx,cy-y0
    p=sw.Part('REF_'+profile+'_electronics',
              'Source PCB XY, nominal cases, assumed heights and cable keepouts; reference only',False)
    width,height=board['board_size_mm']
    p.begin();rounded_rectangle(p,-cx,cy-height,width-cx,cy,min(board['corner_radius_mm'],5))
    for hole in board['mounting_holes']:
        p.circle(*xy(*hole['xy_mm']),hole['diameter_mm']/2)
    p.end('Source_PCB_outline_and_holes',-P['pcb_thickness_mm'],0,merge=False)
    groups=defaultdict(list)
    cases=[]
    for component in board['parts']:
        ref=component['reference'];model=component.get('model_title','')
        mpn=component.get('manufacturer_part','')
        x,y=xy(*component['center_xy_mm'])
        if (not is_rx and 'MA40S4S' in mpn) or (is_rx and ref=='U1'):
            cases.append((x,y))
            continue
        if model.startswith('CAP-TH_D12.5'):
            p.begin();p.circle(x,y,6.25)
            p.end('Nominal_20mm_capacitor_'+ref,-21.6,-1.6,merge=False)
            continue
        ext=component['xy_footprint_extents_by_library_layer_mm']
        box=ext.get('48') or ext.get('3')
        if not box:
            raise ValueError('No component XY envelope: '+profile+'/'+ref)
        b=rectbox(box)
        height_match=re.search(r'(?:-|_)H(\d+(?:\.\d+)?)',model)
        zheight=float(height_match.group(1)) if height_match else 1.5
        if model=='R0603':zheight=1.2
        if 'XT30' in model:zheight=10
        if is_rx and ref=='U4':zheight=4.2
        if is_rx and component['side']=='Bottom' and ref in ('U5','U6','U7','U8','U9'):
            zheight=P['receiver_test_pin_projection_assumed_mm']
        if not is_rx and ref in ('CN1','H1') and 'CONN' in model+component.get('footprint_title',''):
            zheight=15
        low,high=(0,zheight) if component['side']=='Top' else (-1.6-zheight,-1.6)
        groups[(low,high)].append((ref,b))
    for start in range(0,len(cases),8):
        print('CASES '+profile+'/'+str(start),flush=True)
        p.begin()
        for x,y in cases[start:start+8]:p.circle(x,y,P['sensor_case_diameter_mm']/2)
        p.end('Acoustic_cases_'+str(start),0 if is_rx else P['tx_front_to_face_mm']-P['sensor_case_height_mm'],
              P['receiver_front_to_face_mm'] if is_rx else P['tx_front_to_face_mm'],merge=False)
    # Nonoverlapping outlines per sketch. Overlap of separate reference bodies
    # is intentional and excluded from printable-solid topology checks.
    for index,((low,high),rects) in enumerate(groups.items()):
        buckets=[]
        for ref,b in rects:
            # Small independent sketches also keep FullyDefineSketch tractable.
            bucket=next((q for q in buckets if len(q)<8 and all(not overlaps(b,v[1]) for v in q)),None)
            if bucket is None:bucket=[];buckets.append(bucket)
            bucket.append((ref,b))
        for n,bucket in enumerate(buckets):
            print(f'ENVELOPES {profile}/{index+1}/{n+1}',flush=True)
            p.begin()
            for _,b in bucket:p.rect(*b)
            p.end(f'Component_envelopes_{index+1}_{n+1}',low,high,merge=False)
    if not is_rx:
        connector=next(c for c in board['parts'] if c['reference']==('CN1' if profile=='tx4' else 'H1'))
        x,y=xy(*connector['center_xy_mm'])
        w,h=(15,40) if profile=='tx4' else (24,12)
        p.begin();p.rect(x-w/2,y-h/2,x+w/2,y+h/2)
        p.end('ASSUMED_wired_connector_back_keepout_50mm',-1.6-P['tx_wired_back_clearance_mm'],-1.6,merge=False)
    record=p.finish()
    record['envelope_notice']='XY from source footprint; component heights nominal/assumed, not measured. No detailed connector or cable model.'
    (OUT/(record['name']+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record


def screw(metric,length):
    p=sw.Part(f'REF_M{metric}x{length}_screw','Nominal socket screw envelope, no threads; purchase standard hardware',False)
    p.begin();p.circle(0,0,2.75 if metric==3 else 3.5)
    p.end('Socket_head',-3 if metric==3 else -4,0)
    p.begin();p.circle(0,0,metric/2)
    p.end('Nominal_shank',0,length)
    return p.finish()


def washer(metric):
    p=sw.Part(f'REF_M{metric}_washer','Nominal metal flat washer, reference only',False)
    p.begin();p.circle(0,0,3.5 if metric==3 else 4.5);p.circle(0,0,1.6 if metric==3 else 2.15)
    p.end('Metal_washer',0,.5 if metric==3 else .8)
    return p.finish()


def nut(metric,plain=False):
    stem=f'REF_M{metric}_'+('plain_nut' if plain else 'nylock')
    p=sw.Part(stem,'Nominal DIN934 plain nut' if plain else 'Nominal nyloc nut; purchase standard hardware',False)
    radius=(5.5 if metric==3 else 7)/math.sqrt(3)
    p.begin();p.polygon([(radius*math.cos(math.radians(30+60*i)),radius*math.sin(math.radians(30+60*i))) for i in range(6)])
    p.circle(0,0,metric/2)
    p.end('Unthreaded_nominal_nut',0,2.4 if plain else (4 if metric==3 else 5))
    return p.finish()


BUILDERS=dict(side=side_trestle,tx4=lambda:carrier('tx4'),tx8=lambda:carrier('tx8'),sleeve=sleeve,
              tray=receiver_tray,clip=receiver_clip,coupon=coupon,
              ref_tx4=lambda:reference_electronics('tx4'),ref_tx8=lambda:reference_electronics('tx8'),
              ref_rx=lambda:reference_electronics('receiver'),
              m3x25=lambda:screw(3,25),m3x16=lambda:screw(3,16),m4x30=lambda:screw(4,30),
              m3washer=lambda:washer(3),m4washer=lambda:washer(4),
              m3nut=lambda:nut(3),m4nut=lambda:nut(4),m3plain=lambda:nut(3,True))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only',nargs='+',choices=BUILDERS)
    args=parser.parse_args()
    initialize()
    prefs=[('IntegerValue',{211:0,78:3}),('DoubleValue',{2:.000005,3:math.radians(2)}),
           ('Toggle',{69:True,70:False,71:True,191:False})]
    saved=[]
    try:
        for kind,values in prefs:
            getter=getattr(sw.APP,'GetUserPreference'+kind)
            setter=getattr(sw.APP,'SetUserPreference'+kind)
            for key,value in values.items():
                saved.append((kind,key,getter(key)));setter(key,value)
        for name,build in BUILDERS.items():
            if not args.only or name in args.only:
                print('BUILD '+name,flush=True);build()
    finally:
        for kind,key,value in saved:getattr(sw.APP,'SetUserPreference'+kind)(key,value)
    records=[json.loads(f.read_text(encoding='utf-8')) for f in sorted(OUT.glob('*.json'))
             if f.stem.startswith(('0','REF_'))]
    (OUT/'manifest.json').write_text(json.dumps(dict(revision=P['revision'],datums=layout.datums(),parts=records),
                                              ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
