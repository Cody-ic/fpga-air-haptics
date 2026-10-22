"""Publish checked P5 add-ons and an eight-file school order with one spare."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote
import zipfile

import layout
from check_addons import NEW_PRINTABLES

RELEASE = layout.ROOT / 'P5_addons_20261022'
PRINT_COUNTS = {'08_fixed_riser_30':2, '09_riser_bridge':3,
                '10_film_lower_stand':1, '11_film_upper_ring':1, '12_riser_fit_coupon':1}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def markdown(name, full):
    value = (layout.HERE/name).read_text(encoding='utf-8')
    def replace(match):
        label, ref = match.groups()
        target = (layout.HERE/ref).resolve()
        if target.parent == layout.HERE and target.suffix == '.md':
            dest = target.name
        elif target.parent == RELEASE and target.suffix == '.png':
            dest = 'preview/'+target.name
        else:
            relative = target.relative_to(layout.ROOT.parents[1]).as_posix()
            dest = 'https://github.com/Cody-ic/fpga-air-haptics/blob/codex/overhead-fixed-fixtures/'+quote(relative,safe='/')
        return '['+label+']('+dest+')'
    value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace, value)
    if name == 'README.md':
        first, rest = value.split('\n', 1)
        intro = ('完整装配与源文件包；只需送打印时使用单独的 8 件补充打印包。复现命令从本包 `source/` 目录运行。'
                 if full else '补充打印包：只打印 `stl/` 目录，每文件一件，共 8 件，已含备用。其余文档和图片不打印。')
        value = first+'\n\n'+intro+'\n'+rest
    return value


def check_archive(path, full, expected):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        actual = {n for n in z.namelist() if n.startswith('stl/') and n.endswith('.stl')}
        assert actual == set(expected), (path, actual)
        for name, hashvalue in expected.items():
            assert hashlib.sha256(z.read(name)).hexdigest() == hashvalue, name
        guide = z.read('学校打印需求.md').decode('utf-8')
        if not full:
            guide_names = set(re.findall(r'`(stl/[^`]+\.stl)`', guide))
            assert guide_names == actual
        for name in ('README.md', '学校打印需求.md'):
            for ref in re.findall(r'\]\(([^)]+)\)', z.read(name).decode('utf-8')):
                if not ref.startswith(('http:', 'https:', '#')):
                    assert ref in z.namelist(), (name, ref)


def main():
    layout.verify_source()
    reports = {}
    for filename in ('validation.json', 'native_validation.json'):
        reports[filename] = json.loads((layout.OUT/filename).read_text(encoding='utf-8'))
        assert reports[filename]['passed'] and reports[filename]['revision']==layout.P['revision']
    manifest = json.loads((layout.OUT/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['revision'] == layout.P['revision']
    assert set(PRINT_COUNTS) == set(NEW_PRINTABLES) and sum(PRINT_COUNTS.values())==8
    RELEASE.mkdir(exist_ok=True)
    for path in (layout.OUT/'preview').glob('*.png'):
        shutil.copy2(path, RELEASE/path.name)
    for name in ('README.md', '学校打印需求.md'):
        shutil.copy2(layout.HERE/name, RELEASE/name)
    for name in ('validation.json', 'native_validation.json', 'manifest.json'):
        shutil.copy2(layout.OUT/name, RELEASE/name)
    files = []
    piece_manifest = []
    for stem, count in PRINT_COUNTS.items():
        for i in range(1, count+1):
            piece_manifest.append(dict(file=f'stl/{stem}_{i:02}of{count:02}.stl',
                                       original=stem+'.stl', quantity=1,
                                       spare=stem=='09_riser_bridge' and i==3,
                                       fit_coupon=stem=='12_riser_fit_coupon',
                                       sha256=digest(layout.OUT/'stl'/(stem+'.stl'))))
    (RELEASE/'print_manifest.json').write_text(json.dumps(dict(
        revision=layout.P['revision'], total_pieces=8, assembly_pieces=6,
        spare_pieces=1, fit_coupons=1, files=piece_manifest),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for full, filename in ((False, 'TouchSee_P5_Addons_Print_8pcs_20261022.zip'),
                           (True, 'TouchSee_P5_Addons_CAD_20261022.zip')):
        path = RELEASE/filename
        expected = {}
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            if full:
                for stem in NEW_PRINTABLES:
                    name = 'stl/'+stem+'.stl'
                    z.write(layout.OUT/name, name)
                    expected[name] = digest(layout.OUT/name)
                for folder in ('solidworks', 'step', 'reference'):
                    for p in sorted((layout.OUT/folder).iterdir()):
                        if not p.name.startswith('~'):
                            z.write(p, folder+'/'+p.name)
                # Self-contained reproduction source tree with the byte-identical
                # released baseline. Old STLs live here for provenance, not printing.
                for p in layout.HERE.iterdir():
                    if p.is_file() and p.suffix in ('.py', '.json', '.md'):
                        z.write(p, 'source/hardware/mechanical/overhead_addons/'+p.name)
                for name in ('build_solidworks.py','assemble_solidworks.py','check_solidworks.py',
                             'export_array.py','pcb_layout.py','parameters.json'):
                    z.write(layout.ROOT/name, 'source/hardware/mechanical/'+name)
                for name in ('layout.py','parameters.json','pcb_geometry.json'):
                    z.write(layout.ROOT/'overhead_setup'/name, 'source/hardware/mechanical/overhead_setup/'+name)
                z.write(layout.ROOT/'P5_20261015/file_manifest.json',
                        'source/hardware/mechanical/P5_20261015/file_manifest.json')
                for folder in ('solidworks','step','stl','reference'):
                    for p in sorted((layout.SOURCE/folder).iterdir()):
                        if not p.name.startswith('~'):
                            z.write(p, 'source/hardware/mechanical/dist/'+layout.P['source_revision']+'/'+folder+'/'+p.name)
                for p in layout.SOURCE.glob('*.json'):
                    if p.stem.startswith(('0','REF_')):
                        z.write(p, 'source/hardware/mechanical/dist/'+layout.P['source_revision']+'/'+p.name)
                z.write(layout.ROOT.parents[1]/'desktop_app/array_geometry.py', 'source/desktop_app/array_geometry.py')
            else:
                for entry in piece_manifest:
                    z.write(layout.OUT/'stl'/entry['original'], entry['file'])
                    expected[entry['file']] = entry['sha256']
            for p in (layout.OUT/'preview').glob('*.png'):
                z.write(p, 'preview/'+p.name)
            for name in ('README.md','学校打印需求.md'):
                z.writestr(name, markdown(name, full))
            z.writestr('打印数量说明.txt', ('每个 stl/ 文件打印一件；共 8 件，含一件备用压桥和一件试片。只适用于补充打印包。\n'
                                        if not full else '本包为完整设计；送打印请使用单独的 8 件补充打印包，避免漏打重复件。\n').encode('utf-8-sig'))
            for name in ('validation.json','native_validation.json','manifest.json'):
                z.write(layout.OUT/name, 'checks/'+name)
            z.write(RELEASE/'print_manifest.json', 'print_manifest.json')
            provenance = {n:digest(layout.OUT/n) for n in expected} if full else expected
            z.writestr('checks/print_file_hashes.json', json.dumps(provenance,indent=2))
        check_archive(path, full, expected)
        files.append(dict(name=filename, sha256=digest(path), bytes=path.stat().st_size))
    (RELEASE/'SHA256SUMS.txt').write_text(''.join(f['sha256']+'  '+f['name']+'\n' for f in files),encoding='utf-8')
    (RELEASE/'package_validation.json').write_text(json.dumps(dict(
        passed=True, revision=layout.P['revision'], school_stl_count=8,
        assembly_pieces=6, spare_pieces=1, fit_coupons=1,
        geometry_unchanged_when_copying=True, markdown_file_mapping_verified=True,
        zip_integrity_verified=True, files=files),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(release=str(RELEASE),files=files),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
