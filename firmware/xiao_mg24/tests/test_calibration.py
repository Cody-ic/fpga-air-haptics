import asyncio
import copy
import json
import math
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibrate_fpga as cal
from receiver_client import ProtocolError


class FakeLink:
    """Scripted device conversation; phase-solving is tested in Verilog."""
    def __init__(self, source=0):
        self.source = source
        self.boot = 1234
        self.token = 0
        self.sent = []
        self.queue = asyncio.Queue()
        self.steps = [(stage, repeat) for stage in range(8) for repeat in range(3)]
        self.index = 0
        self.ack_args = None
        self.queue.put_nowait(self.status(0))

    def status(self, state, configured=False):
        stage, repeat = self.steps[min(self.index, 23)]
        flags = self.source << 2 | int(state == 2) | int(state == 4) << 1
        flags |= int(configured or state in (2, 4, 5)) << 4 | repeat << 8
        meta = state | stage << 8 | 1 << 16
        hardware = 2 | 1 << 8 | 30 << 16
        return cal.Packet(cal.STATUS, self.boot, self.token, (meta, flags, hardware, 100000, 11000))

    async def receive(self, timeout=3):
        return await asyncio.wait_for(self.queue.get(), timeout)

    async def send(self, kind, boot, token=0, *args):
        self.sent.append((kind, boot, token, args))
        if boot != self.boot:
            raise AssertionError("wrong boot")
        if kind == cal.SETUP:
            self.ack_args = args
            self.queue.put_nowait(cal.Packet(cal.SETUP_ACK, boot, self.token, args))
            self.queue.put_nowait(self.status(0, True))
        elif kind == cal.READ_TRIM:
            base, which = args
            values = [10, 10] if which else [0, 9]
            block = struct.unpack("<4I", bytes(values).ljust(16, b"\0"))
            self.queue.put_nowait(cal.Packet(cal.TRIM, boot, self.token, (base | which << 16, *block)))
        elif kind == cal.UPLOAD:
            self.queue.put_nowait(cal.Packet(cal.UPLOAD_ACK, boot, self.token, args))
        elif kind == cal.READY:
            assert args[0] == self.source
            self.token += 1
            self.queue.put_nowait(self.status(2))
        elif kind == cal.MEASURE:
            assert token == self.token
            assert args[2] == self.source
            stage, repeat = self.steps[self.index]
            assert args[3:] == (1 | stage << 8, repeat)
            if args[1] == 0:
                self.queue.put_nowait(self.status(5))
            else:
                self.index += 1
                if self.index == len(self.steps):
                    self.queue.put_nowait(self.status(4))
                else:
                    self.token += 1
                    self.queue.put_nowait(self.status(2))
        elif kind == cal.RELEASE:
            self.queue.put_nowait(cal.Packet(cal.RELEASED, boot, self.token, self.status(4).args))


class FakeReceiver:
    boot = 42
    can_stop = True
    def __init__(self, bluetooth=False, rail=False):
        self.bluetooth = bluetooth
        self.rail = rail
        self.captures = 0

    async def info(self):
        return dict(transport="BLE" if self.bluetooth else "USB", boot=self.boot)

    async def capture(self, raw=False):
        self.captures += 1
        return dict(peak40_adc=100, valid=not self.rail, adc_rail=self.rail, samples=[2048] * 200)

    async def request(self, command):
        assert command == "STOP"


