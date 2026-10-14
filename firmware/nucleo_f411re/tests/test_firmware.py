"""Compile the actual firmware core and exercise it with the desktop codec/model."""
import binascii
import ctypes as ct
from dataclasses import replace
import os
from pathlib import Path
import random
import queue
import shutil
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from desktop_app.model import ArraySpec, Config, focus_phases, trajectory_sample
from desktop_app.protocol import Snapshot, decode, encode
from desktop_app.controller import Session
from desktop_app.receiver import Capture


class Sample(ct.Structure):
    _fields_ = [("elapsed_us", ct.c_uint64), ("x_um", ct.c_int32), ("y_um", ct.c_int32),
                ("z_um", ct.c_int32), ("phases", ct.c_uint8 * 16), ("stroke", ct.c_uint8),
                ("scan_on", ct.c_bool), ("output", ct.c_bool), ("drive_on", ct.c_bool)]


class Wave(ct.Structure):
    _fields_ = [("b", ct.c_uint32 * 2560), ("c", ct.c_uint32 * 2560), ("samples", Sample * 40)]


class FirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_CC") or shutil.which("gcc")
        if not compiler and os.name == "nt":
            compiler = "D:/STEdgeAI/4.0/Utilities/windows/mingw64/bin/gcc.exe"
        if not compiler or not Path(compiler).exists():
            raise RuntimeError("Set HOST_CC to a native GCC compiler (not arm-none-eabi-gcc)")
        cls.compiler = compiler
        output = ROOT / "build"
        output.mkdir(exist_ok=True)
        library = output / ("test_core.dll" if os.name == "nt" else "test_core.so")
        subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                        "-fPIC", "-Iinclude", "src/haptics.c", "src/geometry.c", "tests/harness.c",
                        "-lm", "-static-libgcc", "-o", str(library.relative_to(ROOT))], cwd=ROOT, check=True)
        cls.dll = ct.CDLL(str(library))
        cls.dll.test_take.restype = ct.c_char_p
        cls.dll.test_feed.argtypes = (ct.c_char_p, ct.c_size_t, ct.c_uint64)
        cls.dll.test_poll.argtypes = (ct.c_uint64,)
        cls.dll.test_geometry.argtypes = (ct.c_uint64, ct.POINTER(Sample))
        cls.dll.test_wave.argtypes = (ct.c_uint64, ct.POINTER(Wave))

    def setUp(self):
        self.dll.test_init()
        self.seq = 0
        self.now = 0
        self.array = ArraySpec(4, 4, 11000)

    def feed(self, raw):
        self.dll.test_feed(raw, len(raw), self.now)
        return [decode(line) for line in self.dll.test_take().splitlines()]

    def command(self, verb, **fields):
        self.seq += 1
        return self.feed(encode("CMD", self.seq, verb, **fields))

    def configure(self, config=Config()):
        if not self.seq:
            self.command("HELLO")
        frames = self.command("CONFIG", **config.wire(), **self.array.wire())
        self.assertEqual(frames[0].kind, "ACK", frames)
        snap = Snapshot.parse(frames[1].fields)
        self.assertEqual(snap.config, config)
        self.assertEqual(snap.array, self.array)
        return snap

    def test_handshake_and_config_start_pause_stop(self):
        frames = self.command("HELLO")
        self.assertEqual(frames[0].fields["device"], "NUCLEO-F411RE")
        self.assertEqual(frames[0].fields["simulated"], "0")
        self.assertEqual(frames[0].fields["max_channels"], "16")
        Snapshot.parse(frames[1].fields)
        self.assertEqual(self.command("START")[0].fields["code"], "CONFIG_REQUIRED")
        self.configure()
        self.assertEqual(self.command("START")[0].kind, "ACK")
        self.assertTrue(self.dll.test_active())
        self.now = 530
        paused = Snapshot.parse(self.command("PAUSE")[1].fields)
        self.assertFalse(self.dll.test_active())
        self.now = 700
        self.assertEqual(Snapshot.parse(self.command("SNAP")[1].fields).focus_mm, paused.focus_mm)
        resumed = Snapshot.parse(self.command("START")[1].fields)
        self.assertEqual(resumed.focus_mm, paused.focus_mm)
        self.assertEqual(self.command("STOP")[1].fields["state"], "IDLE")
        self.assertFalse(self.dll.test_active())

    def test_rejected_config_is_atomic(self):
        original = self.configure()
        base = dict(Config().wire(), **self.array.wire())
        for update, code in [({"hw_rows":8}, "HARDWARE_MISMATCH"),
                             ({"phase_steps":128}, "BAD_CONFIG"),
                             ({"carrier_hz":41000}, "BAD_CONFIG"),
                             ({"cx_um":100000}, "OUT_OF_WORKSPACE"),
                             ({"level":101}, "BAD_CONFIG"),
                             ({"level":"999999999999999999999999"}, "BAD_CONFIG"),
                             ({"shape":"CUSTOM", "scan_paths":"0:0,0:0"}, "BAD_CONFIG")]:
            with self.subTest(update=update):
                response = self.command("CONFIG", **dict(base, **update))
                self.assertEqual(response[0].fields["code"], code)
                after = Snapshot.parse(self.command("SNAP")[1].fields)
                self.assertEqual(after.config, original.config)
                self.assertEqual(after.revision, original.revision)
        missing = base.copy(); del missing["level"]
        self.assertEqual(self.command("CONFIG", **missing)[0].kind, "ERR")
        self.command("START")
        self.assertEqual(self.command("CONFIG", **base)[0].fields["code"], "BUSY")

    def test_adc_capture_roundtrip_and_control_priority(self):
        self.assertEqual(self.command("HELLO")[0].fields["adc_capture"], "1")
        self.configure()
        self.command("START")
        self.assertEqual(self.command("CAPTURE"), [])
        request_seq = self.seq
        self.assertEqual(self.command("CAPTURE")[0].fields["code"], "ADC_BUSY")
        self.assertTrue(self.dll.test_active())
        self.command("STOP")
        self.assertFalse(self.dll.test_active())
        self.dll.test_poll(1)
        frame = decode(self.dll.test_take())
        self.assertEqual((frame.kind, frame.seq, frame.verb), ("ACK", request_seq, "CAPTURE"))
        window = Capture.parse(frame.fields)
        self.assertTrue(window.tx_running)  # State at capture start, not after STOP.
        self.assertFalse(window.simulated)
        self.assertEqual(len(window.raw), 200)
        self.assertAlmostEqual(window.analyze()["mean_mv"], 602*3300/4095, places=4)
        self.assertAlmostEqual(window.analyze()["peak40_mv"], 100*3300/4095, delta=.5)
        self.assertEqual(self.command("CAPTURE")[0].fields["code"], "ADC_RATE_LIMIT")
        self.now=250
        self.assertEqual(self.command("CAPTURE", bad=1)[0].fields["code"], "BAD_FIELDS")
        self.assertEqual(self.command("CAPTURE"), [])

    def test_adc_failures_and_new_handshake_cancel(self):
        self.command("HELLO")
        self.dll.test_capture_mode(-2)
        self.assertEqual(self.command("CAPTURE")[0].fields["code"], "ADC_START_FAILED")
        self.dll.test_capture_mode(0)
        self.command("CAPTURE")
        self.dll.test_poll(20)
        frames=[decode(line) for line in self.dll.test_take().splitlines()]
        self.assertEqual(frames[0].fields["code"], "ADC_TIMEOUT")
        self.assertFalse(self.dll.test_capturing())
        self.now=250
        self.dll.test_capture_mode(-1)
        self.command("CAPTURE")
        self.dll.test_poll(251)
        self.assertEqual(decode(self.dll.test_take()).fields["code"], "ADC_ERROR")
        self.now=500
        self.dll.test_capture_mode(0)
        self.command("CAPTURE")
        self.assertTrue(self.dll.test_capturing())
        self.command("HELLO")
        self.assertFalse(self.dll.test_capturing())
        self.dll.test_poll(521)
        self.assertNotIn(b"CAPTURE", self.dll.test_take())

    def test_receiver_driver_registers(self):
        executable = "build/receiver_register_test" + (".exe" if os.name == "nt" else "")
        subprocess.run([self.compiler,"-std=c11","-Wall","-Wextra","-Werror",
                        "-Wno-int-to-pointer-cast","-DRECEIVER_REGISTER_TEST","-Iinclude",
                        "-Ivendor","-Itests","src/receiver.c","tests/receiver_register_test.c",
                        "-o",executable],cwd=ROOT,check=True)
        subprocess.run([str(ROOT/executable)],cwd=ROOT,check=True)

    def test_crc_corruption_oversize_and_fragmentation(self):
        self.configure()
        valid = encode("CMD", 90, "START")
        damaged = valid.replace(b"START", b"STOPX")
        self.assertEqual(self.feed(damaged), [])
        self.assertFalse(self.dll.test_active())
        self.assertEqual(self.feed(b"x"*8200+valid), [])
        self.assertEqual(self.feed(b"\n"+valid[:8]), [])
        frames = self.feed(valid[8:].replace(b"\n", b"\r\n"))
        self.assertEqual(frames[0].kind, "ACK")
        self.command("STOP")
        raw = b"HAP3 CMD 91 START x=1 x=2"
        self.assertEqual(self.feed(raw+f"*{binascii.crc_hqx(raw,0xffff):04X}\n".encode()), [])
        self.assertEqual(self.command("START", mystery=1)[0].kind, "ERR")
        self.assertFalse(self.dll.test_active())

    def test_heartbeat_and_reconnect_do_not_restart(self):
        self.configure(); self.command("START")
        self.now = 2900; self.command("PING")
        self.dll.test_poll(5800); self.dll.test_take()
        self.assertTrue(self.dll.test_active())
        self.dll.test_poll(5900)
        frames = [decode(x) for x in self.dll.test_take().splitlines()]
        self.assertFalse(self.dll.test_active())
        self.now = 5900
        self.assertEqual(self.command("SNAP")[1].fields["reason"], "HEARTBEAT_TIMEOUT")
        self.command("HELLO")
        self.assertEqual(self.command("START")[0].kind, "ERR")
        self.assertFalse(self.dll.test_active())

    def test_local_controls_and_fault_recovery(self):
        self.configure(); self.command("MODE", value="LOCAL")
        self.assertEqual(self.command("START")[0].fields["code"], "LOCAL_CONTROL")
        self.dll.test_button(1)
        self.assertEqual(self.command("SNAP")[1].fields["shape"], "SQUARE")
        self.dll.test_button(2)
        self.dll.test_poll(10000); self.dll.test_take()
        self.assertTrue(self.dll.test_active())
        self.dll.test_button(0)
        self.assertFalse(self.dll.test_active())
        self.now = 10000
        self.command("MODE", value="REMOTE")
        self.command("START"); self.dll.test_fault()
        self.assertFalse(self.dll.test_active())
        self.assertEqual(self.command("SNAP")[1].fields["state"], "FAULT")
        self.assertEqual(self.command("START")[0].kind, "ERR")
        self.command("STOP")
        self.assertEqual(self.command("START")[0].kind, "ACK")

    def test_start_failure_never_acknowledges_running(self):
        self.configure(); self.dll.test_fail_start()
        self.assertEqual(self.command("START")[0].kind, "ERR")
        snap = Snapshot.parse(self.command("SNAP")[1].fields)
        self.assertEqual(snap.state, "FAULT"); self.assertFalse(snap.output)

    def test_standalone_boot_stays_off_until_local_play(self):
        self.assertFalse(self.dll.test_active())
        self.dll.test_button(2)
        self.assertFalse(self.dll.test_active())
        self.dll.test_button(3)
        self.dll.test_button(1)
        self.dll.test_button(2)
        self.assertTrue(self.dll.test_active())
        state = Snapshot.parse(self.command('HELLO')[1].fields)
        self.assertEqual(state.mode,'LOCAL')
        self.assertEqual(state.state,'RUNNING')
        self.dll.test_button(0)
        self.assertFalse(self.dll.test_active())

    def test_geometry_and_phases_match_desktop(self):
        random.seed(411)
        configs = [replace(Config(), shape=shape) for shape in
                   ("POINT","LINE_X","LINE_Y","CIRCLE","SQUARE","TRIANGLE","ARROW")]
        configs += [replace(Config(), shape="CUSTOM", path_xy_um="-10000:0,0:20000,20000:10000", path_closed=closed)
                    for closed in (0,1)]
        configs += [replace(Config(), shape="CUSTOM", scan_paths="0:0,10000:0,10000:10000,0:0|-20000:0,-10000:0")]
        for config in configs:
            self.configure(config)
            for us in [0, 1500, 1900000, 1999500, 2000000, 2**32+14250] + [random.randrange(3000000) for _ in range(30)]:
                sample = Sample(); self.dll.test_geometry(us, ct.byref(sample))
                expected, on, stroke = trajectory_sample(config, us/1e6)
                with self.subTest(shape=config.shape, us=us):
                    for actual, ideal in zip((sample.x_um,sample.y_um,sample.z_um), expected*1000):
                        self.assertLessEqual(abs(actual-ideal), 1.1)
                    self.assertEqual(sample.scan_on, on); self.assertEqual(sample.stroke, stroke)
                    ideal_phase = focus_phases(config, [sample.x_um/1000,sample.y_um/1000,sample.z_um/1000], self.array)
                    # Single precision can differ by one code at a rounding boundary.
                    for a,b in zip(sample.phases, ideal_phase):
                        self.assertLessEqual(min((int(a)-int(b))%64,(int(b)-int(a))%64),1)

    def test_wave_output_phase_masks_and_blanking(self):
        config = replace(Config(), shape="POINT", level=100, mod_hz=0)
        self.configure(config)
        wave = Wave(); self.dll.test_wave(0,ct.byref(wave))
        pins = (0,1,2,4,5,6,7,8)
        for cycle in range(40):
            sample = wave.samples[cycle]
            self.assertEqual(sample.elapsed_us, (cycle//10)*250)
            self.assertTrue(sample.output)
            for slot in range(64):
                b,c = wave.b[cycle*64+slot],wave.c[cycle*64+slot]
                self.assertEqual((b&0xffff)^(b>>16),0x1f7)
                self.assertEqual((c&0xffff)^(c>>16),0xff)
                for i in range(16):
                    bit = bool((b if i<8 else c)&(1<<(pins[i] if i<8 else i-8)))
                    self.assertEqual(bit, (slot+sample.phases[i])%64 < 32)
        config = replace(config, shape="CUSTOM", scan_paths="0:0,10000:0|20000:0,30000:0", repeat_millihz=20000, blank_us=100)
        self.configure(config)
        blank_count = 0
        for start in range(0,50000,1000):
            self.dll.test_wave(start,ct.byref(wave))
            for cycle,sample in enumerate(wave.samples):
                if not sample.scan_on:
                    blank_count += 1
                    for slot in range(64):
                        self.assertEqual(wave.b[cycle*64+slot],0x1f70000)
                        self.assertEqual(wave.c[cycle*64+slot],0xff0000)
                # No emitted carrier interval may overlap the requested transfer.
                if sample.output:
                    for delta in (1,12,24):
                        self.assertTrue(trajectory_sample(config,(start+cycle*25+delta)/1e6)[1])
        self.assertGreater(blank_count,0)

    def test_level_modulation_and_capacity(self):
        self.configure(replace(Config(),shape="POINT",level=30,mod_hz=0))
        on = 0; wave = Wave()
        for us in range(0,5000,1000):
            self.dll.test_wave(us,ct.byref(wave)); on += sum(s.drive_on for s in wave.samples)
        self.assertEqual(on,60)
        self.configure(replace(Config(),shape="POINT",level=100,mod_hz=200))
        on = 0
        for us in range(0,5000,1000):
            self.dll.test_wave(us,ct.byref(wave)); on += sum(s.drive_on for s in wave.samples)
        self.assertEqual(on,100)
        paths = "|".join(",".join(f"{1000*j}:{1000*i}" for j in range(8)) for i in range(32))
        self.configure(replace(Config(),shape="CUSTOM",scan_paths=paths))
        self.command("START")
        Snapshot.parse(self.command("SNAP")[1].fields)
        self.command("STOP")
        too_fast = replace(Config(),shape="CUSTOM",scan_paths=paths,repeat_millihz=15000,blank_us=2000)
        self.assertEqual(self.command("CONFIG",**too_fast.wire(),**self.array.wire())[0].fields["code"],"SCAN_TOO_FAST")

    def test_desktop_real_session_at_serial_wire_speed(self):
        dll = self.dll

        class FirmwareTransport:
            def __init__(self):
                self.start = time.monotonic()
                self.pending = bytearray()
                self.sent_at = self.start

            def clock(self):
                return int((time.monotonic()-self.start)*1000)

            def collect(self):
                raw = dll.test_take()
                if not self.pending:
                    self.sent_at = time.monotonic()
                self.pending.extend(raw)

            def write(self, raw):
                # Arbitrary receive fragments exercise the same parser as UART RX.
                for offset in range(0,len(raw),17):
                    chunk = raw[offset:offset+17]
                    dll.test_feed(chunk,len(chunk),self.clock())
                self.collect()

            def read(self):
                time.sleep(.003)
                if not self.pending:
                    dll.test_poll(self.clock()); self.collect()
                n = min(len(self.pending),int((time.monotonic()-self.sent_at)*11520))
                raw = bytes(self.pending[:n]); del self.pending[:n]
                self.sent_at += n/11520
                return raw

            def close(self):
                pass

        session = Session(demo=False, factory=FirmwareTransport)
        session.start()

        def event(predicate, timeout=3):
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                try:
                    item = session.events.get(timeout=.05)
                except queue.Empty:
                    continue
                self.assertNotIn(item['kind'],('error','warning','rejected'),item)
                if predicate(item):
                    return item
            self.fail('Timed out waiting for a desktop session event')

        try:
            ready = event(lambda e:e['kind']=='ready')
            self.assertEqual(ready['device'],'NUCLEO-F411RE')
            self.assertFalse(ready['demo'])
            paths = '|'.join(','.join(f'{-90000+j*1000}:{-90000+i*1000}' for j in range(8)) for i in range(32))
            config = replace(Config(),shape='CUSTOM',scan_paths=paths)
            session.command('CONFIG',**config.wire())
            state = event(lambda e:e['kind']=='state' and e['state'].revision==1)['state']
            self.assertEqual(state.config,config)
            session.command('START')
            event(lambda e:e['kind']=='state' and e['state'].state=='RUNNING')
            self.assertIn('ADC_CAPTURE', ready['capabilities'])
            session.command('CAPTURE')
            capture = event(lambda e:e['kind']=='capture')['capture']
            self.assertFalse(capture.simulated)
            self.assertTrue(capture.tx_running)
            self.assertEqual(capture.revision, 1)
            self.assertEqual(len(capture.raw), 200)
            event(lambda e:e['kind']=='state' and e['state'].state=='RUNNING')
            session.command('PAUSE')
            event(lambda e:e['kind']=='state' and e['state'].state=='PAUSED')
            session.command('STOP')
            event(lambda e:e['kind']=='state' and e['state'].state=='IDLE')
        finally:
            session.close(); session.join(2)
        self.assertFalse(session.is_alive())
        self.assertFalse(dll.test_active())


if __name__ == "__main__":
    unittest.main(verbosity=2)
