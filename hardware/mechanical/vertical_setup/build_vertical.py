"""P4 fixed vertical emitter stand and interchangeable wrist/receiver cartridges.

Run with local SOLIDWORKS 2025 and pywin32. All sketches are dimensioned by
the shared native generator; no sketch Fix constraints or printed threads.
"""
import argparse
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import build_solidworks as sw

P = json.loads((HERE / 'parameters.json').read_text(encoding='utf-8'))
sw.PARAMS = P
sw.OUT = ROOT / 'dist' / P['revision']
OUT = sw.OUT


def rounded_rectangle(part, bounds, r):
    x0, y0, x1, y1 = bounds
    points = [(x0+r,y0),(x1-r,y0),(x1,y0+r),(x1,y1-r),
              (x1-r,y1),(x0+r,y1),(x0,y1-r),(x0,y0+r)]
    mids = [(x1-r+r/math.sqrt(2),y0+r-r/math.sqrt(2)),
            (x1-r+r/math.sqrt(2),y1-r+r/math.sqrt(2)),
            (x0+r-r/math.sqrt(2),y1-r+r/math.sqrt(2)),
            (x0+r-r/math.sqrt(2),y0+r-r/math.sqrt(2))]
    for i in range(4):
        a,b,c = points[2*i], points[2*i+1], points[(2*i+2)%8]
        part.sk.CreateLine(*part.point(*a), *part.point(*b))
        part.sk.Create3PointArc(*part.point(*b), *part.point(*c), *part.point(*mids[i]))


def base():
    p = sw.Part('03_fixed_base', 'Fixed M4 cartridge holes; no sliders or pivots')
    p.begin(); p.polygon(sw.chamfer_rectangle(-75,-108,75,80,8))
    for x in [-40,40]:
        for y in P['tx_shoe_bolt_y_mm']: p.circle(x,y+P['tx_frame_origin_mm'][1],2.25)
    p.end('Base_150x188x6_four_M4_shoe_holes',0,6)
    p.begin(1)
    for x in [-55,55]:
        p.polygon(sw.chamfer_rectangle(x-8,6,x+8,140,3))
        p.circle(x,P['wrist_bolt_height_mm'],2.25)
    p.end('Fixed_masts_M4_pitch110',-105,-95)
    for x0,x1,tag in [(-67,-59,'Left'),(59,67,'Right')]:
        p.begin(2); p.polygon([(-105,6),(-42,6),(-98,65),(-105,65)])
        p.end(tag+'_gusset',x0,x1)
    return p.finish()


def spacer():
    p = sw.Part('05_pcb_standoff_10', 'M3 clearance spacer; no printed threads', quantity=4)
    p.begin(); p.circle(0,0,4); p.circle(0,0,1.7)
    p.end('M3_spacer_10',0,P['pcb_standoff_mm'])
    return p.finish()


def tx_frame():
    p = sw.Part('07_vertical_tx_frame', 'Open frame clears rear C1 and CN1; fixed feet')
    p.begin()
    bottom = 6-P['tx_frame_origin_mm'][2]
    bolt_y = 17-P['tx_frame_origin_mm'][2]
    assert bottom < -57 and bolt_y < P['tx_rear_window_mm'][1]-3
    p.polygon([(-52,-57),(-46,-57),(-46,bottom),(-34,bottom),(-34,-57),
               (34,-57),(34,bottom),(46,bottom),(46,-57),(50,-57),(50,46),(-52,46)])
    # Open relief extends downward beside CN1; plugged leads must not touch
    # the bottom frame rail. Leave the original PCB mounting holes intact.
    x0,y0,x1,y1=P['tx_rear_window_mm']
    nx,ny=P['tx_wired_relief_mm']
    p.polygon([(x0,y0),(nx,y0),(nx,ny),(x1,ny),(x1,y1),(x0,y1)])
    for x,y in P['mount_centers_mm']: p.circle(x,y,1.7)
    for x in [-40,40]: p.circle(x,bolt_y,2.25)
    # Two ties through each pair unload the connector without trapping leads.
    for x in [-48.5,46.5]:
        for y in [-15,-7]: p.circle(x,y,1.6)
    p.end('Open_frame_M3_board_and_M4_leg_holes',0,8)
    return p.finish()


def tx_shoe():
    p = sw.Part('08_tx_fixed_shoe','Bolted base shoe with clearance for frame leg',quantity=2)
    p.begin(); p.rect(-12,P['tx_shoe_foot_y_mm'][0],12,P['tx_shoe_foot_y_mm'][1]); p.rect(-6.2,-8.2,6.2,.2)
    for y in P['tx_shoe_bolt_y_mm']: p.circle(0,y,2.25)
    p.end('Shoe_foot_M4_clearance',0,6)
    p.begin(1); p.rect(-10,6,10,19); p.circle(0,11,2.25)
    p.end('Upright_fixed_M4_cross_bolt',0,8)
    return p.finish()


