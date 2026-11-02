"""Regressions for the captured partial-PING + complete-CONFIG stream."""
import queue
import threading
import time
import unittest

from desktop_app.controller import Session
from desktop_app.model import Config
from desktop_app.protocol import Decoder, decode, encode
from desktop_app.transport import DemoTransport


class InterruptedReplyTransport(DemoTransport):
    def __init__(self):
        super().__init__()
        self.interrupted = threading.Event()
        self.writes = []

    def write(self, raw):
        frame = decode(raw)
        self.writes.append(frame.verb)
        if frame.verb == 'PING' and not self.interrupted.is_set():
            replies = self.device.handle(raw)
            self.buffer.extend(replies[0][:29])
            self.interrupted.set()
        else:
            super().write(raw)


class ConnectionRecoveryTests(unittest.TestCase):
    def test_captured_truncated_ping_preserves_valid_config_ack(self):
        raw = b'HAP3 ACK 1003 PING applied=1 revHAP3 ACK 1004 CONFIG applied=1 rev=6*24E1\n'
        decoder = Decoder()
        frames, errors = [], []
        for offset in range(0, len(raw), 7):
            found, warnings = decoder.feed(raw[offset:offset+7])
            frames.extend(found)
            errors.extend(warnings)
        self.assertEqual([(f.seq, f.verb) for f, _ in frames], [(1004, 'CONFIG')])
        self.assertTrue(errors)
        self.assertEqual(frames[0][1], raw[raw.rfind(b'HAP3 '):])

    def test_corrupt_tail_is_never_accepted(self):
        decoder = Decoder()
        valid = encode('ACK', 4, 'CONFIG', applied=1, rev=2)
        frames, errors = decoder.feed(b'broken HAP3 ACK 3 PING' + valid.replace(b'rev=2', b'rev=3'))
        self.assertEqual(frames, [])
        self.assertTrue(errors)

    def test_one_lost_heartbeat_does_not_close_live_session_or_lose_config(self):
        transport = InterruptedReplyTransport()
        session = Session(factory=lambda: transport)
        session.start()
        try:
            self.assertTrue(transport.interrupted.wait(2))
            config = Config(shape='CUSTOM', scan_paths='-21023:13848,25023:-23848', z_um=50000)
            session.command('CONFIG', **config.wire())
            errors, confirmed, ping_acks = [], False, 0
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                try:
                    event = session.events.get(timeout=.1)
                except queue.Empty:
                    continue
                if event['kind'] == 'error':
                    errors.append(event['text'])
                if event['kind'] == 'state' and event['state'].config == config:
                    confirmed = True
                if event['kind'] == 'ack' and event['verb'] == 'PING':
                    ping_acks += 1
            self.assertEqual(errors, [])
            self.assertTrue(session.is_alive())
            self.assertTrue(confirmed)
            self.assertGreaterEqual(ping_acks, 2)
            self.assertEqual(transport.writes.count('CONFIG'), 1)
            self.assertNotIn('START', transport.writes)
        finally:
            session.close()
            session.join(2)

    def test_stop_discards_control_waiting_for_heartbeat(self):
        transport = InterruptedReplyTransport()
        session = Session(factory=lambda: transport)
        session.start()
        try:
            self.assertTrue(transport.interrupted.wait(2))
            session.command('CONFIG', **Config().wire())
            time.sleep(.1)
            self.assertNotIn('CONFIG', transport.writes)
            session.command('STOP')
            deadline = time.monotonic()+1
            while time.monotonic() < deadline and 'STOP' not in transport.writes:
                time.sleep(.01)
            self.assertIn('STOP', transport.writes)
            self.assertNotIn('CONFIG', transport.writes)
        finally:
            session.close()
            session.join(2)

    def test_heartbeat_cannot_confirm_missing_control_reply(self):
        class MissingConfigReply(DemoTransport):
            def write(self, raw):
                if decode(raw).verb == 'CONFIG':
                    self.buffer.extend(self.device.handle(raw)[1])
                else:
                    super().write(raw)

        session = Session(factory=MissingConfigReply)
        session.start()
        try:
            deadline = time.monotonic()+2
            while not session.ready and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(session.ready)
            session.command('CONFIG', **Config().wire())
            errors = []
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                event = session.events.get(timeout=1)
                if event['kind'] == 'error':
                    errors.append(event['text'])
                if event['kind'] == 'closed':
                    break
            self.assertTrue(any('CONFIG' in error for error in errors))
            self.assertFalse(session.ready)
        finally:
            session.close()
            session.join(2)


if __name__ == '__main__':
    unittest.main()
