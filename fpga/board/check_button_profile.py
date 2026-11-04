"""Compare remote START and a local play pulse with the same circle, without hardware."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop_app.model import ArraySpec, BUTTON_OUTPUT_SETTINGS, Config, focus_phases
from desktop_app.protocol import Snapshot, decode, encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog', type=Path, required=True)
    args = parser.parse_args()
    folder = ROOT / '.runtime/button-strength/circle-uart'
    folder.mkdir(parents=True, exist_ok=True)
    array = ArraySpec(8, 8, 10300)
    config = Config(**BUTTON_OUTPUT_SETTINGS)
    offsets = tuple((i*7+3) % 64 for i in range(64))
    commands = [('HELLO', {}), ('CONFIG', dict(config.wire(), **array.wire())),
                ('START', {}), ('PAUSE', {}), ('STOP', {}),
                ('MODE', {'value': 'LOCAL'}), ('SNAP', {}), ('STOP', {})]
    packets = [encode('CMD', i+1, verb, **fields) for i, (verb, fields) in enumerate(commands)]
    raw = b''.join(packets)
    (folder/'commands.hex').write_text('\n'.join(f'{v:02x}' for v in raw))
    (folder/'lengths.hex').write_text('\n'.join(f'{len(v):08x}' for v in packets))
    (folder/'replies.hex').write_text('\n'.join(['02']*len(packets)))
    bench = (ROOT/'fpga/tb/tb_static_phase.v').read_text(encoding='utf-8')
    # Exercise the post-debounce local input; tb_local separately tests the button debounce.
    bench = bench.replace('send(command_i,replies[command_i]);', '''
            if(command_i==6) begin
                force dut.loc_play=1'b1;
                @(negedge clk);
                release dut.loc_play;
                repeat(1000) @(negedge clk);
            end
            send(command_i,replies[command_i]);
            if(command_i>=1 && (dut.cfg_level!=100 || dut.cfg_mod_hz!=0 || dut.cfg_z_um!=50000 || dut.cfg_repeat_millihz!=1000))
                $fatal(1,"button profile changed");
            if(command_i==2 || command_i==6) begin
                repeat(1000) begin
                    @(negedge clk);
                    if(!dut.u_out.mod_on || dut.u_out.duty_thr!=dut.u_out.HALF_CYCLE)
                        $fatal(1,"full drive or continuous envelope differs");
                end
            end
            ''')
    bench = bench.replace('if(dut.view_active || dut.view_level!=0)',
                          'if(dut.cfg_static && (dut.view_active || dut.view_level!=0))')
    source = folder/'tb_static_phase.v'
    source.write_text(bench, encoding='utf-8')
    packed = sum(v << (8*i) for i, v in enumerate(offsets))
    compiler = args.iverilog.resolve()
    with (folder/'test.log').open('w') as log:
        subprocess.run([str(compiler), '-g2012', '-s', 'tb_static_phase',
                        f'-Ptb_static_phase.COUNT={len(commands)}', f'-Ptb_static_phase.BYTES={len(raw)}',
                        '-Ptb_static_phase.CHECK_WATCHDOG=0', f"-Ptb_static_phase.OFFSETS=512'h{packed:0128x}",
                        '-o', str(folder/'sim.vvp'), *map(str, sorted((ROOT/'fpga/rtl').glob('*.v'))), str(source)],
                       cwd=folder, env=dict(os.environ, TMP=str(folder), TMPDIR=str(folder)),
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')), '-i', str(folder/'sim.vvp')],
                       cwd=folder, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    replies = [decode(line) for line in (folder/'uart.log').read_bytes().splitlines(keepends=True)]
    assert all(frame.kind != 'ERR' for frame in replies)
    states = [Snapshot.parse(frame.fields) for frame in replies if frame.verb == 'STATE']
    assert len(states) == len(commands)
    assert [state.state for state in states] == ['IDLE','IDLE','RUNNING','PAUSED','IDLE','IDLE','RUNNING','IDLE']
    errors = []
    for state in states[1:]:
        assert state.config == config and state.phase_offsets == offsets
        expected = [(int(p)+o) % 64 for p, o in zip(focus_phases(config,state.focus_mm,array), offsets)]
        errors.extend(min((a-b) % 64, (b-a) % 64) for a,b in zip(state.phases, expected))
    assert max(errors) <= 1, max(errors)
    assert states[2].mode == 'REMOTE' and states[2].output
    assert states[6].mode == 'LOCAL' and states[6].output
    assert not states[-1].output
    report = dict(passed=True, hardware_connected=False, commands=len(commands),
                  channels=64, max_phase_error_steps=max(errors),
                  local_and_remote_full_drive=True, continuous_envelope=True,
                  final_output=False, config=config.wire())
    (folder/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('PASS', json.dumps(report))


if __name__ == '__main__':
    main()
