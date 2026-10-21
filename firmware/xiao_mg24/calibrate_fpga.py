"""Power-on FPGA calibration bridge: forward measured powers, never solve phase.

The startup binary endpoint exclusively owns the FPGA UART until calibration
succeeds. RELEASE hands it back to the unchanged HAP3 desktop application.
"""
import argparse
import asyncio
import binascii
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct

from receiver_client import BleConnection, ProtocolError, ReceiverClient, SerialConnection

BODY = struct.Struct("<2sBB7I")
SYNC = b"\xa5\x5a"
READY, MEASURE, PING, ABORT, READ_TRIM, RELEASE = range(1, 7)
STATUS, TRIM, RELEASED, SETUP_ACK, UPLOAD_ACK = 0x80, 0x81, 0x82, 0x83, 0x85
SETUP, UPLOAD = 7, 8


def encode(kind, boot, token=0, *args):
    if len(args) > 5:
        raise ValueError("At most five arguments")
    body = BODY.pack(SYNC, 2, kind, boot, token, *(list(args) + [0] * (5 - len(args))))
    return body + struct.pack("<H", binascii.crc_hqx(body, 0xffff))


@dataclass(frozen=True)
class Packet:
    kind: int
    boot: int
    token: int
    args: tuple

    def status(self):
        if self.kind not in (STATUS, RELEASED):
            raise ProtocolError("Expected calibration status")
        meta, flags, hardware, z_um, pitch_um = self.args
        result = dict(boot=self.boot, token=self.token, state=meta & 255,
                      stage=(meta >> 8) & 255, channel=(meta >> 16) & 255,
                      error=meta >> 24, busy=bool(flags & 1), valid=bool(flags & 2),
                      source=(flags >> 2) & 1, attempted=bool(flags & 8),
                      configured=bool(flags & 16), importing=bool(flags & 32),
                      output=bool(flags & 64), repeat=(flags >> 8) & 3,
                      rows=(hardware >> 8) & 255, cols=hardware & 255,
                      level=(hardware >> 16) & 255, reference=hardware >> 24, z_um=z_um,
                      pitch_um=pitch_um)
        count = result["rows"] * result["cols"]
        if (result["state"] > 5 or result["stage"] > 7 or result["error"] > 7
                or not 2 <= count <= 256 or result["channel"] >= count
                or not 1 <= result["level"] <= 100 or not 20000 <= z_um <= 300000
                or flags & ~0x37f or result["repeat"] > 2 or result["reference"] >= count or not 1 <= pitch_um <= 100000):
            raise ProtocolError("Invalid FPGA status")
        return result


class PacketDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_crc = 0

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while True:
            offset = self.buffer.find(SYNC)
            if offset < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1:] == SYNC[:1] else b""
                break
            del self.buffer[:offset]
            if len(self.buffer) < 34:
                break
            body = bytes(self.buffer[:32])
            crc, = struct.unpack("<H", self.buffer[32:34])
            if binascii.crc_hqx(body, 0xffff) != crc or body[2] != 2:
                self.bad_crc += 1
                del self.buffer[0]
                continue
            _, _, kind, boot, token, *args = BODY.unpack(body)
            result.append(Packet(kind, boot, token, tuple(args)))
            del self.buffer[:34]
        return result


class FpgaLink:
    def __init__(self, connection):
        self.connection = connection
        self.decoder = PacketDecoder()
        self.queue = asyncio.Queue(maxsize=64)
        self.error = None
        self.lock = asyncio.Lock()
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while True:
                for packet in self.decoder.feed(await self.connection.read()):
                    self.queue.put_nowait(packet)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = error
            if self.queue.empty():
                self.queue.put_nowait(None)

    async def receive(self, timeout=3):
        if self.error:
            raise self.error
        try:
            packet = await asyncio.wait_for(self.queue.get(), timeout)
        except TimeoutError as error:
            raise ProtocolError("未收到校准状态：请检查串口和校准开关；正常业务固件不提供校准端点") from error
        if self.error:
            raise self.error
        return packet

    async def send(self, kind, boot, token=0, *args):
        async with self.lock:
            if self.error:
                raise self.error
            writing = asyncio.create_task(self.connection.write(encode(kind, boot, token, *args)))
            try:
                await asyncio.shield(writing)
            except asyncio.CancelledError:
                # Finish a bounded serial write before sending ABORT; never
                # interleave it with the remaining bytes of a partial packet.
                with suppress(Exception):
                    await writing
                raise

    async def close(self):
        self.reader.cancel()
        with suppress(asyncio.CancelledError):
            await self.reader
        await self.connection.close()


