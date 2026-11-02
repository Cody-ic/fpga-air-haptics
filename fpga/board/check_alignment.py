"""Offline host-encoded commands through the 8x8 RTL UART; never opens hardware."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from desktop_app.model import ArraySpec, Config, encode_strokes
from desktop_app.protocol import encode, decode, Snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog',required=True)
    parser.add_argument('--pressure-frame',action='store_true',help='Use the saved built-in dataset frame 325 phase vector')
    args=parser.parse_args()
    folder=ROOT/('.runtime/alignment-pressure' if args.pressure_frame else '.runtime/alignment')
    folder.mkdir(parents=True,exist_ok=True)
    array=ArraySpec(8,8,10300)
    offsets=tuple((i*7+3)%64 for i in range(64))
    codes=tuple((i*11+5)%64 for i in range(64))
    if args.pressure_frame:
        fixture=json.loads((ROOT/'fpga/tb/vectors/pressure_frame_325.json').read_text())
        codes=tuple(fixture['phase_codes'])
    static=Config(shape='POINT',radius_um=0,z_um=50000,level=100,mod_hz=0)
    table=bytes(codes).hex().upper()
    strokes=[[(x*700-2450,y*1000-15500) for x in range(8)] for y in range(32)]
    drawing=Config(shape='CUSTOM',scan_paths=encode_strokes(strokes),z_um=50000,level=70,mod_hz=0)
    circle=Config(shape='CIRCLE',z_um=50000,radius_um=12000,level=42,mod_hz=160,repeat_millihz=700)
    base={**static.wire(),**array.wire(),'phase_table':table}
    cmds=[]
    def add(verb,fields=None,error=False):
        cmds.append((verb,fields or {},error))
    add('HELLO')
    add('CONFIG',base)
    add('START');add('PAUSE');add('START');add('STOP')
    for value in [table[:-2],table+'00','40'+table[2:], 'GG'+table[2:]]:
        add('CONFIG',{**base,'phase_table':value},True)
        add('SNAP')
    add('CONFIG',{**base,'hw_pitch_um':11000},True);add('SNAP')
    add('CONFIG',{**base,'carrier_hz':39000},True);add('SNAP')
    add('CONFIG',{**drawing.wire(),**array.wire()})
    add('START');add('PAUSE');add('SNAP');add('START');add('STOP')
    add('CONFIG',{**circle.wire(),**array.wire()});add('START');add('STOP')
    add('MODE',{'value':'LOCAL'});add('SNAP')
    add('MODE',{'value':'REMOTE'});add('START');add('SNAP')
    if args.pressure_frame:
        cmds=cmds[:6]
        add('CONFIG',{**base,'scan_paths':'0:0,1:1'},True);add('SNAP')
        add('CONFIG',{**base,'phase_steps':32},True);add('SNAP')
    frames=[encode('CMD',i+1,v,**f) for i,(v,f,e) in enumerate(cmds)]
    raw=b''.join(frames)
    (folder/'commands.hex').write_text('\n'.join(f'{v:02x}' for v in raw))
    (folder/'lengths.hex').write_text('\n'.join(f'{len(f):08x}' for f in frames))
    (folder/'replies.hex').write_text('\n'.join('01' if e else '02' for _,_,e in cmds))
    packed=sum(v<<(8*i) for i,v in enumerate(offsets))
    expected=sum(((v+o)%64)<<(8*i) for i,(v,o) in enumerate(zip(codes,offsets)))
    compiler=Path(args.iverilog)
    env=dict(os.environ,TMP=str(folder),TMPDIR=str(folder))
    with (folder/'test.log').open('w') as log:
        subprocess.run([str(compiler),'-g2012','-s','tb_static_phase',
                        f'-Ptb_static_phase.COUNT={len(cmds)}',f'-Ptb_static_phase.BYTES={len(raw)}',
                        f'-Ptb_static_phase.CHECK_WATCHDOG={int(not args.pressure_frame)}',
                        f"-Ptb_static_phase.OFFSETS=512'h{packed:0128x}",
                        f"-Ptb_static_phase.EXPECTED=512'h{expected:0128x}",
                        '-o',str(folder/'sim.vvp'),*map(str,sorted((ROOT/'fpga/rtl').glob('*.v'))),
                        str(ROOT/'fpga/tb/tb_static_phase.v')],cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')),'-i',str(folder/'sim.vvp')],cwd=folder,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    replies=[decode(line) for line in (folder/'uart.log').read_bytes().splitlines(keepends=True)]
    states={};acks={};raw_states={};seq=0
    for frame in replies:
        if frame.kind in ('ACK','ERR'):
            seq=frame.seq;acks[seq]=frame
        elif frame.verb=='STATE':
            states[seq]=Snapshot.parse(frame.fields,array)
            raw_states[seq]=frame.fields
    assert 'STATIC_PHASE' in acks[1].fields['caps']
    assert acks[1].fields['carrier_fixed']=='40000'
    assert acks[1].fields['steps_fixed']=='64'
    assert acks[1].fields['cal_profile']=='2'
    # The bare RTL bench boots at level 30; new desktop drafts default to 100.
    # Keep the reset-state expectation independent of that UI default.
    active=Config(level=30);revision=0;static_active=False
    for seq,(verb,fields,error) in enumerate(cmds,1):
        ack=acks[seq]
        assert ack.kind==('ERR' if error else 'ACK'),(seq,ack)
        if error:
            continue
        if verb=='CONFIG':
            active=Config.from_wire(fields);revision+=1
            static_active='phase_table' in fields
        state=states[seq]
        assert state.config==active,(seq,state.config,active)
        assert state.revision==revision
        assert state.phase_offsets==offsets
        assert raw_states[seq]['static_mode']==str(int(static_active))
        if static_active:
            assert raw_states[seq]['phase_table']==table
            assert tuple((p-o)%64 for p,o in zip(state.phases,offsets))==codes
        if verb in ('CONFIG','STOP','PAUSE','MODE'):
            assert not state.output
        if verb=='START':
            assert state.state=='RUNNING'
    # Pause does not advance the scan; a second readback has the same focus.
    if not args.pressure_frame:
        pause_seq=next(i for i,(v,_,_) in enumerate(cmds,1) if v=='PAUSE' and i>10)
        assert states[pause_seq].focus_mm==states[pause_seq+1].focus_mm
        assert states[len(cmds)].reason=='HEARTBEAT_TIMEOUT'
    assert not states[len(cmds)].output
    report=dict(commands=len(cmds),uart_frames=len(replies),channels=64,
                largest_config_bytes=max(map(len,frames)),drawing_points=0 if args.pressure_frame else 256,
                strokes=0 if args.pressure_frame else 32,pressure_frame=325 if args.pressure_frame else None,
                static_phase_and_calibration='all 64 channels matched',hardware_connected=False)
    (folder/'result.json').write_text(json.dumps(report,indent=2))
    print('PASS',json.dumps(report))


if __name__=='__main__':
    main()
