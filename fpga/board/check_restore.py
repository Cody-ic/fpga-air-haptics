"""Offline UART/phase/output checks for the explicit saved-calibration build."""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fpga.board.build_calibration import load_restore
from desktop_app.protocol import encode, decode
from desktop_app.model import Config, ArraySpec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calibration-file', required=True)
    parser.add_argument('--iverilog', required=True)
    button=parser.add_mutually_exclusive_group()
    button.add_argument('--button-focus', action='store_true')
    button.add_argument('--button-line', action='store_true')
    button.add_argument('--button-circle', action='store_true')
    parser.add_argument('--button-radius-mm', type=int, choices=range(1,31), default=20)
    parser.add_argument('--button-z-mm', type=int, choices=(70,100), default=100)
    parser.add_argument('--calibration-z-mm', type=int, choices=(70,100), default=70)
    parser.add_argument('--calibration-reference', type=int, choices=range(64), default=0)
    args = parser.parse_args()
    button_motion=args.button_line or args.button_circle
    button_enabled=args.button_focus or button_motion
    if args.button_radius_mm!=20 and not button_motion:
        parser.error('--button-radius-mm requires --button-line or --button-circle')
    packed, info = load_restore(args.calibration_file, args.calibration_z_mm, args.calibration_reference)
    doc = json.loads(Path(args.calibration_file).read_bytes())
    # Reject simulated, partial and corrupt inputs before they can be synthesized.
    with tempfile.TemporaryDirectory() as temp:
        for change in ('simulated', 'partial', 'range'):
            bad = copy.deepcopy(doc)
            if change == 'simulated': bad['simulated'] = True
            elif change == 'partial': bad['measurements'].pop()
            else: bad['phase_offsets'][3] = 64
            path = Path(temp) / 'bad.json'
            path.write_text(json.dumps(bad), encoding='utf-8')
            try: load_restore(path, args.calibration_z_mm, args.calibration_reference)
            except (ValueError, RuntimeError): pass
            else: raise AssertionError('Accepted invalid calibration: ' + change)
    bench = 'tb_button_focus' if button_enabled else 'tb_cal_restore'
    folder = ROOT / '.runtime/calibration/tests' / (bench + ('_z100' if args.calibration_z_mm==100 else '')
              + (f'_ref{args.calibration_reference}' if args.calibration_reference else '')
              + ('_focus70' if button_enabled and args.button_z_mm==70 else '')
              + ('_circle' if args.button_circle else '_line' if args.button_line else '')
              + (f'_r{args.button_radius_mm}' if button_motion and args.button_radius_mm!=20 else ''))
    folder.mkdir(parents=True, exist_ok=True)
    cfg = Config(shape='POINT', cx_um=0, cy_um=0, z_um=args.calibration_z_mm*1000, radius_um=0, level=100, mod_hz=0)
    fields = {**cfg.wire(), **ArraySpec(8,8,10300).wire()}
    cmds = [('HELLO', {}), ('MODE', dict(value='REMOTE')), ('CONFIG', fields),
            ('SNAP', {}), ('START', {}), ('STOP', {}),
            ('CONFIG', {**fields, 'carrier_hz':39000}), ('START', {}), ('SNAP', {})]
    frames = [encode('CMD', i+1, verb, **data) for i,(verb,data) in enumerate(cmds)]
    raw = b''.join(frames)
    (folder / 'commands.hex').write_text('\n'.join(f'{v:02x}' for v in raw), encoding='ascii')
    (folder / 'lengths.hex').write_text('\n'.join(f'{len(f):08x}' for f in frames), encoding='ascii')
    expected = sum(v << (8*i) for i,v in enumerate(info['expected_center_phases']))
    from desktop_app.model import focus_phases
    nominal100 = focus_phases(cfg, (0, 0, args.button_z_mm), ArraySpec(8,8,10300))
    expected100 = sum(((int(v)+o)%64) << (8*i) for i,(v,o) in enumerate(zip(nominal100,info['phase_offsets'])))
    compiler = Path(args.iverilog)
    output = folder / 'simulation.vvp'
    env = dict(os.environ, TMP=str(folder), TMPDIR=str(folder))
    with (folder / 'test.log').open('w', encoding='utf-8') as log:
        subprocess.run([str(compiler), '-g2012', '-s', bench,
                        f"-P{bench}.OFFSETS=512'h{packed:0128x}",
                        f"-P{bench}.EXPECTED=512'h{expected:0128x}",
                        *([f"-P{bench}.EXPECTED100=512'h{expected100:0128x}",
                           f"-P{bench}.FOCUS_Z_UM={args.button_z_mm*1000}",
                           f"-P{bench}.LINE_TEST={int(args.button_line)}",
                           f"-P{bench}.CIRCLE_TEST={int(args.button_circle)}",
                           f"-P{bench}.LINE_RADIUS_UM={args.button_radius_mm*1000}"] if button_enabled else []),
                        '-o', str(output), *map(str, sorted((ROOT/'fpga/rtl').glob('*.v'))),
                        str(ROOT/'fpga/tb'/f'{bench}.v')], cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')), '-i', str(output)], cwd=folder,
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900 if args.button_circle else 300)
    frames = [decode(line) for line in (folder/'uart.log').read_bytes().splitlines(keepends=True)]
    assert any(f.kind=='ERR' and f.seq==7 for f in frames), 'incompatible config not rejected'
    running = [f for f in frames if f.verb=='STATE' and f.fields.get('output')=='1']
    assert running, 'No running snapshot'
    for frame in running:
        state=frame.fields
        actual=list(map(int,state['phases'].split(',')))
        if state['shape']=='POINT':
            assert actual == info['expected_center_phases']
        else:
            assert button_motion and state['shape']==('CIRCLE' if args.button_circle else 'LINE_X')
            assert state['level']=='100' and state['radius_um']==str(args.button_radius_mm*1000) and state['repeat_millihz']=='200000'
            assert state['fz_um']==str(args.button_z_mm*1000)
            x,y,z=[int(state[k]) for k in ('fx_um','fy_um','fz_um')]
            assert max(abs(x),abs(y))<=args.button_radius_mm*1000
            if args.button_circle:
                assert abs(math.hypot(x,y)-args.button_radius_mm*1000)<100
            else:
                assert y==0
            candidates=[]
            a_fx=((40000<<39)+171500000)//343000000
            mask=(1<<40)-1
            for hx in (2*x,2*x+1):
                for hy in (2*y,2*y+1):
                    codes=[]
                    for ch,off in enumerate(info['phase_offsets']):
                        dx=(2*(ch%8)-7)*10300-hx
                        dy=(2*(ch//8)-7)*10300-hy
                        d=math.isqrt(dx*dx+dy*dy+(2*z)**2)
                        units=(((d*a_fx & mask)+(1<<33)) & mask)>>34
                        codes.append((-units+off)&63)
                    candidates.append(codes)
            assert actual in candidates, 'Moving focus phase snapshot mismatch'
    if button_motion:
        assert any(f.fields['shape']==('CIRCLE' if args.button_circle else 'LINE_X') for f in running), 'Missing actual button motion snapshot'
    assert any(f.fields.get('reason')=='HEARTBEAT_TIMEOUT' and f.fields.get('output')=='0' for f in frames)
    print('PASS invalid input rejection and full UART readback; ' + str(folder/'test.log'))


if __name__ == '__main__':
    main()