def receiver_dimensions():
    r = P['receiver']
    face_depth = r['pcb_back_offset_mm']+P['pcb_thickness_assumed_mm']+r['case_gap_mm']+7.1
    arm_depth = P['palm_forward_from_wrist_mm']+28-face_depth
    assert arm_depth > 20
    return face_depth, arm_depth


def receiver_arm():
    _,depth = receiver_dimensions()
    hole_y = 4-7-P['wrist_pad_assumed_mm']
    p = sw.Part('09_fixed_receiver_arm','Two fixed arms place U1 at palm reference; M3 through bolts')
    p.begin(); p.polygon(sw.chamfer_rectangle(-66,-20,66,20,3))
    for x in [-55,55]: p.circle(x,8,2.25)
    for x in P['receiver']['arm_holes_x_mm']: p.circle(x,hole_y,1.7)
    for y in [-10,-2]: p.circle(0,y,1.6)
    p.end('Common_flange_M4_pitch110',0,8)
    p.begin()
    for x in P['receiver']['arm_holes_x_mm']:
        p.rect(x-6,hole_y-6,x+6,hole_y+6); p.circle(x,hole_y,1.7)
    p.end('Fixed_length_arms_M3_through',8,depth)
    return p.finish()


def receiver_tray():
    r = P['receiver']; x0,y0,x1,y1 = r['pcb_bounds_from_U1_mm']
    p = sw.Part('10_receiver_edge_tray','Edge-only support; open back; original PCB remains undrilled')
    p.begin(); p.rect(-35,-19,46,35); p.rect(x0+.8,y0+.8,x1-.8,y1-.8)
    for x in r['arm_holes_x_mm']: p.circle(x,4,1.7)
    for x in [-10,10]: p.circle(x,y1+4,1.7)
    p.circle(-15,y0-4,1.7)
    p.end('Open_back_tray_and_M3_holes',0,4)
    p.begin()
    for x in [-10,10]:
        p.rect(x-4,y1-.8,x+4,y1+8); p.circle(x,y1+4,1.7)
    p.rect(-4,y1-.8,4,y1+1.7)
    # Third contact on the opposite edge, away from the switch and sensor.
    p.rect(-19,y0-8,-11,y0+.8); p.circle(-15,y0-4,1.7)
    p.end('Board_edge_shelves',4,r['pcb_back_offset_mm'])
    p.begin()
    for x in [-10,10]:
        p.rect(x-4,y1+.2,x+4,y1+8); p.circle(x,y1+4,1.7)
    p.rect(-19,y0-8,-11,y0-.2); p.circle(-15,y0-4,1.7)
    p.end('Outside_clip_bosses',r['pcb_back_offset_mm'],r['pcb_back_offset_mm']+P['pcb_thickness_assumed_mm'])
    p.begin()
    p.rect(-4,y1+.2,4,y1+1.7)
    p.end('Lower_edge_gravity_stop',r['pcb_back_offset_mm'],r['pcb_back_offset_mm']+P['pcb_thickness_assumed_mm']+.8)
    p.begin()
    for left,right in [(x0-2.7,x0-.2),(x1+.2,x1+2.7)]: p.rect(left,-4.5,right,-1.5)
    p.end('Fixed_left_right_edge_stops',4,r['pcb_back_offset_mm']+P['pcb_thickness_assumed_mm']+.8)
    return p.finish()


def receiver_clip():
    p = sw.Part('11_receiver_M3_edge_clip','Three M3 clips capture both opposite board edges; gently tighten',quantity=3)
    p.begin(); p.rect(-4,-5,4,4); p.circle(0,0,1.7)
    p.end('Clip_M3_clearance',0,3)
    return p.finish()


def receiver_pcb():
    p = sw.Part('REF_receiver_pcb','Original 60x34.619 rounded outline; nominal 1.6 thickness',False)
    p.begin(); rounded_rectangle(p,P['receiver']['pcb_bounds_from_U1_mm'],5)
    p.end('Undrilled_original_outline',0,P['pcb_thickness_assumed_mm'])
    return p.finish()


def receiver_sensor():
    p = sw.Part('REF_receiver_U1','MA40S4R nominal body; origin at case back, U1 centre',False)
    p.begin(); p.circle(0,0,4.95); p.end('Nominal_U1_body',0,7.1)
    return p.finish()


