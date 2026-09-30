"""Fixed-receiver relative phase calibration using four-step interference.

No synchronous ADC phase reference is required: squared 40 kHz amplitudes are
fitted versus commanded transmit phase. Receiver position/gain must stay fixed.
"""
from dataclasses import replace
from datetime import datetime, timezone
import math
import queue
import statistics
import threading
import time


def solve_phase(powers, reference_power, channel_power, noise_power, steps=64):
    """Return phase correction and fit diagnostics; reject weak/nonlinear data."""
    if (len(powers) != 4 or steps % 4 or any(not math.isfinite(v) or v < 0
            for v in [*powers, reference_power, channel_power, noise_power])):
        raise ValueError('四步相位测量数据无效')
    a, b = reference_power-noise_power, channel_power-noise_power
    if min(a, b) <= max(1., 25*noise_power):
        raise ValueError('单路信号过弱，请检查接线、接收器位置或参考通道')
    dc = sum(powers)/4-noise_power
    cosine, sine = (powers[0]-powers[2])/2, (powers[3]-powers[1])/2
    contrast = math.hypot(cosine, sine)
    expected = 2*math.sqrt(a*b)
    # The second harmonic residual detects drift/clipping; independent single
    # measurements also prevent a flat/noisy fit from becoming an accepted trim.
    residual = abs(powers[0]+powers[2]-powers[1]-powers[3])/4
    if (abs(dc-(a+b)) > .25*(a+b) or abs(contrast-expected) > .3*expected
            or residual > .12*expected):
        raise ValueError('干涉测量不符合线性模型，可能有移动、噪声或模拟前端削顶')
    trim = round(-math.atan2(sine, cosine)*steps/(2*math.pi)) % steps
    return trim, dict(contrast=contrast, expected_contrast=expected, residual=residual)


def validate_document(document, array, steps, simulated):
    if (document.get('schema') != 'haptics-calibration-1'
            or document.get('geometry_id') != array.resolved_geometry().identity
            or document.get('phase_steps') != steps or document.get('carrier_hz') != 40000
            or type(document.get('simulated')) is not bool or document['simulated'] != simulated):
        raise ValueError('校准文件的阵列、频率、相位精度或模拟/实测来源不匹配')
    offsets = document.get('phase_offsets', [])
    if len(offsets) != array.count or any(type(v) is not int or not 0 <= v < steps for v in offsets):
        raise ValueError('校准相位表无效')
    return tuple(offsets)