async def heartbeat(link, boot):
    while True:
        await link.send(PING, boot)
        await asyncio.sleep(.5)


def measured_power(capture):
    value = capture["peak40_adc"]
    if not math.isfinite(value) or not 0 <= value <= 4095:
        return 0, 0
    # This is a measured amplitude squared. All phase solving stays in FPGA.
    power = math.floor(value * value + .5)
    return power, int(capture["valid"] and not capture["adc_rail"] and power <= 0xffffff)


@dataclass(frozen=True)
class Settings:
    position_um: tuple = (0, 0, 100000)
    level: int = 30
    reference: int = 0

    def validate(self, count):
        if (len(self.position_um) != 3 or any(type(v) is not int for v in self.position_um)
                or not all(-100000 <= v <= 100000 for v in self.position_um[:2])
                or not 20000 <= self.position_um[2] <= 300000
                or type(self.level) is not int or not 1 <= self.level <= 100
                or type(self.reference) is not int or not 0 <= self.reference < count):
            raise ProtocolError("位置、等级或参考通道超出允许范围")


def scope_power(vpp_mv, confirmed):
    if not confirmed or type(vpp_mv) not in (float, int) or not math.isfinite(vpp_mv) or not 0 <= vpp_mv <= 6600:
        raise ProtocolError("请输入有效的毫伏峰峰值，并确认波形稳定、近正弦且未削顶")
    # Same nominal ADC-code-squared units as automatic captures at ADCV_P.
    # The scope number is peak-to-peak; MG24's peak40_adc is a peak amplitude.
    return math.floor((vpp_mv * .5 * 4095 / 3300) ** 2 + .5)


def validate_document(doc, hardware, settings):
    settings.validate(hardware["rows"] * hardware["cols"])
    if (not isinstance(doc, dict) or doc.get("schema") != "touchsee-fpga-calibration-1"
            or doc.get("kind") != "measured_phase_correction" or doc.get("simulated") is not False
            or doc.get("carrier_hz") != 40000 or doc.get("phase_steps") != 64
            or doc.get("mapping") != "ROW_MAJOR_XY"
            or doc.get("position_um") != list(settings.position_um)
            or doc.get("reference") != settings.reference or doc.get("level") != settings.level
            or any(doc.get(key) != hardware[key] for key in ("rows", "cols", "pitch_um"))):
        raise ProtocolError("文件必须为真实实测修正，且阵列、位置、等级、参考通道、频率和档数一致；理论表不能导入")
    offsets = doc.get("phase_offsets")
    count = hardware["rows"] * hardware["cols"]
    if (not isinstance(offsets, list) or len(offsets) != count
            or any(type(v) is not int or not 0 <= v < 64 for v in offsets)
            or offsets[settings.reference] != 0):
        raise ProtocolError("逐通道修正表无效")
    return offsets


async def checked_receive(link, boot):
    packet = await link.receive()
    if packet.boot != boot:
        raise ProtocolError("FPGA 在本轮测量期间复位")
    return packet


async def wait_reply(link, boot, kind, predicate=lambda _: True, timeout=3):
    async with asyncio.timeout(timeout):
        while True:
            packet = await checked_receive(link, boot)
            if packet.kind == kind and predicate(packet):
                return packet
            if packet.kind == STATUS and packet.status()["state"] == 5:
                # A previous failed attempt is expected during explicit restart.
                continue


async def read_table(link, boot, count, token, which=0):
    offsets = []
    for base in range(0, count, 16):
        await link.send(READ_TRIM, boot, 0, base, which)
        reply = await wait_reply(link, boot, TRIM,
                                 lambda p: p.args[0] == base | (which << 16))
        if reply.token != token:
            raise ProtocolError("相位表回读编号不一致")
        block = struct.pack("<4I", *reply.args[1:])[:min(16, count - base)]
        if any(value > 63 for value in block):
            raise ProtocolError("板端返回非法相位档位")
        offsets.extend(block)
    return offsets


