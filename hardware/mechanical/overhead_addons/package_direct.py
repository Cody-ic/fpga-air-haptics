"""Make a flat, print-only handoff ZIP from the checked eight-piece order."""
import hashlib
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parent / 'P5_addons_20261022'
NAME = 'TouchSee_P5_Print_ONLY_8pcs_20261022.zip'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    source = RELEASE / 'TouchSee_P5_Addons_Print_8pcs_20261022.zip'
    report = json.loads((RELEASE / 'package_validation.json').read_text(encoding='utf-8'))
    expected = next(f['sha256'] for f in report['files'] if f['name'] == source.name)
    assert report['passed'] and sha256(source.read_bytes()) == expected
    manifest = json.loads((RELEASE / 'print_manifest.json').read_text(encoding='utf-8'))
    assert manifest['total_pieces'] == 8 and len(manifest['files']) == 8
    instructions = (HERE / '打印说明_简版.txt').read_text(encoding='utf-8')
    files = {}
    with zipfile.ZipFile(source) as z:
        for entry in manifest['files']:
            data = z.read(entry['file'])
            assert sha256(data) == entry['sha256']
            files[Path(entry['file']).name] = data
    assert len(files) == 8
    files['00_打印说明.txt'] = instructions.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8-sig')
    target = RELEASE / NAME
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 22, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None and len(z.namelist()) == 9
        assert set(z.namelist()) == set(files)
        for name, data in files.items():
            assert '/' not in name and z.read(name) == data
        assert sum(n.lower().endswith('.stl') for n in z.namelist()) == 8
        assert sum(n.lower().endswith('.txt') for n in z.namelist()) == 1
    checks = dict(passed=True, file=NAME, bytes=target.stat().st_size,
                  sha256=sha256(target.read_bytes()), stl_files=8, instruction_files=1,
                  other_files=0, geometry_bytes_match_checked_order=True,
                  source_archive_sha256=expected,
                  files={name:sha256(data) for name,data in files.items()})
    (RELEASE / 'direct_print_validation.json').write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in checks.items() if k != 'files'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
