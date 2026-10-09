"""Read-only serial diagnostics against a fragmented, entirely fake transport."""

import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from desktop_app.protocol import decode, encode
from firmware.stm32f103c8_cube.serial_diagnostics import observe


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class MockSerial:
    def __init__(self, clock, *, hello=True, bad_crc=False,
                 restart_at=None, fault=False, lost_ack=None, wrong_ack=False,
                 no_state=False, io_failure_at=None,
                 interrupt_at=None, device="STM32F103C8T6", simulated=0):
        self.clock = clock
        self.hello = hello
        self.bad_crc = bad_crc
        self.restart_at = restart_at
        self.fault = fault
        self.lost_ack = lost_ack
        self.wrong_ack = wrong_ack
        self.no_state = no_state
        self.io_failure_at = io_failure_at
        self.interrupt_at = interrupt_at
        self.device = device
        self.simulated = simulated
        self.commands = []
        self.writes = bytearray()
        self.buffer = bytearray()
        self.closed = False
        self.boot = "fake-boot-1"
        self.sample = 0

    def append(self, kind, seq, verb, **fields):
        self.buffer.extend(encode(kind, seq, verb, **fields))

    def state_fields(self):
        self.sample += 1
        return dict(boot=self.boot, uptime_ms=int(self.clock()*1000), sample=self.sample,
                    rev=1, simulated=self.simulated, mode="LOCAL",
                    state="FAULT" if self.fault else "RUNNING",
                    output=0 if self.fault else 1, drive_on=0 if self.fault else 1,
                    channel_mask=2048, reason="DMA_TRANSFER_ERROR" if self.fault else "PIN_TEST_ON",
                    carrier_hz=40000, phase_steps=64, mod_hz=0, level=100, shape="POINT")

    def state(self):
        if not self.no_state:
            self.append("TEL", 0, "STATE", **self.state_fields())

    def write(self, raw):
        frame = decode(raw)
        self.commands.append(frame.verb)
        self.writes.extend(raw)
        if frame.verb not in ("HELLO", "PING", "SNAP"):
            raise AssertionError(f"Forbidden command {frame.verb}")
        if frame.verb == self.lost_ack:
            if frame.verb == "SNAP":
                self.state()  # TEL alone must not stand in for a missing ACK.
            return
        if frame.verb == "HELLO":
            if not self.hello:
                return
            if self.bad_crc:
                self.buffer.extend(b"HAP3 TEL 0 STATE*0000\n")
            if self.wrong_ack:
                self.append("ACK", frame.seq + 17, "HELLO", proto=3, boot=self.boot)
                self.append("ACK", frame.seq, "PING", applied=1)
            self.append("ACK", frame.seq, "HELLO", proto=3, boot=self.boot,
                        device=self.device, simulated=self.simulated, profile="CHANNEL_TEST",
                        fw_id="F103_20261010", firmware_mode=2, pin_channel=11, on_ms=0)
            self.state()
        else:
            self.append("ACK", frame.seq, frame.verb, boot=self.boot, applied=1)
            if frame.verb == "SNAP":
                self.state()

    def read(self):
        self.clock.now += .005
        if self.restart_at is not None and self.clock() >= self.restart_at:
            self.restart_at = None
            self.boot = "fake-boot-2"
            self.state()
        if self.io_failure_at is not None and self.clock() >= self.io_failure_at:
            self.io_failure_at = None
            raise OSError("fake port read failed")
        if self.interrupt_at is not None and self.clock() >= self.interrupt_at:
            self.interrupt_at = None
            raise KeyboardInterrupt()
        raw = bytes(self.buffer[:113])
        del self.buffer[:113]
        return raw

    def close(self):
        self.closed = True


