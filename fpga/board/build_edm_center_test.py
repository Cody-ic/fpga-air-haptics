"""Isolated center-only phase-shaping experiment. Never programs or emits.

Measured calibration stays untouched. The staged output correction vector is
explicitly composed from measured offsets plus a simulated shaping delta.
All user configurations are restricted to the calibrated center at 70 mm.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fpga.board.build_calibration import load_restore, check_timing
from desktop_app.model import Config, ArraySpec, focus_phases
from desktop_app.protocol import encode, decode

FOLDER = ROOT/'.runtime/fpga-edm-center-phase-only'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replace_once(text, pattern, value):
    text, count = re.subn(pattern, lambda m: m[1]+str(value), text)
    if count != 1:
        raise ValueError('Expected one parameter: '+pattern)
    return text


def prepare(args):
    _, measured = load_restore(args.calibration_file)
    weights = np.load(args.weights_file)
    base = weights['current_weights']
    narrow = weights['edm_narrow_phase_only_weights']
    if base.shape != (64,) or narrow.shape != (64,):
        raise ValueError('64 channels required')
    if not np.all(np.isfinite(narrow)) or not np.allclose(abs(narrow), 1, atol=1e-12):
        raise ValueError('Only constant-amplitude phase-only weights allowed')
    codes = lambda q: np.rint(np.remainder(np.angle(q)/(2*np.pi), 1)*64).astype(int)%64
    nominal = list(map(int, focus_phases(Config(), (0,0,70), ArraySpec(8,8,10300))))
    if not np.array_equal(codes(base), nominal):
        raise ValueError('Simulation baseline is not current center phase table')
    target = codes(narrow)
    if not np.allclose(narrow, np.exp(2j*np.pi*target/64), atol=1e-10):
        raise ValueError('Input is not quantized to 64 phases')
    delta = ((target-codes(base))%64).tolist()
    combined = [(a+b)%64 for a,b in zip(measured['phase_offsets'], delta)]
    expected = [(int(a)+b)%64 for a,b in zip(target, measured['phase_offsets'])]
    assert expected == [(a+b)%64 for a,b in zip(nominal, combined)]
    assert any(delta)
    packed = sum(v << (8*i) for i,v in enumerate(combined))
    staging=FOLDER/'rtl';staging.mkdir(parents=True,exist_ok=True)
    for file in (ROOT/'fpga/rtl').glob('*.v'):
        shutil.copy2(file,staging/file.name)
    top=staging/'board_top.v';text=top.read_text(encoding='utf-8')
    text=replace_once(text,r'(`define FPGA_CALIBRATION_ENABLE )\d+',1)
    values={'CAL_RESTORE_ENABLE':1,'BUTTON_FOCUS_ENABLE':1,'BUTTON_FOCUS_MS':6000,
            'BUTTON_FOCUS_Z_UM':70000,'BUTTON_FOCUS_LEVEL':100,
            'WS_X_MIN_UM':0,'WS_X_MAX_UM':0,'WS_Y_MIN_UM':0,'WS_Y_MAX_UM':0,
            'WS_Z_MIN_UM':70000,'WS_Z_MAX_UM':70000}
    for key,value in values.items():
        text=replace_once(text,r'(parameter integer\s+'+key+r'\s*=\s*)-?\d+',value)
    text=replace_once(text,r"(parameter \[511:0\] CAL_RESTORE_OFFSETS = )512'd0",f"512'h{packed:0128x}")
    top.write_text(text,encoding='utf-8')
    for suffix in ('cst','sdc'):
        shutil.copy2(ROOT/f'fpga/board/tang_mega_138k_pro.{suffix}',FOLDER/f'tang_mega_138k_pro.{suffix}')
    path=lambda p:'{'+str(p.resolve()).replace('\\','/')+'}'
    (FOLDER/'build.tcl').write_text('\n'.join([
        f'create_project -name edm_center -dir {path(FOLDER)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force',
        f'add_file {{*}}[lsort [glob -directory {path(staging)} *.v]]',
        *[f'add_file {path(FOLDER/f"tang_mega_138k_pro.{suffix}")}' for suffix in ('cst','sdc')],
        'set_option -top_module board_top','set_option -output_base_name board_top',
        'set_option -use_cpu_as_gpio 1','set_option -use_sspi_as_gpio 1',
        'set_option -resource_sharing 0','set_option -num_critical_paths 100',
        'run syn','run pnr','exit','']),encoding='utf-8')
    manifest=dict(mode='experimental_edm_phase_only_center',target_um=[0,0,70000],
        measured_calibration=measured,weights_file=str(Path(args.weights_file).resolve()),weights_sha256=digest(args.weights_file),
        source_case='edm_narrow_phase_only',phase_deltas=delta,effective_output_offsets=combined,
        expected_center_phases=expected,level=100,button_deadline_ms=6000,
        amplitude_control=False,geometry_locked=True,acoustic_validation=False,
        sources={p.name:digest(p) for p in sorted(staging.glob('*.v'))},
        constraints={p.name:digest(p) for p in FOLDER.glob('tang_mega_138k_pro.*')},
        output=str(FOLDER/'edm_center/impl/pnr/board_top.fs'))
    (FOLDER/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('PREPARED isolated phase-only center image; measured calibration unchanged',flush=True)


def read_manifest():
    m=json.loads((FOLDER/'manifest.json').read_bytes())
    for name,sha in m['sources'].items():
        assert digest(FOLDER/'rtl'/name)==sha
    for name,sha in m['constraints'].items():
        assert digest(FOLDER/name)==sha
    assert digest(m['weights_file'])==m['weights_sha256']
    assert digest(m['measured_calibration']['file'])==m['measured_calibration']['sha256']
    return m


def test(args):
    m=read_manifest();folder=FOLDER/'test';folder.mkdir(exist_ok=True)
    tb=(ROOT/'fpga/tb/tb_button_focus.v').read_text(encoding='utf-8')
    tb=tb.replace('TEST_LEVEL=MOTION_TEST ? 100 : 1','TEST_LEVEL=100')
    tb=tb.replace('.HW_ROWS(8),.HW_COLS(8),',
        '.WS_X_MIN_UM(0),.WS_X_MAX_UM(0),.WS_Y_MIN_UM(0),.WS_Y_MAX_UM(0),.WS_Z_MIN_UM(70000),.WS_Z_MAX_UM(70000),\n        .HW_ROWS(8),.HW_COLS(8),')
    tb=tb.replace('lengths[0:8]','lengths[0:12]')
    tb=tb.replace('        send(7,2); // START again',
        '''        send(9,1);send(10,1);send(11,1);send(12,1);
        if(on || dut.cfg_cx_um!=0 || dut.cfg_cy_um!=0 || dut.cfg_z_um!=70000 || dut.cfg_shape!=0)
            $fatal(1,"experimental geometry guard changed configuration");
        send(7,2); // START again''')
    (folder/'tb_button_focus.v').write_text(tb,encoding='utf-8')
    cfg=Config(shape='POINT',cx_um=0,cy_um=0,z_um=70000,radius_um=0,level=100,mod_hz=0)
    fields={**cfg.wire(),**ArraySpec(8,8,10300).wire()}
    commands=[('HELLO',{}),('MODE',dict(value='REMOTE')),('CONFIG',fields),('SNAP',{}),('START',{}),('STOP',{}),
        ('CONFIG',{**fields,'carrier_hz':39000}),('START',{}),('SNAP',{}),
        ('CONFIG',{**fields,'cx_um':1000}),('CONFIG',{**fields,'cy_um':1000}),
        ('CONFIG',{**fields,'z_um':71000}),('CONFIG',{**fields,'shape':'CIRCLE','radius_um':1000})]
    frames=[encode('CMD',i+1,v,**data) for i,(v,data) in enumerate(commands)]
    (folder/'commands.hex').write_text('\n'.join(f'{b:02x}' for b in b''.join(frames)),encoding='ascii')
    (folder/'lengths.hex').write_text('\n'.join(f'{len(f):08x}' for f in frames),encoding='ascii')
    packed=sum(v<<(8*i) for i,v in enumerate(m['effective_output_offsets']))
    expected=sum(v<<(8*i) for i,v in enumerate(m['expected_center_phases']))
    compiler=Path(args.iverilog);sim=folder/'simulation.vvp';env=dict(os.environ,TMP=str(folder),TMPDIR=str(folder))
    with (folder/'test.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(compiler),'-g2012','-s','tb_button_focus',
            f"-Ptb_button_focus.OFFSETS=512'h{packed:0128x}",f"-Ptb_button_focus.EXPECTED=512'h{expected:0128x}",
            f"-Ptb_button_focus.EXPECTED100=512'h{expected:0128x}",'-Ptb_button_focus.FOCUS_Z_UM=70000',
            '-o',str(sim),*map(str,sorted((FOLDER/'rtl').glob('*.v'))),str(folder/'tb_button_focus.v')],
            cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')),'-i',str(sim)],cwd=folder,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    received=[decode(line) for line in (folder/'uart.log').read_bytes().splitlines(keepends=True)]
    for seq in (7,10,11,12,13):
        assert any(f.kind=='ERR' and f.seq==seq for f in received), f'Missing rejection {seq}'
    running=[f for f in received if f.verb=='STATE' and f.fields.get('output')=='1']
    assert running
    for f in running:
        assert list(map(int,f.fields['phases'].split(',')))==m['expected_center_phases']
        assert [int(f.fields[k]) for k in ('fx_um','fy_um','fz_um')]==m['target_um']
    (FOLDER/'test_result.json').write_text(json.dumps(dict(passed=True,sources=m['sources'],
        geometry_guard_checked=True,exact_64_phases=True,stop_timeout_reset_checked=True),indent=2),encoding='utf-8')
    print('PASS phase-only center: 64 output/readback phases; geometry guard; button/STOP/watchdog/reset',flush=True)


def build(args):
    m=read_manifest();expected=Path(m['output'])
    if expected.exists():raise FileExistsError('Use the existing isolated result or explicitly archive it before rebuilding')
    with (FOLDER/'build.log').open('w',encoding='utf-8') as log:
        result=subprocess.run([args.gowin,str(FOLDER/'build.tcl')],cwd=FOLDER,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode or not expected.is_file():raise RuntimeError('Build failed; see build.log')
    m['fmax_mhz']=check_timing(expected.with_suffix('.tr').read_text(encoding='utf-8',errors='replace'))
    m['sha256']=digest(expected)
    (FOLDER/'manifest.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print('BUILD PASS phase-only center',m['fmax_mhz'],m['sha256'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','test','build'])
    parser.add_argument('--calibration-file',default='D:/pld/output/calibration/8x8_70mm_calibration_20261024_1227.json')
    parser.add_argument('--weights-file',default='D:/pld/output/focus/edm_70mm_20261025/recheck/fields_weights.npz')
    parser.add_argument('--iverilog',default='D:/pld/.diagnostics/review-2026-10-15/iverilog/app/bin/iverilog.exe')
    parser.add_argument('--gowin',default='D:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe')
    args=parser.parse_args()
    {'prepare':prepare,'test':test,'build':build}[args.action](args)
