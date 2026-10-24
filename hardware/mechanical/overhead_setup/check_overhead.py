"""Reopen native P5 files; inspect real constraints, references and BREP clashes."""
import json
from pathlib import Path
import build_overhead as build
import build_solidworks as sw
from check_solidworks import open_doc,inspect_features
import layout


def main():
    build.initialize()
    results=[]
    for path in sorted((layout.OUT/'solidworks').glob('*.SLDPRT')):
        if path.name.startswith('~'):continue
        doc=open_doc(path)
        doc.ForceRebuild3(False)
        errors,sketches=inspect_features(doc)
        assert not errors,(path.name,errors)
        expected=json.loads((layout.OUT/(path.stem+'.json')).read_text(encoding='utf-8'))
        assert len(sketches)==len(expected['sketches']),(path.name,len(sketches),len(expected['sketches']))
        bodies=sw.typed('IPartDoc',doc).GetBodies2(0,False)
        assert len(bodies)==expected['solid_count']
        results.append(dict(file=path.name,reopened=True,feature_errors=errors,sketches=sketches,solid_count=len(bodies)))
        sw.APP.CloseDoc(doc.GetTitle())
        print('CHECKED '+path.name,flush=True)
    placements=json.loads((layout.OUT/'assemblies.json').read_text(encoding='utf-8'))
    failures=[]
    for path in sorted((layout.OUT/'solidworks').glob('*.SLDASM')):
        if path.name.startswith('~'):continue
        doc=open_doc(path);doc.ForceRebuild3(False)
        assembly=sw.typed('IAssemblyDoc',doc)
        components=assembly.GetComponents(False)
        expected=placements[path.stem]
        assert len(components)==len(expected),(path.name,len(components),len(expected))
        actual_paths=[Path(sw.typed('IComponent2',c).GetPathName()).resolve() for c in components]
        assert all(p.parent==(layout.OUT/'solidworks').resolve() for p in actual_paths),actual_paths
        assert sorted(p.stem for p in actual_paths)==sorted(i['name'] for i in expected)
        matrices=sorted(tuple(i['rotation']+[v/1000 for v in i['translation_mm']]+[1.,0.,0.,0.]) for i in expected)
        actual=sorted(tuple(sw.typed('IMathTransform',sw.typed('IComponent2',c).Transform2).ArrayData) for c in components)
        assert all(abs(a-b)<1e-8 for x,y in zip(matrices,actual) for a,b in zip(x,y)),path.name
        manager=sw.typed('IInterferenceDetectionMgr',assembly.InterferenceDetectionManager)
        manager.TreatCoincidenceAsInterference=False
        manager.IncludeMultibodyPartInterferences=False
        raw=manager.GetInterferences() or []
        clashes=[]
        for interference in raw:
            it=sw.typed('IInterference',interference)
            parts=[sw.typed('IComponent2',c).Name2 for c in (it.Components or [])]
            clashes.append(dict(components=parts,volume_mm3=it.Volume*1e9))
        manager.Done()
        results.append(dict(file=path.name,reopened=True,component_count=len(components),
                            transforms_verified=True,local_part_references_verified=True,
                            modeled_interferences=clashes))
        if clashes:failures.append((path.name,clashes))
        sw.APP.CloseDoc(doc.GetTitle());print('CHECKED '+path.name+' clashes='+str(len(clashes)),flush=True)
    report=dict(passed=not failures,revision=layout.P['revision'],documents=results,
                scope='Native fully defined sketches with no Fix relation; local assembly paths and transforms; BREP clashes between printable components, nominal source-PCB electronics and standard fasteners. Internal overlaps of reference-envelope bodies excluded.',
                measured_hardware_fit=False,printed_or_load_tested=False)
    (layout.OUT/'native_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not failures,failures
    print(json.dumps(dict(passed=True,documents=len(results),sketches=sum(len(r.get('sketches',[])) for r in results))),flush=True)


if __name__=='__main__':main()
