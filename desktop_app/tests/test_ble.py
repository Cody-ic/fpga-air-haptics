"""BLE lifecycle and HAP3 integration without a radio or hardware claims."""

import asyncio
from dataclasses import replace
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_app.ble_transport import BleCancelled, BleProfile, BleScan, BleTransport, RECEIVE_LIMIT
from desktop_app.controller import Session
from desktop_app.model import Config
from desktop_app.protocol import Decoder, encode
from desktop_app.tests.ble_fakes import FakeClientFactory


class BleTransportTests(unittest.TestCase):
    def make(self, profile=None, **options):
        factory = FakeClientFactory(**options)
        transport = BleTransport("test-peer", profile, client_type=factory)
        self.addCleanup(transport.close)
        return transport, factory.client

    def test_uuid_normalization_and_invalid_profile(self):
        profile = BleProfile("FFE0", "ffe1", "FFE1").normalized()
        self.assertEqual(profile.service_uuid, "0000ffe0-0000-1000-8000-00805f9b34fb")
        for bad in (BleProfile(service_uuid="bad-uuid"), BleProfile(chunk_size=-1),
                    BleProfile(chunk_size=513), BleProfile(chunk_size=True), BleProfile(write_mode="bad")):
            with self.assertRaises(ValueError):
                bad.normalized()

    def test_chunking_preserves_full_frames_and_write_order(self):
        for mode, properties, mtu, cap, expected in (
                ("auto", ["write"], 23, 0, 20),
                ("auto", ["write-without-response"], 247, 0, 244),
                ("without-response", ["write", "write-without-response"], 247, 20, 20),
                ("response", ["write"], 1000, 0, 512)):
            with self.subTest(mode=mode, mtu=mtu, cap=cap):
                transport, client = self.make(BleProfile(write_mode=mode, chunk_size=cap),
                                               properties=properties, mtu=mtu)
                first = encode("CMD", 1, "CONFIG", **Config().wire(), fixture_padding="a" * 6000)
                second = encode("CMD", 2, "STOP")
                transport.write(first)
                transport.write(second)
                self.assertEqual(b"".join(data for data, _ in client.writes), first+second)
                self.assertEqual(client.verbs, ["CONFIG", "STOP"])
                self.assertEqual(transport.chunk_size, expected)
                self.assertTrue(all(len(data) <= expected for data, _ in client.writes))
                self.assertEqual(client.writes[0][1], mode != "without-response" and "write" in properties)
                transport.close()
                self.assertTrue(client.unsubscribed and client.disconnected)

    def test_fragmented_notifications_and_buffer_overflow(self):
        transport, client = self.make()
        raw = encode("ACK", 1, "PING", applied=1, rev=0) + encode("ACK", 2, "STOP", applied=1, rev=0)
        decoder = Decoder()
        received = []
        for part in (raw[:7], raw[7:25], raw[25:]):
            client.deliver(part)
            frames, errors = decoder.feed(transport.read())
            self.assertFalse(errors)
            received.extend(frames)
        self.assertEqual([f.verb for f, _ in received], ["PING", "STOP"])
        client.deliver(b"x" * RECEIVE_LIMIT)
        client.deliver(b"x")
        self.assertEqual(len(transport.buffer), 0)
        with self.assertRaisesRegex(OSError, "积压"):
            transport.read()

    def test_bad_characteristics_cleanup(self):
        for fault in ("service", "write", "notify", "duplicate"):
            with self.subTest(fault=fault):
                factory = FakeClientFactory()
                def make_client(*args, **kwargs):
                    client = factory(*args, **kwargs)
                    if fault == "service":
                        client.services.get_service = lambda _: None
                    elif fault == "write":
                        client.write_char.properties = ["read"]
                    elif fault == "notify":
                        client.notify_char.properties = ["read"]
                    else:
                        client.service.characteristics.append(client.write_char)
                    return client
                with self.assertRaises(OSError):
                    BleTransport("test-peer", client_type=make_client)
                self.assertTrue(factory.client.disconnected)

    def test_no_retry_or_trailing_stop_after_partial_write(self):
        transport, client = self.make()
        client.fail_after = 1
        with self.assertRaisesRegex(OSError, "发送未完成"):
            transport.write(encode("CMD", 1, "CONFIG", **Config().wire()))
        self.assertEqual(len(client.writes), 1)
        with self.assertRaises(OSError):
            transport.write(encode("CMD", 2, "STOP"))
        self.assertEqual(len(client.writes), 1)

    def test_write_timeout_is_terminal(self):
        factory = FakeClientFactory(delay_s=0.2)
        transport = BleTransport("test-peer", client_type=factory, write_timeout_s=0.04)
        self.addCleanup(transport.close)
        with self.assertRaisesRegex(OSError, "超时"):
            transport.write(encode("CMD", 1, "HELLO"))
        self.assertFalse(factory.client.writes)

    def test_connection_cancel_and_timeout_cleanup(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                factory = FakeClientFactory(connect_delay_s=5)
                event = threading.Event()
                if cancel:
                    timer = threading.Timer(0.03, event.set)
                    timer.start()
                    self.addCleanup(timer.join)
                with self.assertRaises(BleCancelled if cancel else TimeoutError):
                    BleTransport("test-peer", client_type=factory, cancel_event=event,
                                 connect_timeout_s=1 if cancel else 0.03)
                self.assertTrue(factory.client.disconnected)

    def test_disconnect_and_closed_reads_fail(self):
        transport, client = self.make()
        client.drop()
        with self.assertRaisesRegex(OSError, "断开"):
            transport.read()
        transport.close()
        transport.close()
        self.assertTrue(transport.loop.is_closed())


class BleScanTests(unittest.TestCase):
    def scan(self, scanner_type):
        job = BleScan(timeout_s=0.05, scanner_type=scanner_type)
        job.start()
        job.join(2)
        self.assertFalse(job.is_alive())
        return job.results.get_nowait()

    def test_scan_preserves_backend_devices_and_deduplicates(self):
        device = SimpleNamespace(address="A", name="fallback")
        other = SimpleNamespace(address="B", name=None)
        class Scanner:
            @staticmethod
            async def discover(**kwargs):
                return {1: (device, SimpleNamespace(local_name="TouchSee", rssi=-70)),
                        2: (device, SimpleNamespace(local_name="TouchSee", rssi=-35)),
                        3: (other, SimpleNamespace(local_name=None, rssi=-60))}
        peers, error = self.scan(Scanner)
        self.assertIsNone(error)
        self.assertEqual(len(peers), 2)
        self.assertIs(peers[0].device, device)
        self.assertIn("TouchSee", peers[0].label)
        self.assertIn("未命名", peers[1].label)

    def test_empty_scan_and_backend_error(self):
        class Empty:
            @staticmethod
            async def discover(**kwargs):
                return {}
        self.assertEqual(self.scan(Empty), ([], None))
        class Failed:
            @staticmethod
            async def discover(**kwargs):
                raise OSError("Bluetooth disabled")
        peers, error = self.scan(Failed)
        self.assertFalse(peers)
        self.assertIn("扫描失败", error)

    def test_cancel_scan_is_bounded(self):
        class Slow:
            @staticmethod
            async def discover(**kwargs):
                await asyncio.sleep(10)
        job = BleScan(scanner_type=Slow)
        job.start()
        job.cancel()
        job.join(1)
        self.assertFalse(job.is_alive())
        self.assertEqual(job.results.get_nowait(), ([], None))


class BleSessionTests(unittest.TestCase):
    def event(self, session, predicate, timeout_s=4):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                event = session.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if predicate(event):
                return event
        self.fail("Missing BLE session event")

    def session(self, real_source=True):
        factory = FakeClientFactory(respond=True, real_source=real_source)
        session = Session(demo=False, ble_device="test-peer")
        def cleanup():
            session.close()
            session.join(3)
            self.assertFalse(session.is_alive())
        self.addCleanup(cleanup)
        patcher = patch("desktop_app.ble_transport.BleakClient", factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        session.start()
        return session, factory

    def test_bidirectional_gatt_handshake_config_playback_and_disconnect(self):
        session, factory = self.session()
        self.event(session, lambda e: e["kind"] == "state")
        self.assertEqual(session.connection_label, "BLE 蓝牙")
        config = replace(Config(), shape="TRIANGLE", radius_um=17500)
        session.command("CONFIG", **config.wire())
        ack = self.event(session, lambda e: e["kind"] == "ack" and e["verb"] == "CONFIG")
        state = self.event(session, lambda e: e["kind"] == "state" and e["state"].revision == 1)["state"]
        self.assertEqual(state.config, config)
        self.assertFalse(state.simulated)  # Fixture exercises real-source checks, not real RF.
        self.assertGreater(state.sample, ack["after_sample"])
        session.command("START")
        self.event(session, lambda e: e["kind"] == "state" and e["state"].output)
        session.close()
        self.event(session, lambda e: e["kind"] == "closed")
        self.assertIn("STOP", factory.client.verbs)
        self.assertTrue(factory.client.disconnected)

    def test_ble_rejects_demo_source(self):
        session, factory = self.session(real_source=False)
        error = self.event(session, lambda e: e["kind"] == "error")
        self.assertIn("来源", error["text"])
        self.event(session, lambda e: e["kind"] == "closed")
        self.assertFalse(session.ready)
        self.assertTrue(factory.client.disconnected)


if __name__ == "__main__":
    unittest.main()
