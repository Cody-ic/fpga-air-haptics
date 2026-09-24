"""Deterministic simulated FPGA, not a claim of measured hardware behavior."""

from dataclasses import replace
import time
import uuid

from .model import ArraySpec, Config, Workspace, SHAPES, MAX_PATH_POINTS, focus_phases, trajectory_point
from .protocol import VERSION, decode, encode


class DemoDevice:
    def __init__(self, clock=time.monotonic, array=None):
        self.clock = clock
        self.birth = clock()
        self.last_ping = self.birth
        self.last_tx = -1.0
        self.boot = uuid.uuid4().hex[:8]
        self.array = (array or ArraySpec()).validate()
        self.config = Config()
        self.workspace = Workspace()
        self.mode = "REMOTE"
        self.state = "IDLE"
        self.revision = 0
        self.sample = 0
        self.reason = "NONE"
        self.elapsed = 0.0
        self.started = self.birth
        self.connected = False

    def _play_time(self):
        return self.elapsed + (self.clock() - self.started if self.state == "RUNNING" else 0)

    def _stop(self, reason="NONE"):
        self.state, self.elapsed, self.reason = "IDLE", 0.0, reason

    def snapshot(self):
        focus = trajectory_point(self.config, self._play_time())
        phases = focus_phases(self.config, focus, self.array)
        self.sample += 1
        fields = dict(boot=self.boot, sample=self.sample,
                      uptime_ms=int((self.clock() - self.birth) * 1000), rev=self.revision,
                      mode=self.mode, state=self.state,
                      output=int(self.state == "RUNNING" and self.config.level > 0),
                      simulated=1, reason=self.reason, **self.config.wire(), **self.array.wire(),
                      fx_um=round(focus[0] * 1000), fy_um=round(focus[1] * 1000),
                      fz_um=round(focus[2] * 1000), phases=",".join(map(str, phases)))
        return encode("TEL", 0, "STATE", **fields)

    def handle(self, raw):
        frame = decode(raw)
        if frame.kind != "CMD" or frame.seq == 0:
            return []
        verb, fields = frame.verb, frame.fields
        try:
            if verb == "HELLO":
                self.connected = True
                self.last_ping = self.clock()
                return [encode("ACK", frame.seq, verb, proto=VERSION, device="DEMO-FPGA",
                               boot=self.boot, simulated=1, hb_ms=3000,
                               caps="CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,CUSTOM_XY",
                               max_rows=16, max_cols=16, max_channels=256, max_nodes=MAX_PATH_POINTS,
                               **self.array.wire(), **self.workspace.wire()), self.snapshot()]
            if not self.connected:
                raise ValueError("HANDSHAKE_REQUIRED")
            if verb == "PING":
                self.last_ping = self.clock()
            elif verb == "STOP":
                self._stop()
            elif verb == "MODE":
                if self.state != "IDLE":
                    raise ValueError("BUSY")
                if fields.get("value") not in ("LOCAL", "REMOTE"):
                    raise ValueError("BAD_MODE")
                self.mode = fields["value"]
                self.last_ping = self.clock()
            elif verb == "CONFIG":
                if self.mode != "REMOTE":
                    raise ValueError("LOCAL_CONTROL")
                if self.state != "IDLE":
                    raise ValueError("BUSY")
                try:
                    config = Config.from_wire(fields)
                except (ValueError, KeyError):
                    raise ValueError("BAD_CONFIG") from None
                if self.workspace.incompatibility(config):
                    raise ValueError("OUT_OF_WORKSPACE")
                if any(fields.get(key) != str(value) for key, value in self.array.wire().items()):
                    raise ValueError("HARDWARE_MISMATCH")
                self.config = config  # atomic after complete validation
                self.revision += 1
                self.elapsed = 0
            elif verb == "START":
                if self.mode != "REMOTE":
                    raise ValueError("LOCAL_CONTROL")
                if self.state not in ("IDLE", "PAUSED"):
                    raise ValueError("BUSY")
                self.started = self.clock()
                self.state, self.reason = "RUNNING", "NONE"
                self.last_ping = self.clock()
            elif verb == "PAUSE":
                if self.mode != "REMOTE":
                    raise ValueError("LOCAL_CONTROL")
                if self.state != "RUNNING":
                    raise ValueError("NOT_RUNNING")
                self.elapsed = self._play_time()
                self.state = "PAUSED"
            elif verb != "SNAP":
                raise ValueError("UNKNOWN_COMMAND")
            response = [encode("ACK", frame.seq, verb, applied=1, rev=self.revision)]
            if verb != "PING":
                response.append(self.snapshot())
            return response
        except ValueError as error:
            return [encode("ERR", frame.seq, verb, code=str(error))]

    def button(self, action):
        """A simulated physical button is deliberately not a serial command."""
        if action == "STOP":
            self._stop("LOCAL_STOP")
        elif self.mode == "LOCAL":
            if action == "NEXT" and self.state == "IDLE":
                shapes = [key for key in SHAPES if key != "CUSTOM" or self.config.path_xy_um != "NONE"]
                candidate = replace(self.config, shape=shapes[(shapes.index(self.config.shape) + 1) % len(shapes)])
                if self.workspace.incompatibility(candidate):
                    self.reason = "OUT_OF_WORKSPACE"
                    return self.snapshot()
                self.config = candidate
                self.revision += 1
                self.reason = "NONE"
            elif action == "PLAY":
                if self.state == "RUNNING":
                    self.elapsed = self._play_time()
                    self.state = "PAUSED"
                elif self.state in ("IDLE", "PAUSED"):
                    self.started = self.clock()
                    self.state, self.reason = "RUNNING", "NONE"
        return self.snapshot()

    def poll(self):
        now = self.clock()
        if self.mode == "REMOTE" and self.state in ("RUNNING", "PAUSED") and now - self.last_ping > 3:
            self._stop("HEARTBEAT_TIMEOUT")
        # In-memory Demo feedback is faster for visible motion; this is not a
        # claim about serial throughput or the eventual FPGA scanning rate.
        interval = 0.05 if self.state == "RUNNING" else 0.5
        if self.connected and now - self.last_tx >= interval:
            self.last_tx = now
            return [self.snapshot()]
        return []
