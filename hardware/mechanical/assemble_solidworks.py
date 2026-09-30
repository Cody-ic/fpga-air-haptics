"""Create fixed-position native reference assemblies, preserving editable parts."""
import json
import pythoncom
import win32com.client as wc
import build_solidworks as sw

IDENTITY=[1.,0.,0.,0.,1.,0.,0.,0.,1.]
RX_NEG90=[1.,0.,0.,0.,0.,-1.,0.,1.,0.]
RX_POS90=[1.,0.,0.,0.,0.,1.,0.,-1.,0.]


def item(name, xyz, rotation=IDENTITY, color='#467792'):
    return dict(name=name,translation_mm=xyz,rotation=rotation,color=color)


def placements():
    p=sw.PARAMS
    bottom=p['base_thickness_mm']+p['pcb_standoff_mm']
    top=bottom+p['pcb_thickness_assumed_mm']
    emitter_bottom=top+p['datum_to_pcb_front_mm']-p['body_nominal_height_mm']
    z=p['wrist_bolt_height_mm']
    stand=[item('03_wrist_and_board_base',[0,0,0],color='#324c65'),
           item('04_wrist_cradle',[0,-95,z+8],RX_NEG90,'#eab35b'),
           item('REF_pcb_80x88',[0,0,bottom],color='#258572'),
           item('REF_16_emitters',[0,0,emitter_bottom],color='#cbd2dc')]
    stand += [item('05_pcb_standoff_35',[x,y,p['base_thickness_mm']],color='#4d6376')
              for x,y in p['mount_centers_mm']]
    stand += [item('06_m4_nut_knob',[x,-106,z],RX_POS90,'#e4a345') for x in [-55,55]]
    jig=[item('01_array_alignment_jig',[0,0,0],color='#476f94'),
         item('REF_common_flat_datum',[0,0,0],color='#bdcdd5'),
         item('REF_16_emitters',[0,0,0],color='#cbd2dc'),
         item('REF_top_resistor_envelopes',[0,0,p['datum_to_pcb_front_mm']],color='#d39748'),
         item('REF_pcb_80x88',[0,0,p['datum_to_pcb_front_mm']],color='#258572')]
    return {'Wrist_support_assembly':stand,'Array_jig_assembly':jig}


def make_assembly(name, items):
    doc=sw.typed('IModelDoc2',sw.APP.NewDocument(str(sw.TEMPLATES/'gb_assembly.asmdot'),0,0.,0.))
    assembly=sw.typed('IAssemblyDoc',doc)
    files=[str(sw.OUT/'solidworks'/(i['name']+'.SLDPRT')) for i in items]
    transforms=[]
    for i in items:transforms += i['rotation']+[v/1000 for v in i['translation_mm']]+[1.,0.,0.,0.]
    components=assembly.AddComponents3(wc.VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_BSTR,files),
                                     wc.VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_R8,transforms),
                                     wc.VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_BSTR,['']*len(items)))
    assert len(components)==len(items)
    for comp,entry in zip(components,items):
        c=sw.typed('IComponent2',comp)
        entry['actual_transform']=list(sw.typed('IMathTransform',c.Transform2).ArrayData)
        doc.ClearSelection2(True)
        c.Select4(False,None,False)
        assembly.FixComponent()
    doc.ClearSelection2(True)
    doc.ForceRebuild3(False)
    doc.ShowNamedView2('*Isometric',7);doc.ViewZoomtofit2()
    assert doc.SaveAs3(str(sw.OUT/'solidworks'/(name+'.SLDASM')),0,1)==0
    assert doc.SaveAs3(str(sw.OUT/'step'/(name+'.step')),0,1)==0
    doc.SaveBMP(str(sw.OUT/'preview'/(name+'.bmp')),1400,1000)
    sw.APP.CloseDoc(doc.GetTitle())
    print('Saved assembly',name,flush=True)


if __name__=='__main__':
    sw.initialize()
    assemblies=placements()
    for name,items in assemblies.items():make_assembly(name,items)
    (sw.OUT/'assemblies.json').write_text(json.dumps(assemblies,ensure_ascii=False,indent=2),encoding='utf-8')
