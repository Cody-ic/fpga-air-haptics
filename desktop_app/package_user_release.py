"""Package the user workspace separately from existing engineering releases."""

from datetime import datetime, timezone
import argparse
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from desktop_app import __version__
    app = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='Package in a fresh directory while an older executable is open')
    args = parser.parse_args()
    dist = args.output_dir.resolve() if args.output_dir else app/'dist'/'user'
    dist.mkdir(parents=True, exist_ok=True)
    portable = app/'dist'/'user'/'TouchSee-User'
    shutil.copytree(portable/'_internal', dist/'_internal', dirs_exist_ok=True)
    shutil.copy2(portable/'触见体验台.exe', dist/'触见体验台.exe')
    exe = dist/'触见体验台.exe'
    if not exe.is_file():
        raise FileNotFoundError(exe)
    info = {'built_at':datetime.now(timezone.utc).isoformat(), 'edition':'user', 'version':__version__,
            'executable':exe.name, 'sha256':sha256(exe.read_bytes()).hexdigest(),
            'python':platform.python_version(), 'packages':{d.metadata['Name']:d.version for d in metadata.distributions()}}
    root = app.parent
    try:
        git = ['git', '-c', 'safe.directory='+root.as_posix(), '-C', str(root)]
        info['source_revision'] = subprocess.check_output(git+['rev-parse', 'HEAD'], text=True).strip()
        info['source_dirty'] = bool(subprocess.check_output(git+['status', '--porcelain'], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        info['source_revision'] = None
        info['source_dirty'] = None
    info['desktop_sources_sha256'] = {
        path.relative_to(root).as_posix():sha256(path.read_bytes()).hexdigest()
        for path in sorted(app.glob('*.py'))}
    (dist/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    guide = dist/'使用说明.md'
    guide.write_bytes((app/'USER_GUIDE.md').read_bytes())
    archive = dist/'TouchSee-User-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in [exe,guide,dist/'build-info.json']:
            bundle.write(path,path.name)
        for path in (dist/'_internal').rglob('*'):
            if path.is_file():
                bundle.write(path,path.relative_to(dist).as_posix())
        for path in (app/'assets'/'egopressure').iterdir():
            if path.suffix in ('.md', '.json'):
                bundle.write(path,'DATA_LICENSES/EgoPressure/'+path.name)
        for distribution in metadata.distributions():
            for file in distribution.files or []:
                if any(p.endswith('.dist-info') for p in file.parts) and (
                        file.name=='METADATA' or any('license' in p.lower() or 'copying' in p.lower() or 'notice' in p.lower() for p in file.parts)):
                    source = Path(distribution.locate_file(file))
                    if source.is_file():
                        bundle.write(source,f'THIRD_PARTY_LICENSES/{distribution.metadata["Name"]}/{file.as_posix()}')
        bundle.write(Path(platform.__file__).parents[1]/'LICENSE.txt','THIRD_PARTY_LICENSES/Python-LICENSE.txt')
        for path in (Path(sys.base_prefix)/'tcl').rglob('license.terms'):
            bundle.write(path,'THIRD_PARTY_LICENSES/TclTk/'+path.relative_to(Path(sys.base_prefix)/'tcl').as_posix())
    print(archive)


if __name__=='__main__':
    main()
