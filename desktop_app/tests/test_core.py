import binascii
from dataclasses import replace
import queue
import time
import unittest
from unittest.mock import patch

import numpy as np

from desktop_app.controller import Session
from desktop_app.demo import DemoDevice
from desktop_app.model import ArraySpec, Config, Workspace, MAX_PATH_POINTS, array_coordinates, config_from_document, encode_points, field_slice, focus_phases, trajectory_point
from desktop_app.protocol import Decoder, Snapshot, decode, encode, MAX_LINE
from desktop_app.transport import DemoTransport, SerialTransport


class ProtocolTests(unittest.TestCase):
    def test_crc_and_fragmented_roundtrip(self):
        self.assertEqual(binascii.crc_hqx(b"123456789", 0xFFFF), 0x29B1)
        raw = encode("CMD", 65535, "CONFIG", **Config().wire(), **ArraySpec().wire())
        decoder = Decoder()
        received = []
        for byte in raw:
            frames, errors = decoder.feed(bytes([byte]))
            self.assertFalse(errors)
            received.extend(frames)
        self.assertEqual(len(received), 1)
        self.assertEqual(Config.from_wire(received[0][0].fields), Config())

    def test_corruption_and_oversize_recovery(self):
        raw = encode("CMD", 1, "PING")
        broken = raw.replace(b"PING", b"PONG")
        frames, errors = Decoder().feed(broken + b"A" * (MAX_LINE + 50) + b"\n" + raw)
        self.assertEqual(len(errors), 2)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0].verb, "PING")

    def test_protocol_version_prevents_old_grid_interpretation(self):
        body = b"HAP1 CMD 1 HELLO"
        with self.assertRaisesRegex(ValueError, "版本"):
            decode(body + f"*{binascii.crc_hqx(body, 0xFFFF):04X}\n".encode())
        with self.assertRaisesRegex(ValueError, "版本"):
            decode(b"HAP2 CMD 1 HELLO*B3CE\n")
        self.assertEqual(encode("CMD", 1, "HELLO"), b"HAP3 CMD 1 HELLO*F6AD\n")

    def test_duplicate_field_is_rejected_even_with_correct_crc(self):
        body = b"HAP3 CMD 1 MODE value=LOCAL value=REMOTE"
        with self.assertRaises(ValueError):
            decode(body + f"*{binascii.crc_hqx(body, 0xFFFF):04X}\n".encode())

    def test_maximum_snapshot_fits_one_frame(self):
        device = DemoDevice(array=ArraySpec(16, 16))
        device.config = replace(device.config, shape="CUSTOM",
                                path_xy_um=encode_points([(-100000+i, -100000+i) for i in range(MAX_PATH_POINTS)]), phase_steps=256)
        raw = device.snapshot()
        self.assertLessEqual(len(raw), MAX_LINE)
        state = Snapshot.parse(decode(raw).fields)
        self.assertEqual(len(state.phases), 256)
        fields = decode(raw).fields
        fields["phases"] = "0"
        with self.assertRaises(ValueError):
            Snapshot.parse(fields)


