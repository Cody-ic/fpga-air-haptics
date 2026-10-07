"""Dimensioned side-view reference diagram using generated layout coordinates."""
import json
from pathlib import Path

from PIL import Image,ImageDraw,ImageFont

HERE=Path(__file__).resolve().parent
P=json.loads((HERE/'parameters.json').read_text(encoding='utf-8'))
OUT=HERE.parent/'dist'/P['revision']
L=json.loads((OUT/'vertical_layout.json').read_text(encoding='utf-8'))
FONT='C:/Windows/Fonts/msyh.ttc'
font=lambda s: ImageFont.truetype(FONT,s)
image=Image.new('RGB',(1600,1060),'#f3f5f8')
d=ImageDraw.Draw(image)
d.text((60,32),'固定式布局：手腕支撑与掌心测量分别定位',font=font(36),fill='#203349')
d.text((60,90),'侧视图 · 单位 mm · 底座底面 Z=0，垫脚后桌面 Z=−6 · 非声场仿真',font=font(22),fill='#667789')


def panel(left,receiver):
    d.rounded_rectangle((left,150,left+735,850),radius=18,fill='white',outline='#dce3eb',width=2)
    d.text((left+28,170),'接收架装配' if receiver else '腕托装配',font=font(28),fill='#213f58')
    scale=2.6
    def point(y,z): return (left+55+(y+115)*scale,760-z*scale)
    def box(y0,z0,y1,z1,color):
        a=point(y0,z1); b=point(y1,z0); d.rectangle((*a,*b),fill=color)
    def line(points,color,width=3): d.line([point(y,z) for y,z in points],fill=color,width=width)
    box(-108,0,80,6,'#405b72')
    for fy in [-96,64]: box(fy-8,-6,fy+8,0,'#35434d')
    # Side mast is drawn translucent-looking as an adjacent fixed upright.
    box(-105,6,-95,140,'#8ba0b0')
    z=P['wrist_bolt_height_mm']
    c=point(-95,z); d.ellipse((c[0]-7,c[1]-7,c[0]+7,c[1]+7),fill='#263c4f')
    d.text((left+22,225),'共用 M4 孔\n固定高度',font=font(18),fill='#627689')
    line([(-95,z),(-107,160)],'#9aabba',2)
    fy,fz=P['tx_frame_origin_mm'][1:]
    box(fy-8,6,fy,fz+46,'#5c7b92')
    back=fy-8-P['pcb_standoff_mm']
    box(back-1.6,fz-49.5,back,fz+38.5,'#258572')
    face=L['emitter_front_center_mm'][1]
    for dy in [-16.5,-5.5,5.5,16.5]: box(face,fz+dy-4.95,face+7.1,fz+dy+4.95,'#c7ced5')
    # Keepout is a design reservation; do not imply actual cable paths measured.
    box(back+1,fz-43.554,back+P['tx_wired_rear_depth_mm'],fz-3.554,'#faf0d9')
    d.text((left+460,705),'开放出线区\n向后预留 50',font=font(17),fill='#9b7837')
    py,pz=L['palm_reference_mm'][1:]
    if receiver:
        box(-95,z-12,-87,z+28,'#d8a552')
        box(-87,pz-10,py-L['receiver_face_depth_mm'],pz+2,'#d8a552')
        tray=py-L['receiver_face_depth_mm']
        box(tray,pz-35,tray+4,pz+17,'#bd8845')
        pcb=tray+P['receiver']['pcb_back_offset_mm']
        bounds=P['receiver']['pcb_bounds_from_U1_mm']
        box(pcb,pz-bounds[3],pcb+1.6,pz-bounds[1],'#258572')
        box(py-7.1,pz-4.95,py,pz+4.95,'#aab7c4')
        d.text((left+248,230),'接收头前端中心\n对准掌心参考点',font=font(20),fill='#ba6f1d')
    else:
        box(-95,z-12,-87,z+8,'#d8a552')
        box(-87,z+8,-47,z+15,'#e5b866')
        wy,wz=L['wrist_pad_contact_reference_mm'][1:]
        line([(wy-18,wz),(wy+18,wz)],'#876731',7)
        # Horizontal palm/fingers are a position guide, not an anatomical model.
        line([(wy,wz),(py+85,pz)],'#d5c3b4',18)
        d.text((left+228,230),'手掌水平悬空\n浅色线仅为姿态示意',font=font(20),fill='#947251')
    for start in range(-55,96,10): line([(start,pz),(start+5,pz)],'#90a4b7',2)
    q=point(py,pz); d.ellipse((q[0]-6,q[1]-6,q[0]+6,q[1]+6),fill='#d08026')
    wy,wz=L['wrist_pad_contact_reference_mm'][1:]
    line([(wy,150),(py,150)],'#d08026',2)
    for y in [wy,py]: line([(y,147),(y,153)],'#d08026',2)
    a=point((wy+py)/2,159)
    d.text((a[0]-52,a[1]-22),'前移 60',font=font(19),fill='#b87323')
    d.text((left+30,805),'竖直 PCB，上缘低于掌面参考平面 17.5',font=font(19),fill='#627689')


panel(35,False); panel(830,True)
d.text((60,885),'两套可换件不同时安装；螺栓锁紧后没有活动调节机构。',font=font(24),fill='#304961')
d.text((60,930),'底座 M4×20 配至少 6 mm 垫脚；共用接口 M4×30；接收托架 M3×90，三片压片 M3×25。',font=font(21),fill='#516b81')
d.text((60,974),'尚未试打、承重或测量真实接线包络。接收架定位单点，不能自动测绘整片掌面。',font=font(19),fill='#708496')
image.save(OUT/'preview/layout_reference.png')
print(OUT/'preview/layout_reference.png')
