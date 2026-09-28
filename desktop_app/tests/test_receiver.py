from dataclasses import replace
import math
import queue
import time
import unittest

from desktop_app.receiver import Capture
from desktop_app.protocol import encode, decode, Decoder
from desktop_app.controller import Session, Pending
from desktop_app.demo import DemoDevice


def fields(**changes):
    data = dict(boot="boot1", rev="0", uptime_ms="0", simulated="1", tx_running="0",
                pin="PA0", fs_hz="400000", bits="12", n="200", raw=",".join(["602"]*200))
    return dict(data, **changes)


class CaptureTests(unittest.TestCase):
    def test_voltage_dc_and_coherent_noise_rejection(self):
        raw = tuple(round(602+200*math.cos(2*math.pi*i/10)+40*math.sin(2*math.pi*i/20))
                    for i in range(200))
        cap = Capture.parse(fields(raw=",".join(map(str, raw))))
        a = cap.analyze()
        self.assertAlmostEqual(a["mean_mv"], 602*3300/4095, delta=.1)
        self.assertAlmostEqual(a["peak40_mv"], 200*3300/4095, delta=.5)
        self.assertAlmostEqual(a["stage2_peak40_mv"], a["peak40_mv"]*3.4)
        self.assertAlmostEqual(sum(a["filtered_mv"]), 0, delta=1e-8)
        self.assertGreater(a["rms_mv"], a["peak40_mv"]/math.sqrt(2))
        self.assertFalse(a["adc_rail"])
        self.assertAlmostEqual(cap.analyze(3200)["peak40_mv"]/a["peak40_mv"], 3200/3300)

    def test_zero_signal_rail_and_bad_windows(self):
        cap=Capture.parse(fields())
        self.assertAlmostEqual(cap.analyze()["peak40_mv"], 0, places=10)
        self.assertTrue(replace(cap, raw=(0,)*200).analyze()["adc_rail"])
        for change in ({"n":"199"}, {"raw":"1,2"}, {"fs_hz":"8000"}, {"simulated":"2"},
                       {"raw":",".join(["4096"]*200)}, {"raw":",".join(["-1"]*200)},
                       {"pin":"PC0"}, {"bits":"16"}, {"rev":"-1"}):
            with self.subTest(change=next(iter(change))), self.assertRaises(ValueError):
                Capture.parse(fields(**change))
        for v, gain in ((float("nan"),3.4), (0,3.4), (3300,float("inf")), (3300,0)):
            with self.assertRaises(ValueError):cap.analyze(v,gain)

    def test_crc_fragmentation_and_session_source_checks(self):
        packet=encode("ACK", 2, "CAPTURE", **fields())
        decoder=Decoder(); frames=[]
        for i in range(0,len(packet),7):
            received, errors=decoder.feed(packet[i:i+7]); self.assertFalse(errors);frames+=received
        self.assertEqual(len(frames),1)
        session=Session();session.ready=True;session.boot="boot1"
        session.pending[2]=Pending("CAPTURE",time.monotonic())
        session._receive(decode(packet),packet,time.monotonic())
        events=[]
        while not session.events.empty():events.append(session.events.get_nowait())
        self.assertEqual([e["kind"] for e in events], ["rx","capture","ack"])
        for change in ({"boot":"oldboot"},{"simulated":"0"}):
            session.pending[2]=Pending("CAPTURE",time.monotonic())
            raw=encode("ACK",2,"CAPTURE",**fields(**change))
            with self.assertRaises(RuntimeError):session._receive(decode(raw),raw,time.monotonic())
        session.pending[2]=Pending("CAPTURE",time.monotonic())
        raw=encode("ACK",2,"CAPTURE",**fields(raw="999"))
        session._receive(decode(raw),raw,time.monotonic())
        self.assertNotIn(2,session.pending)

    def test_demo_requires_handshake_and_flags_synthetic(self):
        demo=DemoDevice(clock=lambda:1)
        request=encode("CMD",1,"CAPTURE")
        self.assertEqual(decode(demo.handle(request)[0]).kind,"ERR")
        hello=decode(demo.handle(encode("CMD",2,"HELLO"))[0])
        self.assertEqual(hello.fields["adc_capture"],"1")
        cap=Capture.parse(decode(demo.handle(request)[0]).fields)
        self.assertTrue(cap.simulated)
        self.assertFalse(cap.tx_running)
        self.assertLess(cap.analyze()["peak40_mv"],1)
        self.assertEqual(decode(demo.handle(request)[0]).fields["code"],"ADC_RATE_LIMIT")

    def test_demo_session_capture_does_not_require_playback(self):
        session=Session();session.start()
        try:
            deadline=time.monotonic()+4
            while time.monotonic()<deadline and not session.ready:time.sleep(.01)
            self.assertIn("ADC_CAPTURE",session.capabilities)
            session.command("CAPTURE")
            got=None
            while time.monotonic()<deadline:
                try:event=session.events.get(timeout=.1)
                except queue.Empty:continue
                if event["kind"]=="capture":got=event["capture"];break
            self.assertIsNotNone(got)
            self.assertTrue(got.simulated)
            self.assertFalse(got.tx_running)
            self.assertEqual(session.last_state.state,"IDLE")
        finally:
            session.close();session.join(3)
        self.assertFalse(session.is_alive())

    def test_stop_discards_queued_capture_with_notification(self):
        session=Session();session.command("CAPTURE");session.command("STOP")
        self.assertEqual(session.events.get_nowait()["verb"],"CAPTURE")
        self.assertEqual(session.commands.get_nowait()[2],"STOP")