async def calibrate(link, receiver, bluetooth, record, settings=Settings(), manual=None,
                    notify=lambda value: None, imported=None, maximum_seconds=None):
    """manual(status) -> {vpp_mv, confirmed}; absent means MG24 automatic source.

    Only conversion/recording occurs here. FPGA owns solving and verification.
    The caller owns cancellation and closing its serial/BLE connections.
    """
    boot = None
    beat = None
    released = False
    source = 0 if manual is not None else 1
    evidence = []
    receiver_boot = None
    try:
        initial = await link.receive()
        status = initial.status()
        boot = initial.boot
        count = status["rows"] * status["cols"]
        settings.validate(count)
        if status["source"] != source:
            raise ProtocolError("FPGA 测量来源宏与所选模式不一致；请重新编译正确配置")
        if status["busy"] or status["state"] not in (0, 5):
            raise ProtocolError("板端已有测量或已完成校准；请先停止原会话，完成后需复位再重新进入")
        candidate = validate_document(imported, status, settings) if imported is not None else None
        record(dict(type="fpga_initial", **status))
        beat = asyncio.create_task(heartbeat(link, boot))
        async def pulse_health():
            if beat.done():
                beat.result()
        if source == 1:
            if receiver is None:
                raise ProtocolError("自动模式需要 MG24")
            info = await receiver.info()
            if info.get("transport") != ("BLE" if bluetooth else "USB"):
                raise ProtocolError("MG24 通信宏与所选连接不一致")
            receiver_boot = receiver.boot
            record(dict(type="receiver_info", reply=info))
            preflight = await receiver.capture(raw=True)
            record(dict(type="preflight", capture=preflight))
            if not preflight["valid"]:
                raise ProtocolError("采样输入触轨，尚未启动发射")
        setup = tuple(v & 0xffffffff for v in (*settings.position_um, settings.level, settings.reference))
        await link.send(SETUP, boot, 0, *setup)
        await wait_reply(link, boot, SETUP_ACK, lambda p: p.args == setup)
        async with asyncio.timeout(3):
            while True:
                packet = await checked_receive(link, boot)
                if packet.kind == STATUS and packet.status()["configured"]:
                    status = packet.status()
                    break
        nominal = await read_table(link, boot, count, packet.token, which=1)
        record(dict(type="theoretical_baseline", kind="theoretical_phase", position_um=settings.position_um,
                    carrier_hz=40000, phase_steps=64, phases=nominal))
        notify(dict(type="baseline", phases=nominal, source=source))
        if candidate is not None:
            for base in range(0, count, 16):
                block = bytes(candidate[base:base + 16]).ljust(16, b"\0")
                args = (base, *struct.unpack("<4I", block))
                await link.send(UPLOAD, boot, 0, *args)
                await wait_reply(link, boot, UPLOAD_ACK, lambda p: p.args == args)
        initial_token = packet.token
        last_token = initial_token
        await link.send(READY, boot, 0, source, int(candidate is not None))
        started_at = asyncio.get_running_loop().time()
        has_started = False
        async with asyncio.timeout(maximum_seconds or (43200 if source == 0 else 900)):
            while True:
                packet = await checked_receive(link, boot)
                await pulse_health()
                if packet.kind != STATUS:
                    continue
                status = packet.status()
                record(dict(type="status", **status))
                if status["source"] != source:
                    raise ProtocolError("测量来源发生变化")
                if status["state"] == 5:
                    # Ignore queued failure statuses only until the new run starts.
                    if not has_started and asyncio.get_running_loop().time() - started_at < 3:
                        continue
                    reasons = {1: "已取消或电脑心跳超时", 2: "等待测量超时", 3: "读数无效或三次读数波动过大",
                               4: "单路信号过弱", 5: "相位测试不符合线性模型，请检查固定和削顶",
                               6: "修正后的幅值复测不通过", 7: "配置或测量编号保护"}
                    reason = reasons.get(status["error"], "板端异常")
                    raise ProtocolError(f"校准失败：{reason}（通道 {status['channel']}）；输出已关闭")
                if status["busy"]:
                    has_started = True
                if not has_started and asyncio.get_running_loop().time() - started_at > 3:
                    raise ProtocolError("板端未接受开始指令")
                if status["state"] == 4:
                    if not status["valid"] or status["busy"] or packet.token != last_token:
                        raise ProtocolError("校准完成状态与最后测量编号不一致")
                    break
                if status["state"] != 2 or packet.token == last_token:
                    continue
                if packet.token != last_token + 1:
                    raise ProtocolError("测量编号跳跃或倒退")
                notify(dict(type="measurement", **status))
                if source == 0:
                    # Poll statuses during human input so a device timeout/reset
                    # is surfaced immediately, not after an obsolete submission.
                    answer = asyncio.create_task(manual(status))
                    try:
                        await asyncio.sleep(0)
                        async with asyncio.timeout(120):
                            while not answer.done():
                                try:
                                    async with asyncio.timeout(.2):
                                        update = await checked_receive(link, boot)
                                        if update.kind == STATUS:
                                            current = update.status()
                                            if current["state"] != 2 or update.token != packet.token:
                                                raise ProtocolError("板端已取消或超时，本次读数不能提交")
                                except TimeoutError:
                                    await pulse_health()
                        reading = await answer
                    finally:
                        if not answer.done():
                            answer.cancel()
                            with suppress(asyncio.CancelledError):
                                await answer
                    power, good = scope_power(reading["vpp_mv"], reading.get("confirmed")), 1
                    if reading.get("token", packet.token) != packet.token:
                        raise ProtocolError("人工读数来自旧步骤")
                    item = dict(scope=reading)
                else:
                    capture = await receiver.capture(raw=True)
                    power, good = measured_power(capture)
                    item = dict(receiver_boot=receiver.boot, capture=capture)
                item.update(type="measurement", fpga=status, power_adc2=power, good=bool(good))
                evidence.append(item)
                record(item)
                await link.send(MEASURE, boot, packet.token, power, good, source,
                                status["channel"] | status["stage"] << 8, status["repeat"])
                last_token = packet.token
            offsets = await read_table(link, boot, count, last_token)
            if offsets[settings.reference] != 0 or (candidate is not None and offsets != candidate):
                raise ProtocolError("修正表回读与参考通道或导入候选表不一致")
            result = dict(schema="touchsee-fpga-calibration-1", kind="measured_phase_correction",
                          simulated=False, created_at=datetime.now(timezone.utc).isoformat(),
                          carrier_hz=40000, phase_steps=64, mapping="ROW_MAJOR_XY", rows=status["rows"],
                          cols=status["cols"], pitch_um=status["pitch_um"], position_um=list(settings.position_um),
                          reference=settings.reference, level=settings.level, phase_offsets=offsets,
                          theoretical_phases=nominal, fpga_boot=boot, receiver_boot=receiver_boot,
                          source="scope" if source == 0 else "adc_ble" if bluetooth else "adc_usb",
                          vref_mv_nominal=3300, imported_and_reverified=candidate is not None, measurements=evidence)
            record(dict(type="result", document=result))
            beat.cancel()
            with suppress(asyncio.CancelledError):
                await beat
            beat = None
            await link.send(RELEASE, boot, last_token)
            await wait_reply(link, boot, RELEASED, lambda p: p.token == last_token)
            released = True
            notify(dict(type="complete", document=result))
            return result
    except TimeoutError as error:
        raise ProtocolError("通信或人工读数超时，已请求关闭输出；请核对板端状态后重新开始") from error
    finally:
        if beat:
            beat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await beat
        if not released and boot is not None:
            with suppress(Exception):
                await asyncio.wait_for(link.send(ABORT, boot), 1)
        if not released and receiver is not None and receiver.can_stop:
            with suppress(Exception):
                await asyncio.wait_for(receiver.request("STOP"), 1)


