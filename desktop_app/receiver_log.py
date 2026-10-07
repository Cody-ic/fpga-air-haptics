"""Persist validated ADC windows without blocking the serial worker."""

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import threading
from uuid import uuid4

from .receiver import Capture


SUMMARY_FIELDS = (
    "index", "received_utc", "source", "boot", "rev", "uptime_ms",
    "tx_running_at_start", "pin", "fs_hz", "n", "vref_mv", "divider",
    "min_code", "max_code", "mean_mv", "pp_mv", "rms_mv", "peak40_mv",
    "stage2_peak40_mv", "adc_rail", "last_state_sample", "last_state_rev",
    "last_state_age_ms", "last_state_same_revision", "last_state_shape",
)


def capture_from_record(record):
    if record.get("schema") != "haptics-adc-record-1":
        raise ValueError("不支持的测量记录格式")
    data = record["capture"]
    if type(data["simulated"]) is not bool or type(data["tx_running"]) is not bool:
        raise ValueError("测量记录的来源标志无效")
    return Capture.parse(dict(boot=data["boot"], rev=str(data["revision"]),
        uptime_ms=str(data["uptime_ms"]), simulated=str(int(data["simulated"])),
        tx_running=str(int(data["tx_running"])), pin=data["pin"],
        fs_hz=str(data["fs_hz"]), bits=str(data["bits"]), n=str(len(data["raw"])),
        raw=",".join(map(str, data["raw"]))))


def summarize(record, vref_mv=3300.0, divider=3.4):
    capture = capture_from_record(record)
    analysis = capture.analyze(vref_mv, divider)
    context = record.get("last_state_context") or {}
    same_revision = (context.get("boot") == capture.boot
                     and context.get("rev") == capture.revision)
    return dict(index=record["index"], received_utc=record["received_utc"],
        source="DEMO_SYNTHETIC" if capture.simulated else "ADC_UNCALIBRATED",
        boot=capture.boot, rev=capture.revision, uptime_ms=capture.uptime_ms,
        tx_running_at_start=int(capture.tx_running), pin=capture.pin,
        fs_hz=capture.fs_hz, n=len(capture.raw), vref_mv=vref_mv, divider=divider,
        min_code=min(capture.raw), max_code=max(capture.raw),
        **{key: analysis[key] for key in ("mean_mv", "pp_mv", "rms_mv", "peak40_mv", "stage2_peak40_mv")},
        adc_rail=int(analysis["adc_rail"]), last_state_sample=context.get("sample", ""),
        last_state_rev=context.get("rev", ""), last_state_age_ms=context.get("age_ms", ""),
        last_state_same_revision=int(same_revision),
        last_state_shape=context.get("config", {}).get("shape", ""))


class CaptureRecorder(threading.Thread):
    def __init__(self, root, emit):
        super().__init__(daemon=True, name="adc-recorder")
        name = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
        self.directory = Path(root) / name
        self.emit = emit
        self.records = queue.Queue(maxsize=256)
        self.closing = threading.Event()
        self.failed = threading.Event()
        self.count = 0

    def submit(self, capture, state=None, age_ms=None):
        if self.closing.is_set() or self.failed.is_set():
            return False
        context = None
        if state is not None:
            context = dict(boot=state.boot, sample=state.sample, uptime_ms=state.uptime_ms,
                rev=state.revision, mode=state.mode, state=state.state, output=state.output,
                age_ms=age_ms, config=state.config.wire(), array=state.array.wire(),
                focus_mm=state.focus_mm, phases=state.phases,
                channel_mask=state.channel_mask, phase_offsets=state.phase_offsets)
        record = dict(schema="haptics-adc-record-1", received_utc=datetime.now(timezone.utc).isoformat(),
                      capture=asdict(capture), last_state_context=context)
        try:
            self.records.put_nowait(record)
            return True
        except queue.Full:
            self.emit("recording_error", text="测量记录队列已满，本次数据未保存")
            return False

    def close(self):
        self.closing.set()
        self.join(timeout=1)
        if self.is_alive():
            self.emit("recording_error", text="测量记录仍在写入，请暂时不要关闭电脑")

    def run(self):
        raw_file = summary_file = None
        try:
            while not self.closing.is_set() or not self.records.empty():
                try:
                    record = self.records.get(timeout=.05)
                except queue.Empty:
                    continue
                if raw_file is None:
                    self.directory.mkdir(parents=True, exist_ok=False)
                    raw_file = (self.directory / "captures.jsonl").open("w", encoding="utf-8")
                    summary_file = (self.directory / "summary.csv").open("w", encoding="utf-8-sig", newline="")
                    writer = csv.DictWriter(summary_file, fieldnames=SUMMARY_FIELDS)
                    writer.writeheader()
                record["index"] = self.count + 1
                row = summarize(record)
                raw_file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                writer.writerow(row)
                raw_file.flush()
                summary_file.flush()
                self.count += 1
                self.emit("recording", directory=str(self.directory), count=self.count)
        except Exception as error:
            self.failed.set()
            self.emit("recording_error", text=f"测量记录写入失败：{error}")
        finally:
            for stream in (raw_file, summary_file):
                if stream is not None:
                    stream.close()


def main():
    parser = argparse.ArgumentParser(description="读取接收采样记录，用指定参考电压重新生成分析 CSV")
    parser.add_argument("path", type=Path, help="记录目录或 captures.jsonl")
    parser.add_argument("--vref-mv", type=float, default=3300)
    parser.add_argument("--divider", type=float, default=3.4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.path / "captures.jsonl" if args.path.is_dir() else args.path
    destination = args.output or source.with_name("analysis.csv")
    if destination.resolve() == source.resolve():
        parser.error("分析 CSV 不能覆盖原始记录")
    count = 0
    sources = set()
    # Validate parameters before opening an output, including an empty input.
    Capture("validation", 0, 0, False, False, "PA0", 400000, 12, (0,) * 200).analyze(args.vref_mv, args.divider)
    with source.open(encoding="utf-8") as incoming, destination.open("w", encoding="utf-8-sig", newline="") as outgoing:
        writer = csv.DictWriter(outgoing, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for line_number, line in enumerate(incoming, 1):
            try:
                row = summarize(json.loads(line), args.vref_mv, args.divider)
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"第 {line_number} 条记录无效：{error}") from error
            writer.writerow(row)
            count += 1
            sources.add(row["source"])
    print(f"已分析 {count} 个窗口，来源：{', '.join(sorted(sources)) or '无数据'}")
    print(destination.resolve())


if __name__ == "__main__":
    main()
