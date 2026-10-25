"""No-emission setup check for the experimental two-button circle image.

Does not send START. Physical narrow-circle validation needs a user button
press and cannot be inferred from the ordinary, output-off configuration.
"""
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from fpga.board.build_edm_circle_ab import manifest,digest,FOLDER
from fpga.board.edm_circle_model import baseline
from firmware.xiao_mg24.film_focus import FocusSession
from desktop_app.model import Config,ArraySpec


def main():
    m=manifest();t=json.loads((FOLDER/'test_result.json').read_bytes())
    assert t['passed'] and t['sources']==m['sources'] and digest(m['output'])==m['sha256']
    assert not (ROOT/'.runtime/calibration/film/STOP.request').exists(),'STOP request exists'
    logpath=FOLDER/f'probe_{datetime.now():%Y%m%d_%H%M%S}.jsonl'
    with logpath.open('x',encoding='utf-8') as log:
        fp=FocusSession('COM7',log)
        try:
            ack,_=fp.command('HELLO');fp.stop()
            assert ack.get('simulated')=='0'
            assert all(ack.get(k)==str(v) for k,v in dict(hw_rows=8,hw_cols=8,hw_pitch_um=10300).items())
            fp.command('MODE',value='REMOTE')
            cfg=Config(shape='CIRCLE',cx_um=0,cy_um=0,z_um=70000,radius_um=20000,
                       repeat_millihz=500,level=100,mod_hz=0)
            fields={**cfg.wire(),**ArraySpec(8,8,10300).wire()}
            fp.command('CONFIG',**fields);time.sleep(.25)
            _,s=fp.command('SNAP')
            assert all(s.get(k)==str(v) for k,v in fields.items())
            assert s['output']=='0' and s['state']=='IDLE'
            x,y,z=[int(s[k]) for k in ('fx_um','fy_um','fz_um')]
            assert z==70000 and abs((x*x+y*y)**.5-20000)<100
            offsets=m['measured_calibration']['phase_offsets']
            candidates=[((baseline(hx,hy,z)+offsets)%64).tolist()
                        for hx in (2*x,2*x+1) for hy in (2*y,2*y+1)]
            assert list(map(int,s['phases'].split(','))) in candidates
            fp.record('ab_circle_probe',bitstream_sha256=m['sha256'],normal_circle_verified=True,
                      narrow_physical_button_verified=False,automatic_emission=False,state=s)
            print('PASS ordinary circle configuration + 64 phases, output OFF; two buttons ready',flush=True)
        finally:
            try:fp.stop();print('OUTPUT OFF CONFIRMED; log='+str(logpath),flush=True)
            finally:fp.port.close()


if __name__=='__main__':main()
