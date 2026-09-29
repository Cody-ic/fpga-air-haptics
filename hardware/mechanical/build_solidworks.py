"""Generate native, editable SOLIDWORKS features plus STEP/STL, in millimetres.

Requires a licensed local SOLIDWORKS 2025 installation and pywin32.
Only creates this package's documents; never edits the electrical project.
"""
import argparse
import json
import math
from pathlib import Path

import pythoncom
import win32com.client as wc

ROOT = Path(__file__).resolve().parent
PARAMS = json.loads((ROOT / 'parameters.json').read_text(encoding='utf-8'))
OUT = ROOT / 'dist' / PARAMS['revision']
SW_PATH = Path('D:/Solidworks2025/SOLIDWORKS')
TEMPLATES = Path('C:/ProgramData/SOLIDWORKS/SOLIDWORKS 2025/templates')
M = None
APP = None


def typed(name, obj):
    return getattr(M, name)(obj._oleobj_)


def initialize():
    global M, APP
    if (PARAMS['array_rows'], PARAMS['array_cols'], PARAMS['pitch_mm']) != (4, 4, 11.0):
        raise ValueError('This fixture matches the R5 4x4, 11 mm PCB only.')
    if not 82 <= PARAMS['wrist_bolt_height_mm'] <= 142:
        raise ValueError('Wrist bolt height must stay within the mast slots: 82–142 mm.')
    for filename in ['sldworks', 'swconst']:
        lib = pythoncom.LoadTypeLib(str(SW_PATH / (filename + '.tlb')))
        attrs = lib.GetLibAttr()
        module = wc.gencache.EnsureModule(str(attrs[0]), attrs[1], attrs[3], attrs[4])
        if filename == 'sldworks':
            M = module
    APP = typed('ISldWorks', wc.Dispatch('SldWorks.Application'))
    for folder in ['solidworks', 'step', 'stl', 'reference', 'preview']:
        (OUT / folder).mkdir(parents=True, exist_ok=True)


