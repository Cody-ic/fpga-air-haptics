"""Draw a physical tabletop datum and RX corner/face coordinates from P5 CAD."""
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle, FancyBboxPatch, Polygon
from matplotlib.font_manager import FontProperties
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'overhead_addons'))
import layout

FONT = FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
plt.rcParams['font.family'] = FONT.get_name()
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['svg.fonttype'] = 'path'


def coordinates():
    old = layout.OLD
    half = layout.P['riser_width_mm']/2
    left = old.P['body_placement_x_mm'][0] + old.P['body_plate_thickness_mm']/2
    right = old.P['body_placement_x_mm'][1] - old.P['body_plate_thickness_mm']/2
    x0, y0 = left-half, -layout.P['riser_length_mm']/2
    origin = np.array([x0, y0])
    shift = -origin
    receiver = old.P['receiver']['sensor_center_top_left_mm']
    # Receiver PCB local X right, local Y up. Upper/lower corner meanings
    # below follow the top view: forward = smaller Y, back = larger Y.
    rx_origin = np.array(old.receiver_origin()[:2]) + shift
    w, h = old.BOARDS['receiver']['board_size_mm']
    h = round(h, 3)
    tray_a = rx_origin + [-8, -h-10]
    pcb_a = rx_origin + [0, -h]
    def corners(a, width, height):
        return {name: np.round(a+offset,3).tolist() for name,offset in zip(
            ('A','B','C','D'), ([0,0],[width,0],[width,height],[0,height]))}
    tx = []
    cx, cy = old.P['tx8']['array_center_top_left_mm']
    for p in old.BOARDS['tx8']['parts']:
        if 'MA40S4S' in p.get('manufacturer_part',''):
            x, y = p['center_xy_mm']
            tx.append(dict(pcb_reference=p['reference'],
                           position_mm=[round(x-cx+shift[0],3), round(y-cy+shift[1],3),
                                        round(layout.datums()['tx_face_z_mm'],3)]))
    assert len(tx)==64
    tx.sort(key=lambda p:(p['position_mm'][1],p['position_mm'][0]))
    assert np.allclose(shift,[132,94])
    assert np.allclose(shift-tray_a,[33.273,20.128])
    d = dict(schema='haptics-tabletop-measurement-1', units='mm', nominal=True,
             applies_to='P5 with 30 mm fixed risers; receiver in the reference orientation',
             origin='Left-front outside bottom corner of the left riser, on the table',
             axes='Top view: +X right, +Y back/up in figure; +Z upward from table',
             origin_in_native_assembly_mm=[x0,y0,0],
             floor_envelope_size_mm=[right+half-x0,layout.P['riser_length_mm']],
             receiver_tray_corners_mm=corners(tray_a,76,h+20),
             receiver_pcb_corners_mm=corners(pcb_a,w,h),
             corner_definition='Bounding rectangle intersections; actual tray corners are clipped and PCB corners rounded',
             receiver_face_center_mm=[*shift,round(layout.datums()['receiver_face_z_mm'],3)],
             receiver_pcb_front_z_mm=round(old.datums()['receiver_front_z_mm'],3),
             receiver_face_offset_from_tray_A_mm=np.round(shift-tray_a,3).tolist(),
             array_face_center_mm=[*shift,round(layout.datums()['tx_face_z_mm'],3)],
             emitters=tx,
             measured_coordinates=False,
             receiver_position_requires_manual_measurement=True,
             channel_mapping='pcb_reference is a component name, not a software or electrical channel number')
    (HERE/'coordinates.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return d


def text(ax,x,y,s,**kw):
    return ax.text(x,y,s,fontproperties=FONT,**kw)


def save(fig,name):
    fig.savefig(HERE/(name+'.png'),dpi=180,facecolor='white',bbox_inches='tight')
    svg = HERE/(name+'.svg')
    fig.savefig(svg,facecolor='white',bbox_inches='tight')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    plt.close(fig)


def overview(d):
    fig,ax=plt.subplots(figsize=(12,9))
    ax.set_aspect('equal')
    ax.set_xlim(-23,270); ax.set_ylim(-20,211)
    ax.set_xticks(np.arange(0,261,20)); ax.set_yticks(np.arange(0,201,20))
    ax.grid(color='#e3e8ed',lw=.7,zorder=0)
    for x in (0,200):
        ax.add_patch(Rectangle((x,0),44,188,facecolor='#e0edf7',edgecolor='#316e97',lw=2,zorder=2))
    text(ax,22,158,'左侧增高底座',ha='center',va='center',rotation=90,color='#265c7e',fontsize=12,zorder=10)
    text(ax,222,158,'右侧增高底座',ha='center',va='center',rotation=90,color='#265c7e',fontsize=12,zorder=10)
    # Original frame ground rails sit symmetrically inside the riser channels.
    for x in (14,214):
        ax.add_patch(Rectangle((x,0),16,188,facecolor='#a9c5d8',edgecolor='#4e7c98',lw=.7,zorder=3))
    ax.add_patch(Rectangle((95.95,57.95),72.1,72.1,fill=False,edgecolor='#986aa6',ls='--',lw=1.4,zorder=4))
    xy=np.array([v['position_mm'][:2] for v in d['emitters']])
    ax.scatter(xy[:,0],xy[:,1],s=10,color='#9d7eab',alpha=.6,zorder=4)
    text(ax,132,136,'8×8 发射中心的垂直投影',ha='center',fontsize=10,color='#80558e')
    a=np.array(d['receiver_tray_corners_mm']['A'])
    b=np.array(d['receiver_tray_corners_mm']['C'])
    ax.add_patch(Rectangle(a,*(b-a),facecolor='#f3dfbc',edgecolor='#b77b37',lw=1.6,zorder=5))
    p=np.array(d['receiver_pcb_corners_mm']['A'])
    q=np.array(d['receiver_pcb_corners_mm']['C'])
    ax.add_patch(FancyBboxPatch(p,*(q-p),boxstyle='round,pad=0,rounding_size=5',
                              facecolor='#d9ebe0',edgecolor='#478061',lw=1.4,zorder=6))
    ax.plot([0,253],[0,0],color='#263c50',lw=1.5,zorder=8)
    ax.annotate('',xy=(253,0),xytext=(246,0),arrowprops=dict(arrowstyle='->',color='#263c50',lw=1.5))
    ax.annotate('',xy=(0,203),xytext=(0,0),arrowprops=dict(arrowstyle='->',color='#263c50',lw=1.5))
    text(ax,257,0,'X',fontsize=13,va='center');text(ax,0,205,'Y',fontsize=13,ha='center')
    ax.scatter([0],[0],s=60,color='#be3d39',zorder=10)
    text(ax,-3,-7,'O（0, 0）',ha='left',fontsize=13,color='#be3d39',weight='bold')
    ax.plot([132,132],[0,94],ls=':',color='#c34e46',lw=1.4,zorder=8)
    ax.plot([0,132],[94,94],ls=':',color='#c34e46',lw=1.4,zorder=8)
    ax.add_patch(Circle((132,94),4.95,facecolor='#dde2e7',edgecolor='#53616d',lw=1.1,zorder=9))
    ax.scatter([132],[94],marker='+',s=130,color='#bf3d38',zorder=10)
    ax.annotate('R：接收头中心\n（132, 94）',xy=(132,94),xytext=(61,54),fontproperties=FONT,
                fontsize=12,color='#a23d37',arrowprops=dict(arrowstyle='->',color='#a23d37'),zorder=11)
    for label,pt in d['receiver_tray_corners_mm'].items():
        ax.scatter([pt[0]],[pt[1]],s=15,color='#a56a29',zorder=8)
        text(ax,pt[0]+(-3 if label in ('A','D') else 3),pt[1]+(-4 if label in ('A','B') else 4),
             label,ha='right' if label in ('A','D') else 'left',color='#8e5b25',fontsize=11,zorder=9)
    text(ax,122,-15,'落地底座外包宽度 244 mm；纵向长度 188 mm',ha='center',fontsize=11)
    text(ax,122,196,'后方（远离操作者）',ha='center',fontsize=11,color='#51616e')
    ax.set_title('测量底面坐标总览 · 从正上方俯视',fontproperties=FONT,fontsize=18,pad=18)
    ax.set_xlabel('X / mm',fontproperties=FONT);ax.set_ylabel('Y / mm',fontproperties=FONT)
    for s in ax.spines.values():s.set_visible(False)
    fig.text(.5,.015,'原点是左侧底座左前外角。中央没有整块底板；托盘可移动。图中为居中参考位置。',
             ha='center',fontproperties=FONT,fontsize=11,color='#51616e')
    save(fig,'底面坐标总览')


def detail(d):
    fig,(ax,info)=plt.subplots(1,2,figsize=(14,8),gridspec_kw={'width_ratios':[1.35,1]})
    ax.set_aspect('equal');ax.set_xlim(89,188);ax.set_ylim(61,142)
    ax.set_xticks(np.arange(100,181,10));ax.set_yticks(np.arange(70,141,10));ax.grid(color='#e4e9ee',lw=.7)
    a=np.array(d['receiver_tray_corners_mm']['A']);c=np.array(d['receiver_tray_corners_mm']['C'])
    x0,y0=a;x1,y1=c;r=3
    ax.add_patch(Polygon([(x0+r,y0),(x1-r,y0),(x1,y0+r),(x1,y1-r),(x1-r,y1),
                         (x0+r,y1),(x0,y1-r),(x0,y0+r)],facecolor='#f3dfbc',edgecolor='#ae7433',lw=2,zorder=2))
    pa=np.array(d['receiver_pcb_corners_mm']['A']);pc=np.array(d['receiver_pcb_corners_mm']['C'])
    ax.add_patch(FancyBboxPatch(pa,*(pc-pa),boxstyle='round,pad=0,rounding_size=5',
                              facecolor='#d1e8d9',edgecolor='#477b5c',lw=1.6,zorder=3))
    ax.add_patch(Rectangle(pa,*(pc-pa),fill=False,edgecolor='#477b5c',ls=':',lw=1,zorder=4))
    # Actual connector XY from the source PCB, here shown as orientation marks.
    rx=layout.OLD.P['receiver']['sensor_center_top_left_mm']
    for ref,color,size in (('CN1','#dfb72e',(5.6,10.2)),('U4','#e9e3d4',(4.3,8.3))):
        part=next(p for p in layout.OLD.BOARDS['receiver']['parts'] if p['reference']==ref)
        x,y=part['center_xy_mm'];gx=132+x-rx[0];gy=94+rx[1]-y
        ax.add_patch(Rectangle((gx-size[0]/2,gy-size[1]/2),*size,facecolor=color,edgecolor='#7a704e',lw=1,zorder=5))
        text(ax,gx,gy+(12 if ref=='CN1' else -10),'黄色电源口' if ref=='CN1' else '四针接口',
             ha='center',fontsize=10,color='#665d42',zorder=6)
    ax.add_patch(Circle((132,94),4.95,facecolor='#dfe5e9',edgecolor='#66737d',lw=1.4,zorder=5))
    ax.scatter([132],[94],marker='+',s=100,color='#b83838',zorder=7)
    text(ax,138,94,'R',fontsize=13,color='#b83838',zorder=7)
    ax.plot([a[0],132],[a[1],a[1]],ls=':',color='#b83838',lw=1.2,zorder=6)
    ax.plot([132,132],[a[1],94],ls=':',color='#b83838',lw=1.2,zorder=6)
    ax.annotate('',xy=(132,68),xytext=(a[0],68),arrowprops=dict(arrowstyle='<->',color='#b83838'))
    text(ax,(a[0]+132)/2,65.5,'33.273 mm',ha='center',fontsize=10,color='#b83838')
    ax.annotate('',xy=(181,94),xytext=(181,a[1]),arrowprops=dict(arrowstyle='<->',color='#b83838'))
    text(ax,184,(94+a[1])/2,'20.128 mm',rotation=90,ha='center',va='center',fontsize=10,color='#b83838')
    for label,p in d['receiver_tray_corners_mm'].items():
        ax.scatter(*p,color='#9c6126',s=22,zorder=8)
        text(ax,p[0]+(-2 if label in ('A','D') else 2),p[1]+(-3 if label in ('A','B') else 3),
             label,ha='right' if label in ('A','D') else 'left',fontsize=14,color='#945b21',weight='bold')
    for label,p in d['receiver_pcb_corners_mm'].items():
        text(ax,p[0]+(1.5 if label in ('A','D') else -1.5),p[1]+(2 if label in ('A','B') else -2),
             'P'+str(('A','B','C','D').index(label)+1),ha='left' if label in ('A','D') else 'right',
             va='bottom' if label in ('A','B') else 'top',fontsize=10,color='#316344',zorder=7)
    ax.set_xlabel('X / mm',fontproperties=FONT);ax.set_ylabel('Y / mm',fontproperties=FONT)
    ax.set_title('接收端：托盘、板框与接收头',fontproperties=FONT,fontsize=15,pad=15)
    info.axis('off')
    lines=['居中时的平面坐标（mm）','','托盘外包四角：']
    for label,p in d['receiver_tray_corners_mm'].items():lines.append(f'{label}   ({p[0]:.3f}, {p[1]:.3f})')
    lines+=['','电路板外包四角：']
    for i,p in enumerate(d['receiver_pcb_corners_mm'].values(),1):lines.append(f'P{i}  ({p[0]:.3f}, {p[1]:.3f})')
    lines+=['','R 接收头中心：(132.000, 94.000)','','移动后只量托盘左前角 A：','X_R = X_A + 33.273','Y_R = Y_A + 20.128','','保持图中朝向，四针接口在右侧。','托盘转向后需重新换算。']
    text(info,.02,.98,'\n'.join(lines),transform=info.transAxes,va='top',fontsize=12,linespacing=1.6,color='#293b48')
    fig.suptitle('接收端坐标详图 · 原点沿用底面总览 O',fontproperties=FONT,fontsize=18,y=1.02)
    fig.text(.5,-.005,'A—D、P1—P4 是直边延长后的外包矩形四角；实物托盘带倒角，电路板带圆角。尺寸为设计值。',
             ha='center',fontproperties=FONT,fontsize=10,color='#526472')
    fig.tight_layout()
    save(fig,'接收端坐标详图')


if __name__=='__main__':
    d=coordinates()
    overview(d)
    detail(d)
    print(json.dumps(dict(origin_in_native_assembly_mm=d['origin_in_native_assembly_mm'],
                          tray_corners=d['receiver_tray_corners_mm'],
                          pcb_corners=d['receiver_pcb_corners_mm'],
                          receiver_face=d['receiver_face_center_mm']),ensure_ascii=False))
