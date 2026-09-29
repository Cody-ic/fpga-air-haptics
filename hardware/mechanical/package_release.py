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
    sketches=[s for d in native['documents'] for s in d.get('sketches',[])]
    assert len(sketches)==14 and all(s['constraint_status']==3 and s['fixed_relation_count']==0 for s in sketches)
    for p in (OUT/'solidworks').iterdir():
        if p.suffix.upper() in ('.SLDPRT','.SLDASM'):
            assert p.stat().st_mtime <= (OUT/'native_validation.json').stat().st_mtime, ('Recheck native CAD',p)
    for p in (OUT/'stl').glob('*.stl'):
        assert p.stat().st_mtime <= (OUT/'validation.json').stat().st_mtime, ('Recheck STL',p)
    shutil.copyfile(ROOT/'README.md',OUT/'打印与装配说明.md')
    shutil.copyfile(ROOT/'parameters.json',OUT/'parameters.json')
    summary=dict(passed=True,fully_defined_sketches=14,fixed_sketch_relations=0,
                 printable_part_types=6,printed_or_load_tested=False,
                 note='Side rails leave 94 mm forearm opening; gussets end below lowest cradle position.')
    (OUT/'constraint_update.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    included=[]
    for p in OUT.rglob('*'):
        if p.is_file() and p.suffix.lower() in ('.sldprt','.sldasm','.step','.stl','.png','.json','.md'):
            if not p.name.startswith('~'): included.append(p)
    included.sort()
    hashes={p.relative_to(OUT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in included}
    manifest=OUT/'SHA256SUMS.txt'
    manifest.write_text('\n'.join(value+'  '+name for name,value in hashes.items())+'\n',encoding='utf-8')
    archive=OUT.parent/'TouchSee_4x4_Print_P1_20260929.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in included+[manifest]: z.write(p,PARAMS['revision']+'/'+p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name,digest in hashes.items():
            assert hashlib.sha256(z.read(PARAMS['revision']+'/'+name)).hexdigest()==digest
    print(json.dumps(dict(archive=str(archive),files=len(included)+1,bytes=archive.stat().st_size,
                         sha256=hashlib.sha256(archive.read_bytes()).hexdigest()),ensure_ascii=False))


if __name__=='__main__': main()
