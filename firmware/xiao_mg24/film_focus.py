"""Bounded center-focus film observation. Defaults to a no-emission probe."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import serial
from desktop_app.protocol import encode, decode
from desktop_app.model import Config, ArraySpec, focus_phases


def center_plan(saved, z_mm=70, level=1):
    """Build and validate a center target without opening hardware."""
    if z_mm not in (70, 100) or not 1 <= level <= 100:
        raise ValueError('Require height 70 or 100 mm and level 1..100')
    offsets = saved['phase_offsets']
    if len(offsets) != 64 or any(type(v) is not int or not 0 <= v < 64 for v in offsets):
        raise ValueError('Invalid measured calibration offsets')
    array = ArraySpec(8, 8, 10300)
    cfg = Config(shape='POINT', cx_um=0, cy_um=0, z_um=z_mm*1000,
                 radius_um=0, level=level, mod_hz=0)
    def expected_at(height):
        return [(int(a)+b)%64 for a,b in zip(focus_phases(cfg,(0,0,height),array),offsets)]
    reference_position = saved.get('position_um', [0, 0, 70000])
    if reference_position not in ([0, 0, 70000], [0, 0, 100000]):
        raise ValueError('Unsupported calibration reference position')
    if expected_at(reference_position[2]/1000) != saved['expected_center_phases']:
        raise ValueError('Reference model does not match measured calibration')
    return cfg, {**cfg.wire(), **array.wire()}, expected_at(z_mm)


class FocusSession:
    def __init__(self, port, log):
        self.log = log
        self.port = serial.Serial(port, 115200, timeout=.03, write_timeout=1)
        self.seq = 0
        self.boot = None
        self.sample = -1
        self.buffer = bytearray()
        self.pending = []
        # Discard only the initial partial telemetry line; never reset the board.
        self.record('initial_fragment', raw=self.port.read_until(b'\n').hex())

    def record(self, kind, **data):
        self.log.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),
                                     monotonic=time.monotonic(), type=kind, **data))+'\n')
        self.log.flush()

    def command(self, verb, **fields):
        self.seq += 1
        raw = encode('CMD', self.seq, verb, **fields)
        self.record('tx', raw=raw.decode().strip())
        if self.port.write(raw) != len(raw):
            raise RuntimeError('Partial command write')
        deadline = time.monotonic()+4
        ack = None
        damaged = 0
        while time.monotonic() < deadline:
            self.buffer.extend(self.port.read(4096))
            while b'\n' in self.buffer:
                line, _, tail = self.buffer.partition(b'\n')
                self.buffer = bytearray(tail)
                rawline = bytes(line)+b'\n'
                prefix = rawline.rfind(b'HAP3 ')
                if prefix > 0:
                    self.record('discarded_prefix', hex=rawline[:prefix].hex())
                    damaged += 1
                    rawline = rawline[prefix:]
                try:
                    f = decode(rawline)
                except ValueError as error:
                    self.record('discarded_frame', hex=rawline.hex(), error=str(error))
                    damaged += 1
                    if damaged > 3: raise RuntimeError('Repeated damaged telemetry')
                    continue
                if damaged > 3: raise RuntimeError('Repeated damaged telemetry')
                self.record('rx', raw=rawline.decode().strip())
                if 'boot' in f.fields:
                    if self.boot is None: self.boot = f.fields['boot']
                    if self.boot != f.fields['boot']: raise RuntimeError('Board reset')
                fresh = True
                if f.kind == 'TEL' and f.verb == 'STATE':
                    sample = int(f.fields['sample'])
                    fresh = sample > self.sample
                    self.sample = max(self.sample, sample)
                if f.seq == self.seq and f.verb == verb:
                    if f.kind == 'ERR': raise RuntimeError(str(f.fields))
                    if f.kind == 'ACK':
                        ack = f.fields
                        if verb == 'PING': return ack, None
                if ack is not None and fresh and f.kind == 'TEL' and f.verb == 'STATE':
                    if 'rev' in ack and f.fields.get('rev') != ack['rev']: continue
                    return ack, f.fields
            if len(self.buffer) > 8192: raise RuntimeError('Oversized telemetry')
        raise TimeoutError(verb)

    def stop(self):
        _, state = self.command('STOP')
        if state.get('output') != '0' or state.get('state') != 'IDLE':
            raise RuntimeError('STOP not confirmed')
        self.record('off_confirmed', state=state)
        return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', default='COM7')
    p.add_argument('--manifest', required=True)
    p.add_argument('--level', type=int, default=1)
    p.add_argument('--z-mm', type=int, choices=(70,100), default=70)
    p.add_argument('--seconds', type=float, default=2)
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--off-seconds', type=float, default=4, help='Output-off interval between repetitions, 1..30 seconds')
    p.add_argument('--run', action='store_true', help='Explicitly emit; otherwise verify with output off')
    args = p.parse_args()
    if not 1 <= args.level <= 100 or not 0 < args.seconds <= 10 or not 1 <= args.repeat <= 3:
        p.error('Require level 1..100, duration 0..10 seconds, repeats 1..3')
    if not 1 <= args.off_seconds <= 30:
        p.error('Require output-off interval 1..30 seconds')
    manifest = json.loads(Path(args.manifest).read_bytes())
    saved = manifest.get('restored_calibration')
    if manifest.get('mode') != 'restore' or not saved or manifest.get('synthesis_only'):
        raise ValueError('Requires a completed measured-calibration restore build')
    if hashlib.sha256(Path(manifest['output']).read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Bitstream hash mismatch')
    if hashlib.sha256(Path(saved['file']).read_bytes()).hexdigest() != saved['sha256']:
        raise ValueError('Calibration source hash mismatch')
    folder = ROOT / '.runtime/calibration/film'
    folder.mkdir(parents=True, exist_ok=True)
    stop_file = folder/'STOP.request'
    if stop_file.exists(): raise RuntimeError('STOP.request exists; inspect before clearing')
    path = folder / ('film_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.jsonl')
    with path.open('x', encoding='utf-8') as log:
        fp = FocusSession(args.port, log)
        try:
            fp.record('context', args=vars(args), restored_calibration=saved,
                      acoustic_reverified=False, receiver_used=False)
            ack, _ = fp.command('HELLO')
            if ack.get('simulated') != '0' or any(ack.get(k) != str(v) for k,v in
                    dict(hw_rows=8, hw_cols=8, hw_pitch_um=10300).items()):
                raise RuntimeError('Wrong physical array identity')
            fp.stop()
            fp.command('MODE', value='REMOTE')
            cfg, fields, expected = center_plan(saved, args.z_mm, args.level)
            fp.command('CONFIG', **fields)
            time.sleep(.2)
            _, state = fp.command('SNAP')
            if any(state.get(k) != str(v) for k,v in fields.items()):
                raise RuntimeError('Configuration readback mismatch')
            if (list(map(int,state['phases'].split(','))) != expected
                    or [int(state[k]) for k in ('fx_um','fy_um','fz_um')] != [0,0,args.z_mm*1000]
                    or state['output'] != '0'):
                raise RuntimeError('Center phases/position/off state mismatch')
            fp.record('center_verified', state=state)
            print(f'VERIFIED output=0 center=(0,0,{args.z_mm}) level='+str(args.level)+' log='+str(path), flush=True)
            if not args.run: return
            for repeat in range(args.repeat):
                if stop_file.exists(): raise RuntimeError('User stop request')
                print('START level='+str(args.level)+'; observe output LED and film', flush=True)
                started = time.monotonic()
                _, state = fp.command('START')
                if (state['output']!='1' or state['state']!='RUNNING'
                        or list(map(int,state['phases'].split(','))) != expected):
                    raise RuntimeError('Running state/phase mismatch')
                while time.monotonic()-started < args.seconds:
                    if stop_file.exists(): raise RuntimeError('User stop request')
                    fp.command('PING')
                    time.sleep(.15)
                fp.stop()
                print('STOP confirmed; output LED off', flush=True)
                if repeat+1 < args.repeat: time.sleep(args.off_seconds)
            fp.record('complete')
        finally:
            try: fp.stop()
            finally: fp.port.close()


if __name__ == '__main__':
    main()