class FormatTests(unittest.TestCase):
    def test_fragmentation_crc_resync_and_bounded_tail(self):
        packet = cal.encode(cal.STATUS, 123, 4, 0, 0, 2 | 1 << 8 | 30 << 16, 100000, 11000)
        for split in range(len(packet) + 1):
            decoder = cal.PacketDecoder()
            decoded = decoder.feed(packet[:split]) + decoder.feed(packet[split:])
            self.assertEqual(decoded[0].token, 4)
        broken = bytearray(packet)
        broken[12] ^= 1
        decoder = cal.PacketDecoder()
        decoded = decoder.feed(b"noise" + broken + packet + b"x" * 20000)
        self.assertEqual(len(decoded), 1)
        self.assertGreater(decoder.bad_crc, 0)
        self.assertLessEqual(len(decoder.buffer), 1)

    def test_scope_units_and_bad_readings(self):
        self.assertEqual(cal.scope_power(200 * 3300 / 4095, True), 10000)
        for value, confirmed in [(float("nan"), True), (-1, True), (6601, True), (1, False)]:
            with self.assertRaises(ProtocolError):
                cal.scope_power(value, confirmed)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def manual(self, status):
        return dict(vpp_mv=100, confirmed=True, token=status["token"])

    async def test_scope_no_receiver_and_complete_record(self):
        link = FakeLink()
        records = []
        result = await cal.calibrate(link, None, False, records.append, manual=self.manual)
        self.assertEqual(result["phase_offsets"], [0, 9])
        self.assertEqual(result["theoretical_phases"], [10, 10])
        self.assertEqual(len(result["measurements"]), 24)
        self.assertEqual(result["source"], "scope")
        self.assertFalse(result["simulated"])
        self.assertEqual(link.sent[-1][0], cal.RELEASE)
        self.assertNotIn(cal.ABORT, [v[0] for v in link.sent])
        cal.validate_document(result, dict(rows=1, cols=2, pitch_um=11000), cal.Settings())
        for field, value in [("kind", "theoretical_phase"), ("simulated", True), ("level", 50),
                             ("position_um", [0, 0, 110000]), ("phase_offsets", [0, 64])]:
            invalid = copy.deepcopy(result)
            invalid[field] = value
            with self.assertRaises(ProtocolError):
                cal.validate_document(invalid, dict(rows=1, cols=2, pitch_um=11000), cal.Settings())
        # Import is transmitted as a candidate, never directly committed.
        repeat = FakeLink()
        verified = await cal.calibrate(repeat, None, False, lambda _: None, manual=self.manual, imported=result)
        self.assertTrue(verified["imported_and_reverified"])
        self.assertIn(cal.UPLOAD, [v[0] for v in repeat.sent])

    async def test_adc_usb_and_ble_share_workflow(self):
        for bluetooth in (False, True):
            receiver = FakeReceiver(bluetooth)
            result = await cal.calibrate(FakeLink(1), receiver, bluetooth, lambda _: None)
            self.assertEqual(result["source"], "adc_ble" if bluetooth else "adc_usb")
            self.assertEqual(receiver.captures, 25)

    async def test_wrong_source_never_starts(self):
        link = FakeLink(1)
        with self.assertRaises(ProtocolError):
            await cal.calibrate(link, None, False, lambda _: None, manual=self.manual)
        self.assertNotIn(cal.READY, [v[0] for v in link.sent])

    async def test_save_failure_prevents_release(self):
        link = FakeLink()
        def disk_full(value):
            if value["type"] == "result":
                raise OSError("disk full")
        with self.assertRaises(OSError):
            await cal.calibrate(link, None, False, disk_full, manual=self.manual)
        self.assertNotIn(cal.RELEASE, [v[0] for v in link.sent])
        self.assertEqual(link.sent[-1][0], cal.ABORT)

    async def test_old_manual_token_is_rejected_and_aborted(self):
        async def old(status):
            return dict(vpp_mv=100, confirmed=True, token=status["token"] - 1)
        link = FakeLink()
        with self.assertRaises(ProtocolError):
            await cal.calibrate(link, None, False, lambda _: None, manual=old)
        self.assertEqual(link.sent[-1][0], cal.ABORT)

    async def test_cancel_while_waiting_human_sends_abort(self):
        ready = asyncio.Event()
        async def waiting(status):
            ready.set()
            await asyncio.Future()
        link = FakeLink()
        task = asyncio.create_task(cal.calibrate(link, None, False, lambda _: None, manual=waiting))
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(link.sent[-1][0], cal.ABORT)

    async def test_reset_and_bad_adc_preflight(self):
        link = FakeLink(1)
        with self.assertRaises(ProtocolError):
            await cal.calibrate(link, FakeReceiver(rail=True), False, lambda _: None)
        self.assertNotIn(cal.READY, [v[0] for v in link.sent])
        link = FakeLink()
        async def reset(status):
            link.queue.put_nowait(cal.Packet(cal.STATUS, 999, link.token, link.status(2).args))
            await asyncio.Future()
        with self.assertRaises(ProtocolError):
            await cal.calibrate(link, None, False, lambda _: None, manual=reset)
        self.assertEqual(link.sent[-1][0], cal.ABORT)


if __name__ == "__main__":
    unittest.main()
