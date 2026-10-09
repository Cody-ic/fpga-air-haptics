"""Exercise the F103 profile and both GPIO DMA waveforms against the host codec."""
import ctypes as ct
from dataclasses import replace
import os
import json
from pathlib import Path
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from desktop_app.model import ArraySpec, Config, focus_phases, trajectory_sample
from desktop_app.protocol import Snapshot, decode, encode
from desktop_app.receiver import Capture
from desktop_app.controller import Session
from firmware.nucleo_f411re.tests.test_firmware import Sample


class F103Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_CC") or shutil.which("gcc")
        if not compiler:
            compiler = "D:/STEdgeAI/4.0/Utilities/windows/mingw64/bin/gcc.exe"
        cls.compiler=compiler
        output = ROOT / "build"
        output.mkdir(exist_ok=True)
        library = output / ("test_f103.dll" if os.name == "nt" else "test_f103.so")
        subprocess.run([compiler,"-std=c11","-O2","-Wall","-Wextra","-Werror","-shared","-fPIC",
            "-ICore/Inc","Core/Src/haptics.c","Core/Src/geometry.c","Core/Src/wave_f103.c",
            "tests/harness.c","-lm","-static-libgcc","-o",str(library.relative_to(ROOT))],cwd=ROOT,check=True)
        cls.dll=ct.CDLL(str(library))
        cls.dll.test_feed.argtypes=(ct.c_char_p,ct.c_size_t,ct.c_uint64)
        cls.dll.test_take.restype=ct.c_char_p
        cls.dll.test_poll.argtypes=(ct.c_uint64,)
        cls.dll.test_half_words.restype=ct.c_uint
        cls.dll.test_half_us.restype=ct.c_uint
        cls.dll.test_phase.argtypes=(ct.POINTER(Sample),)
        cls.dll.test_wave_ready.argtypes=(ct.c_uint64,)
        cls.dll.test_geometry.argtypes=(ct.c_uint64,ct.POINTER(Sample))
        cls.half_words=cls.dll.test_half_words()
        cls.half_us=cls.dll.test_half_us()
        cls.dll.test_wave.argtypes=(ct.c_uint64,ct.POINTER(ct.c_uint16),ct.POINTER(ct.c_uint16),
                                   ct.c_uint16,ct.POINTER(Sample))

    def setUp(self):
        self.dll.test_init()
        self.seq=0
        self.now=0
        self.array=ArraySpec(4,4,11000)

    def feed(self, data):
        self.dll.test_feed(data,len(data),self.now)
        return [decode(line) for line in self.dll.test_take().splitlines()]

    def command(self, verb, **fields):
        self.seq+=1
        return self.feed(encode("CMD",self.seq,verb,**fields))

    def configure(self, config=Config()):
        if not self.seq:
            self.command("HELLO")
        result=self.command("CONFIG",**config.wire(),**self.array.wire())
        self.assertEqual(result[0].kind,"ACK",result)
        self.assertEqual(Snapshot.parse(result[1].fields).config,config)
        return Snapshot.parse(result[1].fields)

    def ports(self, us, idle=0xe0):
        words_b=(ct.c_uint16*self.half_words)()
        words_a=(ct.c_uint16*self.half_words)()
        sample=Sample()
        self.dll.test_wave(us,words_b,words_a,idle,ct.byref(sample))
        return list(words_b),list(words_a),sample

    def wave(self, us):
        words_b,words_a,sample=self.ports(us)
        # Reconstruct logical channels from physical PB pins and PA8.
        return [b|(((a>>8)&1)<<2) for b,a in zip(words_b,words_a)],sample

    def reference_wave(self, config, sample, start_us, mask=0xffff):
        # Independent per-channel edge state machine; not cached carrier copies.
        high = [False]*16
        words = []
        for index in range(self.half_words):
            cycle, slot = divmod(index, 64)
            time_us = start_us + cycle*25
            density = ((time_us//25 % 100)*config.level) % 100 + config.level >= 100
            modulation = not config.mod_hz or ((time_us % 1000000)*config.mod_hz) % 1000000 < 500000
            enabled = sample.scan_on and config.level > 0 and density and modulation
            for channel, phase in enumerate(sample.phases):
                position = (slot + phase) % 64
                if not cycle or not mask & (1 << channel) or position == 32:
                    high[channel] = False
                elif position == 0:
                    high[channel] = enabled
            words.append(sum(1 << ch for ch, value in enumerate(high) if value))
        return words

    def assert_cycle_readback(self, words):
        expected = sum(1 << cycle for cycle in range(self.half_words//64)
                       if any(words[cycle*64:(cycle+1)*64]))
        self.assertEqual(self.dll.test_active_cycles(), expected)

    def test_limits_and_handshake(self):
        fields=self.command("HELLO")[0].fields
        self.assertEqual(fields["device"],"STM32F103C8T6")
        for key,value in (("max_nodes",64),("max_scan_points",64),("max_strokes",8),
                          ("max_channels",16),("focus_hz",1000)):
            self.assertEqual(int(fields[key]),value)
        self.assertEqual(self.command("START")[0].fields["code"],"CONFIG_REQUIRED")

    def test_maximum_path_readback_and_oversize_atomicity(self):
        # Every coordinate has the longest valid signed representation after translation.
        path=",".join(f"{-200000+i}:{-200000+i}" for i in range(64))
        config=Config(shape="CUSTOM",path_xy_um=path,path_closed=0,cx_um=100000,cy_um=100000)
        before=self.configure(config)
        bad=replace(config,path_xy_um=path+",-199900:-199900")
        fields=dict(config.wire(),path_xy_um=bad.path_xy_um)
        self.assertEqual(self.command("CONFIG",**fields,**self.array.wire())[0].kind,"ERR")
        after=Snapshot.parse(self.command("SNAP")[1].fields)
        self.assertEqual(after.config,config)
        self.assertEqual(after.revision,before.revision)

    def test_stroke_limit_and_transfer_budget(self):
        strokes="|".join(f"{i*1000}:0,{i*1000}:1000" for i in range(8))
        c=Config(shape="CUSTOM",scan_paths=strokes,repeat_millihz=1000)
        before=self.configure(c)
        for changes in ({"scan_paths":strokes+"|9000:0,9000:1000"}, {"repeat_millihz":200000}):
            fields=dict(c.wire(),**changes)
            self.assertEqual(self.command("CONFIG",**fields,**self.array.wire())[0].kind,"ERR")
            self.assertEqual(Snapshot.parse(self.command("SNAP")[1].fields).revision,before.revision)

    def test_corruption_long_frame_recovery_and_stop(self):
        self.configure()
        damaged=bytearray(encode("CMD",45,"START"))
        damaged[0]^=1
        self.assertEqual(self.feed(bytes(damaged)),[])
        self.assertFalse(self.dll.test_active())
        self.assertEqual(self.feed(b"x"*2000+b"\n"),[])
        self.assertEqual(self.command("START")[0].kind,"ACK")
        self.assertTrue(self.dll.test_active())
        self.now=3000
        self.dll.test_poll(self.now)
        frames=[decode(line) for line in self.dll.test_take().splitlines()]
        self.assertEqual(frames[-1].fields["reason"],"HEARTBEAT_TIMEOUT")
        self.assertFalse(self.dll.test_active())

    def test_gpio_bits_duty_cycle_phases_and_guard(self):
        self.configure(Config(shape="POINT",level=100,mod_hz=0))
        words,sample=self.wave(0)
        self.assertEqual(words[:64],[0]*64)
        expected=tuple(focus_phases(Config(shape="POINT",level=100,mod_hz=0),(0,0,150),self.array))
        self.assertEqual(tuple(sample.phases),expected)
        for channel,phase in enumerate(sample.phases):
            bits=[(w>>channel)&1 for w in words[128:192]]
            self.assertEqual(sum(bits),32)
            self.assertEqual(bits,[int(((slot+phase)&63)<32) for slot in range(64)])
        self.assertEqual(words,self.reference_wave(Config(shape="POINT",level=100,mod_hz=0),sample,0))
        self.assert_cycle_readback(words)

    def test_modulation_level_channel_mask_and_blanking(self):
        c=Config(shape="POINT",level=100,mod_hz=0)
        self.configure(c)
        self.assertEqual(self.command("CALIBRATION",action="trial",geometry_id=self.array.resolved_geometry().identity,
                         mask=1,offsets=",".join(["7"]*16))[0].kind,"ACK")
        words,_=self.wave(0)
        self.assertTrue(any(words))
        self.assertTrue(all(w&~1 == 0 for w in words))
        self.command("STOP")
        self.configure(replace(c,level=0))
        words,_=self.wave(0)
        self.assertEqual(words,[0]*self.half_words)
        self.configure(replace(c,mod_hz=200))
        words,_=self.wave(2500)
        self.assertEqual(words,[0]*self.half_words)
        self.configure(Config(shape="CUSTOM",scan_paths="0:0,1000:0|10000:0,11000:0",
                              repeat_millihz=1000,blank_us=2000,mod_hz=0,level=100))
        words,sample=self.wave(498000)
        self.assertFalse(sample.scan_on)
        self.assertEqual(words,[0]*self.half_words)

    def test_pa8_replacement_preserves_other_latches_and_boot1(self):
        c=Config(shape="POINT",level=100,mod_hz=0)
        self.configure(c)
        for mask in (4,0xfffb,0xffff):
            self.assertEqual(self.command("CALIBRATION",action="trial",
                geometry_id=self.array.resolved_geometry().identity,mask=mask,
                offsets=",".join(["0"]*16))[0].kind,"ACK")
            for idle in (0xe0,0xa5e7,0xffff):
                words_b,words_a,sample=self.ports(0,idle)
                baseline=idle&~0x100
                self.assertTrue(all(b&4 == 0 for b in words_b))
                self.assertTrue(all(a&~0x100 == baseline for a in words_a))
                self.assertEqual(words_b[:64],[0]*64)
                self.assertEqual(words_a[:64],[baseline]*64)
                bits=[(a>>8)&1 for a in words_a[128:192]]
                expected=[int(bool(mask&4) and ((slot+sample.phases[2])&63)<32)
                          for slot in range(64)]
                self.assertEqual(bits,expected)

    def test_focus_cadence_and_guard_in_both_ports(self):
        self.configure(Config(shape="CIRCLE",level=100,mod_hz=0))
        samples=[]
        for us in range(0,2000,self.half_us):
            words_b,words_a,sample=self.ports(us)
            samples.append((sample.elapsed_us,tuple(sample.phases)))
            self.assertEqual(words_b[:64],[0]*64)
            self.assertEqual(words_a[:64],[0xe0]*64)
        first=samples[:1000//self.half_us]
        second=samples[1000//self.half_us:]
        self.assertTrue(all(s == first[0] for s in first))
        self.assertTrue(all(s == second[0] for s in second))
        self.assertEqual(first[0][0],0)
        self.assertEqual(second[0][0],1000)

    def test_optimized_drive_matches_integer_reference_at_time_boundaries(self):
        # Cross second/density wraps, non-zero resume origins and >32-bit times.
        for mod_hz, level in ((0, 100), (200, 30), (997, 73), (1000, 1), (1, 0)):
            c = Config(shape="POINT", mod_hz=mod_hz, level=level)
            self.configure(c)
            for start in (0, 999750, 1000000, 4294967250, 10**12 + 999750):
                self.dll.test_reset_wave()
                for offset in (0, self.half_us, 2*self.half_us):
                    us = start + offset
                    words_b, words_a, sample = self.ports(us)
                    reference = self.reference_wave(c,sample,us)
                    self.assertEqual(words_b,[w & 0xfffb for w in reference])
                    self.assertEqual(words_a,[0xe0 | (0x100 if w & 4 else 0) for w in reference])
                    self.assert_cycle_readback(reference)

    def test_all_phase_enables_start_only_at_natural_rising_edges(self):
        # Sweep all phases, including the old short 58/39-slot PB0 intervals.
        point = Config(shape="POINT",level=100,mod_hz=0)
        self.configure(point)
        initial = Sample()
        self.dll.test_geometry(0,ct.byref(initial))
        self.dll.test_phase(ct.byref(initial))
        nominal = list(initial.phases)
        for level, mod in ((100,0),(30,200),(73,997),(1,1000),(0,0)):
            c = replace(point,level=level,mod_hz=mod)
            self.configure(c)
            for phase in range(64):
                offsets = ",".join(str((phase-p)%64) for p in nominal)
                result = self.command("CALIBRATION",action="store",mask=0xffff,offsets=offsets,
                                      geometry_id=self.array.resolved_geometry().identity)
                self.assertEqual(result[0].kind,"ACK",result)
                self.dll.test_reset_wave()
                stream = []
                for us in (0,250,2500,999750):
                    words,sample = self.wave(us)
                    self.assertEqual(list(sample.phases),[phase]*16)
                    self.assertEqual(words,self.reference_wave(c,sample,us))
                    self.assert_cycle_readback(words)
                    if us <= 250:
                        stream += words
                rises = [i for i,w in enumerate(stream) if w & 1 and (i==0 or not stream[i-1]&1)]
                self.assertTrue(all(b-a >= 64 for a,b in zip(rises,rises[1:])),(phase,level,mod))

    def test_scanned_circle_has_no_subperiod_rising_edges(self):
        # This is the real default configuration, tested as a digital stream.
        c = Config(shape="CIRCLE",level=30,mod_hz=200)
        self.configure(c)
        last_rise = [-64]*16
        previous = 0
        for us in range(0,2000000,self.half_us):
            words,sample = self.wave(us)
            self.assert_cycle_readback(words)
            for slot,value in enumerate(words):
                rising = value & ~previous
                index = us//25*64 + slot
                while rising:
                    bit = rising & -rising
                    channel = bit.bit_length()-1
                    self.assertGreaterEqual(index-last_rise[channel],64,(us,channel,sample.phases[channel]))
                    last_rise[channel] = index
                    rising ^= bit
                previous = value

    def test_guard_resets_phase_and_can_truncate_final_pulse(self):
        c = Config(shape="POINT",level=100,mod_hz=0)
        self.configure(c)
        sample = Sample()
        self.dll.test_geometry(0,ct.byref(sample))
        self.dll.test_phase(ct.byref(sample))
        nominal = list(sample.phases)
        stream = []
        for us, phase in ((0,1),(250,63),(500,25),(750,6)):
            self.assertEqual(self.command("CALIBRATION",action="trial",mask=1,
                offsets=",".join(str((phase-p)%64) for p in nominal),
                geometry_id=self.array.resolved_geometry().identity)[0].kind,"ACK")
            self.dll.test_reset_wave()
            words,sample = self.wave(us)
            self.assertEqual(list(sample.phases),[phase]*16)
            self.assertEqual(words[:64],[0]*64)
            if us == 0:
                # Explicit exception: the guard cuts this final pulse after one slot.
                self.assertEqual(words[-2:],[0,1])
            stream += words
        rises = [i for i,w in enumerate(stream) if w and (i==0 or not stream[i-1])]
        self.assertTrue(all(b-a >= 64 for a,b in zip(rises,rises[1:])))

    def test_integer_phase_against_double_precision_reference(self):
        self.configure(Config(shape="POINT"))
        rng = random.Random(20261007)
        points = [(x, y, z) for x in (-100000, 0, 100000)
                  for y in (-100000, 0, 100000) for z in (20000, 150000, 300000)]
        points += [(rng.randint(-100000,100000), rng.randint(-100000,100000),
                    rng.randint(20000,300000)) for _ in range(1000)]
        for x, y, z in points:
            sample = Sample(x_um=x, y_um=y, z_um=z)
            self.dll.test_phase(ct.byref(sample))
            expected = focus_phases(Config(shape="POINT"), (x/1000,y/1000,z/1000), self.array)
            for actual, reference in zip(sample.phases, expected):
                # 1/8 um distance truncation may cross a nearest-phase boundary.
                delta = (actual-reference) % 64
                self.assertLessEqual(min(delta,64-delta),1,(x,y,z,actual,reference))

    def test_incremental_lookahead_is_published_only_when_complete(self):
        self.configure(Config(shape="CIRCLE", level=100, mod_hz=0))
        self.ports(0)
        self.assertFalse(self.dll.test_wave_ready(1000))
        for _ in range(16):
            self.dll.test_prepare_wave()
            self.assertFalse(self.dll.test_wave_ready(1000))
        self.dll.test_prepare_wave()
        self.assertTrue(self.dll.test_wave_ready(1000))
        prepared = self.ports(1000)
        self.assertFalse(self.dll.test_wave_ready(2000))
        self.dll.test_reset_wave()
        synchronous = self.ports(1000)
        self.assertEqual(prepared[:2], synchronous[:2])
        self.assertEqual(bytes(prepared[2]), bytes(synchronous[2]))

    def test_cached_segments_preserve_sketch_coordinates(self):
        configs = [Config(shape=s) for s in ("SQUARE", "TRIANGLE", "ARROW")]
        configs += [Config(shape="CUSTOM", path_closed=0,
                           path_xy_um=",".join(f"{-15000+i*470}:{15000 if i%2 else -15000}" for i in range(64))),
                    Config(shape="CUSTOM", scan_paths="|".join(
                        f"{-12000+i*3000}:-15000,{-12000+i*3000}:15000" for i in range(8)))]
        for c in configs:
            self.configure(c)
            for us in range(0, 2000000, 23017):
                sample = Sample()
                self.dll.test_geometry(us,ct.byref(sample))
                expected, scan, stroke = trajectory_sample(c,us/1000000)
                self.assertAlmostEqual(sample.x_um,float(expected[0])*1000,delta=3)
                self.assertAlmostEqual(sample.y_um,float(expected[1])*1000,delta=3)
                self.assertEqual(sample.scan_on,scan)
                self.assertEqual(sample.stroke,stroke)

    def test_cubemx_pin_and_dma_configuration(self):
        config=dict(line.split("=",1) for line in (ROOT/"haptics_f103c8.ioc").read_text().splitlines()
                    if "=" in line and not line.startswith("#"))
        self.assertNotIn("PB2.Signal",config)
        self.assertEqual(config["PA8.Signal"],"GPIO_Output")
        self.assertEqual(config["PA13.Signal"],"SYS_JTMS-SWDIO")
        self.assertEqual(config["PA14.Signal"],"SYS_JTCK-SWCLK")
        self.assertEqual(config["Dma.TIM1_CH1.2.Instance"],"DMA1_Channel2")
        self.assertEqual(config["Dma.TIM1_UP.1.Instance"],"DMA1_Channel5")
        self.assertEqual(config["Dma.ADC1.0.Instance"],"DMA1_Channel1")
        self.assertEqual(config["VP_TIM1_VS_no_output1.Mode"],"Output Compare1 No Output")
        self.assertEqual(config["PA3.GPIO_PuPd"],"GPIO_PULLUP")
        self.assertTrue(config["NVIC.USART2_IRQn"].startswith("true\\:0\\:0"))
        self.assertTrue(config["NVIC.DMA1_Channel2_IRQn"].startswith("true\\:1\\:0"))
        self.assertTrue(config["NVIC.DMA1_Channel5_IRQn"].startswith("true\\:1\\:0"))

    def test_dual_dma_output_and_fail_safe(self):
        executable=ROOT/"build"/("test_wave_driver.exe" if os.name == "nt" else "test_wave_driver")
        includes=("Core/Inc","Drivers/STM32F1xx_HAL_Driver/Inc",
                  "Drivers/STM32F1xx_HAL_Driver/Inc/Legacy",
                  "Drivers/CMSIS/Device/ST/STM32F1xx/Include","Drivers/CMSIS/Include")
        subprocess.run([self.compiler,"-std=c11","-O2","-Wall","-Wextra","-Werror",
            "-Wno-pointer-to-int-cast","-Wno-int-to-pointer-cast",
            "-ffunction-sections","-fdata-sections",
            "-DSTM32F103xB","-DUSE_HAL_DRIVER",*("-I"+p for p in includes),
            "tests/wave_driver_harness.c","Core/Src/haptics.c","Core/Src/geometry.c",
            "Core/Src/wave_f103.c","-lm","-static-libgcc","-Wl,--gc-sections",
            "-o",str(executable.relative_to(ROOT))],cwd=ROOT,check=True)
        subprocess.run([str(executable)],cwd=ROOT,check=True)

    def test_channel_diagnostic_profile(self):
        executable=ROOT/"build"/("test_channel_driver.exe" if os.name == "nt" else "test_channel_driver")
        includes=("Core/Inc","Drivers/STM32F1xx_HAL_Driver/Inc",
                  "Drivers/STM32F1xx_HAL_Driver/Inc/Legacy",
                  "Drivers/CMSIS/Device/ST/STM32F1xx/Include","Drivers/CMSIS/Include")
        subprocess.run([self.compiler,"-std=c11","-O2","-Wall","-Wextra","-Werror",
            "-Wno-pointer-to-int-cast","-Wno-int-to-pointer-cast",
            "-ffunction-sections","-fdata-sections",
            "-DSTM32F103xB","-DUSE_HAL_DRIVER","-DF103_CHANNEL_TEST=1",*("-I"+p for p in includes),
            "tests/channel_test_driver_harness.c","Core/Src/haptics.c","Core/Src/geometry.c",
            "Core/Src/wave_f103.c","-lm","-static-libgcc","-Wl,--gc-sections",
            "-o",str(executable.relative_to(ROOT))],cwd=ROOT,check=True)
        subprocess.run([str(executable)],cwd=ROOT,check=True)

    def test_adc_and_uptime_format(self):
        self.configure()
        self.command("CAPTURE")
        self.now=5000000000
        self.dll.test_poll(self.now)
        frame=decode(self.dll.test_take().splitlines()[0])
        capture=Capture.parse(frame.fields)
        self.assertEqual(len(capture.raw),200)
        self.assertEqual(capture.fs_hz,400000)
        snap=Snapshot.parse(self.command("SNAP")[1].fields)
        self.assertEqual(snap.uptime_ms,self.now)

    def test_fixed_pin_profile_and_parameterized_pa8_mapping(self):
        includes=("Core/Inc","Drivers/STM32F1xx_HAL_Driver/Inc",
                  "Drivers/STM32F1xx_HAL_Driver/Inc/Legacy",
                  "Drivers/CMSIS/Device/ST/STM32F1xx/Include","Drivers/CMSIS/Include")
        for label, extra in (("pb11",()),
                             ("pb10",("-DF103_PIN_TEST_CHANNEL=10","-DF103_PIN_TEST_ON_MS=10000")),
                             ("pa8",("-DF103_PIN_TEST_CHANNEL=2","-DF103_PIN_TEST_ON_MS=1500")),
                             ("pa8_continuous",("-DF103_PIN_TEST_CHANNEL=2","-DF103_PIN_TEST_ON_MS=0")),
                             ("pb11_short",("-DF103_PIN_TEST_ON_MS=1",)),
                             ("pb11_long",("-DF103_PIN_TEST_ON_MS=0x7fffffffu",))):
            executable=ROOT/"build"/("test_pin_"+label+(".exe" if os.name=="nt" else ""))
            subprocess.run([self.compiler,"-std=c11","-O2","-Wall","-Wextra","-Werror",
                "-Wno-pointer-to-int-cast","-Wno-int-to-pointer-cast",
                "-ffunction-sections","-fdata-sections","-DSTM32F103xB","-DUSE_HAL_DRIVER",
                "-DF103_CHANNEL_TEST=1","-DF103_PIN_TEST=1",*extra,*("-I"+p for p in includes),
                "tests/channel_test_driver_harness.c","Core/Src/haptics.c","Core/Src/geometry.c",
                "Core/Src/wave_f103.c","-lm","-static-libgcc","-Wl,--gc-sections",
                "-o",str(executable.relative_to(ROOT))],cwd=ROOT,check=True)
            subprocess.run([str(executable)],cwd=ROOT,check=True)

    def test_invalid_pin_profile_parameters_fail_before_build(self):
        for extra in (("-DF103_PIN_TEST_CHANNEL=-1",),("-DF103_PIN_TEST_CHANNEL=16",),
                      ("-DF103_PIN_TEST_ON_MS=-1",),("-DF103_PIN_TEST_ON_MS=-1u",),
                      ("-DF103_PIN_TEST_ON_MS=0x80000000u",),
                      ("-DF103_CHANNEL_TEST=0",)):
            result=subprocess.run([self.compiler,"-std=c11","-fsyntax-only","-ICore/Inc",
                "-DF103_CHANNEL_TEST=1","-DF103_PIN_TEST=1",*extra,"-x","c","-"],cwd=ROOT,
                input='#include "app_f103.h"\n',text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0,extra)
            self.assertIn("PinTest",result.stderr)

    def test_desktop_takeover_and_logged_adc_at_serial_wire_speed(self):
        # Actual F103 C core, with synthetic GPIO/ADC callbacks, not a board test.
        dll = self.dll

        class FirmwareTransport:
            def __init__(self):
                dll.test_local_start()
                self.started = self.sent_at = time.monotonic()
                self.buffer = bytearray()

            def clock(self):
                return int((time.monotonic()-self.started)*1000)

            def collect(self):
                raw = dll.test_take()
                if not self.buffer:
                    self.sent_at = time.monotonic()
                self.buffer.extend(raw)

            def write(self, raw):
                for offset in range(0, len(raw), 17):
                    part = raw[offset:offset+17]
                    dll.test_feed(part, len(part), self.clock())
                self.collect()

            def read(self):
                time.sleep(.003)
                if not self.buffer:
                    dll.test_poll(self.clock())
                    self.collect()
                size = min(len(self.buffer), int((time.monotonic()-self.sent_at)*11520))
                raw = bytes(self.buffer[:size])
                del self.buffer[:size]
                self.sent_at += size / 11520
                return raw

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            session = Session(demo=False, factory=FirmwareTransport, record_root=directory)
            session.start()

            def event(predicate):
                deadline = time.monotonic()+4
                while time.monotonic() < deadline:
                    try:
                        value = session.events.get(timeout=.05)
                    except queue.Empty:
                        continue
                    self.assertNotIn(value["kind"], ("error", "warning", "rejected", "recording_error"), value)
                    if predicate(value):
                        return value
                self.fail("Expected native F103 serial event did not arrive")

            try:
                state = event(lambda e: e["kind"] == "state")["state"]
                self.assertEqual((state.mode, state.state), ("LOCAL", "RUNNING"))
                self.assertEqual(session.limits["max_scan_points"], 64)
                session.command("CAPTURE")
                capture = event(lambda e: e["kind"] == "capture")["capture"]
                self.assertTrue(capture.tx_running)
                self.assertEqual(capture.raw, tuple(600+i % 10 for i in range(200)))
                recorded = event(lambda e: e["kind"] == "recording")
                session.command("MODE", value="REMOTE")
                state = event(lambda e: e["kind"] == "state" and e["state"].mode == "REMOTE")["state"]
                self.assertEqual(state.state, "IDLE")
                self.assertFalse(dll.test_active())
                session.command("CONFIG", **Config(shape="POINT", mod_hz=0).wire())
                event(lambda e: e["kind"] == "state" and e["state"].revision == 1)
                session.command("START")
                event(lambda e: e["kind"] == "state" and e["state"].state == "RUNNING")
                session.command("STOP")
                event(lambda e: e["kind"] == "state" and e["state"].state == "IDLE")
                records = [json.loads(line) for line in (Path(recorded["directory"])/"captures.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["capture"]["raw"], list(capture.raw))
                self.assertFalse(records[0]["capture"]["simulated"])
            finally:
                session.close()
                session.join(3)
            self.assertFalse(session.is_alive())
            self.assertFalse(dll.test_active())


if __name__ == "__main__":
    unittest.main(verbosity=2)
