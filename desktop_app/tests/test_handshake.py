"""Connection recovery without replaying output commands or accepting old ACKs."""

import queue
import threading
import time
import unittest

from desktop_app.controller import Session
from desktop_app.demo import DemoDevice
from desktop_app.protocol import decode, encode


class HandshakeTransport:
    simulated = False
    startup_delay_s = 0.03

    def __init__(self, respond_on=1, delayed_ack=False, bad_source=False, drop_start=False,
                 incomplete_prefix=False):
        self.device = DemoDevice()
        self.respond_on = respond_on
        self.delayed_ack = delayed_ack
        self.bad_source = bad_source
        self.drop_start = drop_start
        self.buffer = bytearray()
        self.writes = []
        self.old_hello = None
        self.hello_count = 0
        self.closed = False
        self.read_started = threading.Event()
        self.hello_sent = threading.Event()
        # Includes a complete old STATE and stale bytes at port open.
        # Neither can replace a matching HELLO acknowledgement.
        self.append_real(self.device.snapshot())
        self.buffer.extend(b"old USB bytes" + (b"" if incomplete_prefix else b"\n"))

    def append_real(self, raw):
        frame = decode(raw)
        fields = dict(frame.fields)
        if "simulated" in fields:
            fields["simulated"] = "1" if self.bad_source else "0"
        self.buffer.extend(encode(frame.kind, frame.seq, frame.verb, **fields))

    def write(self, raw):
        frame = decode(raw)
        self.writes.append(frame)
        replies = self.device.handle(raw)
        if frame.verb == "HELLO":
            self.hello_count += 1
            self.hello_sent.set()
            if self.hello_count == 1:
                self.old_hello = replies[0]
            if self.delayed_ack and self.hello_count == 2:
                self.append_real(self.old_hello)
                old = decode(replies[0])
                self.append_real(encode("ACK", frame.seq + 100, "HELLO", **old.fields))
                self.append_real(encode("ACK", frame.seq, "PING"))
            if self.hello_count < self.respond_on:
                return
        if frame.verb == "START" and self.drop_start:
            return
        for reply in replies:
            self.append_real(reply)

    def read(self):
        self.read_started.set()
        for raw in self.device.poll():
            self.append_real(raw)
        result = bytes(self.buffer[:173])
        del self.buffer[:173]
        time.sleep(0.005)
        return result

    def close(self):
        self.closed = True


class HandshakeTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
            session.join(2)
            self.assertFalse(session.is_alive())

    def start(self, transport):
        session = Session(demo=False, factory=lambda: transport)
        session.HELLO_TIMEOUT_S = 0.2
        self.sessions.append(session)
        session.start()
        return session

    def event(self, session, kind, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = session.events.get(timeout=0.02)
            except queue.Empty:
                continue
            if event["kind"] == "error" and kind != "error":
                self.fail(event["text"])
            if event["kind"] == kind:
                return event
        self.fail(f"Missing {kind} event")

    def test_lost_first_hello_recovers_without_output_commands(self):
        transport = HandshakeTransport(respond_on=2)
        session = self.start(transport)
        self.event(session, "ready")
        self.event(session, "state")
        self.assertEqual(transport.hello_count, 2)
        self.assertEqual([frame.verb for frame in transport.writes], ["HELLO", "HELLO"])
        self.assertFalse(session.last_state.output)

    def test_late_and_wrong_sequence_acks_cannot_finish_handshake(self):
        transport = HandshakeTransport(respond_on=3, delayed_ack=True)
        session = self.start(transport)
        self.event(session, "ready")
        self.assertEqual(transport.hello_count, 3)
        self.assertEqual([frame.seq for frame in transport.writes], [1, 2, 3])
        self.assertFalse(any(p.verb == "HELLO" for p in session.pending.values()))

    def test_incomplete_old_line_cannot_permanently_break_handshake(self):
        transport = HandshakeTransport(incomplete_prefix=True)
        session = self.start(transport)
        self.event(session, "ready")
        self.assertEqual(transport.hello_count, 2)

    def test_state_without_hello_still_times_out_with_bounded_attempts(self):
        transport = HandshakeTransport(respond_on=100)
        session = self.start(transport)
        error = self.event(session, "error")
        self.assertIn("握手未回应", error["text"])
        self.event(session, "closed")
        self.assertEqual(transport.hello_count, 3)
        self.assertFalse(session.ready)
        self.assertIsNone(session.boot)
        self.assertTrue(transport.closed)
        self.assertTrue(all(frame.verb == "HELLO" for frame in transport.writes))

    def test_cancel_during_startup_drain_writes_nothing(self):
        transport = HandshakeTransport()
        transport.startup_delay_s = 10
        session = self.start(transport)
        self.assertTrue(transport.read_started.wait(1))
        session.close()
        self.event(session, "closed")
        self.assertEqual(transport.writes, [])
        self.assertTrue(transport.closed)

    def test_cancel_while_waiting_for_hello_does_not_retry(self):
        transport = HandshakeTransport(respond_on=100)
        session = self.start(transport)
        self.assertTrue(transport.hello_sent.wait(1))
        session.close()
        self.event(session, "closed")
        self.assertEqual(transport.hello_count, 1)
        self.assertTrue(transport.closed)

    def test_invalid_source_is_terminal_and_not_retried(self):
        transport = HandshakeTransport(bad_source=True)
        session = self.start(transport)
        error = self.event(session, "error")
        self.assertIn("来源", error["text"])
        self.event(session, "closed")
        self.assertEqual(transport.hello_count, 1)

    def test_lost_start_ack_does_not_replay_start(self):
        transport = HandshakeTransport(drop_start=True)
        session = self.start(transport)
        self.event(session, "ready")
        self.event(session, "state")
        session.command("START")
        error = self.event(session, "error", timeout=3)
        self.assertIn("START", error["text"])
        self.event(session, "closed")
        self.assertEqual(sum(frame.verb == "START" for frame in transport.writes), 1)
        self.assertEqual(sum(frame.verb == "STOP" for frame in transport.writes), 1)


if __name__ == "__main__":
    unittest.main()
