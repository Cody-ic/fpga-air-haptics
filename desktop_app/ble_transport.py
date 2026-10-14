"""BLE byte stream for HAP3. Each connection owns a loop on the I/O thread.

GATT writes only deliver bytes: HELLO, ACK and STATE remain Session's job.
No Tk calls, automatic reconnects or control-command retries live here.
"""

import asyncio
from dataclasses import dataclass
import queue
import threading
from uuid import UUID

from bleak import BleakClient, BleakScanner
from bleak.uuids import normalize_uuid_str


NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RECEIVE_LIMIT = 65536


@dataclass(frozen=True)
class BleProfile:
    service_uuid: str = NUS_SERVICE
    write_uuid: str = NUS_WRITE
    notify_uuid: str = NUS_NOTIFY
    write_mode: str = "auto"
    chunk_size: int = 0  # 0: use the characteristic/MTU limit, at most 512 bytes.

    def normalized(self):
        values = []
        for value in (self.service_uuid, self.write_uuid, self.notify_uuid):
            try:
                values.append(str(UUID(normalize_uuid_str(value.strip()))))
            except (ValueError, AttributeError) as error:
                raise ValueError("服务和通道 UUID 无效，请按模块资料填写。") from error
        if self.write_mode not in ("auto", "response", "without-response"):
            raise ValueError("请选择有效的写入方式。")
        if type(self.chunk_size) is not int or not 0 <= self.chunk_size <= 512:
            raise ValueError("分包上限应为 0～512 字节；0 表示自动。")
        return BleProfile(*values, self.write_mode, self.chunk_size)


@dataclass(frozen=True)
class BlePeer:
    device: object
    name: str
    rssi: int

    @property
    def label(self):
        return f"{self.name or '未命名设备'} · {self.device.address}"


class BleCancelled(OSError):
    pass


async def _bounded(operation, timeout_s, cancel_event=None):
    """Poll only a threading.Event; the Tk thread never touches an asyncio loop."""
    task = asyncio.ensure_future(operation)
    deadline = asyncio.get_running_loop().time() + timeout_s
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise BleCancelled("蓝牙操作已取消")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("蓝牙操作超时")
            done, _ = await asyncio.wait({task}, timeout=min(0.05, remaining))
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _scan(timeout_s, cancel_event, scanner_type):
    devices = await _bounded(scanner_type.discover(timeout=timeout_s, return_adv=True),
                             timeout_s + 3, cancel_event)
    peers = {}
    for device, advertisement in devices.values():
        peer = BlePeer(device, advertisement.local_name or device.name or "未命名设备",
                       advertisement.rssi)
        previous = peers.get(device.address)
        if previous is None or peer.rssi > previous.rssi:
            peers[device.address] = peer
    return sorted(peers.values(), key=lambda peer: (-peer.rssi, peer.label))


class BleScan(threading.Thread):
    """One cancellable scan; results are consumed by the UI's existing tick."""

    def __init__(self, timeout_s=5, scanner_type=None):
        super().__init__(daemon=True, name="haptics-ble-scan")
        self.timeout_s = timeout_s
        self.scanner_type = scanner_type or BleakScanner
        self.cancelled = threading.Event()
        self.results = queue.Queue(maxsize=1)

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            peers = asyncio.run(_scan(self.timeout_s, self.cancelled, self.scanner_type))
            result = (peers, None)
        except BleCancelled:
            result = ([], None)
        except Exception as error:
            result = ([], f"蓝牙扫描失败：{error}。请检查电脑蓝牙是否开启及系统权限。")
        self.results.put_nowait(result)


