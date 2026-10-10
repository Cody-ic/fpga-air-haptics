"""Mock transport checks: a monitor never enables outputs and always stops safely."""

import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from desktop_app.protocol import decode, encode
from firmware.stm32f103c8_cube.channel_monitor import channel_fields, observe


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class MockSerial:
    """Fragmented serial replies plus autonomous test states; no real COM port."""
    def __init__(self, clock, *, profile="CHANNEL_TEST", done_at=0.8,
                 hello=True, bad_crc=False, interrupt_at=None, stop_ack=True,
                 io_failure_at=None, mask=4, done_reason="CHANNEL_TEST_DONE"):
        self.clock = clock
        self.profile = profile
        self.done_at = done_at
        self.hello = hello
        self.bad_crc = bad_crc
        self.interrupt_at = interrupt_at
        self.stop_ack = stop_ack
        self.io_failure_at = io_failure_at
        self.mask = mask
        self.done_reason = done_reason
        self.commands = []
        self.buffer = bytearray()
        self.closed = False
        self.stopped = False

    def append(self, kind, seq, verb, **fields):
        self.buffer.extend(encode(kind, seq, verb, **fields))

    def state(self):
        done = self.clock() >= self.done_at
        self.append("TEL", 0, "STATE", boot="mock-boot", uptime_ms=int(self.clock()*1000),
                    sample=len(self.commands), rev=1, simulated=0, mode="LOCAL",
                    state="IDLE" if self.stopped or done else "RUNNING",
                    output=0 if self.stopped or done else 1,
                    drive_on=0 if self.stopped or done else 1,
                    channel_mask=0 if self.stopped or done else self.mask,
                    reason="NONE" if self.stopped else (self.done_reason if done else "CHANNEL_TEST_ON"))

    def write(self, raw):
        frame = decode(raw)
        self.commands.append(frame.verb)
        if frame.verb == "HELLO":
            if self.bad_crc:
                self.buffer.extend(b"HAP3 TEL 0 STATE*0000\n")
            if self.hello:
                self.append("ACK", frame.seq, "HELLO", proto=3, boot="mock-boot",
                            device="STM32F103C8T6", simulated=0, hw_rows=4, hw_cols=4,
                            profile=self.profile)
                self.state()
        elif frame.verb == "PING":
            self.append("ACK", frame.seq, "PING", applied=1)
        elif frame.verb == "SNAP":
            self.append("ACK", frame.seq, "SNAP", applied=1)
            self.state()
        elif frame.verb == "STOP":
            self.stopped = True
            if self.stop_ack:
                self.append("ACK", frame.seq, "STOP", applied=1)
            self.state()
        elif frame.verb == "CAPTURE":
            self.append("ACK", frame.seq, "CAPTURE", boot="mock-boot", rev=1,
                        uptime_ms=int(self.clock()*1000), simulated=0, tx_running=1,
                        pin="PA0", fs_hz=400000, bits=12, n=200,
                        raw=",".join(str(600+i % 10) for i in range(200)))
        else:
            raise AssertionError(f"Forbidden monitor command: {frame.verb}")

    def read(self):
        self.clock.now += 0.01
        if self.interrupt_at is not None and self.clock() >= self.interrupt_at:
            self.interrupt_at = None
            raise KeyboardInterrupt()
        if self.io_failure_at is not None and self.clock() >= self.io_failure_at:
            self.io_failure_at = None
            raise OSError("mock read failed")
        raw = bytes(self.buffer[:97])
        del self.buffer[:97]
        return raw

    def close(self):
        self.closed = True


