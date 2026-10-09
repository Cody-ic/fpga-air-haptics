"""Observe one bounded CHANNEL_TEST run; never starts or configures outputs."""

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from desktop_app.protocol import Decoder, decode, encode
from desktop_app.receiver import Capture


# Logical channel order is preserved by the F103 adaptation of the R5 PCB.
PIN_MAP = (
    ("PB0", 4, 7), ("PB1", 5, 8), ("PA8", 6, 9), ("PB3", 7, 10),
    ("PB4", 0, 3), ("PB5", 1, 4), ("PB6", 2, 5), ("PB7", 3, 6),
    ("PB8", 12, 11), ("PB9", 13, 12), ("PB10", 14, 17), ("PB11", 15, 18),
    ("PB12", 8, 13), ("PB13", 9, 14), ("PB14", 10, 15), ("PB15", 11, 16),
)
STATE_COLUMNS = (
    "received_utc", "elapsed_s", "boot", "uptime_ms", "sample", "rev",
    "logical_channels", "mcu_pins", "pcb_channels", "cn1_pins", "channel_mask",
    "mode", "state", "output", "drive_on", "reason",
)


class MonitorError(Exception):
    pass


def channel_fields(mask):
    channels = [i for i in range(16) if mask & (1 << i)]
    return dict(
        logical_channels=";".join(map(str, channels)),
        mcu_pins=";".join(PIN_MAP[i][0] for i in channels),
        pcb_channels=";".join(f"CH{PIN_MAP[i][1]}" for i in channels),
        cn1_pins=";".join(f"CN1-{PIN_MAP[i][2]}" for i in channels),
        channel_mask=f"0x{mask:04X}",
    )


def state_fields(fields, *, require_single_channel=True):
    """CHANNEL_TEST gaps legitimately have mask=0, unlike business snapshots."""
    try:
        mask = int(fields["channel_mask"])
        uptime = int(fields["uptime_ms"])
        output = fields["output"]
        state = fields["state"]
        if (not 0 <= mask <= 65535 or uptime < 0 or output not in ("0", "1")
                or state not in ("IDLE", "RUNNING", "PAUSED", "FAULT")
                or fields["mode"] not in ("LOCAL", "REMOTE")
                or fields.get("simulated") != "0" or not fields["boot"]
                or fields.get("drive_on", "0") not in ("0", "1")):
            raise ValueError("invalid state values")
        if output == "1" and (state != "RUNNING" or not mask
                              or (require_single_channel and mask & (mask - 1))):
            raise ValueError("channel test must enable exactly one channel")
        if fields.get("drive_on") == "1" and output != "1":
            raise ValueError("drive_on contradicts output")
    except (KeyError, ValueError) as error:
        raise MonitorError(f"状态帧无效：{error}") from error
    return dict(boot=fields["boot"], uptime_ms=uptime,
                sample=fields.get("sample", ""), rev=fields.get("rev", ""),
                mode=fields["mode"], state=state, output=output,
                drive_on=fields.get("drive_on", ""),
                reason=fields.get("reason", "NONE"), **channel_fields(mask))


class Recorder:
    def __init__(self, directory, clock):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.started = clock()
        self.received = (self.directory / "received.bin").open("wb")
        self.sent = (self.directory / "sent.bin").open("wb")
        self.frames = (self.directory / "frames.jsonl").open("w", encoding="utf-8")
        self.states_file = (self.directory / "states.csv").open("w", encoding="utf-8-sig", newline="")
        self.states = csv.DictWriter(self.states_file, fieldnames=STATE_COLUMNS)
        self.states.writeheader()
        self.captures = (self.directory / "captures.jsonl").open("w", encoding="utf-8")
        self.capture_file = (self.directory / "captures.csv").open("w", encoding="utf-8-sig", newline="")
        self.capture_csv = csv.DictWriter(self.capture_file, fieldnames=(
            "received_utc", "elapsed_s", "boot", "uptime_ms", "revision",
            "tx_running", "request_state_sample", "request_channel_mask",
            "receiver_connected", "mean_mv", "pp_mv", "rms_mv", "peak40_mv", "adc_rail",
        ))
        self.capture_csv.writeheader()

    def stamp(self):
        return dict(received_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    elapsed_s=round(self.clock() - self.started, 6))

    def event(self, direction, frame=None, error=None):
        value = dict(self.stamp(), direction=direction)
        if frame is not None:
            value.update(asdict(frame))
        if error is not None:
            value["error"] = str(error)
        self.frames.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.frames.flush()

    def state(self, state):
        row = dict(self.stamp(), **state)
        self.states.writerow(row)
        self.states_file.flush()
        return row

    def capture(self, capture, request_state, receiver_connected):
        analysis = capture.analyze()
        metrics = {key: analysis[key] for key in
                   ("mean_mv", "pp_mv", "rms_mv", "peak40_mv", "adc_rail")}
        row = dict(self.stamp(), boot=capture.boot, uptime_ms=capture.uptime_ms,
                   revision=capture.revision, tx_running=capture.tx_running,
                   request_state_sample=(request_state or {}).get("sample", ""),
                   request_channel_mask=(request_state or {}).get("channel_mask", ""),
                   receiver_connected=receiver_connected, **metrics)
        value = dict(row, capture=asdict(capture), analysis=metrics,
                     source="ADC voltage; not calibrated acoustic pressure",
                     request_state=request_state,
                     correlation="Request state is the latest digital snapshot, not a measured channel association.")
        self.captures.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.captures.flush()
        self.capture_csv.writerow(row)
        self.capture_file.flush()

    def close(self):
        for stream in (self.received, self.sent, self.frames, self.states_file,
                       self.captures, self.capture_file):
            stream.close()


