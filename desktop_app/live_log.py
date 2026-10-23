"""Read-only serial mirror. Only Session owns the COM port; readers open files."""
import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid


def default_root():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TouchSee" / "live"


class LiveLog(threading.Thread):
    def __init__(self, metadata, on_error, root=None):
        super().__init__(daemon=True, name="haptics-live-log")
        self.root = Path(root) if root is not None else default_root()
        self.directory = self.root / (datetime.now().strftime("%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:8])
        self.metadata = dict(metadata)
        self.on_error = on_error
        self.queue = queue.Queue(maxsize=4096)
        self.closing = threading.Event()
        self.failed = threading.Event()
        self.dropped = 0
        self.submit("session", status="opening", **self.metadata)

    def submit(self, kind, **data):
        if self.failed.is_set():
            return
        event = dict(schema="touchsee-live-1", utc=datetime.now(timezone.utc).isoformat(),
                     monotonic_s=time.monotonic(), kind=kind, **data)
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1:
                self.on_error("实时记录队列已满，部分记录丢失；串口通信继续运行")

    def raw(self, direction, raw):
        if raw:
            self.submit("raw", direction=direction, hex=raw.hex(), text=raw.decode("ascii", errors="backslashreplace"))

    def close(self):
        self.closing.set()
        if self.ident is not None:
            self.join(timeout=2)

    @staticmethod
    def serialize(value):
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, (set, tuple)):
            return list(value)
        raise TypeError(type(value).__name__)

    def replace_json(self, temporary, target):
        # Windows readers can briefly hold a file without delete sharing.
        # A locked snapshot must not terminate the raw traffic mirror.
        for _ in range(4):
            try:
                os.replace(temporary, target)
                return True
            except PermissionError:
                time.sleep(.01)
        return False

    def run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            info = dict(schema="touchsee-live-session-1", directory=str(self.directory),
                        pid=os.getpid(), **self.metadata)
            (self.directory/"session.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            # Distinct temporary names allow multiple apps without a shared temp collision.
            temporary = self.root / (self.directory.name+".tmp")
            temporary.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            if not self.replace_json(temporary, self.root/"latest.json"):
                self.on_error("最新记录索引暂被占用；当前目录仍继续记录")
            snapshot = dict(session=info, connected=False, ready=False, last_state=None, last_capture=None)
            with (self.directory/"traffic.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
                while not self.closing.is_set() or not self.queue.empty():
                    try:
                        record = self.queue.get(timeout=.05)
                    except queue.Empty:
                        continue
                    record["dropped_before"] = self.dropped
                    stream.write(json.dumps(record, ensure_ascii=False, default=self.serialize)+"\n")
                    stream.flush()
                    event = record.get("event", {})
                    kind = event.get("kind")
                    if kind in ("opening", "ready", "state", "capture", "closed", "error") or record["kind"] == "session":
                        snapshot.update(updated_utc=record["utc"], dropped=self.dropped, last_event=kind or "session")
                        if kind in ("opening", "ready"):
                            snapshot["connected"] = True
                        if kind == "ready":
                            snapshot["ready"] = True
                        if kind in ("closed", "error"):
                            snapshot["connected"] = False
                            snapshot["ready"] = False
                        if kind == "state":
                            snapshot["last_state"] = event["state"]
                        if kind == "capture":
                            snapshot["last_capture"] = event["capture"]
                        pending = self.directory/"snapshot.tmp"
                        pending.write_text(json.dumps(snapshot, ensure_ascii=False, default=self.serialize), encoding="utf-8")
                        self.replace_json(pending, self.directory/"snapshot.json")
        except (OSError, TypeError, ValueError) as error:
            self.failed.set()
            self.on_error(f"实时记录失败：{error}；串口通信继续运行")


def main():
    from collections import deque
    parser = argparse.ArgumentParser(description="读取上位机的实时镜像，不占用串口")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--summary", action="store_true", help="读取连接状态、最新设备状态和采样")
    parser.add_argument("--tail", type=int, default=20)
    args = parser.parse_args()
    try:
        while True:
            info = json.loads((args.root/"latest.json").read_text(encoding="utf-8"))
            if args.summary:
                print((Path(info["directory"])/"snapshot.json").read_text(encoding="utf-8"), flush=True)
                return
            print(json.dumps(info, ensure_ascii=False), flush=True)
            with (Path(info["directory"])/"traffic.jsonl").open(encoding="utf-8") as stream:
                for line in deque(stream, maxlen=max(0, args.tail)):
                    print(line.rstrip(), flush=True)
                if not args.follow:
                    return
                while True:
                    line = stream.readline()
                    if line:
                        print(line.rstrip(), flush=True)
                    else:
                        latest = json.loads((args.root/"latest.json").read_text(encoding="utf-8"))
                        if latest["directory"] != info["directory"]:
                            break
                        time.sleep(.1)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"尚无可读实时记录：{error}\n")


if __name__ == "__main__":
    main()