def tx_rear():
    p = sw.Part('REF_tx_rear_clearances','C1 vendor size and CN1 assumed insertion envelope; not full PCB electronics',False)
    p.begin(); p.circle(-29.024,-20.76,6.25)
    p.end('C1_nominal_12p5x20',-20,0,merge=False)
    x0,y0,x1,y1=P['tx_wired_connector_keepout_mm']
    p.begin(); p.rect(x0,y0,x1,y1)
    p.end('CN1_assumed_fully_wired_keepout',-P['tx_wired_rear_depth_mm'],0,merge=False)
    return p.finish()


def reference_screw(metric, length):
    """Nominal external envelope; threads/drive recesses deliberately omitted."""
    d,head_d,head_h = (4,7,4) if metric==4 else (3,5.5,3)
    p=sw.Part(f'REF_M{metric}x{length}_screw','Nominal DIN 912 / ISO 4762 external envelope, not printable',False)
    p.begin(); p.circle(0,0,head_d/2); p.end('Head_envelope',-head_h,0)
    p.begin(); p.circle(0,0,d/2); p.end('Shank_envelope_no_threads',0,length)
    return p.finish()


def reference_washer(metric):
    inside,outside,height=(4.3,9,.8) if metric==4 else (3.2,7,.5)
    p=sw.Part(f'REF_M{metric}_washer','Nominal DIN 125 flat washer envelope, not printable',False)
    p.begin(); p.circle(0,0,outside/2); p.circle(0,0,inside/2)
    p.end('Washer_envelope',0,height)
    return p.finish()


def reference_nut(metric):
    across,height=(7,5) if metric==4 else (5.5,4)
    p=sw.Part(f'REF_M{metric}_nylock','Nominal DIN 985 conservative hex envelope; major-diameter bore, no modeled thread',False)
    p.begin(); radius=across/math.sqrt(3)
    p.polygon([(radius*math.cos(i*math.pi/3),radius*math.sin(i*math.pi/3)) for i in range(6)])
    p.circle(0,0,metric/2); p.end('Nylock_external_envelope',0,height)
    return p.finish()


def reference_foot():
    p=sw.Part('REF_rubber_foot_16x6','Purchased flat rubber foot; 6 mm minimum installed support height',False)
    p.begin(); p.circle(0,0,8); p.end('Foot_envelope',-6,0)
    return p.finish()


BUILDERS = dict(guide=sw.guide,coupon=sw.fit_coupon,base=base,cradle=sw.cradle,
                spacer=spacer,frame=tx_frame,shoe=tx_shoe,arm=receiver_arm,
                tray=receiver_tray,clip=receiver_clip,pcb=sw.reference_pcb,
                emitters=sw.reference_emitters,datum=sw.reference_datum,
                resistors=sw.reference_resistors,receiver_pcb=receiver_pcb,
                receiver_sensor=receiver_sensor,tx_rear=tx_rear)
for metric,lengths in [(4,[20,25,30]),(3,[25,30,90])]:
    for length in lengths:
        BUILDERS[f'm{metric}x{length}']=lambda metric=metric,length=length: reference_screw(metric,length)
    BUILDERS[f'washer{metric}']=lambda metric=metric: reference_washer(metric)
    BUILDERS[f'nut{metric}']=lambda metric=metric: reference_nut(metric)
BUILDERS['foot']=reference_foot


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--only',choices=BUILDERS)
    parser.add_argument('--fasteners-only',action='store_true')
    args = parser.parse_args()
    assert 82 <= P['wrist_bolt_height_mm'] <= 128, 'Fixed mast must contain the cartridge holes'
    assert 0 <= P['wrist_pad_assumed_mm'] <= 8
    assert P['wrist_bolt_height_mm']+15+P['wrist_pad_assumed_mm'] >= P['tx_frame_origin_mm'][2]+54, 'Keep frame below nominal horizontal palm'
    sw.initialize()
    preferences = [('IntegerValue',211,0),('IntegerValue',78,3),
                   ('DoubleValue',2,.000005),('DoubleValue',3,math.radians(2)),
                   ('Toggle',69,True),('Toggle',70,False),('Toggle',71,True),('Toggle',191,False)]
    old = [(k,n,getattr(sw.APP,'GetUserPreference'+k)(n)) for k,n,v in preferences]
    try:
        for k,n,v in preferences: getattr(sw.APP,'SetUserPreference'+k)(n,v)
        for name,build in BUILDERS.items():
            if args.fasteners_only and not name.startswith(('m3x','m4x','washer','nut','foot')): continue
            if args.only is None or name==args.only: build()
    finally:
        for k,n,v in old: getattr(sw.APP,'SetUserPreference'+k)(n,v)


if __name__=='__main__': main()
