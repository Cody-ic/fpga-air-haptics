"""Standalone MG24 receiver client; never sends transmitter HAP3 commands."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
import time

SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
MAX_FRAME = 2048


class ProtocolError(RuntimeError):
    pass


class JsonLines:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        replies = []
        for value in chunk:
            if value == 10:
                if not self.buffer:
                    continue
                try:
                    reply = json.loads(self.buffer.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as error:
                    raise ProtocolError("Invalid receiver JSON frame") from error
                self.buffer.clear()
                if not isinstance(reply, dict) or reply.get("proto") != "MG24_RX1":
                    raise ProtocolError("This is not an MG24_RX1 receiver")
                replies.append(reply)
            else:
                self.buffer.append(value)
                if len(self.buffer) >= MAX_FRAME:
                    raise ProtocolError("Receiver frame exceeds 2048 bytes")
        return replies


def validate_capture(reply, raw=False):
    if reply.get("type") != "CAPTURE" or reply.get("simulated") is not False:
        raise ProtocolError("Unexpected capture source/type")
    for key in ("start_us", "end_us", "count", "adc_bits", "min_adc", "max_adc"):
        if type(reply.get(key)) is not int or reply[key] < 0:
            raise ProtocolError(f"Invalid {key}")
    if (reply["count"] != 200 or reply["adc_bits"] != 12
            or reply["end_us"] < reply["start_us"]
            or reply["end_us"] - reply["start_us"] > 10000
            or not 0 <= reply["min_adc"] <= reply["max_adc"] <= 4095):
        raise ProtocolError("Invalid capture window")
    for key in ("sample_hz", "vref_mv_nominal", "mean_adc", "rms_adc", "peak40_adc", "rms_mv", "peak40_mv"):
        value = reply.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ProtocolError(f"Invalid {key}")
    if not 360000 <= reply["sample_hz"] <= 440000:
        raise ProtocolError("Unexpected sample rate")
    if type(reply.get("adc_rail")) is not bool or reply.get("valid") is not (not reply["adc_rail"]):
        raise ProtocolError("Invalid capture quality flags")
    if raw:
        samples = reply.get("samples")
        if (not isinstance(samples, list) or len(samples) != 200
                or any(type(v) is not int or not 0 <= v <= 4095 for v in samples)):
            raise ProtocolError("Invalid raw samples")
    return reply


class SerialConnection:
    def __init__(self, port):
        import serial
        self.serial = serial.Serial(port, 115200, timeout=.1, write_timeout=2)
        self.serial.reset_input_buffer()

    async def read(self):
        return await asyncio.to_thread(self.serial.read, 512)

    async def write(self, data):
        count = await asyncio.to_thread(self.serial.write, data)
        if count != len(data):
            raise ConnectionError("Incomplete serial write")

    async def close(self):
        self.serial.close()


class BleConnection:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=32)
        self.client = None
        self.error = None
        self.write_limit = 20
        self.write_response = True

    @classmethod
    async def connect(cls, address):
        from bleak import BleakClient
        connection = cls()
        loop = asyncio.get_running_loop()
        connection.client = BleakClient(address, disconnected_callback=lambda _: loop.call_soon_threadsafe(
            connection.fail, ConnectionError("Bluetooth disconnected")))
        try:
            await connection.client.connect(timeout=15)
            rx = connection.client.services.get_characteristic(RX)
            tx = connection.client.services.get_characteristic(TX)
            if not rx or not tx or "notify" not in tx.properties:
                raise ProtocolError("Receiver Nordic UART service is missing")
            connection.write_response = "write" in rx.properties
            if not connection.write_response and "write-without-response" not in rx.properties:
                raise ProtocolError("Receiver command characteristic is not writable")
            # A conservative 20-byte write also works before MTU exchange.
            await connection.client.start_notify(TX, lambda _, data: loop.call_soon_threadsafe(
                connection.receive, bytes(data)))
            return connection
        except BaseException:
            await connection.close()
            raise

    def fail(self, error):
        self.error = error
        # Wake a pending reader even if no more notifications arrive.
        if self.queue.empty():
            self.queue.put_nowait(b"")

    def receive(self, data):
        if self.error:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            self.fail(ProtocolError("Bluetooth receive queue overflow"))

    async def read(self):
        if self.error:
            raise self.error
        data = await self.queue.get()
        if self.error:
            raise self.error
        return data

    async def write(self, data):
        if self.error:
            raise self.error
        for offset in range(0, len(data), self.write_limit):
            await self.client.write_gatt_char(RX, data[offset:offset + self.write_limit],
                                             response=self.write_response)

    async def close(self):
        if self.client:
            await self.client.disconnect()


class ReceiverClient:
    def __init__(self, connection):
        self.connection = connection
        self.decoder = JsonLines()
        self.replies = []
        self.next_id = secrets.randbelow(2**31)
        self.boot = None
        self.can_stop = True
        self.lock = asyncio.Lock()

    async def request(self, command, **fields):
        async with self.lock:
            self.next_id = (self.next_id + 1) % 2**32
            request_id = self.next_id
            request = dict(cmd=command, id=request_id, **fields)
            async with asyncio.timeout(5):
                try:
                    await self.connection.write((json.dumps(request, separators=(",", ":")) + "\n").encode("ascii"))
                except BaseException:
                    self.can_stop = False  # Do not append STOP to a possibly partial command.
                    raise
                while True:
                    if self.replies:
                        reply = self.replies.pop(0)
                        if type(reply.get("id")) is not int or reply["id"] != request_id:
                            continue
                        if type(reply.get("boot")) is not int or type(reply.get("ok")) is not bool:
                            raise ProtocolError("Invalid reply identity/status")
                        if self.boot is not None and reply["boot"] != self.boot:
                            raise ProtocolError("Receiver reset during this session")
                        if not reply["ok"]:
                            raise ProtocolError(str(reply.get("error", "Receiver rejected request")))
                        if reply.get("type") != command:
                            raise ProtocolError("Reply does not match command")
                        return reply
                    self.replies.extend(self.decoder.feed(await self.connection.read()))

    async def info(self):
        reply = await self.request("INFO")
        if reply.get("device") != "TouchSee-MG24-RX" or reply.get("simulated") is not False:
            raise ProtocolError("Unexpected device")
        self.boot = reply["boot"]
        return reply

    async def capture(self, raw=False):
        if self.boot is None:
            raise ProtocolError("INFO handshake required")
        return validate_capture(await self.request("CAPTURE", raw=raw), raw)


async def run(args):
    if args.command == "scan":
        from bleak import BleakScanner
        devices = await BleakScanner.discover(timeout=5, return_adv=True)
        for device, advertisement in devices.values():
            if SERVICE in [s.lower() for s in advertisement.service_uuids] or (device.name or "").startswith("TouchSee-MG24"):
                print(json.dumps(dict(address=device.address, name=device.name), ensure_ascii=False))
        return
    if not args.port and not args.ble:
        raise ValueError("Specify --port COMx or --ble ADDRESS")
    connection = SerialConnection(args.port) if args.port else await BleConnection.connect(args.ble)
    client = ReceiverClient(connection)
    try:
        info = await client.info()
        print(json.dumps(info, ensure_ascii=False))
        if args.command == "capture":
            output = Path(args.output) if args.output else None
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
            for index in range(args.repeat):
                started = time.monotonic()
                reply = await client.capture(args.raw)
                record = dict(received_at=datetime.now(timezone.utc).isoformat(),
                              transport="USB" if args.port else "BLE", capture=reply)
                print(json.dumps(record, ensure_ascii=False))
                if output:
                    with output.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                if index + 1 < args.repeat:
                    await asyncio.sleep(max(0, args.interval - (time.monotonic() - started)))
        elif args.command == "stop":
            print(json.dumps(await client.request("STOP")))
    except BaseException:
        # Cancel the device-side window on Ctrl+C/errors when the link survives.
        # There is no retry of the failed CAPTURE or partial command.
        try:
            if client.can_stop:
                await asyncio.wait_for(client.request("STOP"), timeout=1)
        except (Exception, asyncio.CancelledError):
            pass
        raise
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "info", "capture", "stop"))
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--port")
    transport.add_argument("--ble", metavar="ADDRESS")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interval", type=float, default=.25)
    parser.add_argument("--output", help="Append original capture records as JSONL")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10000 or not math.isfinite(args.interval) or args.interval < .2:
        parser.error("--repeat must be 1..10000 and --interval at least 0.2 seconds")
    try:
        asyncio.run(run(args))
    except (ProtocolError, OSError, ValueError, TimeoutError) as error:
        parser.exit(1, f"MG24: {error}\n")


if __name__ == "__main__":
    main()
