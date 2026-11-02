"""Explicit real-board protocol test. Defaults to output disabled; --run enables bounded level-1 tests."""
import argparse
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from desktop_app.controller import Session
from desktop_app.model import ArraySpec, Config, encode_strokes, focus_phases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--pressure-config',type=Path,required=True)
    p.add_argument('--run',action='store_true')
    args=p.parse_args()
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    assert hashlib.sha256(Path(manifest['output']).read_bytes()).hexdigest()==manifest['sha256']
    saved=manifest['restored_calibration']
    assert hashlib.sha256(Path(saved['file']).read_bytes()).hexdigest()==saved['sha256']
    source=json.loads(args.pressure_config.read_text(encoding='utf-8'))
    array=ArraySpec(8,8,10300)
    assert ArraySpec.from_wire(source)==array
    pressure=replace(Config.from_wire(source),level=1 if args.run else 0)
    offsets=tuple(saved['phase_offsets'])
    folder=ROOT/'.runtime/board-pressure-test'
    folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    report=dict(started_at=datetime.now(timezone.utc).isoformat(),port=args.port,
                bitstream_sha256=manifest['sha256'],calibration_sha256=saved['sha256'],
                pressure_config_source=str(args.pressure_config.resolve()),
                hardware_connected=True,acoustic_measurement=False,level=pressure.level,checks=[])
    session=Session(demo=False,port=args.port)
    log=(folder/f'test_{stamp}.jsonl').open('x',encoding='utf-8')
    def record(event):
        log.write(json.dumps(event,ensure_ascii=False,default=lambda v:asdict(v) if is_dataclass(v) else str(v))+'\n')
        log.flush()
    def event(timeout=5):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                item=session.events.get(timeout=.1)
            except queue.Empty:
                continue
            record(item)
            if item['kind'] in ('error','rejected','closed'):
                raise RuntimeError(str(item))
            return item
        raise TimeoutError('Device event')
    def command(verb,**fields):
        assert session.command(verb,**fields)
        ack=None
        end=time.monotonic()+5
        while time.monotonic()<end:
            item=event()
            if item['kind']=='ack' and item['verb']==verb:
                ack=item
            if ack and item['kind']=='state':
                state=item['state']
                if state.sample>ack['after_sample'] and state.revision>=int(ack['fields'].get('rev',0)):
                    assert state.phase_offsets==offsets
                    return state
        raise TimeoutError(verb)
    def checked(name,state):
        report['checks'].append(dict(name=name,revision=state.revision,state=state.state,
                                     output=state.output,static=state.config.phase_table!='NONE'))
        print('PASS',name,state.state,'output='+str(state.output),flush=True)
    def configure(config):
        state=command('CONFIG',**config.wire(),**array.wire())
        assert state.config==config and state.state=='IDLE' and not state.output
        return state
    def cycle(config,name):
        configure(config)
        state=command('START')
        assert state.state=='RUNNING' and state.output==(config.level>0)
        checked(name+' start',state)
        time.sleep(.25)
        state=command('PAUSE')
        assert state.state=='PAUSED' and not state.output
        checked(name+' pause',state)
        state=command('START')
        assert state.state=='RUNNING' and state.output==(config.level>0)
        checked(name+' resume',state)
        time.sleep(.25)
        state=command('STOP')
        assert state.state=='IDLE' and not state.output
        checked(name+' stop',state)
    session.start()
    try:
        while True:
            item=event()
            if item['kind']=='state':
                break
        assert session.ready and session.array==array and not session.last_state.simulated
        assert {'STATIC_PHASE','CAL_READBACK'}<=session.capabilities
        assert session.hello_fields['fw_id']=='HAP3_STATIC_1'
        assert session.hello_fields['cal_profile']=='2'
        report['hello']=session.hello_fields
        state=command('STOP');assert not state.output and state.state=='IDLE'
        command('MODE',value='REMOTE')
        checked('handshake and calibration readback',state)
        center=Config(shape='POINT',radius_um=0,z_um=50000,level=0,mod_hz=0)
        configure(center)
        state=command('SNAP')
        expected=tuple((int(p)+o)%64 for p,o in zip(focus_phases(center,(0,0,50),array),offsets))
        assert state.phases==expected
        checked('64 calibrated center phases',state)
        circle=replace(center,shape='CIRCLE',radius_um=12000,repeat_millihz=700,mod_hz=160)
        cycle(circle,'circle disabled-output')
        strokes=[[(x*700-2450,y*1000-15500) for x in range(8)] for y in range(32)]
        drawing=replace(center,shape='CUSTOM',scan_paths=encode_strokes(strokes))
        cycle(drawing,'32-stroke 256-point drawing disabled-output')
        state=command('MODE',value='LOCAL');assert state.mode=='LOCAL' and not state.output
        state=command('MODE',value='REMOTE');assert state.mode=='REMOTE' and not state.output
        checked('local/remote mode switch',state)
        if args.run:
            cycle(replace(center,level=1),'center level-1')
        cycle(pressure,'pressure frame 325')
        state=command('SNAP')
        assert state.config==pressure
        assert state.phases==tuple((a+b)%64 for a,b in zip(pressure.static_phases(array),offsets))
        checked('64 static phases and compensation matched',state)
        configure(center)
        checked('return to drawing',command('SNAP'))
        # Leave the tested pressure frame loaded, with output OFF and level 1.
        state=configure(pressure)
        checked('pressure frame saved idle',state)
        report['passed']=True
    except Exception as error:
        report['passed']=False
        report['error']=repr(error)
        raise
    finally:
        try:
            state=command('STOP')
            report['final_stop_confirmed']=state.state=='IDLE' and not state.output
            report['final_state']=asdict(state)
        except Exception as error:
            report['final_stop_confirmed']=False
            report['stop_error']=repr(error)
        session.close();session.join(5)
        while not session.events.empty():
            record(session.events.get_nowait())
        log.close()
        report['session_closed']=not session.is_alive()
        (folder/f'result_{stamp}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('RESULT',folder/f'result_{stamp}.json',flush=True)
    assert report['final_stop_confirmed'] and report['session_closed']


if __name__=='__main__':
    main()