async def run(args):
    if args.receiver_port and args.receiver_port.upper() == args.fpga_port.upper():
        raise ValueError("FPGA 与 MG24 不能使用同一个串口")
    filename = Path(args.output) if args.output else Path(".runtime/calibration") / (
        "calibration_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jsonl")
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open("x", encoding="utf-8") as stream:
        def record(value):
            stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), **value), ensure_ascii=False) + "\n")
            stream.flush()
            if value.get("type") == "result":
                with filename.with_suffix(".json").open("x", encoding="utf-8") as output:
                    json.dump(value["document"], output, ensure_ascii=False, indent=2)
        connection = (SerialConnection(args.receiver_port) if args.receiver_port
                      else await BleConnection.connect(args.ble_address))
        link = None
        try:
            link = FpgaLink(SerialConnection(args.fpga_port))
            result = await calibrate(link, ReceiverClient(connection), bool(args.ble_address), record,
                                     Settings(tuple(round(v * 1000) for v in args.position_mm), args.level, args.reference))
        finally:
            if link:
                await link.close()
            await connection.close()
    print("校准完成，输出已关闭。记录：", filename.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fpga-port", required=True)
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--receiver-port")
    transport.add_argument("--ble-address")
    parser.add_argument("--position-mm", nargs=3, type=float, default=(0, 0, 100))
    parser.add_argument("--level", type=int, default=30)
    parser.add_argument("--reference", type=int, default=0)
    parser.add_argument("--output")
    try:
        asyncio.run(run(parser.parse_args()))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f"校准停止：{error}\n")


if __name__ == "__main__":
    main()
