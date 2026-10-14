"""Bidirectional session state: ACK is never substituted for actual telemetry."""

from dataclasses import dataclass, replace
import itertools
import queue
import threading
import time

from .protocol import VERSION, Decoder, Snapshot, encode
from .receiver import Capture
from .receiver_log import CaptureRecorder
from .model import ArraySpec, Config, Workspace
from .array_geometry import Geometry
from .transport import DemoTransport, SerialTransport
from .ble_transport import BleTransport


@dataclass
class Pending:
    verb: str
    sent: float


@dataclass
class RemoteTakeover:
    stop_seq: int
    after_sample: int
    deadline: float
    revision: int | None = None


class Session(threading.Thread):
    def __init__(self, demo=True, port="", baudrate=115200, factory=None, demo_array=None,
                 ble_device=None, ble_profile=None, record_root=None):
        super().__init__(daemon=True, name="haptics-io")
        if demo and ble_device is not None:
            raise ValueError("BLE 连接不能作为 Demo")
        self.is_demo = demo
        self.connection_label = "Demo" if demo else ("BLE 蓝牙" if ble_device is not None else "串口")
        self.events = queue.Queue(maxsize=1000)
        self.commands = queue.PriorityQueue(maxsize=64)
        self.order = itertools.count()
        self.closing = threading.Event()
        self.factory = factory or (
            (lambda: DemoTransport(array=demo_array)) if demo else
            (lambda: BleTransport(ble_device, ble_profile, cancel_event=self.closing)) if ble_device is not None else
            (lambda: SerialTransport(port, baudrate)))
        self.pending = {}
        self.seq = 0
        self.ready = False
        self.last_state = None
        self.state_received = 0.0
        self.boot = None
        self.drop_count = 0
        self.capabilities = set()
        self.limits = {}
        self.array = None
        self.workspace = None
        self.geometry_parts = []
        self.hello_fields = None
        self.observers = []
        self.takeover = None
        self.record_root = record_root
        self.recorder = None

    def subscribe(self):
        observer = queue.Queue(maxsize=2000)
        self.observers.append(observer)
        return observer

    def unsubscribe(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)

    def emit(self, kind, **data):
        event = dict(kind=kind, when=time.monotonic(), **data)
        for observer in tuple(self.observers):
            try:
                observer.put_nowait(event)
            except queue.Full:
                # An abandoned observer must never block STOP or heartbeat.
                pass
        try:
            self.events.put_nowait(event)
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.drop_count += 1
            self.events.put_nowait(event)

    def command(self, verb, **fields):
        if self.closing.is_set():
            return False
        # STOP discards commands not yet written; do not execute queued START
        # or CONFIG after stopping. No automatic retry of mutating commands.
        if verb == "STOP":
            while True:
                try:
                    discarded = self.commands.get_nowait()
                    if discarded[2] == "CAPTURE":
                        self.emit("rejected", verb="CAPTURE", text="采样请求被停止操作取消")
                except queue.Empty:
                    break
        try:
            self.commands.put_nowait((0 if verb == "STOP" else 1, next(self.order), verb, fields))
            return True
        except queue.Full:
            self.emit("error", text="发送队列已满，请等待应答")
            return False

    def close(self):
        self.closing.set()

    def _write(self, transport, verb, fields=None):
        for _ in range(65535):
            self.seq = self.seq % 65535 + 1
            if self.seq not in self.pending:
                break
        raw = encode("CMD", self.seq, verb, **(fields or {}))
        transport.write(raw)
        self.pending[self.seq] = Pending(verb, time.monotonic())
        self.emit("tx", text=raw.decode("ascii").rstrip())
        return self.seq

    def _cancel_takeover(self, text):
        if self.takeover is not None:
            self.takeover = None
            self.emit("rejected", verb="MODE", text=text)

    def _advance_takeover(self, transport, now):
        operation = self.takeover
        if operation is None:
            return
        if now >= operation.deadline:
            raise RuntimeError("未确认设备停止，无法切换电脑控制；实际输出状态未知")
        state = self.last_state
        if (operation.revision is not None and state is not None
                and state.sample > operation.after_sample
                and state.revision >= operation.revision
                and state.state == "IDLE" and not state.output):
            self._write(transport, "MODE", {"value": "REMOTE"})
            self.takeover = None

    def _receive(self, frame, raw, now):
        self.emit("rx", text=raw.decode("ascii").rstrip())
        if frame.kind in ("ACK", "ERR"):
            request = self.pending.get(frame.seq)
            if not request or request.verb != frame.verb:
                self.emit("warning", text="忽略未知或迟到的应答")
                return
            del self.pending[frame.seq]
            if frame.kind == "ERR":
                if self.takeover is not None and frame.seq == self.takeover.stop_seq:
                    self._cancel_takeover("设备未能停止，未切换电脑控制")
                self.emit("rejected", verb=frame.verb, text=frame.fields.get("code", "UNKNOWN"))
                if frame.verb == "HELLO":
                    raise RuntimeError("设备拒绝握手")
                if frame.verb == 'GEOMETRY' and not self.ready:
                    raise RuntimeError('无法读取设备阵列坐标')
                return
            if frame.verb == "HELLO":
                if frame.fields.get("proto") != str(VERSION) or not frame.fields.get("boot"):
                    raise RuntimeError("设备协议版本不匹配")
                if frame.fields.get("simulated") not in ("0", "1"):
                    raise RuntimeError("设备未声明数据来源")
                if (frame.fields["simulated"] == "1") != self.is_demo:
                    raise RuntimeError("设备数据来源与所选连接模式不一致")
                capabilities = set(frame.fields.get("caps", "").split(","))
                if frame.fields.get("adc_capture") == "1":
                    capabilities.add("ADC_CAPTURE")
                if not {"CONFIG", "MODE", "START", "PAUSE", "STOP", "STATE", "PHASE"} <= capabilities:
                    raise RuntimeError("设备缺少必要协议能力")
                if int(frame.fields.get("hb_ms", 0)) < 2000:
                    raise RuntimeError("设备心跳超时要求过短")
                self.limits = {key: int(frame.fields.get(key, 0)) for key in
                               ("max_rows", "max_cols", "max_channels", "max_nodes", "max_scan_points", "max_strokes")}
                if any(value <= 0 for value in self.limits.values()):
                    raise RuntimeError("设备未报告阵列及路径容量")
                self.capabilities = capabilities
                self.array = ArraySpec.from_wire(frame.fields)
                self.workspace = Workspace.from_wire(frame.fields)
                if (self.array.rows > self.limits["max_rows"] or self.array.cols > self.limits["max_cols"]
                        or self.array.count > self.limits["max_channels"]):
                    raise RuntimeError("实际阵列超过设备声明的容量")
                self.boot = frame.fields["boot"]
                self.hello_fields = frame.fields
                if self.array.mapping == 'EXPLICIT_XYZ':
                    if 'GEOMETRY' not in capabilities:
                        raise RuntimeError('设备未提供三维阵列坐标读取能力')
                    self.command('GEOMETRY', start=0, count=min(16, self.array.count))
                else:
                    self._geometry_ready()
            elif frame.verb == 'GEOMETRY' and not self.ready:
                f = frame.fields
                start, count = int(f['start']), int(f['count'])
                parts = tuple(tuple(int(v) for v in row.split(',')) for row in f['elements'].split('|'))
                if (f['geometry_id'] != self.array.geometry_id or start != len(self.geometry_parts)
                        or count != min(16, self.array.count-start) or len(parts) != count):
                    raise RuntimeError('阵列分块坐标不完整或顺序错误')
                self.geometry_parts.extend(parts)
                if len(self.geometry_parts) == self.array.count:
                    geometry = Geometry(self.array.rows, self.array.cols, self.array.pitch_um,
                                        tuple(self.geometry_parts), int(f['sound_speed_mm_s'])).validate()
                    self.array = replace(self.array, geometry=geometry).validate()
                    self._geometry_ready()
                    self.command('SNAP')
                else:
                    self.command('GEOMETRY', start=len(self.geometry_parts), count=min(16, self.array.count-len(self.geometry_parts)))
            elif frame.verb == "CAPTURE":
                try:
                    capture = Capture.parse(frame.fields)
                except (ValueError, KeyError, TypeError) as error:
                    self.emit("rejected", verb="CAPTURE", text=f"无效采样窗口：{error}")
                    return
                if capture.boot != self.boot or capture.simulated != self.is_demo:
                    raise RuntimeError("采样来源或启动标识与当前连接不一致")
                if self.recorder is not None:
                    age_ms = max(0, (now-self.state_received)*1000) if self.last_state else None
                    self.recorder.submit(capture, self.last_state, age_ms)
                self.emit("capture", capture=capture)
            elif frame.verb in ("CONFIG", "MODE", "START", "PAUSE", "STOP", "CALIBRATION"):
                if frame.fields.get("applied") != "1" or not frame.fields.get("rev", "").isdigit():
                    raise RuntimeError("控制应答缺少已生效标志或配置版本")
                if self.takeover is not None and frame.seq == self.takeover.stop_seq:
                    self.takeover.revision = int(frame.fields["rev"])
                    # Require a state received after this ACK, not an earlier IDLE.
                    self.takeover.after_sample = self.last_state.sample if self.last_state else -1
            self.emit("ack", verb=frame.verb, fields=frame.fields,
                      after_sample=self.last_state.sample if self.last_state else -1,
                      latency_ms=round((now - request.sent) * 1000))
        elif frame.kind == "TEL" and frame.seq == 0 and frame.verb == "STATE":
            if not self.ready:
                return
            try:
                if ArraySpec.from_wire(frame.fields) != self.array:
                    raise RuntimeError('实际阵列配置改变，请重新连接')
                state = Snapshot.parse(frame.fields, self.array)
            except (ValueError, KeyError, TypeError) as error:
                self.emit("warning", text=f"无效状态快照：{error}")
                return
            if state.boot != self.boot:
                raise RuntimeError("设备已复位，需重新连接以核实状态")
            if state.simulated != self.is_demo:
                raise RuntimeError("状态来源与握手不一致")
            if state.array != self.array:
                raise RuntimeError("实际阵列配置改变，请重新连接")
            if self.workspace.incompatibility(state.config):
                raise RuntimeError("设备回读的轨迹超出其声明的工作空间")
            if self.last_state:
                if state.sample <= self.last_state.sample or state.uptime_ms < self.last_state.uptime_ms:
                    self.emit("warning", text="丢弃旧状态帧")
                    return
                if state.revision < self.last_state.revision:
                    self.emit("warning", text="丢弃配置版本倒退的状态帧")
                    return
            self.last_state = state
            self.state_received = now
            self.emit("state", state=state)

    def _geometry_ready(self):
        self.ready = True
        self.state_received = time.monotonic()
        self.emit('ready', device=self.hello_fields.get('device', 'FPGA'), demo=self.is_demo,
                  limits=self.limits, capabilities=sorted(self.capabilities), array=self.array, workspace=self.workspace)

    def run(self):
        transport = None
        decoder = Decoder()
        next_ping = time.monotonic() + 0.8
        opened = time.monotonic()
        stop_sent = False
        try:
            transport = self.factory()
            if self.closing.is_set():
                return
            if self.record_root is not None:
                self.recorder = CaptureRecorder(self.record_root, self.emit)
                self.recorder.start()
            opened = time.monotonic()
            next_ping = opened + 0.8
            self.emit("opening", text=f"{self.connection_label} 已接通，等待设备确认")
            self._write(transport, "HELLO")
            while not self.closing.is_set():
                now = time.monotonic()
                if self.boot and now >= next_ping:
                    self._write(transport, "PING")
                    next_ping = now + 0.8
                try:
                    _, _, verb, fields = self.commands.get_nowait()
                except queue.Empty:
                    verb, fields = None, {}
                if verb:
                    if verb == "STOP":
                        self._cancel_takeover("停止操作取消了控制模式切换")
                    if verb == "_MUTE" and self.is_demo:
                        transport.muted = fields["value"]
                    elif verb == "_BUTTON" and self.is_demo and self.ready:
                        transport.button(fields["action"])
                    elif verb == 'GEOMETRY' and self.boot and not self.ready:
                        self._write(transport, verb, fields)
                    elif not self.ready:
                        self.emit("rejected", verb=verb, text="尚未握手，未发送")
                    elif self.takeover is not None and verb in ("CONFIG", "MODE", "START", "PAUSE", "CALIBRATION", "CAPTURE"):
                        self.emit("rejected", verb=verb, text="正在等待设备停止并切换电脑控制")
                    elif verb in ("CONFIG", "MODE", "START", "PAUSE", "CALIBRATION") and any(
                            p.verb in ("CONFIG", "MODE", "START", "PAUSE", "STOP", "CALIBRATION") for p in self.pending.values()):
                        self.emit("rejected", verb=verb, text="上一控制命令尚未应答")
                    else:
                        try:
                            if (verb == "MODE" and fields == {"value": "REMOTE"}
                                    and self.last_state and self.last_state.mode == "LOCAL"
                                    and self.last_state.state != "IDLE"):
                                if now - self.state_received >= 1.6:
                                    raise ValueError("设备状态已过期，请等待新状态")
                                stop_seq = self._write(transport, "STOP")
                                self.takeover = RemoteTakeover(stop_seq, self.last_state.sample, now + 2.5)
                                self.emit("taking_control", text="正在停止设备，确认后切换电脑控制…")
                                continue
                            if verb == 'CALIBRATION' and verb not in self.capabilities:
                                raise ValueError('设备固件不支持相位校准')
                            if verb == "CAPTURE":
                                if "ADC_CAPTURE" not in self.capabilities:
                                    raise ValueError("设备固件不支持接收采样")
                                if any(p.verb == "CAPTURE" for p in self.pending.values()):
                                    raise ValueError("上一采样请求尚未完成")
                            if verb == "CONFIG":
                                config = Config.from_wire(fields)
                                mismatch = self.workspace.incompatibility(config)
                                if mismatch:
                                    raise ValueError(mismatch)
                                if config.path_xy_um != "NONE" and ("CUSTOM_XY" not in self.capabilities
                                        or len(config.points_um()) > self.limits["max_nodes"]):
                                    raise ValueError("设备不支持此自定义路径")
                                if config.scan_paths != 'NONE' and ('SCAN_PATHS' not in self.capabilities
                                        or len(config.strokes_um()) > self.limits['max_strokes']
                                        or sum(map(len, config.strokes_um())) > self.limits['max_scan_points']):
                                    raise ValueError("设备不支持此草图或路径超出容量")
                                fields = dict(fields, **self.array.wire())
                            self._write(transport, verb, fields)
                        except ValueError as error:
                            self.emit("rejected", verb=verb, text=str(error))
                frames, errors = decoder.feed(transport.read())
                for error in errors:
                    self.emit("warning", text=error)
                for frame, raw in frames:
                    self._receive(frame, raw, time.monotonic())
                now = time.monotonic()
                self._advance_takeover(transport, now)
                expired = [(seq, p) for seq, p in self.pending.items() if now - p.sent > 2]
                if expired:
                    names = ",".join(p.verb for _, p in expired)
                    raise RuntimeError(f"命令应答超时：{names}；实际输出状态未知")
                if self.ready and now - (self.state_received or opened) > 2.5:
                    raise RuntimeError("板端状态回传超时；已请求停止，不能确认实际输出")
        except Exception as error:
            self.emit("error", text=str(error))
        finally:
            # Normal LOCAL disconnect leaves standalone operation intact.
            # REMOTE/unknown disconnect: attempt STOP, then rely on board watchdog.
            if transport is not None:
                if self.ready and (not self.last_state or self.last_state.mode != "LOCAL"):
                    try:
                        previous_sample = self.last_state.sample if self.last_state else -1
                        stop_seq = self._write(transport, "STOP")
                        stop_sent = True
                        deadline = time.monotonic() + 0.3
                        while time.monotonic() < deadline:
                            frames, _ = decoder.feed(transport.read())
                            for frame, raw in frames:
                                self._receive(frame, raw, time.monotonic())
                            if (stop_seq not in self.pending and self.last_state
                                    and self.last_state.sample > previous_sample
                                    and not self.last_state.output and self.last_state.state == "IDLE"):
                                break
                    except Exception:
                        pass
                try:
                    transport.close()
                except Exception:
                    pass
            self.ready = False
            if self.recorder is not None:
                self.recorder.close()
            self.emit("closed", stop_sent=stop_sent)
