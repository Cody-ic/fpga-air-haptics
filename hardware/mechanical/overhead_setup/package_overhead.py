"""Publish only the current P5 checked CAD and printable STLs, never old outputs."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote
import zipfile
import layout


def main():
    out=layout.OUT
    for filename in ('validation.json','native_validation.json'):
        report=json.loads((out/filename).read_text(encoding='utf-8'))
        assert report['passed'],filename
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['revision']==layout.P['revision']
    release=layout.ROOT/'P5_20261015'
    release.mkdir(exist_ok=True)
    provenance={}
    for folder in ('stl','reference','solidworks','step'):
        for path in sorted((out/folder).iterdir()):
            if not path.name.startswith('~'):
                name=path.name if folder=='solidworks' else path.stem+path.suffix.lower()
                provenance[folder+'/'+name]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('parameters.json','pcb_geometry.json','pcb_contact_regions.json'):
        provenance['source/hardware/mechanical/overhead_setup/'+name]=hashlib.sha256((layout.HERE/name).read_bytes()).hexdigest()
    (out/'file_manifest.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for source in ('validation.json','native_validation.json','manifest.json','file_manifest.json'):
        shutil.copy2(out/source,release/source)
    for source in (out/'preview').glob('*.png'):
        if not source.name.startswith('_'):shutil.copy2(source,release/source.name)
    def packaged_markdown(filename,full):
        contents=(layout.HERE/filename).read_text(encoding='utf-8')
        def link(match):
            label,ref=match.group(1),match.group(2)
            if re.match(r'https?://|#',ref):return match.group(0)
            target=(layout.HERE/ref).resolve()
            if target.parent==layout.HERE and target.suffix=='.md':destination=target.name
            elif full and target.parent==layout.HERE:
                destination='source/hardware/mechanical/overhead_setup/'+target.name
            elif full and target.parent==release and target.suffix=='.png':
                destination='preview/'+target.name
            else:
                rel=target.relative_to(layout.ROOT.parents[1]).as_posix()
                branch='codex/overhead-fixed-fixtures' if target.parent in (layout.HERE,release) else 'main'
                destination='https://github.com/Cody-ic/fpga-air-haptics/blob/'+branch+'/'+quote(rel,safe='/')
            return '['+label+']('+destination+')'
        contents=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',link,contents)
        if filename=='README.md':
            first,rest=contents.split('\n',1)
            note=('完整 CAD 包。复现生成时先进入 `source` 目录，再执行第 8 节命令。'
                  if full else '学校打印包：只打印 `stl/` 中的零件，参考电子件不在此包；源码和预览链接指向仓库。')
            contents=first+'\n\n'+note+'\n'+rest
        return contents
    def write_zip(filename,full):
        path=release/filename
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for source in sorted((out/'stl').glob('*.stl')):
                z.write(source,'stl/'+source.stem+'.stl')
            assert len([i for i in z.namelist() if i.endswith('.stl')])==7
            if full:
                for folder in ('solidworks','step','reference'):
                    for source in sorted((out/folder).iterdir()):
                        if source.name.startswith('~'):continue
                        name=source.name if folder=='solidworks' else source.stem+source.suffix.lower()
                        z.write(source,folder+'/'+name)
                for source in sorted(layout.HERE.iterdir()):
                    if source.is_file() and source.suffix in ('.py','.json'):
                        z.write(source,'source/hardware/mechanical/overhead_setup/'+source.name)
                # Keep the import tree intact for reproduction without the PCB
                # archives. Legacy utilities load the original parameter file.
                for name in ('build_solidworks.py','assemble_solidworks.py','check_solidworks.py',
                             'export_array.py','pcb_layout.py','parameters.json'):
                    z.write(layout.ROOT/name,'source/hardware/mechanical/'+name)
                z.write(layout.ROOT.parents[1]/'desktop_app/array_geometry.py','source/desktop_app/array_geometry.py')
                for source in (out/'preview').glob('*.png'):
                    if not source.name.startswith('_'):z.write(source,'preview/'+source.name)
            for filename in ('README.md','装配清单.md'):
                z.writestr(filename,packaged_markdown(filename,full))
            for filename in ('manifest.json','validation.json','native_validation.json','file_manifest.json'):
                z.write(out/filename,'checks/'+filename)
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None
            if full:
                for entry,digest in provenance.items():
                    assert hashlib.sha256(z.read(entry)).hexdigest()==digest,entry
        return path
    files=[write_zip('TouchSee_Overhead_P5_20261015.zip',True),
           write_zip('TouchSee_Overhead_STL_P5_20261015.zip',False)]
    sums=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files)
    (release/'SHA256SUMS.txt').write_text(sums,encoding='utf-8')
    print(json.dumps(dict(release_directory=str(release),files=[dict(name=p.name,bytes=p.stat().st_size) for p in files]),ensure_ascii=False))


if __name__=='__main__':main()
