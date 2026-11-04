"""Build the 8x8 control/display candidate with explicit calibration semantics.

Startup retains the complete solver; restore requires a registered measured
record. Neither path substitutes an uncalibrated or preview-only image.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'fpga/preview'))
from fpga.board.build_calibration import load_restore
import build_display_restore


def load_profile(path):
    profile = json.loads(Path(path).read_text(encoding='utf-8'))
    hw, tx = profile['hardware'], profile['transmitter']
    if (profile.get('schema') != 1 or (hw['rows'], hw['cols'], hw['pitch_um']) != (8, 8, 10300)
            or tx['calibration_required'] is not True or tx['auto_start'] is not False
            or tx['default_calibration_mode'] != 'startup' or tx['startup_source'] != 'adc'
            or tx['expected_cal_profile'] != 1 or tx['expected_preview_only'] != 0
            or profile['calibration']['reference_channel'] != 0):
        raise ValueError('正式发布配置必须为8×8、10.3 mm、完整启动校准与上电待机')
    return profile


def validate_calibration(profile, override=None):
    cal = profile['calibration']
    path = Path(override).resolve() if override else ROOT/cal['file']
    if not path.is_file():
        raise ValueError('缺少完整实测校准文件：'+str(path)+'。正式构建已停止，不能用零补偿替代。')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != cal['sha256']:
        raise ValueError('校准文件校验值与发布配置不一致；请核对测量记录并更新发布配置')
    _, info = load_restore(path, cal['reference_height_mm'], cal['reference_channel'], cal['measurement_level'])
    return path, info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=ROOT/'release/software.json')
    parser.add_argument('--calibration-mode', choices=('startup', 'restore'),
                        help='startup保留完整板内校准；restore恢复登记的真实补偿，默认startup')
    parser.add_argument('--calibration-file', type=Path, help='同一已记录实测文件的备用位置，仍核对发布SHA-256')
    parser.add_argument('--output-root', type=Path, default=ROOT/'.runtime/final-software')
    parser.add_argument('--gowin', default='gw_sh')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--validate-only', action='store_true')
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--existing-manifest', type=Path,
                      help='打包已完成的同模式构建，重新核对位流和全部暂存源码哈希，不重新综合')
    args = parser.parse_args(argv)
    profile = load_profile(args.profile)
    selected = args.calibration_mode or profile['transmitter']['default_calibration_mode']
    if selected == 'startup' and args.calibration_file:
        parser.error('--calibration-file requires --calibration-mode restore')
    calibration, info = validate_calibration(profile, args.calibration_file) if selected == 'restore' else (None, None)
    if args.validate_only:
        print('VALIDATED', profile['version'], selected,
              info['sha256'] if info else '完整板内算法；尚需实际测量，不含恢复补偿')
        return
    cal = profile['calibration']
    output = args.output_root.resolve()/profile['version']
    source = profile['transmitter']['startup_source']
    if not args.existing_manifest:
        build_display_restore.main([*(['--calibration-file', str(calibration)] if selected == 'restore'
                                     else ['--startup-calibration', source]),
            '--calibration-z-mm', str(cal['reference_height_mm']),
            '--calibration-level', str(cal['measurement_level']),
            '--calibration-reference', str(cal['reference_channel']),
            '--output-root', str(output), '--gowin', args.gowin,
            *(['--prepare-only'] if args.prepare_only else [])])
    if args.prepare_only:
        return
    folder = f"z{cal['reference_height_mm']}" if selected == 'restore' else f"startup_{source}_z{cal['reference_height_mm']}"
    manifest_path = args.existing_manifest.resolve() if args.existing_manifest else output/folder/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    expected_mode = 'display_restore' if selected == 'restore' else 'display_startup_'+source
    startup = manifest.get('startup_calibration') or {}
    if (manifest.get('mode') != expected_mode or manifest.get('auto_start') is not False
            or manifest.get('calibration_applied') is not (selected == 'restore')
            or (selected == 'startup' and (startup.get('solver_on_fpga') is not True
                                          or startup.get('measurement_required') is not True
                                          or startup.get('verification_required') is not True))):
        raise RuntimeError('生成产物不符合正式发射与显示配置')
    if selected == 'startup' and (startup.get('default_position_um') != [0, 0, cal['reference_height_mm']*1000]
                                  or startup.get('default_level') != cal['measurement_level']
                                  or startup.get('default_reference') != cal['reference_channel']):
        raise RuntimeError('启动校准条件与发布配置不一致')
    for name, digest in manifest['sources'].items():
        staged = manifest_path.parent/'src'/name
        if Path(name).name != name or not staged.is_file() or hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
            raise RuntimeError('构建暂存源码哈希不一致：'+name)
    bundle = output/'delivery'
    bundle.mkdir(parents=True, exist_ok=True)
    label = 'Restored' if selected == 'restore' else 'StartupCalibration'
    name = f"TouchSee_8x8_{label}_Display_{profile['version']}.fs"
    firmware = bundle/name
    shutil.copy2(manifest['output'], firmware)
    if calibration is not None:
        shutil.copy2(calibration, bundle/calibration.name)
    if hashlib.sha256(firmware.read_bytes()).hexdigest() != manifest['sha256']:
        raise RuntimeError('位流校验值与构建记录不一致')
    try:
        git = ['git', '-c', 'safe.directory='+ROOT.as_posix(), '-C', str(ROOT)]
        revision = subprocess.check_output(git+['rev-parse', 'HEAD'], text=True).strip()
        dirty = bool(subprocess.check_output(git+['status', '--porcelain'], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        revision = None
        dirty = None
    expected = dict(entry_protocol='CAL2', output=0, cal_profile_after_successful_release=1) if selected == 'startup' else dict(
        hw_rows=8, hw_cols=8, hw_pitch_um=10300, fw_id='HAP3_STATIC_1',
        cal_profile=2, preview_only=0, output=0, phase_offsets=info['phase_offsets'])
    release = dict(version=profile['version'], status='built_requires_measurement' if selected == 'startup' else 'built_requires_hardware_readback',
                   calibration_mode=selected, source_revision=revision, source_dirty=dirty,
                   firmware=name, firmware_sha256=hashlib.sha256(firmware.read_bytes()).hexdigest(),
                   calibration=info, build=manifest, programmed=False, expected_readback=expected)
    (bundle/'manifest.json').write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding='utf-8')
    instructions = ('本镜像保留完整四步校准算法，不含旧实测修正。上电进入CAL2等待，不自动发射。\n'
        '接好MG24接收端后，使用独立校准工具完成真实测量、求解、复验及RELEASE，才进入HAP3业务。\n'
        '补偿驻留本次上电寄存器，复位后需重做；保存完整JSON可再构建restore镜像。\n'
        if selected == 'startup' else '本镜像只恢复随包实测补偿，不重新校准。核对全部64项修正及固件标识。\n')
    (bundle/'烧录说明.txt').write_text(instructions+
        '烧录操作选择Arora V外部Flash写入并校验；构建器不自动烧录。\n', encoding='utf-8')
    print('BUILT', bundle)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(str(error))