class CalibrationRunner(threading.Thread):
    """Exclusive controller task. GUI continues draining normal session events."""
    def __init__(self, session, position_mm, reference=0, repeats=3, apply_document=None):
        super().__init__(daemon=True, name='receiver-calibration')
        self.session = session
        self.position_mm = tuple(position_mm)
        self.reference = reference
        self.repeats = repeats
        self.apply_document = apply_document
        self.cancelled = threading.Event()
        self.progress = queue.Queue()
        self.result = None
        self.error = None
        self.incoming = session.subscribe()
        self.capture_after = 0.

    def cancel(self):
        self.cancelled.set()
        self.session.command('STOP')

    def _event(self, deadline, cleanup=False):
        while time.monotonic() < deadline:
            if self.cancelled.is_set() and not cleanup:
                raise RuntimeError('校准已取消')
            try:
                event = self.incoming.get(timeout=.05)
            except queue.Empty:
                continue
            if event['kind'] in ('error', 'closed'):
                raise RuntimeError(event.get('text', '连接已断开'))
            if event['kind'] == 'rejected':
                raise RuntimeError(event.get('text', '设备拒绝校准命令'))
            return event
        raise RuntimeError('校准等待设备反馈超时')

    def _control(self, verb, cleanup=False, **fields):
        # Commands are serialized and also verified against a newer STATE.
        while True:
            try:
                self.incoming.get_nowait()
            except queue.Empty:
                break
        if not self.session.command(verb, **fields):
            raise RuntimeError('无法发送校准命令')
        ack = None
        deadline = time.monotonic()+5
        while True:
            event = self._event(deadline, cleanup)
            if event['kind'] == 'ack' and event['verb'] == verb:
                ack = event
            if event['kind'] == 'state' and ack:
                state = event['state']
                if state.revision != int(ack['fields']['rev']) or state.sample <= ack['after_sample']:
                    continue
                if verb == 'START' and (state.state != 'RUNNING' or not state.output):
                    raise RuntimeError('设备未确认测试输出')
                if verb != 'START' and (state.state != 'IDLE' or state.output):
                    raise RuntimeError('设备未确认停止输出')
                if verb == 'CALIBRATION' and (state.channel_mask != int(fields['mask'])
                        or state.phase_offsets != tuple(map(int, fields['offsets'].split(',')))):
                    raise RuntimeError('设备相位修正回读不一致')
                return state

    def _capture_power(self, state):
        samples = []
        for _ in range(self.repeats):
            delay = max(0., self.capture_after-time.monotonic())
            if self.cancelled.wait(delay):
                raise RuntimeError('校准已取消')
            self.session.command('CAPTURE')
            deadline = time.monotonic()+4
            while True:
                event = self._event(deadline)
                if event['kind'] == 'capture':
                    c = event['capture']
                    self.capture_after = time.monotonic()+.22
                    if (c.boot != state.boot or c.revision != state.revision or c.tx_running != state.output
                            or c.uptime_ms < state.uptime_ms):
                        raise RuntimeError('采样窗口与当前测试输出不匹配')
                    a = c.analyze()
                    if a['adc_rail']:
                        raise RuntimeError('ADC 已触及量程边界，不能用于相位校准')
                    samples.append(a['peak40_mv']**2)
                    break
        median = statistics.median(samples)
        if max(samples)-min(samples) > max(4., .15*median):
            raise RuntimeError('连续采样不稳定，请固定接收器后重试')
        return median

    def _trim(self, offsets, mask, action='trial', cleanup=False):
        return self._control('CALIBRATION', cleanup=cleanup, action=action,
                             geometry_id=self.session.array.resolved_geometry().identity,
                             mask=mask, offsets=','.join(map(str, offsets)))

    def _measure(self, offsets, mask):
        self._control('STOP')
        self._trim(offsets, mask)
        state = self._control('START')
        # Serial ACK/STATE latency plus settling margin; capture after this state.
        if self.cancelled.wait(.05):
            raise RuntimeError('校准已取消')
        return self._capture_power(state)

    def run(self):
        original = self.session.last_state
        changed = False
        old_offsets = None
        stored = False
        try:
            if (not self.session.ready or original is None or original.mode != 'REMOTE'
                    or original.state != 'IDLE' or original.output
                    or not {'CALIBRATION', 'ADC_CAPTURE'} <= self.session.capabilities):
                raise RuntimeError('请在支持接收采样和校准的设备上，停止输出并选择电脑控制')
            if original.config.phase_steps != 64 or original.config.carrier_hz != 40000:
                raise ValueError('当前校准流程使用 40 kHz、64 级相位')
            n, steps = original.array.count, original.config.phase_steps
            if not 0 <= self.reference < n or not 2 <= self.repeats <= 10:
                raise ValueError('参考通道或重复次数无效')
            old_offsets = original.phase_offsets
            if self.apply_document is not None:
                offsets = validate_document(self.apply_document, original.array, steps, self.session.is_demo)
                stored = True  # ACK loss can still mean the device applied it.
                self._trim(offsets, (1 << n)-1, 'store')
                self.result = self.apply_document
                return
            if len(self.position_mm) != 3 or not all(math.isfinite(v) for v in self.position_mm):
                raise ValueError('接收器位置须为有限的三维坐标')
            config = replace(original.config, shape='POINT', cx_um=round(self.position_mm[0]*1000),
                             cy_um=round(self.position_mm[1]*1000), z_um=round(self.position_mm[2]*1000),
                             mod_hz=0, level=100, path_xy_um='NONE', scan_paths='NONE')
            config.validate()
            changed = True
            idle = self._control('CONFIG', **config.wire())
            noise = self._capture_power(idle)
            zeros = (0,)*n
            reference_power = self._measure(zeros, 1 << self.reference)
            offsets = [0]*n
            records = []
            for channel in range(n):
                if channel == self.reference:
                    continue
                self.progress.put(f'测量通道 {channel} / {n-1}，请保持接收器不动')
                single = self._measure(zeros, 1 << channel)
                powers = []
                mask = (1 << channel) | (1 << self.reference)
                for step in (0, steps//4, steps//2, 3*steps//4):
                    trial = list(zeros)
                    trial[channel] = step
                    powers.append(self._measure(trial, mask))
                trim, diagnostics = solve_phase(powers, reference_power, single, noise, steps)
                trial[channel] = trim
                peak = self._measure(trial, mask)
                expected = (math.sqrt(max(0., reference_power-noise))+math.sqrt(max(0., single-noise)))**2+noise
                if peak < .85*expected or peak > 1.2*expected or peak < .9*max(powers):
                    raise RuntimeError(f'通道 {channel} 修正后复测不符合预期，未保存校准')
                offsets[channel] = trim
                records.append(dict(channel=channel, single_power_mv2=single, powers_mv2=powers,
                                    verified_power_mv2=peak, **diagnostics))
            self._control('STOP')
            stored = True
            self._trim(offsets, (1 << n)-1, 'store')
            self.result = dict(schema='haptics-calibration-1', geometry_id=original.array.resolved_geometry().identity,
                               simulated=self.session.is_demo, carrier_hz=40000, phase_steps=steps,
                               phase_offsets=offsets, reference_channel=self.reference, receiver_position_mm=self.position_mm,
                               boot=original.boot, created_at=datetime.now(timezone.utc).isoformat(),
                               noise_power_mv2=noise, reference_power_mv2=reference_power, measurements=records,
                               method='four_step_relative_phase', scope='fixed_receiver_position')
        except Exception as error:
            self.error = str(error)
        finally:
            if changed:
                try:
                    self._control('STOP', cleanup=True)
                    # Successful calibration is retained; failed trials never
                    # overwrite the saved offsets. Always restore the old figure.
                    self._control('CONFIG', cleanup=True, **original.config.wire())
                except Exception as error:
                    self.error = f'{self.error or "校准结束"}；恢复状态未确认：{error}'
                    self.result = None
            if self.error and stored and old_offsets is not None:
                try:
                    self._control('STOP', cleanup=True)
                    self._trim(old_offsets, (1 << original.array.count)-1, 'store', cleanup=True)
                except Exception:
                    self.error += '；原修正值恢复未确认'
            self.session.unsubscribe(self.incoming)