class BleTransport:
    simulated = False

    def __init__(self, device, profile=None, cancel_event=None, client_type=None,
                 connect_timeout_s=15, write_timeout_s=1.5):
        self.profile = (profile or BleProfile()).normalized()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.client = None
        self.buffer = bytearray()
        self.failure = None
        self.closed = False
        self.subscribed = False
        self.write_timeout_s = write_timeout_s
        self.chunk_size = 20
        self.response = True
        self.write_char = self.notify_char = None
        try:
            self.loop.run_until_complete(_bounded(
                self._connect(device, client_type or BleakClient, connect_timeout_s),
                connect_timeout_s, cancel_event))
        except Exception:
            self.close()
            raise

    async def _connect(self, device, client_type, timeout_s):
        self.client = client_type(device, disconnected_callback=self._disconnected, timeout=timeout_s)
        await self.client.connect()
        service = self.client.services.get_service(self.profile.service_uuid)
        if service is None:
            raise OSError("设备不支持当前蓝牙配置，请检查模块型号或修改“连接设置”。")
        writes = [c for c in service.characteristics if c.uuid.lower() == self.profile.write_uuid]
        notifications = [c for c in service.characteristics if c.uuid.lower() == self.profile.notify_uuid]
        if len(writes) != 1 or len(notifications) != 1:
            raise OSError("找不到唯一的蓝牙收发通道，请检查连接设置。")
        self.write_char, self.notify_char = writes[0], notifications[0]
        properties = set(self.write_char.properties)
        mode = self.profile.write_mode
        self.response = "write" in properties if mode == "auto" else mode == "response"
        required = "write" if self.response else "write-without-response"
        if required not in properties:
            raise OSError("蓝牙发送通道不支持所选写入方式。")
        if not {"notify", "indicate"}.intersection(self.notify_char.properties):
            raise OSError("蓝牙接收通道不支持状态通知，无法使用双向控制。")
        # Subscribe BEFORE HELLO. Fast replies may arrive during a write.
        await self.client.start_notify(self.notify_char, self._notification)
        self.subscribed = True
        self._check()

    def _notification(self, _characteristic, data):
        if self.closed or self.failure:
            return
        if len(self.buffer) + len(data) > RECEIVE_LIMIT:
            self.failure = "蓝牙回传积压过多，已中止连接；请降低设备回传频率。"
            self.buffer.clear()
            return
        self.buffer.extend(data)

    def _disconnected(self, _client):
        if not self.closed:
            self.failure = "蓝牙连接已断开；当前设备输出状态未知，请重新连接。"

    def _check(self):
        if self.failure:
            raise OSError(self.failure)
        if self.closed or not self.client or not self.client.is_connected:
            raise OSError("蓝牙设备未连接")

    async def _write(self, raw):
        self._check()
        # Re-read the negotiated limit: some backends update it after connection.
        if self.response:
            maximum = max(1, (self.client.mtu_size or 23) - 3)
        else:
            maximum = self.write_char.max_write_without_response_size or 20
        self.chunk_size = min(512, maximum, self.profile.chunk_size or 512)
        for offset in range(0, len(raw), self.chunk_size):
            self._check()
            await self.client.write_gatt_char(self.write_char, raw[offset:offset+self.chunk_size],
                                              response=self.response)
            await asyncio.sleep(0)  # Let notifications/disconnection callbacks run.
        self._check()

    def write(self, raw):
        self._check()
        try:
            self.loop.run_until_complete(_bounded(self._write(bytes(raw)), self.write_timeout_s))
        except Exception as error:
            # A partial frame must not be followed by another control command.
            self.failure = f"蓝牙发送未完成：{error}；已中止连接，请重新连接后发送。"
            self.buffer.clear()
            raise OSError(self.failure) from error

    def read(self):
        self._check()
        self.loop.run_until_complete(asyncio.sleep(0 if self.buffer else 0.03))
        self._check()
        raw = bytes(self.buffer[:2048])
        del self.buffer[:2048]
        return raw

    async def _disconnect(self):
        if not self.client:
            return
        try:
            if self.subscribed and self.client.is_connected:
                await asyncio.wait_for(self.client.stop_notify(self.notify_char), timeout=0.5)
        finally:
            # Also release partially opened connections when connect() failed.
            await asyncio.wait_for(self.client.disconnect(), timeout=1.0)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.loop.run_until_complete(self._disconnect())
        except Exception:
            pass
        finally:
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self.loop.close()
            asyncio.set_event_loop(None)
            self.buffer.clear()
