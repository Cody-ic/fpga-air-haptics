"""Package checked native CAD, neutral files, print meshes and instructions."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parent
PARAMS=json.loads((ROOT/'parameters.json').read_text(encoding='utf-8'))
OUT=ROOT/'dist'/PARAMS['revision']


def main():
    native=json.loads((OUT/'native_validation.json').read_text(encoding='utf-8'))
    mesh=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    travel=json.loads((OUT/'travel_validation.json').read_text(encoding='utf-8'))
    assert native['passed'] and mesh['passed'] and travel['passed']
    assert mesh['common_datum']['modeled_face_height_spread_mm'] < 1e-5
    assert all(r['window_clear'] for r in mesh['common_datum']['resistor_checks'])
    sketches=[s for d in native['documents'] for s in d.get('sketches',[])]
    assert len(sketches)==15 and all(s['constraint_status']==3 and s['fixed_relation_count']==0 for s in sketches)
    for p in (OUT/'solidworks').iterdir():
        if not p.name.startswith('~') and p.suffix.upper() in ('.SLDPRT','.SLDASM'):
            assert p.stat().st_mtime <= (OUT/'native_validation.json').stat().st_mtime, ('Recheck native CAD',p)
    for p in (OUT/'stl').glob('*.stl'):
        assert p.stat().st_mtime <= (OUT/'validation.json').stat().st_mtime, ('Recheck STL',p)
    shutil.copyfile(ROOT/'README.md',OUT/'打印与装配说明.md')
    shutil.copyfile(ROOT/'parameters.json',OUT/'parameters.json')
    summary=dict(passed=True,fully_defined_sketches=len(sketches),fixed_sketch_relations=0,
                 printable_part_types=6,printed_or_load_tested=False,
                 note='P3 flat guide without PCB support posts; external PCB holder required; common Z0 datum preserved.')
    (OUT/'constraint_update.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    included=[]
    for p in OUT.rglob('*'):
        if p.is_file() and p.suffix.lower() in ('.sldprt','.sldasm','.step','.stl','.png','.json','.md'):
            if not p.name.startswith('~'): included.append(p)
    included.sort()
    hashes={p.relative_to(OUT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in included}
    manifest=OUT/'SHA256SUMS.txt'
    manifest.write_text('\n'.join(value+'  '+name for name,value in hashes.items())+'\n',encoding='utf-8')
    archive=OUT.parent/('TouchSee_4x4_Print_'+PARAMS['revision'].replace('-','_')+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in included+[manifest]: z.write(p,PARAMS['revision']+'/'+p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name,digest in hashes.items():
            assert hashlib.sha256(z.read(PARAMS['revision']+'/'+name)).hexdigest()==digest
    print(json.dumps(dict(archive=str(archive),files=len(included)+1,bytes=archive.stat().st_size,
                         sha256=hashlib.sha256(archive.read_bytes()).hexdigest()),ensure_ascii=False))
    # The school accepts STL only; keep reference solids out of this small update.
    school=OUT.parent/('TouchSee_Jig_STL_'+PARAMS['revision'].replace('-','_')+'.zip')
    names={'01_array_alignment_jig':'01_换能器等高定位夹具_打印1件.stl',
           '02_bore_fit_coupon':'02_孔径试片_先打印1件.stl'}
    with zipfile.ZipFile(school,'w',zipfile.ZIP_DEFLATED) as z:
        for stem,name in names.items():
            z.write(OUT/'stl'/(stem+'.stl'),name)
    with zipfile.ZipFile(school) as z:
        assert z.testzip() is None and set(z.namelist())==set(names.values())
    print(json.dumps(dict(school_stl_archive=str(school),bytes=school.stat().st_size,
                         sha256=hashlib.sha256(school.read_bytes()).hexdigest()),ensure_ascii=False))


if __name__=='__main__': main()
