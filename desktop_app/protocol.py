"""Versioned ASCII/CRC16 wire protocol shared by serial and demo transports."""

import binascii
from dataclasses import dataclass
import re

from .model import ArraySpec, Config, MODES, STATES

MAX_LINE = 4096
VERSION = 2
MAGIC = "HAP2"
TOKEN = re.compile(r"^[A-Za-z0-9_,.?:+\-]+$")


@dataclass(frozen=True)
class Frame:
    kind: str
    seq: int
    verb: str
    fields: dict


def encode(kind, seq, verb, **fields):
    if kind not in ("CMD", "ACK", "ERR", "TEL") or not 0 <= seq <= 65535:
        raise ValueError("帧类型或序号错误")
    tokens = [MAGIC, kind, str(seq), verb]
    for key, value in fields.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not TOKEN.fullmatch(str(value)):
            raise ValueError("协议字段包含非法字符")
        tokens.append(f"{key}={value}")
    if not TOKEN.fullmatch(verb):
        raise ValueError("命令非法")
    body = " ".join(tokens).encode("ascii")
    result = body + f"*{binascii.crc_hqx(body, 0xFFFF):04X}\n".encode("ascii")
    if len(result) > MAX_LINE:
        raise ValueError("报文过长")
    return result


def decode(raw):
    if len(raw) > MAX_LINE:
        raise ValueError("报文过长")
    try:
        line = raw.rstrip(b"\r\n")
        body, checksum = line.rsplit(b"*", 1)
        if len(checksum) != 4 or not re.fullmatch(b"[0-9A-Fa-f]{4}", checksum):
            raise ValueError("CRC 格式错误")
        if binascii.crc_hqx(body, 0xFFFF) != int(checksum, 16):
            raise ValueError("CRC 校验失败")
        tokens = body.decode("ascii").split(" ")
        if len(tokens) < 4 or tokens[0] != MAGIC:
            raise ValueError("协议版本错误")
        kind, seq, verb = tokens[1], int(tokens[2]), tokens[3]
        if kind not in ("CMD", "ACK", "ERR", "TEL") or not 0 <= seq <= 65535:
            raise ValueError("帧头错误")
        if not TOKEN.fullmatch(verb):
            raise ValueError("命令错误")
        fields = {}
        for token in tokens[4:]:
            key, value = token.split("=", 1)
            if key in fields or not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not TOKEN.fullmatch(value):
                raise ValueError("重复或非法字段")
            fields[key] = value
        return Frame(kind, seq, verb, fields)
    except (UnicodeError, IndexError, TypeError) as error:
        raise ValueError("无效报文") from error


class Decoder:
    """Bounded line reassembly; discard a whole oversized line, not just its prefix."""

    def __init__(self):
        self.buffer = bytearray()
        self.dropping = False

    def feed(self, data):
        frames, errors = [], []
        for byte in data:
            if self.dropping:
                if byte == 10:
                    self.dropping = False
                continue
            self.buffer.append(byte)
            if len(self.buffer) > MAX_LINE:
                self.buffer.clear()
                self.dropping = byte != 10
                errors.append("超长报文已丢弃")
            elif byte == 10:
                raw = bytes(self.buffer)
                self.buffer.clear()
                try:
                    frames.append((decode(raw), raw))
                except ValueError as error:
                    errors.append(str(error))
        return frames, errors


@dataclass(frozen=True)
class Snapshot:
    boot: str
    sample: int
    uptime_ms: int
    revision: int
    mode: str
    state: str
    output: bool
    config: Config
    array: ArraySpec
    focus_mm: tuple
    phases: tuple
    simulated: bool
    reason: str

    @classmethod
    def parse(cls, fields):
        config = Config.from_wire(fields)
        array = ArraySpec.from_wire(fields)
        if fields["mode"] not in MODES or fields["state"] not in STATES:
            raise ValueError("设备状态非法")
        if fields["output"] not in ("0", "1") or fields["simulated"] not in ("0", "1"):
            raise ValueError("输出标志非法")
        output = fields["output"] == "1"
        if output and (fields["state"] != "RUNNING" or config.level == 0):
            raise ValueError("输出使能与状态矛盾")
        focus = tuple(int(fields[key]) / 1000 for key in ("fx_um", "fy_um", "fz_um"))
        if not (-400 <= focus[0] <= 400 and -400 <= focus[1] <= 400 and 20 <= focus[2] <= 300):
            raise ValueError("焦点回读越界")
        phases = tuple(int(value) for value in fields["phases"].split(","))
        if len(phases) != array.count or any(not 0 <= x < config.phase_steps for x in phases):
            raise ValueError("相位快照长度或数值错误")
        counters = [int(fields[k]) for k in ("sample", "uptime_ms", "rev")]
        if min(counters) < 0 or not fields["boot"]:
            raise ValueError("状态计数非法")
        return cls(fields["boot"], *counters, fields["mode"], fields["state"], output,
                   config, array, focus, phases, fields["simulated"] == "1", fields.get("reason", "NONE"))
