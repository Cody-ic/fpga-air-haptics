"""Single-frequency free-field prediction, separate from measured ADC data.

An optional baffled circular piston is an approximation of element directivity,
not a fitted MA40S4S model. All pressures use a fixed unit source at 100 mm;
there is deliberately no conversion to Pa, SPL or human perceptibility.
"""
from dataclasses import dataclass
import math

import numpy as np
from scipy.ndimage import label
from scipy.special import j1


@dataclass(frozen=True)
class FieldSettings:
    plane: str = "XY"
    diameter_mm: float = 10.0
    span_mm: float = 60.0
    z_max_mm: float = 220.0
    resolution: int = 101
    phase_direction: int = 1  # +1 delay code; -1 advancing (slot + code) waveform

    def validate(self):
        if (self.plane not in ("XY", "XZ")
                or any(not math.isfinite(v) for v in
                       (self.diameter_mm, self.span_mm, self.z_max_mm))
                or not 0 <= self.diameter_mm <= 15
                or not 20 <= self.span_mm <= 250
                or not 30 <= self.z_max_mm <= 400
                or type(self.resolution) is not int or not 31 <= self.resolution <= 151):
            raise ValueError("模型参数无效：口径 0～15、半宽 20～250、深度 30～400 mm")
        if type(self.phase_direction) is not int or self.phase_direction not in (-1, 1):
            raise ValueError("相位码时序必须为提前或延迟")
        return self


def piston_directivity(cosine, ka):
    """Signed far-field amplitude; sources do not radiate behind the baffle."""
    cosine = np.clip(np.asarray(cosine, dtype=float), -1, 1)
    argument = ka * np.sqrt(np.maximum(0, 1-cosine*cosine))
    result = np.ones_like(argument)
    np.divide(2*j1(argument), argument, out=result, where=argument > 1e-8)
    return np.where(cosine > 0, result, 0.)


def pressure_at(array, config, phases, positions_mm, channel_mask=None, diameter_mm=10.0,
                phase_direction=1):
    """Complex carrier amplitude including the already-applied electrical phases.

    A source has amplitude one at 100 mm on axis. Output level is NOT treated
    as voltage: the F103 gates whole carriers, whereas other firmware may use
    pulse width. Intrinsic transducer phase/gain and reflections are unknown.
    """
    config.validate()
    geometry = array.resolved_geometry()
    phases = np.asarray(phases, dtype=float)
    positions = np.asarray(positions_mm, dtype=float)
    if (phases.shape != (array.count,) or not np.isfinite(phases).all()
            or np.any(phases < 0) or np.any(phases >= config.phase_steps)
            or positions.shape[-1:] != (3,) or not np.isfinite(positions).all()
            or not math.isfinite(diameter_mm) or not 0 <= diameter_mm <= 15
            or type(phase_direction) is not int or phase_direction not in (-1, 1)):
        raise ValueError("声场坐标、相位表或口径无效")
    if channel_mask is None:
        channel_mask = (1 << array.count)-1
    if type(channel_mask) is not int or not 0 <= channel_mask < (1 << array.count):
        raise ValueError("启用通道范围无效")
    k = 2*math.pi*config.carrier_hz/geometry.sound_speed_mm_s
    pressure = np.zeros(positions.shape[:-1], dtype=np.complex128)
    for channel, (origin, normal, phase) in enumerate(zip(
            geometry.positions_mm, geometry.normals, phases)):
        if not (channel_mask >> channel) & 1:
            continue
        delta = positions-origin
        distance = np.linalg.norm(delta, axis=-1)
        if np.any(distance < 1):
            raise ValueError("预测测点距阵元不足 1 mm，超出当前模型适用范围")
        directivity = (piston_directivity(np.sum(delta*normal, axis=-1)/distance,
                                         k*diameter_mm/2)
                       if diameter_mm else np.ones_like(distance))
        pressure += (100/distance * directivity
                     * np.exp(1j*(k*distance+phase_direction*phase*2*math.pi/config.phase_steps)))
    return pressure


@dataclass(frozen=True)
class FieldResult:
    horizontal: np.ndarray
    vertical: np.ndarray
    amplitude: np.ndarray
    peak: float
    peak_position_mm: tuple
    width_mm: float | None
    height_mm: float | None
    clipped: bool
    enabled_channels: int
    target_amplitude: float

    def values(self, quantity):
        if quantity == "相对声压":
            return self.amplitude, "相对 |p| · 非 Pa"
        if quantity == "相对辐射压力":
            return self.amplitude**2, "相对 |p|² · 非 Pa"
        if quantity == "相对 dB":
            return 20*np.log10(np.maximum(self.amplitude, 1e-6)), "相对 dB · 非 SPL"
        raise ValueError("未知声场显示量")


def compute_field(array, config, phases, focus_mm, enabled=True, channel_mask=None,
                  settings=FieldSettings()):
    settings.validate()
    focus = np.asarray(focus_mm, dtype=float)
    if focus.shape != (3,) or not np.isfinite(focus).all() or focus[2] < 10:
        raise ValueError("焦点需为有效三维坐标，z 不小于 10 mm")
    span = max(settings.span_mm, abs(focus[0])+15,
               abs(focus[1])+15 if settings.plane == "XY" else 0)
    horizontal = np.linspace(-span, span, settings.resolution)
    vertical = (np.linspace(-span, span, settings.resolution) if settings.plane == "XY"
                else np.linspace(10, max(settings.z_max_mm, focus[2]+20), settings.resolution))
    xx, vv = np.meshgrid(horizontal, vertical)
    positions = (np.stack((xx, vv, np.full_like(xx, focus[2])), axis=-1)
                 if settings.plane == "XY" else
                 np.stack((xx, np.full_like(xx, focus[1]), vv), axis=-1))
    mask = (1 << array.count)-1 if channel_mask is None else channel_mask
    if type(mask) is not int or not 0 <= mask < (1 << array.count):
        raise ValueError("启用通道范围无效")
    active_mask = mask if enabled and config.level else 0
    pressure = pressure_at(array, config, phases, positions, active_mask,
                           settings.diameter_mm, settings.phase_direction)
    target = abs(pressure_at(array, config, phases, focus, active_mask,
                            settings.diameter_mm, settings.phase_direction))
    amplitude = abs(pressure) if enabled and config.level else np.zeros_like(xx)
    peak = float(amplitude.max())
    iy, ix = np.unravel_index(np.argmax(amplitude), amplitude.shape)
    width = height = None
    clipped = False
    if peak > 1e-10:
        components, _ = label(amplitude >= peak/2, structure=np.ones((3, 3)))
        ys, xs = np.where(components == components[iy, ix])
        clipped = bool(xs.min() == 0 or xs.max() == len(horizontal)-1
                       or ys.min() == 0 or ys.max() == len(vertical)-1)
        width = float(horizontal[xs.max()]-horizontal[xs.min()])
        height = float(vertical[ys.max()]-vertical[ys.min()])
    return FieldResult(horizontal, vertical, amplitude, peak,
                       (float(horizontal[ix]), float(vertical[iy])), width, height,
                       clipped, mask.bit_count(), float(target) if enabled and config.level else 0.)
