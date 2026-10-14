import csv
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_app.controller import Pending, Session
from desktop_app.demo import DemoDevice
from desktop_app.protocol import Snapshot, decode, encode
from desktop_app.receiver import Capture
from desktop_app.receiver_log import CaptureRecorder, capture_from_record, main, summarize


class ReceiverLogTests(unittest.TestCase):
    def capture(self, simulated=True):
        return Capture("test-boot", 2, 1000, simulated, True, "PA0", 400000, 12,
                       tuple(600 + i % 10 for i in range(200)))

    def test_raw_windows_context_summary_and_offline_recalculation(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = CaptureRecorder(directory, lambda kind, **data: events.append((kind, data)))
            state = Snapshot.parse(decode(DemoDevice().snapshot()).fields)
            state = replace(state, boot="test-boot", revision=2)
            recorder.start()
            recorder.submit(self.capture(), state, 12.5)
            recorder.submit(replace(self.capture(), uptime_ms=1500), replace(state, revision=1), 500)
            recorder.close()
            self.assertFalse(recorder.is_alive())
            self.assertEqual([kind for kind, _ in events], ["recording", "recording"])
            source = recorder.directory / "captures.jsonl"
            records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(capture_from_record(records[0]), self.capture())
            self.assertEqual(records[0]["last_state_context"]["age_ms"], 12.5)
            self.assertEqual(records[0]["last_state_context"]["phases"], list(state.phases))
            with (recorder.directory / "summary.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["source"], "DEMO_SYNTHETIC")
            self.assertEqual(rows[0]["last_state_same_revision"], "1")
            self.assertEqual(rows[1]["last_state_same_revision"], "0")
            before = source.read_bytes()
            with patch("sys.argv", ["receiver_log", str(recorder.directory), "--vref-mv", "3200", "--divider", "4"]), patch("sys.stdout", io.StringIO()):
                main()
            self.assertEqual(source.read_bytes(), before)
            with (recorder.directory / "analysis.csv").open(encoding="utf-8-sig", newline="") as stream:
                analyzed = list(csv.DictReader(stream))
            self.assertAlmostEqual(float(analyzed[0]["pp_mv"]) / float(rows[0]["pp_mv"]), 3200 / 3300)
            self.assertEqual(analyzed[0]["divider"], "4.0")

    def test_record_write_failure_is_explicit(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "not-a-directory"
            root.write_text("occupied", encoding="utf-8")
            recorder = CaptureRecorder(root, lambda kind, **data: events.append((kind, data)))
            recorder.start()
            recorder.submit(self.capture())
            recorder.close()
            self.assertTrue(recorder.failed.is_set())
            self.assertEqual(recorder.count, 0)
            self.assertEqual(events[0][0], "recording_error")
            self.assertFalse(recorder.submit(self.capture()))

    def test_invalid_capture_never_enters_recorder(self):
        session = Session()
        session.boot = "test-boot"
        session.recorder = unittest.mock.Mock()
        session.pending[1] = Pending("CAPTURE", time.monotonic())
        raw = encode("ACK", 1, "CAPTURE", boot="test-boot", rev=2, uptime_ms=1000,
                     simulated=1, tx_running=1, pin="PA0", fs_hz=400000, bits=12, n=200, raw="600")
        session._receive(decode(raw), raw, time.monotonic())
        session.recorder.submit.assert_not_called()
        self.assertTrue(any(event["kind"] == "rejected" for event in list(session.events.queue)))

    def test_source_and_raw_range_survive_roundtrip(self):
        record = dict(schema="haptics-adc-record-1", index=1, received_utc="2026-10-04T00:00:00+00:00",
                      capture=replace(self.capture(False), raw=(0, 4095) * 100).__dict__)
        row = summarize(record)
        self.assertEqual(row["source"], "ADC_UNCALIBRATED")
        self.assertEqual(row["adc_rail"], 1)
        self.assertEqual(row["last_state_same_revision"], 0)
        record["capture"] = dict(record["capture"], raw=[4096] * 200)
        with self.assertRaises(ValueError):
            summarize(record)


if __name__ == "__main__":
    unittest.main()
