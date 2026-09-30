"""Reopen native CAD, check features/interference and optionally refine STL exports."""
import argparse
import json
import math
from pathlib import Path
import pythoncom
import win32com.client as wc
import build_solidworks as sw


def open_doc(path):
    spec = sw.typed('IDocumentSpecification', sw.APP.GetOpenDocSpec(str(path)))
    spec.Silent = True
    spec.ReadOnly = True
    spec.LightWeight = False
    spec.UseLightWeightDefault = False
    result = sw.APP.OpenDoc7(spec)
    assert result and not spec.Error, (path.name, spec.Error)
    return sw.typed('IModelDoc2', result)


def inspect_features(doc):
    errors, sketches, seen = [], [], set()

    def visit(raw):
        feature = sw.typed('IFeature', raw)
        key = feature.Name
        if key in seen: return
        seen.add(key)
        code = feature.GetErrorCode()
        if code: errors.append([key, code])
        if feature.GetTypeName2() in ('ProfileFeature', '3DProfileFeature'):
            sketch = sw.typed('ISketch', feature.GetSpecificFeature2())
            manager = sw.typed('ISketchRelationManager', sketch.RelationManager)
            relations = manager.GetRelations(0) or []
            kinds = [sw.typed('ISketchRelation', r).GetRelationType() for r in relations]
            record = dict(name=key, constraint_status=sketch.GetConstrainedStatus(),
                          relation_count=len(kinds), fixed_relation_count=kinds.count(17))
            assert record['constraint_status'] == 3 and record['fixed_relation_count'] == 0, record
            sketches.append(record)
        child = feature.GetFirstSubFeature()
        while child:
            visit(child)
            child = sw.typed('IFeature', child).GetNextSubFeature()

    feature = doc.FirstFeature()
    while feature:
        visit(feature)
        feature = sw.typed('IFeature', feature).GetNextFeature()
    return errors, sketches


def check_travel(doc, assembly):
    math_utility = sw.typed('IMathUtility', sw.APP.GetMathUtility())
    movable = []
    for raw in assembly.GetComponents(False):
        component = sw.typed('IComponent2', raw)
        if any(name in component.GetPathName() for name in ('04_wrist_cradle', '06_m4_nut_knob')):
            movable.append((component, list(sw.typed('IMathTransform', component.Transform2).ArrayData)))
    assert len(movable) == 3
    checks = []
    try:
        for height in (82, 142):
            for component, original in movable:
                transform = original[:]
                transform[11] += (height-sw.PARAMS['wrist_bolt_height_mm'])/1000
                component.Transform2 = math_utility.CreateTransform(wc.VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_R8, transform))
                actual = sw.typed('IMathTransform', component.Transform2).ArrayData
                assert max(abs(a-b) for a,b in zip(transform, actual)) < 1e-8
            doc.ForceRebuild3(False)
            manager = sw.typed('IInterferenceDetectionMgr', assembly.InterferenceDetectionManager)
            manager.TreatCoincidenceAsInterference = False
            manager.IncludeMultibodyPartInterferences = False
            count = len(manager.GetInterferences() or [])
            manager.Done()
            assert count == 0, (height, count)
            checks.append(dict(bolt_height_mm=height, modeled_interferences=count))
    finally:
        for component, original in movable:
            component.Transform2 = math_utility.CreateTransform(wc.VARIANT(pythoncom.VT_ARRAY|pythoncom.VT_R8, original))
        doc.ForceRebuild3(False)
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refine-stl', action='store_true')
    args = parser.parse_args()
    sw.initialize()
    preferences = [('IntegerValue', 211, 0), ('IntegerValue', 78, 3),
                   ('DoubleValue', 2, .000005), ('DoubleValue', 3, math.radians(2)),
                   ('Toggle', 69, True), ('Toggle', 70, False),
                   ('Toggle', 71, True), ('Toggle', 191, False)]
    old = [(kind, key, getattr(sw.APP, 'GetUserPreference'+kind)(key))
           for kind, key, value in preferences]
    results = []
    travel_checks = []
    try:
        for kind, key, value in preferences:
            getattr(sw.APP, 'SetUserPreference'+kind)(key, value)
        for path in sorted((sw.OUT/'solidworks').glob('*.SLDPRT')):
            if path.name.startswith('~'):
                continue
            doc = open_doc(path)
            doc.ForceRebuild3(False)
            errors, sketches = inspect_features(doc)
            assert not errors, (path.name, errors)
            expected_sketches = json.loads((sw.OUT/(path.stem+'.json')).read_text(encoding='utf-8'))['sketches']
            assert len(sketches) == len(expected_sketches), (path.name, sketches)
            if args.refine_stl:
                folder = 'reference' if path.stem.startswith('REF_') else 'stl'
                assert doc.SaveAs3(str(sw.OUT/folder/(path.stem+'.stl')), 0, 1) == 0
            results.append(dict(file=path.name, reopened=True, feature_errors=errors, sketches=sketches))
            sw.APP.CloseDoc(doc.GetTitle())
            print('Checked', path.name, flush=True)
        transforms = json.loads((sw.OUT/'assemblies.json').read_text(encoding='utf-8'))
        for path in sorted((sw.OUT/'solidworks').glob('*.SLDASM')):
            if path.name.startswith('~'):
                continue
            doc = open_doc(path)
            doc.ForceRebuild3(False)
            assembly = sw.typed('IAssemblyDoc', doc)
            components = assembly.GetComponents(False)
            assert len(components) == len(transforms[path.stem])
            resolved_paths = [Path(sw.typed('IComponent2', c).GetPathName()).resolve() for c in components]
            assert all(p.parent == (sw.OUT/'solidworks').resolve() for p in resolved_paths), resolved_paths
            assert sorted(p.stem for p in resolved_paths) == sorted(i['name'] for i in transforms[path.stem])
            expected = sorted(tuple(item['rotation']+[v/1000 for v in item['translation_mm']]+[1.,0.,0.,0.])
                              for item in transforms[path.stem])
            actual = sorted(tuple(sw.typed('IMathTransform', sw.typed('IComponent2', c).Transform2).ArrayData)
                            for c in components)
            assert all(abs(a-b)<1e-8 for row_a,row_b in zip(actual,expected) for a,b in zip(row_a,row_b))
            manager = sw.typed('IInterferenceDetectionMgr', assembly.InterferenceDetectionManager)
            manager.TreatCoincidenceAsInterference = False
            manager.IncludeMultibodyPartInterferences = False
            interferences = manager.GetInterferences()
            count = len(interferences or [])
            manager.Done()
            assert count == 0, (path.name, count)
            results.append(dict(file=path.name, reopened=True, component_count=len(components),
                                transforms_verified=True, local_part_references_verified=True,
                                modeled_interferences=count))
            if path.stem == 'Wrist_support_assembly':
                travel_checks = check_travel(doc, assembly)
            sw.APP.CloseDoc(doc.GetTitle())
            print('Checked', path.name, flush=True)
    finally:
        for kind, key, value in old:
            getattr(sw.APP, 'SetUserPreference'+kind)(key, value)
    report = dict(passed=True, scope='Native parts, common datum, nominal emitters/PCB and assumed front resistor envelopes; fasteners/padding/back electronics not modeled',
                  documents=results)
    (sw.OUT/'native_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert len(travel_checks) == 2
    (sw.OUT/'travel_validation.json').write_text(json.dumps(dict(passed=True, checks=travel_checks),indent=2),encoding='utf-8')


if __name__ == '__main__': main()
