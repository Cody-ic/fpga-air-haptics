import json
from pathlib import Path
import tempfile
import time
import unittest

from desktop_app.controller import Session
from desktop_app.live_log import LiveLog


class LiveLogTests(unittest.TestCase):
    def wait(self, condition, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.02)
        self.fail("live log did not update")

    def test_concurrent_session_reader_and_closed_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Session(demo=True, live_root=temporary)
            session.start()
            try:
                self.wait(lambda: session.last_state is not None)
                mirror = session.mirror
                path = mirror.directory/"traffic.jsonl"
                self.wait(lambda: (mirror.directory/"snapshot.json").is_file())
                self.wait(lambda: any('"kind": "state"' in line for line in path.read_text(encoding="utf-8").splitlines()))
                previous = session.last_state.sample
                # File reads never open a second COM port, and don't stop telemetry.
                state = json.loads((mirror.directory/"snapshot.json").read_text(encoding="utf-8"))
                self.assertTrue(state["session"]["simulated"])
                self.wait(lambda: session.last_state.sample > previous)
                records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                self.assertTrue(any(r.get("direction") == "RX" for r in records))
                self.assertTrue(any(r.get("direction") == "TX" for r in records))
                self.assertTrue(session.ready)
            finally:
                session.close()
                session.join(5)
            self.assertFalse(session.is_alive())
            state = json.loads((mirror.directory/"snapshot.json").read_text(encoding="utf-8"))
            self.assertFalse(state["connected"])
            self.assertEqual(state["last_event"], "closed")

    def test_invalid_bytes_and_failure_do_not_block_io(self):
        with tempfile.TemporaryDirectory() as temporary:
            errors = []
            mirror = LiveLog(dict(simulated=False, port="FAKE"), errors.append, temporary)
            mirror.start()
            mirror.raw("RX", b"\x00\xffnoise\n")
            mirror.close()
            records = [json.loads(line) for line in (mirror.directory/"traffic.jsonl").read_text(encoding="utf-8").splitlines()]
            raw = next(r for r in records if r["kind"] == "raw")
            self.assertEqual(bytes.fromhex(raw["hex"]), b"\x00\xffnoise\n")
            blocked = Path(temporary)/"file"
            blocked.write_text("file")
            broken = LiveLog({}, errors.append, blocked)
            broken.start()
            broken.join(2)
            broken.raw("RX", b"still running")
            self.assertTrue(errors)
            self.assertTrue(broken.failed.is_set())


if __name__ == "__main__":
    unittest.main()