class ChannelMonitorTests(unittest.TestCase):
    def run_monitor(self, **settings):
        clock = Clock()
        options = {key: settings.pop(key) for key in ("max_seconds", "capture") if key in settings}
        transport = MockSerial(clock, **settings)
        with tempfile.TemporaryDirectory() as directory:
            result = observe(transport, directory, clock=clock, emit=lambda message: None, **options)
            summary = json.loads((Path(directory)/"summary.json").read_text(encoding="utf-8"))
            with (Path(directory)/"states.csv").open(encoding="utf-8-sig") as stream:
                states = list(csv.DictReader(stream))
            captures = [json.loads(line) for line in (Path(directory)/"captures.jsonl").read_text().splitlines()]
            raw = (Path(directory)/"received.bin").read_bytes()
        self.assertTrue(transport.closed)
        self.assertEqual(result, summary)
        self.assertFalse(set(transport.commands) & {"START", "CONFIG", "MODE", "CALIBRATION", "RESET"})
        return transport, result, states, captures, raw

    def test_complete_fragmented_run_records_mapping_and_confirms_stop(self):
        transport, result, states, captures, raw = self.run_monitor()
        self.assertEqual(result["outcome"], "completed")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(transport.commands.count("STOP"), 1)
        self.assertNotIn("CAPTURE", transport.commands)
        self.assertEqual(captures, [])
        pa8 = next(row for row in states if row["channel_mask"] == "0x0004")
        self.assertEqual((pa8["logical_channels"], pa8["mcu_pins"], pa8["pcb_channels"], pa8["cn1_pins"]),
                         ("2", "PA8", "CH6", "CN1-9"))
        self.assertIn(b"CHANNEL_TEST_DONE", raw)

    def test_wrong_profile_stops_after_handshake_then_reports_error(self):
        # Prior business telemetry can precede the STOP reply and enable all channels.
        transport, result, *_ = self.run_monitor(profile="BUSINESS", mask=65535)
        self.assertEqual(result["outcome"], "error")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(transport.commands[:2], ["HELLO", "STOP"])
        self.assertIn("不是", result["error"])

    def test_fixed_pb11_run_completes_and_records_correct_mapping(self):
        _, result, states, _, raw = self.run_monitor(mask=1 << 11, done_reason="PIN_TEST_DONE")
        self.assertEqual(result["outcome"],"completed")
        self.assertTrue(result["stop_confirmed"])
        pb11 = next(row for row in states if row["output"]=="1")
        self.assertEqual((pb11["logical_channels"],pb11["mcu_pins"],pb11["pcb_channels"],pb11["cn1_pins"]),
                         ("11","PB11","CH15","CN1-18"))
        self.assertIn(b"PIN_TEST_DONE",raw)

    def test_timeout_stops_once_without_restart(self):
        transport, result, *_ = self.run_monitor(done_at=10, max_seconds=0.6)
        self.assertEqual(result["outcome"], "timeout")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(transport.commands.count("STOP"), 1)

    def test_ctrl_c_stops_and_closes(self):
        _, result, *_ = self.run_monitor(done_at=10, interrupt_at=0.4)
        self.assertEqual(result["outcome"], "interrupted")
        self.assertTrue(result["stop_confirmed"])

    def test_read_failure_after_hello_still_stops(self):
        _, result, *_ = self.run_monitor(done_at=10, io_failure_at=0.4)
        self.assertEqual(result["outcome"], "error")
        self.assertIn("mock read failed", result["error"])
        self.assertTrue(result["stop_confirmed"])

    def test_no_handshake_never_sends_stop(self):
        transport, result, *_ = self.run_monitor(hello=False, max_seconds=0.2)
        self.assertEqual(transport.commands, ["HELLO"])
        self.assertEqual(result["outcome"], "error")
        self.assertFalse(result["stop_confirmed"])

    def test_output_zero_without_stop_ack_is_not_confirmation(self):
        _, result, *_ = self.run_monitor(stop_ack=False)
        self.assertFalse(result["stop_confirmed"])
        self.assertIn("output=0", result["stop_error"])

    def test_crc_error_is_recorded_and_next_valid_frame_recovers(self):
        _, result, *_ = self.run_monitor(bad_crc=True)
        self.assertEqual(result["decode_errors"], 1)
        self.assertEqual(result["outcome"], "completed")
        self.assertTrue(result["stop_confirmed"])

    def test_capture_requires_opt_in_and_preserves_raw_codes_with_source(self):
        transport, result, _, captures, _ = self.run_monitor(capture=True, done_at=1.5)
        self.assertIn("CAPTURE", transport.commands)
        self.assertGreater(result["captures"], 0)
        self.assertTrue(result["stop_confirmed"])
        self.assertFalse(captures[0]["receiver_connected"])
        self.assertEqual(len(captures[0]["capture"]["raw"]), 200)
        self.assertIn("not calibrated", captures[0]["source"])

    def test_all_logical_to_physical_mappings_match_r5(self):
        expected = (("PB0", 4, 7), ("PB1", 5, 8), ("PA8", 6, 9), ("PB3", 7, 10),
                    ("PB4", 0, 3), ("PB5", 1, 4), ("PB6", 2, 5), ("PB7", 3, 6),
                    ("PB8", 12, 11), ("PB9", 13, 12), ("PB10", 14, 17), ("PB11", 15, 18),
                    ("PB12", 8, 13), ("PB13", 9, 14), ("PB14", 10, 15), ("PB15", 11, 16))
        for channel, (pin, pcb, cn1) in enumerate(expected):
            row = channel_fields(1 << channel)
            self.assertEqual(row["mcu_pins"], pin)
            self.assertEqual(row["pcb_channels"], f"CH{pcb}")
            self.assertEqual(row["cn1_pins"], f"CN1-{cn1}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
