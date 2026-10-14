"""Deterministic simulated FPGA, not a claim of measured hardware behavior."""

from dataclasses import replace
import time
import uuid
import math
import cmath

from .model import (ArraySpec, Config, Workspace, SHAPES, MAX_PATH_POINTS,
                    MAX_SCAN_POINTS, MAX_STROKES, focus_phases, trajectory_sample)
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
        self.last_capture = -1.0
        self.saved_offsets = (0,) * self.array.count
        self.phase_offsets = self.saved_offsets
        self.channel_mask = (1 << self.array.count)-1
        self.calibration_trial = False

    def _play_time(self):
        return self.elapsed + (self.clock() - self.started if self.state == "RUNNING" else 0)

    def _stop(self, reason="NONE"):
        self.state, self.elapsed, self.reason = "IDLE", 0.0, reason
        if self.calibration_trial:
            self.phase_offsets = self.saved_offsets
            self.channel_mask = (1 << self.array.count)-1
            self.calibration_trial = False
            self.revision += 1

    def snapshot(self):
        focus, scan_on, stroke = trajectory_sample(self.config, self._play_time())
        phases = (focus_phases(self.config, focus, self.array) + self.phase_offsets) % self.config.phase_steps
        self.sample += 1
        fields = dict(boot=self.boot, sample=self.sample,
                      uptime_ms=int((self.clock() - self.birth) * 1000), rev=self.revision,
                      mode=self.mode, state=self.state,
                      output=int(self.state == "RUNNING" and self.config.level > 0 and scan_on),
                      scan_on=int(scan_on), stroke_index=stroke,
                      simulated=1, reason=self.reason, **self.config.wire(), **self.array.wire(),
                      fx_um=round(focus[0] * 1000), fy_um=round(focus[1] * 1000),
                      fz_um=round(focus[2] * 1000), phases=",".join(map(str, phases)),
                      channel_mask=self.channel_mask, phase_offsets=','.join(map(str, self.phase_offsets)))
        return encode("TEL", 0, "STATE", **fields)

    def handle(self, raw):
        frame = decode(raw)
        if frame.kind != "CMD" or frame.seq == 0:
            return []
        verb, fields = frame.verb, frame.fields
        try:
            if verb == "HELLO":
                if self.mode == 'REMOTE':
                    self._stop()
                self.connected = True
                self.last_ping = self.clock()
                return [encode("ACK", frame.seq, verb, proto=VERSION, device="DEMO-FPGA",
                               boot=self.boot, simulated=1, hb_ms=3000, adc_capture=1,
                               caps="CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,CUSTOM_XY,SCAN_PATHS,GEOMETRY,CALIBRATION",
                               max_rows=16, max_cols=16, max_channels=256, max_nodes=MAX_PATH_POINTS,
                               max_scan_points=MAX_SCAN_POINTS, max_strokes=MAX_STROKES,
                               **self.array.wire(), **self.workspace.wire()), self.snapshot()]
            if not self.connected:
                raise ValueError("HANDSHAKE_REQUIRED")
            if verb == 'GEOMETRY':
                if set(fields) != {'start', 'count'}:
                    raise ValueError('BAD_FIELDS')
                return [encode('ACK', frame.seq, verb, **self.array.resolved_geometry().chunk(int(fields['start']), int(fields['count'])))]
            if verb == "CAPTURE":
                if fields:
                    raise ValueError("BAD_FIELDS")
                now = self.clock()
                if now-self.last_capture < .2:
                    raise ValueError("ADC_RATE_LIMIT")
                self.last_capture = now
                # Illustrative signal only; not an acoustic simulation or measurement.
                amplitude = (180*self.config.level/100 if self.state == "RUNNING" else 0)
                if self.calibration_trial and self.state == 'RUNNING':
                    # Synthetic channel errors exercise calibration; no physical pressure claim.
                    signal = sum((18+i % 5)*cmath.exp(2j*math.pi*((i*7 % 19)+self.phase_offsets[i])/64)
                                 for i in range(self.array.count) if self.channel_mask >> i & 1)
                    amplitude = min(500., abs(signal))
                raw = [round(602+amplitude*math.sin(2*math.pi*i/10)+2*math.sin(i*1.7))
                       for i in range(200)]
                return [encode("ACK", frame.seq, verb, boot=self.boot, rev=self.revision,
                               uptime_ms=int((now-self.birth)*1000), simulated=1,
                               tx_running=int(self.state == "RUNNING"), pin="PA0", fs_hz=400000,
                               bits=12, n=200, raw=",".join(map(str, raw)))]
            if verb == "PING":
                self.last_ping = self.clock()
            elif verb == "STOP":
                self._stop()
            elif verb == 'CALIBRATION':
                if self.mode != 'REMOTE':
                    raise ValueError('LOCAL_CONTROL')
                if self.state != 'IDLE':
                    raise ValueError('BUSY')
                if (set(fields) != {'action', 'offsets', 'mask', 'geometry_id'}
                        or fields['geometry_id'] != self.array.resolved_geometry().identity
                        or fields['action'] not in ('trial', 'store')):
                    raise ValueError('BAD_CALIBRATION')
                offsets = tuple(int(v) for v in fields['offsets'].split(','))
                mask = int(fields['mask'])
                if len(offsets) != self.array.count or any(not 0 <= v < self.config.phase_steps for v in offsets) or not 0 < mask < (1 << self.array.count):
                    raise ValueError('BAD_CALIBRATION')
                trial = fields['action'] == 'trial'
                if trial and (self.config.shape != 'POINT' or self.config.mod_hz or self.config.level != 100):
                    raise ValueError('STEADY_POINT_REQUIRED')
                if not trial and mask != (1 << self.array.count)-1:
                    raise ValueError('BAD_CALIBRATION')
                self.phase_offsets, self.channel_mask, self.calibration_trial = offsets, mask, trial
                if not trial:
                    self.saved_offsets = offsets
                self.revision += 1
            elif verb == "MODE":
                if self.state != "IDLE" or self.calibration_trial:
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
                self.phase_offsets = self.saved_offsets
                self.channel_mask = (1 << self.array.count)-1
                self.calibration_trial = False
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
                shapes = [key for key in SHAPES if key != "CUSTOM" or self.config.path_xy_um != "NONE" or self.config.scan_paths != "NONE"]
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