class Part:
    def __init__(self, name, description, printable=True, quantity=1):
        self.name, self.description = name, description
        self.printable, self.quantity = printable, quantity
        self.doc = typed('IModelDoc2', APP.NewDocument(str(TEMPLATES / 'gb_part.prtdot'), 0, 0., 0.))
        self.sk = typed('ISketchManager', self.doc.SketchManager)
        self.fm = typed('IFeatureManager', self.doc.FeatureManager)
        self.planes = []
        f = typed('IFeature', self.doc.FirstFeature())
        while f:
            if f.GetTypeName2() == 'RefPlane':
                self.planes.append(f)
            nxt = f.GetNextFeature()
            f = typed('IFeature', nxt) if nxt else None
        assert len(self.planes) == 3
        self.feature_names = []
        self.sketch_records = []

    def begin(self, plane=0):
        # Native standard planes: XY(+Z), XZ(+Y), YZ(+X).
        self.doc.ClearSelection2(True)
        assert self.planes[plane].Select2(False, 0)
        self.sk.InsertSketch(True)
        self.sk.AddToDB = True  # Disable inference/snapping; keep exact coordinates.
        self.sk.DisplayWhenAdded = False
        self.plane = plane

    def point(self, u, v):
        if self.plane == 1:
            u, v = u, -v
        elif self.plane == 2:
            u, v = -v, u
        return u / 1000, v / 1000, 0.

    def polygon(self, points):
        for a, b in zip(points, points[1:] + points[:1]):
            assert self.sk.CreateLine(*self.point(*a), *self.point(*b))

    def rect(self, x0, y0, x1, y1):
        self.polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    def circle(self, x, y, radius):
        assert self.sk.CreateCircleByRadius(*self.point(x, y), radius / 1000)

    def slot(self, x, y0, y1, diameter):
        r = diameter / 2
        a, b, c, d = (x-r, y0), (x-r, y1), (x+r, y1), (x+r, y0)
        self.sk.CreateLine(*self.point(*a), *self.point(*b))
        self.sk.Create3PointArc(*self.point(*b), *self.point(*c), *self.point(x, y1+r))
        self.sk.CreateLine(*self.point(*c), *self.point(*d))
        self.sk.Create3PointArc(*self.point(*d), *self.point(*a), *self.point(x, y0-r))

    def end(self, name, start_mm, end_mm, merge=True):
        self.sk.AddToDB = False
        self.sk.DisplayWhenAdded = True
        self.doc.ClearSelection2(True)
        extension = typed('IModelDocExtension', self.doc.Extension)
        if not extension.SelectByID2('', 'EXTSKETCHPOINT', 0., 0., 0., False, 6, None, 0):
            raise RuntimeError(f'{self.name}: cannot select sketch origin')
        # Native dimensions + equal/coincident/tangent/H/V/etc. relations.
        # The supported relation mask does not include a Fix relation.
        result = self.sk.FullyDefineSketch(True, True, 1023, True, 1, None, 1, None, 1, 1)
        sketch = typed('ISketch', self.sk.ActiveSketch)
        status = sketch.GetConstrainedStatus()
        if result != 0 or status != 3:
            raise RuntimeError(f'{self.name}/{name}: fully define failed: {result}, status {status}')
        self.sketch_records.append(dict(feature=name, constraint_status=status, fully_defined=True))
        self.doc.ClearSelection2(True)
        self.sk.InsertSketch(True)
        feature = self.fm.FeatureExtrusion3(
            True, False, end_mm < start_mm, 0, 0, abs(end_mm-start_mm)/1000, 0.,
            False, False, False, False, 0., 0., False, False, False, False,
            merge, False, True, 3 if start_mm else 0, abs(start_mm)/1000, start_mm < 0)
        if not feature:
            raise RuntimeError(f'{self.name}: feature failed: {name}')
        typed('IFeature', feature).Name = name
        self.feature_names.append(name)
        self.doc.ClearSelection2(True)

    def finish(self):
        self.doc.ForceRebuild3(False)
        bodies = typed('IPartDoc', self.doc).GetBodies2(0, False)
        if not bodies or (self.printable and len(bodies) != 1):
            raise RuntimeError(f'{self.name}: expected one joined printable solid, got {len(bodies or [])}')
        boxes, volume = [], 0.
        for body in bodies:
            b = typed('IBody2', body)
            boxes.append([v*1000 for v in b.GetBodyBox()])
            volume += b.GetMassProperties(1)[3]*1e9
        self.doc.ShowNamedView2('*Isometric', 7)
        self.doc.ViewZoomtofit2()
        self.doc.ClearSelection2(True)
        path = OUT / 'solidworks' / (self.name + '.SLDPRT')
        assert self.doc.SaveAs3(str(path), 0, 1) == 0, path
        assert self.doc.SaveAs3(str(OUT / 'step' / (self.name + '.step')), 0, 1) == 0
        mesh_folder = 'stl' if self.printable else 'reference'
        assert self.doc.SaveAs3(str(OUT / mesh_folder / (self.name + '.stl')), 0, 1) == 0
        self.doc.SaveBMP(str(OUT / 'preview' / (self.name + '.bmp')), 1200, 900)
        record = dict(name=self.name, description=self.description, printable=self.printable,
                      quantity=self.quantity, boxes_mm=boxes, volume_mm3=volume,
                      solid_count=len(bodies), features=self.feature_names, sketches=self.sketch_records)
        (OUT / (self.name + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        APP.CloseDoc(self.doc.GetTitle())
        print(json.dumps(record, ensure_ascii=False), flush=True)
        return record


def chamfer_rectangle(x0, y0, x1, y1, c):
    return [(x0+c,y0),(x1-c,y0),(x1,y0+c),(x1,y1-c),
            (x1-c,y1),(x0+c,y1),(x0,y1-c),(x0,y0+c)]


def array_centers():
    return [(PARAMS['pitch_mm']*(c-1.5), PARAMS['pitch_mm']*(r-1.5))
            for r in range(4) for c in range(4)]


def guide():
    p = Part('01_array_alignment_jig', '4x4 removable soldering guide; front faces rest on external flat datum')
    p.begin()
    p.polygon(chamfer_rectangle(-42,-53,45,42,5))
    for x,y in array_centers():
        p.circle(x,y,PARAMS['guide_bore_mm']/2)
    for x,y in PARAMS['mount_centers_mm']:
        p.circle(x,y,1.7)
    p.end('Guide_16_bores_pitch11',0,PARAMS['guide_depth_mm'])
    p.begin()
    for x,y in PARAMS['mount_centers_mm']:
        p.circle(x,y,3.6);p.circle(x,y,1.7)
    p.end('PCB_front_datum_10p6',PARAMS['guide_depth_mm'],PARAMS['datum_to_pcb_front_mm'])
    return p.finish()


def fit_coupon():
    p = Part('02_bore_fit_coupon','Fit holes LEFT to RIGHT: 10.0, 10.2, 10.4, 10.6 mm')
    p.begin()
    # Clipped upper-left corner identifies the first / 10.0 mm hole.
    p.polygon([(-31,-10),(31,-10),(31,10),(-26,10),(-31,5)])
    for i,diameter in enumerate([10.,10.2,10.4,10.6]):p.circle(-22.5+15*i,0,diameter/2)
    p.end('Four_clearance_trials',0,5)
    return p.finish()


def stand():
    p = Part('03_wrist_and_board_base','Integrated base, two side rails leaving a 94 mm forearm opening, and gussets')
    p.begin()
    p.polygon(chamfer_rectangle(-75,-128,75,60,8))
    for x,y in PARAMS['mount_centers_mm']:p.circle(x,y,1.7)
    for x,y in [(-65,-114),(65,-114),(-65,45),(65,45)]:p.circle(x,y,2.25)
    p.end('Base_150x188x6',0,6)
    p.begin(1)
    for x in [-55,55]:
        p.polygon(chamfer_rectangle(x-8,6,x+8,156,3))
        p.slot(x,82,142,4.6)
    p.end('Side_rails_open94_M4_travel60',-105,-95)
    for x0,x1,tag in [(-67,-59,'Left'),(59,67,'Right')]:
        p.begin(2)
        # Stay below the cradle even at the lowest adjustment (flange bottom Z=70).
        p.polygon([(-105,6),(-42,6),(-98,65),(-105,65)])
        p.end(tag+'_gusset',x0,x1)
    return p.finish()


def cradle():
    p=Part('04_wrist_cradle','Curved wrist saddle; print with flat rear flange on bed, add soft pad')
    # Print coordinates = [assembly X, -assembly Z, assembly Y].
    radius=160.; center_z=167.; half_width=66.
    rim_z=center_z-math.sqrt(radius**2-half_width**2)
    p.begin()
    outline=[(-66,17),(-63,20),(63,20),(66,17),(66,-rim_z)]
    for a,b in zip(outline,outline[1:]):p.sk.CreateLine(*p.point(*a),*p.point(*b))
    p.sk.Create3PointArc(*p.point(66,-rim_z),*p.point(-66,-rim_z),*p.point(0,-7))
    p.sk.CreateLine(*p.point(-66,-rim_z),*p.point(-66,17))
    for x in [-55,55]:p.circle(x,8,2.25)
    p.end('Back_flange_M4_pitch110',0,8)
    p.begin()
    p.sk.CreateLine(*p.point(-66,0),*p.point(66,0))
    p.sk.CreateLine(*p.point(66,0),*p.point(66,-rim_z))
    p.sk.Create3PointArc(*p.point(66,-rim_z),*p.point(-66,-rim_z),*p.point(0,-7))
    p.sk.CreateLine(*p.point(-66,-rim_z),*p.point(-66,0))
    p.end('Saddle_R160_depth40',8,48)
    return p.finish()


def spacer():
    p=Part('05_pcb_standoff_35','PCB underside clearance spacer; four required',quantity=4)
    p.begin();p.circle(0,0,4);p.circle(0,0,1.7)
    p.end('M3_clearance_standoff',0,PARAMS['pcb_standoff_mm'])
    return p.finish()


def knob():
    p=Part('06_m4_nut_knob','Hand nut knob; insert standard M4 hex nut from outer face',quantity=2)
    p.begin();p.circle(0,0,11);p.circle(0,0,2.25)
    p.end('Knob_M4_through',0,5)
    p.begin();p.circle(0,0,11)
    # 7.3 mm across flats, open-top nut recess. No printed thread.
    radius=7.3/math.sqrt(3)
    p.polygon([(radius*math.cos(i*math.pi/3),radius*math.sin(i*math.pi/3)) for i in range(6)])
    p.end('Hex_nut_pocket_AF7p3',5,9)
    return p.finish()


def reference_pcb():
    p=Part('REF_pcb_80x88','Envelope and actual mounting holes ONLY, not an electronics CAD model',False)
    p.begin();p.polygon(chamfer_rectangle(*PARAMS['pcb_bounds_mm'],5))
    for x,y in PARAMS['mount_centers_mm']:p.circle(x,y,1.6)
    p.end('Reference_PCB_assumed_1p6',0,PARAMS['pcb_thickness_assumed_mm'])
    return p.finish()


def reference_emitters():
    p=Part('REF_16_emitters','Nominal body envelopes ONLY; front plane shared, no acoustic simulation',False)
    p.begin()
    for x,y in array_centers():p.circle(x,y,PARAMS['body_nominal_diameter_mm']/2)
    p.end('Sixteen_nominal_bodies',0,PARAMS['body_nominal_height_mm'],merge=False)
    return p.finish()


BUILDERS=dict(guide=guide,coupon=fit_coupon,stand=stand,cradle=cradle,spacer=spacer,knob=knob,
              pcb=reference_pcb,emitters=reference_emitters)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--only',choices=BUILDERS)
    args=parser.parse_args()
    initialize()
    # Restore export preferences after generation; do not change the user's defaults.
    integer_prefs={211:0,78:3}  # mm, custom mesh
    double_prefs={2:0.000005,3:math.radians(2)}  # 0.005 mm chordal / 2 degree tolerance
    toggle_prefs={69:True,70:False,71:True,191:False}
    old_int={k:APP.GetUserPreferenceIntegerValue(k) for k in integer_prefs}
    old_double={k:APP.GetUserPreferenceDoubleValue(k) for k in double_prefs}
    old_toggle={k:APP.GetUserPreferenceToggle(k) for k in toggle_prefs}
    try:
        for k,v in integer_prefs.items():APP.SetUserPreferenceIntegerValue(k,v)
        for k,v in double_prefs.items():APP.SetUserPreferenceDoubleValue(k,v)
        for k,v in toggle_prefs.items():APP.SetUserPreferenceToggle(k,v)
        for name,build in BUILDERS.items():
            if args.only is None or args.only==name:build()
    finally:
        for k,v in old_int.items():APP.SetUserPreferenceIntegerValue(k,v)
        for k,v in old_double.items():APP.SetUserPreferenceDoubleValue(k,v)
        for k,v in old_toggle.items():APP.SetUserPreferenceToggle(k,v)


if __name__=='__main__':main()
