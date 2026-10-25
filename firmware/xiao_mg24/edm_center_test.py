"""Probe or run the explicitly identified phase-only center experiment.

No emission without --run. Measured calibration is not redefined as beam shape.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from firmware.xiao_mg24.film_focus import FocusSession
from desktop_app.model import Config,ArraySpec


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    folder=ROOT/'.runtime/fpga-edm-center-phase-only'
    m=json.loads((folder/'manifest.json').read_bytes())
    tested=json.loads((folder/'test_result.json').read_bytes())
    if (m['mode']!='experimental_edm_phase_only_center' or not tested['passed']
            or tested['sources']!=m['sources'] or sha(m['output'])!=m['sha256']):
        raise RuntimeError('Experimental build/test identity mismatch')
    if (sha(m['weights_file'])!=m['weights_sha256'] or
            sha(m['measured_calibration']['file'])!=m['measured_calibration']['sha256']):
        raise RuntimeError('Source hash mismatch')
    stop_file=ROOT/'.runtime/calibration/film/STOP.request'
    if stop_file.exists():raise RuntimeError('Stop requested')
    logs=Path('D:/pld/output/focus/edm_center_test_20261025');logs.mkdir(parents=True,exist_ok=True)
    logpath=logs/f'phase_only_{datetime.now():%H%M%S}.jsonl'
    cfg=Config(shape='POINT',cx_um=0,cy_um=0,z_um=70000,radius_um=0,level=100,mod_hz=0)
    fields={**cfg.wire(),**ArraySpec(8,8,10300).wire()}

    def verify(state,on):
        if any(state.get(k)!=str(v) for k,v in fields.items()):raise RuntimeError('Config mismatch')
        if state.get('output')!=str(int(on)) or state.get('state')!=('RUNNING' if on else 'IDLE'):
            raise RuntimeError('Output/state mismatch')
        if [int(state[k]) for k in ('fx_um','fy_um','fz_um')]!=m['target_um']:
            raise RuntimeError('Not at locked center')
        if list(map(int,state['phases'].split(',')))!=m['expected_center_phases']:
            raise RuntimeError('Exact experimental 64-phase readback mismatch')

    with logpath.open('x',encoding='utf-8') as log:
        fp=FocusSession('COM7',log)
        try:
            fp.record('experiment_context',manifest=m,run=args.run,
                      acoustic_measurement=False,phase_only=True,requested_seconds=6)
            ack,_=fp.command('HELLO')
            fp.stop()
            if ack.get('simulated')!='0' or any(ack.get(k)!=str(v) for k,v in
                    dict(hw_rows=8,hw_cols=8,hw_pitch_um=10300).items()):
                raise RuntimeError('Wrong transmitter')
            fp.command('MODE',value='REMOTE')
            fp.command('CONFIG',**fields)
            time.sleep(.2)
            _,s=fp.command('SNAP');verify(s,False)
            fp.record('phase_only_center_verified',state=s)
            print('VERIFIED phase-only narrow center; output=0; level100; z70',flush=True)
            if args.run:
                start=time.monotonic();count=0
                _,s=fp.command('START');verify(s,True)
                while time.monotonic()-start<6:
                    if stop_file.exists():raise RuntimeError('Stop requested')
                    fp.command('PING')
                    if time.monotonic()-start>=6:break
                    _,s=fp.command('SNAP');verify(s,True);count+=1
                    time.sleep(.08)
                fp.stop()
                fp.record('bounded_run_complete',snapshots=count,seconds_until_stop=time.monotonic()-start,
                          observed_acoustic_improvement=None)
                print(f'RUN COMPLETE {count} verified snapshots; elapsed={time.monotonic()-start:.3f}s',flush=True)
        finally:
            try:
                fp.stop()
                _,s=fp.command('SNAP')
                if s.get('output')!='0':raise RuntimeError('Final output not off')
                print('OUTPUT OFF CONFIRMED; log='+str(logpath),flush=True)
            finally:fp.port.close()


if __name__=='__main__':main()
