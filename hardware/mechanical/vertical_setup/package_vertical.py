"""Package verified P4 files; STL-only school archive excludes reference objects."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

HERE=Path(__file__).resolve().parent
P=json.loads((HERE/'parameters.json').read_text(encoding='utf-8'))
OUT=HERE.parent/'dist'/P['revision']
DEST=HERE.parent/'P4_20261007'


def main():
    native=json.loads((OUT/'native_validation.json').read_text(encoding='utf-8'))
    mesh=json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    assert native['passed'] and mesh['passed']
    sketches=[s for d in native['documents'] for s in d.get('sketches',[])]
    assert all(s['constraint_status']==3 and s['fixed_relation_count']==0 for s in sketches)
    for folder,report in [('solidworks','native_validation.json'),('stl','validation.json')]:
        for f in (OUT/folder).iterdir():
            if not f.name.startswith('~'):
                assert f.stat().st_mtime <= (OUT/report).stat().st_mtime,('Revalidate',f)
    for name in ['vertical_wrist','vertical_receiver','receiver_cartridge','layout_reference','rear_wiring_clearance']:
        assert (OUT/'preview'/(name+'.png')).is_file(),name
    documentation=(HERE/'README.md').read_text(encoding='utf-8')
    documentation=documentation.replace('../README.md#5-',
        'https://github.com/Cody-ic/fpga-air-haptics/blob/main/hardware/mechanical/README.md#5-')
    documentation=documentation.replace('../../Haptics_4x4_R5_12VDC/',
        'https://github.com/Cody-ic/fpga-air-haptics/blob/main/hardware/Haptics_4x4_R5_12VDC/')
    documentation=documentation.replace('../P4_20261007/',
        'https://github.com/Cody-ic/fpga-air-haptics/blob/main/hardware/mechanical/P4_20261007/')
    (OUT/'打印与装配说明.md').write_text(documentation,encoding='utf-8')
    if (HERE/'复查记录.md').is_file():
        shutil.copyfile(HERE/'复查记录.md',OUT/'复查记录.md')
    shutil.copyfile(HERE/'parameters.json',OUT/'parameters.json')
    summary=dict(passed=True,fully_defined_sketches=len(sketches),fixed_sketch_relations=0,
                 printable_part_types=sum(m['printable'] for m in mesh['meshes']),
                 fixed_mounts_only=True,printed_or_load_tested=False)
    (OUT/'constraint_update.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    included=sorted(p for p in OUT.rglob('*') if p.is_file() and not p.name.startswith('~')
                    and p.suffix.lower() in ['.sldprt','.sldasm','.step','.stl','.png','.json','.md'])
    hashes={f.relative_to(OUT).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in included}
    manifest=OUT/'SHA256SUMS.txt'
    manifest.write_text(''.join(f'{h}  {n}\n' for n,h in hashes.items()),encoding='utf-8')
    DEST.mkdir(parents=True,exist_ok=True)
    archive=DEST/'TouchSee_Vertical_P4_20261007.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in included+[manifest]: z.write(f,P['revision']+'/'+f.relative_to(OUT).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name,digest in hashes.items():
            assert hashlib.sha256(z.read(P['revision']+'/'+name)).hexdigest()==digest
    printables=[]
    for f in sorted((OUT/'stl').glob('*.stl')):
        info=json.loads((OUT/(f.stem+'.json')).read_text(encoding='utf-8'))
        assert info['printable']; printables.append((f,info['quantity']))
    school=DEST/'TouchSee_Vertical_STL_P4_20261007.zip'
    with zipfile.ZipFile(school,'w',zipfile.ZIP_DEFLATED) as z:
        for f,quantity in printables: z.write(f,f.stem+f'_qty{quantity}.stl')
    with zipfile.ZipFile(school) as z:
        assert z.testzip() is None and len(z.namelist())==10
        assert all(n.endswith('.stl') and not n.startswith('REF_') for n in z.namelist())
    for n in ['native_validation.json','validation.json','constraint_update.json','vertical_layout.json']:
        shutil.copyfile(OUT/n,DEST/n)
    if (OUT/'复查记录.md').is_file(): shutil.copyfile(OUT/'复查记录.md',DEST/'复查记录.md')
    for n in ['vertical_wrist','vertical_receiver','receiver_cartridge','layout_reference','rear_wiring_clearance']:
        shutil.copyfile(OUT/'preview'/(n+'.png'),DEST/(n+'.png'))
    (DEST/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name+'\n'
                                            for f in [archive,school]),encoding='utf-8')
    print(json.dumps(dict(archives=[str(archive),str(school)],**summary),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
