import asyncio
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("receiver_client", ROOT / "receiver_client.py")
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class NativeCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        library = Path("D:/ArduinoData/sketchbook/libraries/ArduinoJson-7.4.2/src")
        if not compiler or not library.exists():
            raise RuntimeError("Install host g++ and pinned ArduinoJson 7.4.2 before testing")
        build = ROOT / ".runtime" / "tests"
        build.mkdir(parents=True, exist_ok=True)
        cls.binary = build / "core_harness.exe"
        subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O2",
                        "-I", str(ROOT / "receiver"), "-I", str(library),
                        str(ROOT / "tests/core_harness.cpp"), "-o", str(cls.binary)], check=True)

    def commands(self, *lines):
        result = subprocess.run([str(self.binary)], input="\n".join(lines) + "\n",
                                capture_output=True, text=True, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_native_fit_against_known_signal(self):
        for fs in (400000, 390000, 410000):
            for phase in (0, .7, 2.4):
                values = [round(1200 + 250 * math.sin(2 * math.pi * 40000 * i / fs + phase))
                          for i in range(200)]
                result = subprocess.run([str(self.binary), "analyze"],
                                        input=f"{fs} " + " ".join(map(str, values)),
                                        text=True, capture_output=True, check=True)
                mean, rms, peak, rail = map(float, result.stdout.split())
                self.assertAlmostEqual(peak, 250, delta=.5)  # 12-bit rounding of the known analog sine.
                self.assertAlmostEqual(mean, sum(values)/200, places=5)
                self.assertAlmostEqual(rms, math.sqrt(sum((v-mean)**2 for v in values)/200), places=5)
                self.assertEqual(rail, 0)

    def test_dc_and_rail(self):
        for dc in (0, 1500, 4095):
            result = subprocess.run([str(self.binary), "analyze"], input="400000 " + " ".join([str(dc)]*200),
                                    text=True, capture_output=True, check=True)
            mean, rms, peak, rail = map(float, result.stdout.split())
            self.assertEqual(mean, dc)
            self.assertLess(rms, 1e-6)
            self.assertLess(peak, 1e-6)
            self.assertEqual(rail, dc in (0, 4095))

    def test_partial_request_and_raw_capture(self):
        info, capture = self.commands('!chunk {"id":1,"cmd":"INFO"}',
                                     '!chunk {"id":2,"cmd":"CAPTURE","raw":true}', '!done')
        self.assertEqual(info["type"], "INFO")
        self.assertEqual(info["capabilities"], "INFO,CAPTURE,RAW,STOP")
        self.assertEqual(capture["id"], 2)
        client.validate_capture(capture, raw=True)
        self.assertAlmostEqual(capture["peak40_adc"], 200, delta=.5)

    def test_busy_cancel_and_no_late_completion(self):
        replies = self.commands('{"id":1,"cmd":"CAPTURE"}', '{"id":2,"cmd":"CAPTURE"}',
                                '{"id":3,"cmd":"STOP"}', '!done')
        self.assertEqual([(r["id"], r.get("error", r.get("type"))) for r in replies],
                         [(2, "BUSY"), (1, "CANCELLED"), (3, "STOP")])

    def test_timeout_overflow_and_disconnect(self):
        self.assertEqual(self.commands('{"id":1,"cmd":"CAPTURE"}', '!advance 10000')[0]["error"], "ADC_TIMEOUT")
        self.assertEqual(self.commands('{"id":1,"cmd":"CAPTURE"}', '!overflow')[0]["error"], "ADC_OVERFLOW")
        self.assertEqual(self.commands('{"id":1,"cmd":"CAPTURE"}', '!reset', '!done'), [])

    def test_invalid_json_types_and_frame_recovery(self):
        for request in ('{}', '{"id":true,"cmd":"INFO"}', '{"id":-1,"cmd":"INFO"}',
                        '{"id":1,"cmd":"CAPTURE","raw":1}', '[]', '{'):
            self.assertEqual(self.commands(request)[0]["error"], "BAD_REQUEST")
        replies = self.commands("x"*300, '{"id":1,"cmd":"INFO"}')
        self.assertEqual(replies[0]["error"], "FRAME_TOO_LONG")
        self.assertTrue(replies[1]["ok"])
        self.assertEqual(self.commands('!partial', '!timeout')[0]["error"], "FRAME_TIMEOUT")

    def test_info_resets_old_capture(self):
        replies = self.commands('{"id":1,"cmd":"CAPTURE"}', '{"id":2,"cmd":"INFO"}', '!done')
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["id"], 2)

    def test_bounded_reply_queue_stops_on_flood(self):
        result = subprocess.run([str(self.binary)], input='{"id":1,"cmd":"CAPTURE"}\n!burst\n!done\n',
                                text=True, capture_output=True, check=True)
        self.assertIn("FAILED", result.stdout)
        self.assertNotIn('"type":"CAPTURE"', result.stdout)

    def test_bad_macro_does_not_compile(self):
        result = subprocess.run([shutil.which("g++"), "-x", "c++", "-fsyntax-only", "-DMG24_USE_BLUETOOTH=2",
                                 "-include", str(ROOT / "receiver/config.h"), "-"],
                                input="", text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 0", result.stderr)


class ProtocolTests(unittest.TestCase):
    def test_twenty_byte_notifications_and_multiple_frames(self):
        payload = (json.dumps(dict(proto="MG24_RX1", id=1, name="中文"), ensure_ascii=False) + "\n").encode()
        decoder = client.JsonLines()
        replies = []
        for i in range(0, len(payload), 20):
            replies.extend(decoder.feed(payload[i:i+20]))
        self.assertEqual(replies[0]["name"], "中文")
        self.assertEqual(len(decoder.feed(payload + payload)), 2)

    def test_corruption_and_bounded_buffer(self):
        for payload in (b"{\n", b"{}\n", b"x"*2048, b"\xff\n"):
            with self.assertRaises(client.ProtocolError):
                client.JsonLines().feed(payload)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_write_is_not_retried_or_followed_by_stop(self):
        class Fake:
            async def write(self, _):
                raise ConnectionError("partial write")
        receiver = client.ReceiverClient(Fake())
        with self.assertRaises(ConnectionError):
            await receiver.info()
        self.assertFalse(receiver.can_stop)

    async def test_ble_writes_preserve_frame_in_small_packets(self):
        class Fake:
            def __init__(self):
                self.chunks = []
            async def write_gatt_char(self, uuid, data, response):
                self.chunks.append((uuid, data, response))
        transport = client.BleConnection()
        transport.client = Fake()
        payload = b'{"id":123,"cmd":"CAPTURE","raw":true}\n'
        await transport.write(payload)
        self.assertTrue(all(len(chunk[1]) <= 20 for chunk in transport.client.chunks))
        self.assertEqual(b"".join(chunk[1] for chunk in transport.client.chunks), payload)

    async def test_wrong_id_and_reset_rejected(self):
        class Fake:
            async def write(self, data):
                self.id = json.loads(data)["id"]
            async def read(self):
                old = dict(proto="MG24_RX1", id=self.id-1, boot=1, ok=True, type="CAPTURE")
                current = dict(old, id=self.id, boot=2)
                return (json.dumps(old) + "\n" + json.dumps(current) + "\n").encode()
        receiver = client.ReceiverClient(Fake())
        receiver.boot = 1
        with self.assertRaisesRegex(client.ProtocolError, "reset"):
            await receiver.request("CAPTURE")

    async def test_disconnect_wakes_pending_read_and_overflow_fails(self):
        transport = client.BleConnection()
        pending = asyncio.create_task(transport.read())
        await asyncio.sleep(0)
        transport.fail(ConnectionError("disconnected"))
        with self.assertRaises(ConnectionError):
            await pending
        transport = client.BleConnection()
        for _ in range(33):
            transport.receive(b"abc")
        with self.assertRaisesRegex(client.ProtocolError, "overflow"):
            await transport.read()


if __name__ == "__main__":
    unittest.main()