def observe(transport, output_dir, *, max_seconds=65.0, capture=False,
            receiver_connected=False, clock=time.monotonic, emit=print):
    """Own and close transport; cleanup only sends STOP after a valid HELLO ACK."""
    if not 0 < max_seconds <= 65:
        raise ValueError("监听时间须大于 0 且不超过 65 秒")
    try:
        recorder = Recorder(output_dir, clock)
    except Exception:
        transport.close()
        raise
    decoder = Decoder()
    sequence = 0
    handshake = False
    boot = None
    latest = None
    capture_requests = {}
    stats = dict(outcome="error", error=None, profile=None, states=0, captures=0,
                 decode_errors=0, stop_confirmed=False, receiver_connected=receiver_connected,
                 output_directory=str(recorder.directory.resolve()), max_seconds=max_seconds,
                 commands_never_sent=["START", "CONFIG", "MODE", "CALIBRATION", "RESET"],
                 adc_note="ADC is voltage data; an unconnected receiver cannot support acoustic conclusions.")

    def send(verb):
        nonlocal sequence
        sequence = sequence % 65535 + 1
        raw = encode("CMD", sequence, verb)
        recorder.sent.write(raw)
        recorder.sent.flush()
        transport.write(raw)
        recorder.event("tx", frame=decode(raw))
        return sequence

    def read():
        raw = transport.read()
        recorder.received.write(raw)
        recorder.received.flush()
        frames, errors = decoder.feed(raw)
        for error in errors:
            stats["decode_errors"] += 1
            recorder.event("rx", error=error)
            emit(f"报文未通过校验：{error}")
        for frame, _ in frames:
            recorder.event("rx", frame=frame)
        return [frame for frame, _ in frames]

    def record_state(frame, *, cleanup=False):
        nonlocal latest
        value = state_fields(frame.fields,
                             require_single_channel=not cleanup and stats["profile"] == "CHANNEL_TEST")
        if boot is not None and value["boot"] != boot:
            raise MonitorError("监听期间设备重启或更换；停止观察")
        latest = value
        row = recorder.state(value)
        stats["states"] += 1
        if (value["logical_channels"], value["state"], value["output"], value["reason"]) != record_state.previous:
            emit(f"{row['elapsed_s']:6.2f}s MCU {value['uptime_ms']}ms "
                 f"逻辑 {value['logical_channels'] or '-'} / {value['mcu_pins'] or '-'} / "
                 f"{value['pcb_channels'] or '-'} / {value['cn1_pins'] or '-'} "
                 f"mask={value['channel_mask']} {value['state']} "
                 f"output={value['output']} reason={value['reason']}")
            record_state.previous = (value["logical_channels"], value["state"], value["output"], value["reason"])
        return value

    record_state.previous = None
    try:
        hello_seq = send("HELLO")
        hello_deadline = min(clock() + 3, recorder.started + max_seconds)
        while not handshake and clock() < hello_deadline:
            for frame in read():
                if frame.seq == hello_seq and frame.verb == "HELLO":
                    if frame.kind == "ERR":
                        raise MonitorError(f"握手被拒绝：{frame.fields.get('code', 'UNKNOWN')}")
                    if frame.kind == "ACK":
                        if frame.fields.get("proto") != "3" or not frame.fields.get("boot"):
                            raise MonitorError("握手不是有效 HAP3 设备")
                        handshake = True
                        boot = frame.fields["boot"]
                        stats["profile"] = frame.fields.get("profile")
                        if (stats["profile"] != "CHANNEL_TEST"
                                or frame.fields.get("device") != "STM32F103C8T6"
                                or frame.fields.get("simulated") != "0"
                                or frame.fields.get("hw_rows") != "4"
                                or frame.fields.get("hw_cols") != "4"):
                            raise MonitorError("设备不是 F103 4×4 CHANNEL_TEST 固件；将发送 STOP")
                if handshake and frame.kind == "TEL" and frame.verb == "STATE":
                    record_state(frame)
        if not handshake:
            raise MonitorError("3 秒内未收到有效握手；未发送 STOP，请自行确认驱动电源关闭")
        emit("仅观察固件自行执行的一轮测试；本工具不会启动或重启输出。")
        if capture:
            emit("ADC 采集已启用。接收板未接时只记录输入原码，不能判断声音或声场。"
                 if not receiver_connected else "ADC 仅为电压窗口，尚无声压校准；通道关联取最近数字状态。")
        next_ping = next_snap = next_capture = clock()
        deadline = recorder.started + max_seconds
        while clock() < deadline:
            if latest and latest["reason"] in ("CHANNEL_TEST_DONE", "PIN_TEST_DONE"):
                if latest["state"] != "IDLE" or latest["output"] != "0":
                    raise MonitorError("测试完成标志与输出状态矛盾")
                stats["outcome"] = "completed"
                break
            if latest and latest["state"] == "FAULT":
                raise MonitorError(f"设备故障：{latest['reason']}")
            now = clock()
            if now >= next_ping:
                send("PING")
                next_ping = now + 0.8
            if now >= next_snap:
                send("SNAP")
                next_snap = now + (1.0 if capture else 0.5)
            if capture and now >= next_capture and not capture_requests:
                seq = send("CAPTURE")
                capture_requests[seq] = (latest.copy() if latest else None, now)
                next_capture = now + 0.2
            if capture_requests and now - next(iter(capture_requests.values()))[1] > 2:
                raise MonitorError("ADC 回复超时；停止观察")
            for frame in read():
                if frame.kind == "TEL" and frame.verb == "STATE":
                    record_state(frame)
                elif frame.verb == "CAPTURE" and frame.seq in capture_requests:
                    request_state, _ = capture_requests.pop(frame.seq)
                    if frame.kind == "ERR":
                        raise MonitorError(f"ADC 请求失败：{frame.fields.get('code', 'UNKNOWN')}")
                    if frame.kind != "ACK":
                        continue
                    window = Capture.parse(frame.fields)
                    if window.boot != boot or window.simulated:
                        raise MonitorError("ADC 来源与真实握手不一致")
                    recorder.capture(window, request_state, receiver_connected)
                    stats["captures"] += 1
                elif frame.kind == "ERR":
                    raise MonitorError(f"命令失败：{frame.verb}/{frame.fields.get('code', 'UNKNOWN')}")
        else:
            stats["outcome"] = "timeout"
    except KeyboardInterrupt:
        stats["outcome"] = "interrupted"
        stats["error"] = "用户取消观察"
    except Exception as error:
        stats["error"] = str(error)
        emit(str(error))
    finally:
        if handshake:
            try:
                stop_seq = send("STOP")
                stop_ack = False
                stop_deadline = clock() + 2
                while clock() < stop_deadline and not stats["stop_confirmed"]:
                    for frame in read():
                        if frame.seq == stop_seq and frame.verb == "STOP":
                            if frame.kind == "ERR":
                                raise MonitorError("STOP 被设备拒绝")
                            if frame.kind == "ACK":
                                stop_ack = True
                                send("SNAP")
                        if frame.kind == "TEL" and frame.verb == "STATE":
                            value = record_state(frame, cleanup=True)
                            if stop_ack and value["state"] == "IDLE" and value["output"] == "0":
                                stats["stop_confirmed"] = True
                                break
                if not stats["stop_confirmed"]:
                    raise MonitorError("未确认 STOP 后 output=0；请关闭驱动板电源")
                emit("已确认 STOP：IDLE、output=0。")
            except (Exception, KeyboardInterrupt) as error:
                stats["stop_error"] = str(error) or "停止确认被取消"
                emit(f"停止确认失败：{stats['stop_error']}；请关闭驱动板电源。")
        try:
            transport.close()
        except Exception as error:
            stats["close_error"] = str(error)
        finally:
            stats["elapsed_s"] = round(clock() - recorder.started, 6)
            (recorder.directory / "summary.json").write_text(
                json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            recorder.close()
    return stats


def main(argv=None):
    # Windows redirected Python output can otherwise use the local ANSI code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="观察 F103 一轮逐通道测试，不发送启动命令")
    parser.add_argument("--port", required=True, help="USB-UART 端口，例如 COM4")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=65, help="最多监听 65 秒，之后 STOP")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--capture", action="store_true", help="额外请求 200 ms 间隔 ADC 窗口")
    parser.add_argument("--receiver-connected", action="store_true", help="仅标记实际已接好并供电的接收板")
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 65 or args.baudrate <= 0:
        parser.error("seconds 必须在 (0, 65] 内，baudrate 必须为正数")
    directory = args.output_dir or (REPO / "desktop_app" / ".runtime" / "channel_checks" /
                                   datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    from desktop_app.transport import SerialTransport
    try:
        transport = SerialTransport(args.port, args.baudrate)
    except Exception as error:
        print(f"无法打开串口：{error}。未发送任何命令。", file=sys.stderr)
        return 2
    try:
        result = observe(transport, directory, max_seconds=args.seconds, capture=args.capture,
                         receiver_connected=args.receiver_connected)
    except Exception as error:
        print(f"无法创建观察记录：{error}。", file=sys.stderr)
        return 2
    print(f"记录目录：{result['output_directory']}")
    return 0 if result["outcome"] == "completed" and result["stop_confirmed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
