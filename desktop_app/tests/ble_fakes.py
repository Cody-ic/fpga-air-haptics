"""In-process GATT fixture, never selectable as a real device in the product."""

import asyncio
from types import SimpleNamespace

from desktop_app.ble_transport import NUS_SERVICE, NUS_WRITE, NUS_NOTIFY
from desktop_app.demo import DemoDevice
from desktop_app.protocol import Decoder, decode, encode


class FakeBleClient:
    def __init__(self, device, disconnected_callback, timeout, *, respond=False,
                 real_source=True, mtu=23, properties=None, delay_s=0, connect_delay_s=0):
        self.device = device
        self.disconnected_callback = disconnected_callback
        self.respond = respond
        self.real_source = real_source
        self.mtu_size = mtu
        self.delay_s = delay_s
        self.connect_delay_s = connect_delay_s
        self.is_connected = False
        self.callback = None
        self.writes = []
        self.verbs = []
        self.disconnected = False
        self.unsubscribed = False
        self.fail_after = None
        self.hold_state = False
        self.decoder = Decoder()
        self.simulator = DemoDevice()
        self.telemetry = None
        self.write_char = SimpleNamespace(uuid=NUS_WRITE,
            properties=properties if properties is not None else ["write", "write-without-response"],
            max_write_without_response_size=mtu-3)
        self.notify_char = SimpleNamespace(uuid=NUS_NOTIFY, properties=["notify"])
        self.service = SimpleNamespace(characteristics=[self.write_char, self.notify_char])
        self.services = SimpleNamespace(get_service=lambda uuid: self.service if uuid == NUS_SERVICE else None)

    async def connect(self):
        self.loop = asyncio.get_running_loop()
        await asyncio.sleep(self.connect_delay_s)
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False
        self.disconnected = True
        if self.telemetry:
            self.telemetry.cancel()
            await asyncio.gather(self.telemetry, return_exceptions=True)
        self.disconnected_callback(self)

    async def start_notify(self, characteristic, callback):
        self.callback = callback
        if self.respond:
            self.telemetry = asyncio.create_task(self._poll())

    async def stop_notify(self, characteristic):
        self.unsubscribed = True
        self.callback = None

    def deliver(self, data):
        if self.callback:
            self.callback(self.notify_char, bytearray(data))

    def reply(self, frames):
        wire = bytearray()
        for raw in frames:
            frame = decode(raw)
            if self.hold_state and frame.kind == "TEL":
                continue
            fields = dict(frame.fields)
            if self.real_source and "simulated" in fields:
                fields["simulated"] = "0"
            wire.extend(encode(frame.kind, frame.seq, frame.verb, **fields))
        # Deliberately split CRCs, newlines and combined ACK/STATE frames.
        for offset in range(0, len(wire), 13):
            self.deliver(wire[offset:offset+13])

    async def _poll(self):
        while self.is_connected:
            self.reply(self.simulator.poll())
            await asyncio.sleep(0.05)

    async def write_gatt_char(self, characteristic, data, response):
        assert self.callback is not None, "Subscribe before sending HELLO"
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            raise OSError("Injected GATT write failure")
        self.writes.append((bytes(data), response))
        frames, errors = self.decoder.feed(data)
        if errors:
            raise AssertionError(errors)
        for frame, raw in frames:
            self.verbs.append(frame.verb)
            if self.respond:
                self.reply(self.simulator.handle(raw))

    def drop(self):
        self.is_connected = False
        self.disconnected_callback(self)


class FakeClientFactory:
    def __init__(self, **options):
        self.options = options
        self.clients = []

    def __call__(self, *args, **kwargs):
        client = FakeBleClient(*args, **kwargs, **self.options)
        self.clients.append(client)
        return client

    @property
    def client(self):
        return self.clients[-1]
