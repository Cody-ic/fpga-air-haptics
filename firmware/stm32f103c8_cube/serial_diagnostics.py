"""Bounded HAP3 diagnostics that never send output-control commands."""

import argparse
from contextlib import ExitStack
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from desktop_app.protocol import Decoder, decode, encode
from firmware.stm32f103c8_cube.channel_monitor import state_fields


READ_COMMANDS = frozenset(("HELLO", "PING", "SNAP"))
STATE_COLUMNS = (
    "received_utc", "elapsed_s", "boot", "uptime_ms", "sample", "rev",
    "logical_channels", "mcu_pins", "pcb_channels", "cn1_pins", "channel_mask",
    "mode", "state", "output", "drive_on", "reason", "fault",
    "carrier_hz", "phase_steps", "mod_hz", "level", "shape",
)


class DiagnosticError(Exception):
    pass


class Recorder:
    """Raw bytes are retained even when the decoder rejects their frames."""

    def __init__(self, directory, clock):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.started = clock()
        self.streams = ExitStack()
        try:
            self.received = self.open("received.bin", "xb")
            self.sent = self.open("sent.bin", "xb")
            self.frames = self.open("frames.jsonl", "x", encoding="utf-8")
            self.states_file = self.open("states.csv", "x", encoding="utf-8-sig", newline="")
            self.states = csv.DictWriter(self.states_file, fieldnames=STATE_COLUMNS)
            self.states.writeheader()
        except Exception:
            self.close()
            raise

    def open(self, name, mode, **options):
        return self.streams.enter_context((self.directory / name).open(mode, **options))

    def stamp(self):
        return dict(received_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    elapsed_s=round(self.clock() - self.started, 6))

    def event(self, direction, frame=None, error=None, **context):
        value = dict(self.stamp(), direction=direction, **context)
        if frame is not None:
            value.update(asdict(frame))
        if error is not None:
            value["error"] = str(error)
        self.frames.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.frames.flush()

    def state(self, fields):
        value = state_fields(fields, require_single_channel=False)
        value.update({key: fields.get(key, "") for key in
                      ("carrier_hz", "phase_steps", "mod_hz", "level", "shape")})
        value["fault"] = value["reason"] if value["state"] == "FAULT" else "NONE"
        row = dict(self.stamp(), **value)
        self.states.writerow(row)
        self.states_file.flush()
        return row

    def close(self):
        self.streams.close()


