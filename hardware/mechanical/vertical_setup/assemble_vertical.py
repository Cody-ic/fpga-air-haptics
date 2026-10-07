"""Fixed component transforms: vertical array and horizontal palm reference."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from build_vertical import P,OUT,sw,receiver_dimensions
from assemble_solidworks import item,make_assembly,IDENTITY,RX_NEG90,RX_POS90

RZ_180=[-1.,0.,0.,0.,-1.,0.,0.,0.,1.]
# Local PCB XY half-turn, then receiver XY-to-world XZ transform.
RECEIVER_UPPER_CLIP=[-1.,0.,0.,0.,0.,1.,0.,1.,0.]


def fastener(metric,length,origin,rotation,thickness):
    """Origin is the stack entrance; axial directions follow the screw shank."""
    washer_height,nut_height=(.8,5) if metric==4 else (.5,4)
    axis=rotation[6:9]
    def shift(distance): return [p+v*distance for p,v in zip(origin,axis)]
    return [item(f'REF_M{metric}x{length}_screw',shift(-washer_height),rotation,'#bcc4cb'),
            item(f'REF_M{metric}_washer',shift(-washer_height),rotation,'#a2acb6'),
            item(f'REF_M{metric}_washer',shift(thickness),rotation,'#a2acb6'),
            item(f'REF_M{metric}_nylock',shift(thickness+washer_height),rotation,'#95a0ab')]


def layout():
    z = P['wrist_bolt_height_mm']+8
    wrist = [0,-67,z+7+P['wrist_pad_assumed_mm']]
    palm = [0,wrist[1]+P['palm_forward_from_wrist_mm'],wrist[2]]
    face_depth,arm_depth = receiver_dimensions()
    frame_origin = P['tx_frame_origin_mm']
    pcb_back_depth = 8+P['pcb_standoff_mm']
    face_y = frame_origin[1]-pcb_back_depth-P['pcb_thickness_assumed_mm']-P['datum_to_pcb_front_mm']
    return dict(revision=P['revision'],units='mm',
                frame='Origin at base bottom centre; X left/right; +Y wrist toward emitter; +Z up; table plane Z=-6 mm',
                emitter_front_center_mm=[0,face_y,frame_origin[2]],emitter_normal=[0,-1,0],
                palm_reference_mm=palm,palm_plane_normal=[0,0,-1],
                wrist_pad_contact_reference_mm=wrist,
                receiver_U1_front_center_mm=palm,receiver_normal=[0,1,0],
                receiver_arm_length_mm=arm_depth,receiver_face_depth_mm=face_depth,
                actual_hand_position_measured=False,actual_case_gap_measured=False,
                minimum_installed_foot_height_mm=6,table_plane_z_mm=-6,
                firmware_coordinate_transform_applied=False)


def placements():
    z = P['wrist_bolt_height_mm']+8
    l = layout(); back = 8+P['pcb_standoff_mm']; fy,fz=P['tx_frame_origin_mm'][1:]
    common = [item('03_fixed_base',[0,0,0],color='#324c65'),
              item('07_vertical_tx_frame',P['tx_frame_origin_mm'],RX_POS90,'#456784'),
              item('REF_pcb_80x88',[0,fy-back,fz],RX_POS90,'#258572'),
              item('REF_16_emitters',[0,l['emitter_front_center_mm'][1]+7.1,fz],RX_POS90,'#cbd2dc'),
              item('REF_tx_rear_clearances',[0,fy-back,fz],RX_POS90,'#aa976f')]
    common += [item('05_pcb_standoff_10',[x,fy-8,fz+y],RX_POS90,'#6f8192') for x,y in P['mount_centers_mm']]
    common += [item('08_tx_fixed_shoe',[x,fy,6],color='#5c7487') for x in [-40,40]]
    common += [item('REF_rubber_foot_16x6',[x,y,0],color='#35434d')
               for x in [-65,65] for y in [-96,64]]
    for x in [-40,40]:
        for y in P['tx_shoe_bolt_y_mm']:
            common += fastener(4,20,[x,fy+y,0],IDENTITY,12)
        # Put the drive head on the outer/rear face; an Allen key can enter
        # from +Y without passing through the vertical base bolts.
        common += fastener(4,25,[x,fy+8,17],RX_POS90,16)
    for x,y in P['mount_centers_mm']:
        common += fastener(3,30,[x,fy,fz+y],RX_POS90,back+P['pcb_thickness_assumed_mm'])
    cartridge_bolts=[]
    for x in [-55,55]: cartridge_bolts+=fastener(4,30,[x,-87,P['wrist_bolt_height_mm']],RX_POS90,18)
    wrist = common + [item('04_wrist_cradle',[0,-95,z],RX_NEG90,'#eab35b')] + cartridge_bolts
    r = P['receiver']; depth = l['receiver_face_depth_mm']
    tray = [0,l['palm_reference_mm'][1]-depth,l['palm_reference_mm'][2]]
    receiver = common + [item('09_fixed_receiver_arm',[0,-95,z],RX_NEG90,'#d9a658'),
                         item('10_receiver_edge_tray',tray,RX_NEG90,'#bd8845'),
                         item('REF_receiver_pcb',[tray[0],tray[1]+r['pcb_back_offset_mm'],tray[2]],RX_NEG90,'#238674'),
                         item('REF_receiver_U1',[tray[0],tray[1]+depth-7.1,tray[2]],RX_NEG90,'#ced8e1')]
    pcb_thickness=P['pcb_thickness_assumed_mm']
    receiver += [item('11_receiver_M3_edge_clip',[x,tray[1]+r['pcb_back_offset_mm']+pcb_thickness,
                                              tray[2]-r['pcb_bounds_from_U1_mm'][3]-4],RX_NEG90,'#eac082')
                 for x in [-10,10]]
    upper_clip_z=tray[2]-r['pcb_bounds_from_U1_mm'][1]+4
    receiver += [item('11_receiver_M3_edge_clip',[-15,tray[1]+r['pcb_back_offset_mm']+pcb_thickness,upper_clip_z],
                      RECEIVER_UPPER_CLIP,'#eac082')]+cartridge_bolts
    for x in r['arm_holes_x_mm']:
        receiver+=fastener(3,90,[x,tray[1]+4,tray[2]-4],RX_POS90,l['receiver_arm_length_mm']+4)
    for x,zclip in [(-10,tray[2]-r['pcb_bounds_from_U1_mm'][3]-4),
                    (10,tray[2]-r['pcb_bounds_from_U1_mm'][3]-4),(-15,upper_clip_z)]:
        receiver+=fastener(3,25,[x,tray[1]+r['pcb_back_offset_mm']+pcb_thickness+3,zclip],RX_POS90,
                           r['pcb_back_offset_mm']+pcb_thickness+3)
    jig = [item('01_array_alignment_jig',[0,0,0],color='#476f94'),
           item('REF_common_flat_datum',[0,0,0],color='#bdcdd5'),
           item('REF_16_emitters',[0,0,0],color='#cbd2dc'),
           item('REF_top_resistor_envelopes',[0,0,P['datum_to_pcb_front_mm']],color='#d39748'),
           item('REF_pcb_80x88',[0,0,P['datum_to_pcb_front_mm']],color='#258572')]
    return dict(Vertical_wrist_assembly=wrist,Vertical_receiver_assembly=receiver,Array_jig_assembly=jig)


if __name__=='__main__':
    sw.initialize()
    assemblies = placements()
    for name,entries in assemblies.items(): make_assembly(name,entries)
    (OUT/'assemblies.json').write_text(json.dumps(assemblies,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'vertical_layout.json').write_text(json.dumps(layout(),ensure_ascii=False,indent=2),encoding='utf-8')