class SerialDiagnosticsTests(unittest.TestCase):
    def run_diagnostics(self, **settings):
        clock = Clock()
        max_seconds = settings.pop("max_seconds", 1.7)
        reply_timeout = settings.pop("reply_timeout", .3)
        transport = MockSerial(clock, **settings)
        with tempfile.TemporaryDirectory() as directory:
            result = observe(transport, directory, max_seconds=max_seconds,
                             reply_timeout=reply_timeout, clock=clock, emit=lambda message: None)
            path = Path(directory)
            summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
            with (path / "states.csv").open(encoding="utf-8-sig", newline="") as stream:
                states = list(csv.DictReader(stream))
            frames = [json.loads(line) for line in (path / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
            raw_rx = (path / "received.bin").read_bytes()
            raw_tx = (path / "sent.bin").read_bytes()
            self.assertFalse((path / "diagnostics.csv").exists())
            self.assertFalse((path / "diagnostics.jsonl").exists())
        self.assertTrue(transport.closed)
        self.assertEqual(result, summary)
        self.assertEqual(raw_tx, bytes(transport.writes))
        self.assertEqual(set(transport.commands) - {"HELLO", "PING", "SNAP"}, set())
        return transport, result, states, frames, raw_rx

    def test_fragmented_run_saves_bidirectional_ack_and_pin_mapping(self):
        transport, result, states, frames, raw = self.run_diagnostics()
        self.assertEqual(result["outcome"], "completed")
        self.assertTrue(all(result["acknowledgements"][verb] for verb in ("HELLO", "PING", "SNAP")))
        self.assertEqual((result["fw_id"], result["firmware_mode"], result["pin_channel"], result["on_ms"]),
                         ("F103_20261010", "2", "11", "0"))
        self.assertGreater(len(raw), 0)
        self.assertEqual((states[0]["mcu_pins"], states[0]["pcb_channels"], states[0]["cn1_pins"]),
                         ("PB11", "CH15", "CN1-18"))
        self.assertEqual((states[0]["carrier_hz"], states[0]["mod_hz"], states[0]["level"]),
                         ("40000", "0", "100"))
        self.assertEqual(len([r for r in frames if r["direction"] == "tx"]), len(transport.commands))
        self.assertFalse(result["cleanup_stop_sent"])

    def test_crc_rejection_keeps_raw_bytes_and_recovers(self):
        _, result, _, frames, raw = self.run_diagnostics(bad_crc=True)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["decode_errors"], 1)
        self.assertIn(b"STATE*0000", raw)
        self.assertTrue(any("error" in frame for frame in frames))

    def test_no_response_is_bounded_and_never_stops(self):
        transport, result, *_ = self.run_diagnostics(hello=False)
        self.assertEqual(result["outcome"], "error")
        self.assertFalse(result["handshake"])
        self.assertEqual(transport.commands, ["HELLO"])
        self.assertLess(result["elapsed_s"], 1)
        self.assertEqual(result["reply_timeouts"][0]["verb"], "HELLO")

    def test_tel_state_does_not_replace_missing_snap_ack(self):
        _, result, states, *_ = self.run_diagnostics(lost_ack="SNAP")
        self.assertEqual(result["outcome"], "error")
        self.assertGreater(len(states), 0)
        self.assertIn("SNAP", result["error"])

    def test_short_observation_does_not_claim_no_reply_as_success(self):
        transport, result, *_ = self.run_diagnostics(hello=False, max_seconds=.1, reply_timeout=2)
        self.assertEqual(transport.commands, ["HELLO"])
        self.assertEqual(result["outcome"], "error")
        self.assertIn("HELLO", result["error"])
        self.assertLess(result["elapsed_s"], .2)

    def test_seq_and_verb_mismatches_are_logged_and_ignored(self):
        _, result, _, frames, _ = self.run_diagnostics(wrong_ack=True)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["unexpected_replies"], 2)
        self.assertEqual(len([row for row in frames if row["direction"] == "validation"]), 2)

    def test_changed_boot_is_saved_and_reports_restart(self):
        _, result, states, frames, _ = self.run_diagnostics(restart_at=.6)
        self.assertEqual(result["outcome"], "device_restarted")
        self.assertEqual(states[-1]["boot"], "fake-boot-2")
        self.assertTrue(any(row.get("fields", {}).get("boot") == "fake-boot-2" for row in frames))

    def test_fault_states_are_still_saved(self):
        _, result, states, *_ = self.run_diagnostics(fault=True)
        self.assertEqual(result["outcome"], "device_fault")
        self.assertGreater(result["fault_states"], 0)
        self.assertEqual(states[0]["fault"], "DMA_TRANSFER_ERROR")

    def test_no_state_is_error_even_when_acknowledgements_succeed(self):
        _, result, *_ = self.run_diagnostics(no_state=True)
        self.assertEqual(result["outcome"], "error")
        self.assertIn("STATE", result["error"])

    def test_unknown_device_and_demo_are_rejected(self):
        for values in (dict(device="OTHER"), dict(simulated=1)):
            with self.subTest(values=values):
                transport, result, *_ = self.run_diagnostics(**values)
                self.assertEqual(result["outcome"], "error")
                self.assertEqual(transport.commands, ["HELLO"])

    def test_io_failure_and_cancel_only_close_transport(self):
        for values, expected in ((dict(io_failure_at=.5), "error"),
                                 (dict(interrupt_at=.5), "interrupted")):
            with self.subTest(values=values):
                _, result, *_ = self.run_diagnostics(**values)
                self.assertEqual(result["outcome"], expected)
                self.assertFalse(result["cleanup_stop_sent"])

    def test_refuses_overwriting_existing_diagnostic_logs(self):
        clock = Clock()
        transport = MockSerial(clock)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "received.bin"
            source.write_bytes(b"previous session")
            with self.assertRaises(FileExistsError):
                observe(transport, directory, clock=clock)
            self.assertEqual(source.read_bytes(), b"previous session")
        self.assertTrue(transport.closed)
        self.assertEqual(transport.commands, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
