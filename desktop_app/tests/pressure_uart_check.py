"""Send an exported GUI pressure CONFIG into the FPGA UART simulation, never a port."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from desktop_app.model import ArraySpec, Config
from desktop_app.protocol import Snapshot, decode, encode

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--iverilog',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=ROOT/'.runtime/pressure-uart')
    args=parser.parse_args()
    fields=json.loads(args.config.read_text(encoding='utf-8'))
    config=Config.from_wire(fields)
    array=ArraySpec.from_wire(fields)
    assert array==ArraySpec(8,8,10300), 'Simulation fixture is the 8x8 board'
    nominal=config.static_phases(array)
    offsets=tuple((i*7+3)%64 for i in range(64))
    expected=tuple((a+b)%64 for a,b in zip(nominal,offsets))
    folder=args.out.resolve()
    folder.mkdir(parents=True,exist_ok=True)
    verbs=['HELLO','CONFIG','START','PAUSE','START','STOP']
    packets=[encode('CMD',i+1,verb,**(fields if verb=='CONFIG' else {})) for i,verb in enumerate(verbs)]
    raw=b''.join(packets)
    (folder/'commands.hex').write_text('\n'.join(f'{v:02x}' for v in raw))
    (folder/'lengths.hex').write_text('\n'.join(f'{len(v):08x}' for v in packets))
    (folder/'replies.hex').write_text('\n'.join(['02']*len(packets)))
    # Observe at least one full 200 Hz modulation cycle, rather than the
    # original bench's short unmodulated-output window. Production RTL unchanged.
    bench=(ROOT/'fpga/tb/tb_static_phase.v').read_text(encoding='utf-8')
    bench=bench.replace('repeat(1000) begin @(negedge clk);seen=seen|waves;end',
                        'repeat(30000) begin @(negedge clk);seen=seen|waves;end')
    source=folder/'tb_static_phase.v'
    source.write_text(bench,encoding='utf-8')
    packed=lambda values:sum(v<<(8*i) for i,v in enumerate(values))
    with (folder/'test.log').open('w') as log:
        subprocess.run([str(args.iverilog.resolve()),'-g2012','-s','tb_static_phase',
                        '-Ptb_static_phase.COUNT=6',f'-Ptb_static_phase.BYTES={len(raw)}',
                        '-Ptb_static_phase.CHECK_WATCHDOG=0',
                        f"-Ptb_static_phase.OFFSETS=512'h{packed(offsets):0128x}",
                        f"-Ptb_static_phase.EXPECTED=512'h{packed(expected):0128x}",
                        '-o',str(folder/'sim.vvp'),*map(str,sorted((ROOT/'fpga/rtl').glob('*.v'))),str(source)],
                       cwd=folder,env=dict(os.environ,TMP=str(folder),TMPDIR=str(folder)),
                       stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run([str(args.iverilog.resolve().with_name('vvp.exe')),'-i',str(folder/'sim.vvp')],
                       cwd=folder,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    replies=[decode(line) for line in (folder/'uart.log').read_bytes().splitlines(keepends=True)]
    states=[Snapshot.parse(f.fields) for f in replies if f.verb=='STATE']
    assert all(f.kind!='ERR' for f in replies)
    assert len(states)==6
    for state in states[1:]:
        assert state.config==config and state.phases==expected
    assert [s.state for s in states]==['IDLE','IDLE','RUNNING','PAUSED','RUNNING','IDLE']
    report=dict(passed=True,commands=6,uart_frames=len(replies),channels=64,
                config_source=str(args.config.resolve()),mod_hz=config.mod_hz,level=config.level,hardware_connected=False)
    (folder/'result.json').write_text(json.dumps(report,indent=2))
    print('PASS',json.dumps(report))


if __name__=='__main__':
    main()
