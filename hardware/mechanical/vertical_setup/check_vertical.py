"""Reopen all P4 native documents and verify constraints and fixed assemblies."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_vertical import sw,OUT
from check_solidworks import open_doc,inspect_features


def main():
    sw.initialize(); records=[]
    for path in sorted((OUT/'solidworks').glob('*.SLDPRT')):
        if path.name.startswith('~'): continue
        doc=open_doc(path); doc.ForceRebuild3(False)
        errors,sketches=inspect_features(doc)
        assert not errors,(path.name,errors)
        expected=json.loads((OUT/(path.stem+'.json')).read_text(encoding='utf-8'))
        assert len(sketches)==len(expected['sketches'])
        records.append(dict(file=path.name,reopened=True,feature_errors=errors,sketches=sketches))
        sw.APP.CloseDoc(doc.GetTitle()); print('Checked',path.name,flush=True)
    entries=json.loads((OUT/'assemblies.json').read_text(encoding='utf-8'))
    for path in sorted((OUT/'solidworks').glob('*.SLDASM')):
        if path.name.startswith('~'): continue
        doc=open_doc(path); doc.ForceRebuild3(False)
        a=sw.typed('IAssemblyDoc',doc); components=a.GetComponents(False)
        assert len(components)==len(entries[path.stem])
        actual=[]; names=[]
        for c in components:
            c=sw.typed('IComponent2',c); p=Path(c.GetPathName()).resolve()
            assert p.parent==(OUT/'solidworks').resolve(),p
            names.append(p.stem)
            actual.append(tuple(sw.typed('IMathTransform',c.Transform2).ArrayData))
        assert sorted(names)==sorted(i['name'] for i in entries[path.stem])
        expected=sorted(tuple(i['rotation']+[v/1000 for v in i['translation_mm']]+[1.,0.,0.,0.])
                        for i in entries[path.stem])
        assert all(abs(x-y)<1e-8 for v,w in zip(sorted(actual),expected) for x,y in zip(v,w))
        mgr=sw.typed('IInterferenceDetectionMgr',a.InterferenceDetectionManager)
        mgr.TreatCoincidenceAsInterference=False; mgr.IncludeMultibodyPartInterferences=False
        clashes=mgr.GetInterferences() or []
        print(path.name,'interferences:',len(clashes),flush=True)
        for raw in clashes:
            clash=sw.typed('IInterference',raw)
            print('Clash volume:',clash.Volume,'components:',
                  [sw.typed('IComponent2',c).Name2 for c in (clash.Components or [])],flush=True)
        mgr.Done()
        assert not clashes,(path.name,len(clashes))
        records.append(dict(file=path.name,reopened=True,component_count=len(components),
                            transforms_verified=True,local_part_references_verified=True,modeled_interferences=0))
        sw.APP.CloseDoc(doc.GetTitle())
    report=dict(passed=True,documents=records,
                scope='Printed parts; nominal PCB/transducer bodies; C1 and assumed fully wired CN1 keepout; DIN 912 screw, DIN 125 washer and conservative DIN 985 nut envelopes; 6 mm feet. Threads, drive/tool access, wrist padding, actual cable routing and remaining electronics not modeled.',
                load_or_physical_fit_tested=False)
    (OUT/'native_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