def observe(transport, output_dir, *, max_seconds=10.0, reply_timeout=2.0,
            clock=time.monotonic, emit=print):
    """Own the transport; exiting only closes it, including all error paths.

    HELLO on an old REMOTE business firmware may clear its previous control
    session. No output command, reset, sampling request or probe action is sent.
    """
    if (not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600
            or not math.isfinite(reply_timeout) or not 0 < reply_timeout <= 10):
        raise ValueError("监听时间须在 (0, 3600] 秒内，应答超时须在 (0, 10] 秒内")
    try:
        recorder = Recorder(output_dir, clock)
    except Exception:
        transport.close()
        raise
    stats = dict(outcome="error", error=None, handshake=False, profile=None,
                 boot=None, states=0, fault_states=0,
                 decode_errors=0, unexpected_replies=0, acknowledgements={},
                 reply_timeouts=[], pending_replies=[],
                 max_seconds=max_seconds, reply_timeout=reply_timeout,
                 output_directory=str(recorder.directory.resolve()),
                 commands_sent=[], forbidden_commands_sent=False,
                 output_control_commands_sent=False,
                 cleanup_stop_sent=False,
                 measurement_note="STATE is digital readback, not a measured GPIO waveform or acoustic pressure.",
                 handshake_note="REMOTE business firmware clears its previous control session on HELLO; this tool only closes the connection on exit.")
    decoder = Decoder()
    pending = {}
    sequence = 0
    latest_state = None
    last_state_at = None
    next_send = {verb: recorder.started for verb in ("PING", "SNAP")}
    deadline = recorder.started + max_seconds

    def send(verb):
        nonlocal sequence
        if verb not in READ_COMMANDS:
            raise AssertionError("诊断工具不允许输出控制命令")
        sequence = sequence % 65535 + 1
        raw = encode("CMD", sequence, verb)
        recorder.sent.write(raw)
        recorder.sent.flush()
        recorder.event("tx", frame=decode(raw))
        transport.write(raw)
        pending[sequence] = (verb, clock())
        stats["commands_sent"].append(verb)

    def check_boot(fields):
        value = fields.get("boot")
        if value and stats["boot"] and value != stats["boot"]:
            stats["outcome"] = "device_restarted"
            raise DiagnosticError("监听期间 boot 标识改变，设备可能重启或更换")

    def receive(frame):
        nonlocal latest_state, last_state_at
        if frame.kind == "TEL" and frame.verb == "STATE":
            # Persist the new boot/fault state before ending an invalid session.
            latest_state = recorder.state(frame.fields)
            last_state_at = clock()
            stats["states"] += 1
            if latest_state["state"] == "FAULT":
                stats["fault_states"] += 1
            check_boot(frame.fields)
            return
        if frame.kind not in ("ACK", "ERR"):
            return
        request = pending.get(frame.seq)
        if request is None or request[0] != frame.verb:
            stats["unexpected_replies"] += 1
            recorder.event("validation", error="忽略序号或命令不匹配的应答", seq=frame.seq)
            return
        del pending[frame.seq]
        check_boot(frame.fields)
        if frame.kind == "ERR":
            code = frame.fields.get("code", "UNKNOWN")
            raise DiagnosticError(f"{frame.verb} 被设备拒绝：{code}")
        stats["acknowledgements"][frame.verb] = stats["acknowledgements"].get(frame.verb, 0) + 1
        if frame.verb == "HELLO":
            fields = frame.fields
            if (fields.get("proto") != "3" or not fields.get("boot")
                    or fields.get("simulated") != "0"
                    or fields.get("device") != "STM32F103C8T6"):
                raise DiagnosticError("握手不是有效的真实 F103 HAP3 设备")
            stats.update(handshake=True, boot=fields["boot"], profile=fields.get("profile", "BUSINESS"),
                         fw_id=fields.get("fw_id"), firmware_mode=fields.get("firmware_mode"),
                         pin_channel=fields.get("pin_channel"), group_mask=fields.get("group_mask"),
                         group_first=fields.get("group_first"), group_last=fields.get("group_last"),
                         on_ms=fields.get("on_ms"))
            if latest_state is not None:
                check_boot(latest_state)

    try:
        send("HELLO")
        emit("验证串口双向收发并记录状态，退出仅关闭连接，不发送 START 或 STOP。")
        while clock() < deadline:
            now = clock()
            if stats["handshake"] and now < deadline - min(.15, max_seconds / 10):
                for verb in ("PING", "SNAP"):
                    if (now >= next_send[verb]
                            and not any(value[0] == verb for value in pending.values())):
                        send(verb)
                        next_send[verb] = now + (0.8 if verb == "PING" else 1.0)
            raw = transport.read()
            if raw:
                recorder.received.write(raw)
                recorder.received.flush()
                frames, errors = decoder.feed(raw)
                for error in errors:
                    stats["decode_errors"] += 1
                    recorder.event("rx", error=error)
                # All frames from this read are saved even if one shows a reboot.
                for frame, _ in frames:
                    recorder.event("rx", frame=frame)
                for frame, _ in frames:
                    receive(frame)
            now = clock()
            expired = [(seq, verb) for seq, (verb, sent) in pending.items()
                       if now - sent >= min(reply_timeout, 3.0 if verb == "HELLO" else reply_timeout)]
            if expired:
                stats["reply_timeouts"] = [dict(seq=seq, verb=verb) for seq, verb in expired]
                raise DiagnosticError("应答超时：" + ",".join(verb for _, verb in expired))
            if stats["handshake"] and last_state_at is not None and now - last_state_at > 2.5:
                raise DiagnosticError("STATE 回传超过 2.5 秒未更新")
        if not stats["handshake"]:
            raise DiagnosticError("监听时间结束仍未收到匹配的 HELLO ACK")
        if not stats["states"]:
            raise DiagnosticError("监听时间结束未收到有效 STATE")
        if pending:
            raise DiagnosticError("监听时间结束仍有未回复命令：" + ",".join(value[0] for value in pending.values()))
        if not all(stats["acknowledgements"].get(verb) for verb in ("PING", "SNAP")):
            raise DiagnosticError("未完成 PING/SNAP 双向应答验证")
        stats["outcome"] = "device_fault" if stats["fault_states"] else "completed"
    except KeyboardInterrupt:
        stats["outcome"] = "interrupted"
        stats["error"] = "用户取消诊断"
    except Exception as error:
        stats["error"] = str(error)
    finally:
        stats["pending_replies"] = [dict(seq=seq, verb=value[0]) for seq, value in pending.items()]
        stats["elapsed_s"] = round(clock() - recorder.started, 6)
        stats["latest_state"] = latest_state
        try:
            transport.close()
        except Exception as error:
            stats["close_error"] = str(error)
            if stats["outcome"] == "completed":
                stats["outcome"] = "error"
                stats["error"] = "串口关闭失败：" + str(error)
        try:
            with (recorder.directory / "summary.json").open("x", encoding="utf-8") as stream:
                json.dump(stats, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        finally:
            recorder.close()
    emit(f"检查结束：{stats['outcome']}，STATE {stats['states']} 条。")
    if stats["error"]:
        emit(stats["error"])
    emit(str(recorder.directory.resolve()))
    return stats


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="只读 HAP3 串口诊断；退出不停止现有输出")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--reply-timeout", type=float, default=2)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if (args.baudrate <= 0 or not math.isfinite(args.seconds) or not 0 < args.seconds <= 3600
            or not math.isfinite(args.reply_timeout) or not 0 < args.reply_timeout <= 10):
        parser.error("波特率须为正数；seconds 在 (0, 3600]，reply-timeout 在 (0, 10] 秒内")
    directory = args.output_dir or (REPO / "desktop_app" / ".runtime" / "serial_diagnostics" /
                                   datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    from desktop_app.transport import SerialTransport
    try:
        transport = SerialTransport(args.port, args.baudrate)
        result = observe(transport, directory, max_seconds=args.seconds,
                         reply_timeout=args.reply_timeout)
    except Exception as error:
        print(f"诊断失败：{error}；未发送启动或停止命令。", file=sys.stderr)
        return 2
    return 0 if result["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