class ModelTests(unittest.TestCase):
    def test_invalid_json_does_not_silently_coerce(self):
        for value in (True, 4.2, float("nan")):
            with self.assertRaises(ValueError):
                Config.from_wire(dict(Config().wire(), cx_um=value))

    def test_coordinates_and_phase_sign(self):
        array = ArraySpec(8, 8)
        config = Config(level=100, shape="POINT")
        coords = array_coordinates(array)
        np.testing.assert_equal(coords[0], [-35, -35, 0])
        np.testing.assert_equal(coords[-1], [35, 35, 0])
        focus = np.array([7.0, 4.0, 150.0])
        phases = focus_phases(config, focus, array)
        self.assertEqual(len(phases), 64)
        distances = np.linalg.norm(coords - focus, axis=1) / 1000
        residual = np.angle(np.exp(1j * (2*np.pi*config.carrier_hz/343*distances + phases*2*np.pi/config.phase_steps)))
        self.assertLessEqual(abs(residual).max(), np.pi / config.phase_steps + 1e-10)
        _, _, field = field_slice(config, phases, focus, array=array, enabled=False, resolution=15)
        self.assertTrue(np.all(field == 0))

    def test_array_change_preserves_arbitrary_coordinates_and_uses_every_channel(self):
        config = Config(shape="CUSTOM", path_xy_um="-15372:-13124,16253:-14751,18343:19765,-13736:18426")
        target = trajectory_point(config, .23)
        small = focus_phases(config, target, ArraySpec())
        large = focus_phases(config, target, ArraySpec(8, 8))
        np.testing.assert_equal(trajectory_point(config, .23), target)
        self.assertEqual((len(small), len(large)), (16, 64))
        self.assertFalse(Workspace().incompatibility(config))

    def test_arbitrary_coordinates_on_small_array_and_workspace_check(self):
        config = Config(shape="CUSTOM", path_xy_um="-25123:-25789,24234:-21345,26111:23567,-22555:21444")
        focus = trajectory_point(config, 0)
        self.assertFalse(Workspace().incompatibility(config))
        self.assertEqual(len(focus_phases(config, focus, ArraySpec(4, 4))), 16)
        self.assertTrue(Workspace().incompatibility(replace(config, cx_um=100000)))
        with self.assertRaises(TypeError):
            array_coordinates(config)

    def test_legacy_grid_file_migrates_to_coordinates(self):
        fields = dict(Config().wire(), shape="CUSTOM")
        fields.pop("path_xy_um")
        fields.update(rows=4, cols=4, pitch_um=10000, path_nodes="0,3,15,12")
        migrated = config_from_document({"schema": "haptics-config-1", "config": fields})
        self.assertEqual(migrated.points_um(), ((-15000, -15000), (15000, -15000), (15000, 15000), (-15000, 15000)))
        self.assertEqual(config_from_document({"schema": "haptics-config-2", "config": migrated.wire()}), migrated)
        fields["path_nodes"] = "16"
        with self.assertRaises(ValueError):
            config_from_document({"schema": "haptics-config-1", "config": fields})

    def test_open_path_retraces_without_closing_diagonal(self):
        config = Config(shape="CUSTOM", path_xy_um="-15000:-15000,15000:-15000,15000:15000", path_closed=0, repeat_millihz=1000)
        np.testing.assert_allclose(trajectory_point(config, .25), [15, -15, 150])
        np.testing.assert_allclose(trajectory_point(config, .5), [15, 15, 150])
        np.testing.assert_allclose(trajectory_point(config, .75), [15, -15, 150])

    def test_bad_nodes_and_unsupported_mapping(self):
        for nodes in ("0:0,0:0", "0:300001", "1:2:3", "1.1:2", "nan:0", "0:0,1:2,0:0", "NONE", "0,3,15,12"):
            with self.assertRaises(ValueError):
                replace(Config(), shape="CUSTOM", path_xy_um=nodes).validate()
        with self.assertRaises(ValueError):
            Config(shape="CUSTOM", path_xy_um=encode_points([(i, -i) for i in range(65)])).validate()
        with self.assertRaises(ValueError):
            ArraySpec(mapping="UNKNOWN").validate()

    def test_single_custom_node_is_stationary_in_both_path_modes(self):
        for closed in (0, 1):
            config = Config(cx_um=3000, shape="CUSTOM", path_xy_um="-25123:-25789", path_closed=closed).validate()
            self.assertFalse(Workspace().incompatibility(config))
            for seconds in (0, .25, 1, 10):
                np.testing.assert_allclose(trajectory_point(config, seconds), [-22.123, -25.789, 150])


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.device = DemoDevice(clock=lambda: self.now, array=ArraySpec(8, 8))
        self.seq = 0
        self.call("HELLO")

    def call(self, verb, **fields):
        self.seq += 1
        return [decode(raw) for raw in self.device.handle(encode("CMD", self.seq, verb, **fields))]

    def test_complete_config_is_atomic_and_uses_installed_geometry(self):
        config = Config(shape="CUSTOM", path_xy_um="-15123:-15789,14321:-14876,19345:18678")
        response = self.call("CONFIG", **config.wire(), **self.device.array.wire())
        self.assertEqual(response[0].kind, "ACK")
        state = Snapshot.parse(response[1].fields)
        self.assertEqual(state.config, config)
        self.assertEqual(len(state.phases), 64)
        revision = state.revision
        response = self.call("CONFIG", **dict(config.wire(), path_xy_um="0:999999"), **self.device.array.wire())
        self.assertEqual(response[0].kind, "ERR")
        self.assertEqual(self.device.config, config)
        self.assertEqual(self.device.revision, revision)

    def test_hardware_mismatch_is_rejected_by_device(self):
        response = self.call("CONFIG", **Config().wire(), **ArraySpec().wire())
        self.assertEqual(response[0].fields["code"], "HARDWARE_MISMATCH")
        response = self.call("CONFIG", **Config(cx_um=100000, radius_um=80000).wire(), **self.device.array.wire())
        self.assertEqual(response[0].fields["code"], "OUT_OF_WORKSPACE")

    def test_square_preset_upload_and_path(self):
        config = Config(shape="SQUARE", radius_um=10000)
        response = self.call("CONFIG", **config.wire(), **self.device.array.wire())
        self.assertEqual(response[0].kind, "ACK")
        self.assertEqual(Snapshot.parse(response[1].fields).config, config)
        for seconds, xy in ((0, [-10, -10]), (.5, [10, -10]), (1, [10, 10]), (1.5, [-10, 10])):
            np.testing.assert_allclose(trajectory_point(config, seconds)[:2], xy)
        self.assertFalse(self.device.workspace.incompatibility(config))
        self.assertTrue(self.device.workspace.incompatibility(replace(config, cx_um=100000)))

    def test_pause_resume_and_heartbeat_timeout(self):
        self.call("START")
        self.now = .25
        self.call("PAUSE")
        position = Snapshot.parse(decode(self.device.snapshot()).fields).focus_mm
        self.now = .75
        self.assertEqual(Snapshot.parse(decode(self.device.snapshot()).fields).focus_mm, position)
        self.call("START")
        self.now = 1.0
        self.assertNotEqual(Snapshot.parse(decode(self.device.snapshot()).fields).focus_mm, position)
        self.now = 5
        self.device.poll()
        self.assertEqual(self.device.state, "IDLE")
        self.assertEqual(self.device.reason, "HEARTBEAT_TIMEOUT")

    def test_demo_running_feedback_moves_and_paused_feedback_stays_fixed(self):
        self.call("START")
        initial = Snapshot.parse(decode(self.device.poll()[0]).fields)
        self.now = .06
        moved = Snapshot.parse(decode(self.device.poll()[0]).fields)
        self.assertNotEqual(initial.focus_mm, moved.focus_mm)
        self.call("PAUSE")
        self.now = .7
        paused = Snapshot.parse(decode(self.device.poll()[0]).fields)
        self.assertEqual(paused.focus_mm, moved.focus_mm)
        self.assertFalse(paused.output)

    def test_local_buttons_and_independent_operation(self):
        self.call("MODE", value="LOCAL")
        old = self.device.config.shape
        self.device.button("NEXT")
        self.assertNotEqual(old, self.device.config.shape)
        self.assertEqual(self.call("START")[0].kind, "ERR")
        self.device.button("PLAY")
        self.now = 20
        self.device.poll()
        self.assertEqual(self.device.state, "RUNNING")
        self.device.button("STOP")
        self.assertEqual(self.device.state, "IDLE")

    def test_local_shape_selection_checks_workspace(self):
        config = Config(shape="POINT", cx_um=100000, radius_um=20000)
        self.call("CONFIG", **config.wire(), **self.device.array.wire())
        self.call("MODE", value="LOCAL")
        revision = self.device.revision
        state = Snapshot.parse(decode(self.device.button("NEXT")).fields)
        self.assertEqual(state.config, config)
        self.assertEqual(state.revision, revision)
        self.assertEqual(state.reason, "OUT_OF_WORKSPACE")
        self.assertFalse(state.output)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
            session.join(2)
            self.assertFalse(session.is_alive())

    def start(self, **kwargs):
        session = Session(**kwargs)
        self.sessions.append(session)
        session.start()
        self.event(session, lambda event: event["kind"] == "state")
        return session

    def event(self, session, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = session.events.get(timeout=.1)
            except queue.Empty:
                continue
            if predicate(event):
                return event
        self.fail("Expected session event did not arrive")

    def test_bidirectional_config_and_real_readback(self):
        session = self.start(demo_array=ArraySpec(8, 8))
        config = Config(shape="CUSTOM", path_xy_um="-15123:-15789,14321:-14876,19345:18678")
        session.command("CONFIG", **config.wire())
        state = self.event(session, lambda e: e["kind"] == "state" and e["state"].revision == 1)["state"]
        self.assertEqual(state.config, config)
        self.assertEqual(len(state.phases), 64)
        session.command("START")
        running = self.event(session, lambda e: e["kind"] == "state" and e["state"].output)["state"]
        self.assertEqual(running.state, "RUNNING")
        session.command("STOP")
        self.event(session, lambda e: e["kind"] == "state" and e["state"].state == "IDLE")

    def test_small_hardware_accepts_coordinates_but_rejects_out_of_bounds(self):
        session = self.start()
        config = Config(shape="CUSTOM", path_xy_um="-25123:-25789,24234:-21345,26111:23567,-22555:21444")
        session.command("CONFIG", **config.wire())
        state = self.event(session, lambda e: e["kind"] == "state" and e["state"].revision == 1)["state"]
        self.assertEqual(state.config, config)
        self.assertEqual(len(state.phases), 16)
        session.command("CONFIG", **replace(config, cx_um=100000).wire())
        event = self.event(session, lambda e: e["kind"] == "rejected")
        self.assertIn("工作空间", event["text"])
        self.assertEqual(session.last_state.revision, 1)

    def test_muted_return_times_out_and_closes(self):
        session = self.start()
        session.command("_MUTE", value=True)
        self.event(session, lambda e: e["kind"] == "error")
        self.event(session, lambda e: e["kind"] == "closed")

    def test_stop_discards_queued_start(self):
        session = Session()
        session.command("START")
        session.command("STOP")
        self.assertEqual(session.commands.qsize(), 1)
        self.assertEqual(session.commands.get_nowait()[2], "STOP")

    def test_old_snapshot_and_reboot(self):
        session = self.start()
        device = DemoDevice()
        fields = decode(device.snapshot()).fields
        fields.update(boot=session.boot, sample="0", uptime_ms="0")
        raw = encode("TEL", 0, "STATE", **fields)
        old = session.last_state
        session._receive(decode(raw), raw, time.monotonic())
        self.assertIs(session.last_state, old)
        fields["boot"] = "anotherboot"
        raw = encode("TEL", 0, "STATE", **fields)
        with self.assertRaisesRegex(RuntimeError, "复位"):
            session._receive(decode(raw), raw, time.monotonic())

    def test_ack_alone_does_not_change_applied_state(self):
        class AckOnly(DemoTransport):
            def read(self):
                if self.muted:
                    data = bytes(self.buffer)
                    self.buffer.clear()
                    time.sleep(.005)
                    return data
                return super().read()

            def write(self, raw):
                if decode(raw).verb == "START":
                    self.muted = True
                    response = self.device.handle(raw)
                    self.buffer.extend(response[0])
                else:
                    super().write(raw)

        session = self.start(factory=AckOnly)
        session.command("START")
        self.event(session, lambda e: e["kind"] == "ack" and e["verb"] == "START")
        self.assertEqual(session.last_state.state, "IDLE")
        self.assertFalse(session.last_state.output)


class SerialAdapterTests(unittest.TestCase):
    def test_real_adapter_configuration_and_partial_write(self):
        with patch("desktop_app.transport.serial.Serial") as constructor:
            port = constructor.return_value
            port.in_waiting = 7
            port.read.return_value = b"example"
            port.write.return_value = 4
            transport = SerialTransport("COM99", 115200)
            self.assertFalse(port.dtr)
            self.assertFalse(port.rts)
            self.assertEqual(port.port, "COM99")
            port.open.assert_called_once()
            self.assertEqual(transport.read(), b"example")
            with self.assertRaises(IOError):
                transport.write(b"12345")
            transport.close()
            port.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
