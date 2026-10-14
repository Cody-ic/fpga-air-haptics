import queue
import threading
import time
import unittest

from desktop_app.controller import Session
from desktop_app.model import Config
from desktop_app.protocol import decode, encode
from desktop_app.transport import DemoTransport


class StandaloneTransport(DemoTransport):
    def __init__(self, hold_stop=False, reject_stop=False):
        super().__init__()
        self.device.mode, self.device.state = "LOCAL", "RUNNING"
        self.verbs = []
        self.hold_stop, self.reject_stop = hold_stop, reject_stop
        self.release = threading.Event()
        self.held_state = None

    def write(self, raw):
        frame = decode(raw)
        self.verbs.append(frame.verb)
        if frame.verb == "STOP" and self.reject_stop:
            self.buffer.extend(encode("ERR", frame.seq, "STOP", code="OUTPUT_ERROR"))
        elif frame.verb == "STOP" and self.hold_stop:
            response = self.device.handle(raw)
            self.buffer.extend(response[0])
            self.held_state = response[1]
            self.muted = True
            self.hold_stop = False
        else:
            super().write(raw)

    def read(self):
        if self.release.is_set() and self.held_state is not None:
            self.buffer.extend(self.held_state)
            self.held_state = None
            self.muted = False
        return super().read()


class SerialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
            session.join(3)
            self.assertFalse(session.is_alive())

    def event(self, session, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                event = session.events.get(timeout=.05)
            except queue.Empty:
                continue
            if event["kind"] == "error":
                self.fail(event["text"])
            if predicate(event):
                return event
        self.fail("Expected serial workflow event did not arrive")

    def start(self, transport):
        session = Session(factory=lambda: transport)
        self.sessions.append(session)
        session.start()
        state = self.event(session, lambda event: event["kind"] == "state")["state"]
        self.assertEqual((state.mode, state.state), ("LOCAL", "RUNNING"))
        return session

    def test_takeover_waits_for_stop_state_and_does_not_start(self):
        transport = StandaloneTransport(hold_stop=True)
        session = self.start(transport)
        session.command("MODE", value="REMOTE")
        self.event(session, lambda event: event["kind"] == "ack" and event["verb"] == "STOP")
        self.assertNotIn("MODE", transport.verbs)
        session.command("START")
        self.event(session, lambda event: event["kind"] == "rejected" and event["verb"] == "START")
        transport.release.set()
        state = self.event(session, lambda event: event["kind"] == "state" and event["state"].mode == "REMOTE")["state"]
        self.assertEqual(state.state, "IDLE")
        self.assertFalse(state.output)
        self.assertLess(transport.verbs.index("STOP"), transport.verbs.index("MODE"))
        self.assertNotIn("START", transport.verbs)
        session.command("CONFIG", **Config().wire())
        self.event(session, lambda event: event["kind"] == "ack" and event["verb"] == "CONFIG")

    def test_stop_failure_cancels_takeover(self):
        transport = StandaloneTransport(reject_stop=True)
        session = self.start(transport)
        session.command("MODE", value="REMOTE")
        self.event(session, lambda event: event["kind"] == "rejected" and event["verb"] == "MODE")
        self.assertIsNone(session.takeover)
        self.assertNotIn("MODE", transport.verbs)
        self.assertEqual(session.last_state.mode, "LOCAL")

    def test_user_stop_cancels_pending_mode_switch(self):
        transport = StandaloneTransport(hold_stop=True)
        session = self.start(transport)
        session.command("MODE", value="REMOTE")
        self.event(session, lambda event: event["kind"] == "ack" and event["verb"] == "STOP")
        session.command("STOP")
        self.event(session, lambda event: event["kind"] == "rejected" and event["verb"] == "MODE")
        transport.release.set()
        self.event(session, lambda event: event["kind"] == "state" and event["state"].state == "IDLE")
        self.assertNotIn("MODE", transport.verbs)


if __name__ == "__main__":
    unittest.main()
