"""Build an XIAO MG24 image in an ASCII workspace; never uploads automatically."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', default=os.environ.get('ARDUINO_CLI',
        'D:/tools/arduino-cli/arduino-cli.exe' if os.name == 'nt' else 'arduino-cli'))
    parser.add_argument('--config', help='Arduino CLI configuration file')
    parser.add_argument('--mode', choices=('business', 'adc-test'), default='business')
    parser.add_argument('--stage', default='D:/FPGADev/mg24-build' if os.name == 'nt' else '/tmp/haptics-mg24-build')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    stage = Path(args.stage).resolve()/args.mode
    if not str(stage).isascii():
        parser.error('Use an ASCII staging path')
    sketch = stage/'haptics_mg24'
    sketch.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for src in (root/'haptics_mg24').iterdir():
        if src.suffix in ('.h', '.cpp', '.ino'):
            shutil.copy2(src, sketch/src.name)
            hashes[src.name] = hashlib.sha256(src.read_bytes()).hexdigest()
    fqbn = 'SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs'
    command = [args.cli]
    if args.config:
        command += ['--config-file', args.config]
    command += ['compile', '--fqbn', fqbn, '--warnings', 'all',
        '--build-property', f'compiler.cpp.extra_flags=-DHAPTICS_MG24_MODE={0 if args.mode == "business" else 1}',
        '--build-path', str(stage/'build'), '--output-dir', str(stage/'output'), str(sketch)]
    result = subprocess.run(command)
    report = dict(mode=args.mode, fqbn=fqbn, exit_code=result.returncode,
                  sources=hashes, flashed=False)
    (stage/'build_manifest.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
