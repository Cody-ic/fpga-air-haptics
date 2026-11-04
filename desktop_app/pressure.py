"""Pressure single-frame compiler. Pure numerical code; no transport or hardware access.

Contact pressure -> up to two Gaussian regions -> nominal 64-step phases.
The relative amplitude encoding is heuristic, not an absolute pressure conversion.
"""
from dataclasses import dataclass
import numpy as np
from scipy.ndimage import label
from scipy.optimize import minimize
from scipy.special import logsumexp, j1
from threadpoolctl import threadpool_limits
from .model import ArraySpec, BUTTON_OUTPUT_SETTINGS, Config, DEFAULT_OUTPUT_LEVEL, array_coordinates

DEFAULT_TARGET_PEAK = .5


@dataclass(frozen=True)
class PressureMapping:
    """One immutable sensor-display -> output-plane transform, in millimetres.

    Reuse the same mapping for a sequence to preserve motion and contact size.
    ``span_mm`` records the chosen output span; it is not the sensor's size.
    """
    origin_display_mm: tuple
    scale: float
    span_mm: float

    def validate(self):
        try:
            valid = (isinstance(self.origin_display_mm, tuple)
                     and len(self.origin_display_mm) == 2
                     and np.asarray(self.origin_display_mm).shape == (2,)
                     and np.isfinite(self.origin_display_mm).all()
                     and isinstance(self.scale, (int, float, np.integer, np.floating))
                     and np.isfinite(self.scale) and self.scale > 0
                     and isinstance(self.span_mm, (int, float, np.integer, np.floating))
                     and np.isfinite(self.span_mm) and self.span_mm > 0
                     and not isinstance(self.scale, (bool, np.bool_))
                     and not isinstance(self.span_mm, (bool, np.bool_)))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('映射须包含有限二维原点、正比例和正范围')
        return self

def propagator(points_mm, emitters_mm, *, frequency_hz=40000, speed_mm_s=343000,
               piston_radius_mm=0.0):
    """H[m,n] = D(theta)*exp(+j*k*r)/r, r in mm. +Z normals.

    radius=0 is the repository's isotropic model. Otherwise use an assumed
    uniform circular piston: D=2*J1(k*a*sin(theta))/(k*a*sin(theta)). This is
    a sensitivity model, NOT a measured MA40S4S directivity curve.
    """
    points = np.asarray(points_mm, dtype=float)
    emitters = np.asarray(emitters_mm, dtype=float)
    if (points.ndim != 2 or emitters.ndim != 2 or points.shape[1] != 3
            or emitters.shape[1] != 3 or not len(points) or not len(emitters)
            or not np.isfinite(points).all() or not np.isfinite(emitters).all()
            or frequency_hz <= 0 or speed_mm_s <= 0 or piston_radius_mm < 0):
        raise ValueError('Invalid propagation geometry/parameters')
    delta = points[:, None, :] - emitters[None, :, :]
    distance = np.linalg.norm(delta, axis=-1)
    if np.any(distance <= 0) or np.any(delta[..., 2] <= 0):
        raise ValueError('All sample points must be in front of the planar array')
    k = 2 * np.pi * frequency_hz / speed_mm_s
    directivity = np.ones_like(distance)
    if piston_radius_mm:
        arg = k * piston_radius_mm * np.linalg.norm(delta[..., :2], axis=-1) / distance
        np.divide(2 * j1(arg), arg, out=directivity, where=arg != 0)
    return directivity * np.exp(1j * k * distance) / distance

def quantize_phase(phase_rad, steps=64):
    if steps < 2:
        raise ValueError('At least two phase steps required')
    return np.floor(np.remainder(phase_rad, 2*np.pi) * steps / (2*np.pi) + .5).astype(int) % steps

def plane_points(span_mm, step_mm, z_mm):
    axis = np.arange(-span_mm, span_mm + step_mm/2, step_mm)
    xx, yy = np.meshgrid(axis, axis)
    return axis, np.column_stack((xx.ravel(), yy.ravel(), np.full(xx.size, z_mm)))

def extract_patches(pressure, threshold_kpa=1., span_mm=40., *, mapping=None):
    """Keep two largest connected components ranked by integrated pressure.

    Coordinates match existing replay's 240x137.5 mm texture extent, rotated
    into display coordinates (+Y up, no X mirror). Cell area for force remains
    the dataset's 1.25 mm squared; texture extent is NOT used to recalculate N.
    """
    pressure = np.asarray(pressure, dtype=float)
    if pressure.shape != (105, 185) or not np.isfinite(pressure).all() or np.any(pressure < 0):
        raise ValueError('Expected a finite, nonnegative 105x185 pressure frame')
    if not np.isfinite(threshold_kpa) or threshold_kpa < 0:
        raise ValueError('接触阈值须为有限非负数')
    if mapping is not None:
        if not isinstance(mapping, PressureMapping):
            raise ValueError('Expected a PressureMapping')
        mapping.validate()
        span_mm = mapping.span_mm
    if not np.isfinite(span_mm) or span_mm <= 0:
        raise ValueError('映射范围须为有限正数')
    mask = pressure > threshold_kpa
    if not mask.any():
        raise ValueError('No contact above threshold; no output target generated')
    x_edges = np.linspace(-120, 120, 186)
    y_edges = np.linspace(68.75, -68.75, 106)
    xx, yy = np.meshgrid((x_edges[:-1]+x_edges[1:])/2, (y_edges[:-1]+y_edges[1:])/2)
    xy = np.stack([xx, yy], axis=-1)
    active_xy = xy[mask]
    if mapping is None:
        low, high = active_xy.min(0), active_xy.max(0)
        origin = (low+high)/2
        scale = span_mm / max(float(max(high-low)), 1.25)
    else:
        origin = np.asarray(mapping.origin_display_mm, dtype=float)
        scale = float(mapping.scale)
    regions, count = label(mask)  # Explicit 4-neighbour components.
    ranks = sorted(range(1, count+1), key=lambda k: float(pressure[regions==k].sum()), reverse=True)[:2]

    patches = []
    for rank in ranks:
        selected = regions == rank
        w = pressure[selected]
        pos = (xy[selected]-origin)*scale
        mean = np.average(pos, axis=0, weights=w)
        delta = pos-mean
        covariance = (delta.T*w) @ delta / w.sum()
        covariance += np.eye(2)*.25**2  # Small documented regularisation.
        patches.append(dict(centre_mm=mean.tolist(), covariance_mm2=covariance.tolist(),
                            weight=float(w.sum()), cell_count=int(selected.sum()),
                            peak_kpa=float(w.max()), label=int(rank)))
    retained = sum(p['weight'] for p in patches)/pressure.sum()
    return (patches, dict(origin_display_mm=origin.tolist(), scale=scale, threshold_kpa=threshold_kpa,
                          mapped_contact_span_mm=span_mm, retained_pressure_sum_fraction=float(retained),
                          mapping_mode='fixed' if mapping is not None else 'per_frame',
                          retained_above_threshold_fraction=float(sum(p['weight'] for p in patches)/pressure[mask].sum()),
                          detected_components=count, raw_active_touches_sensor_edge=bool(
                              mask[0].any() or mask[-1].any() or mask[:,0].any() or mask[:,-1].any())),
            ((x_edges-origin[0])*scale, (y_edges-origin[1])*scale))

def patch_density(points_xy, patches):
    """Mixture of two moment-matched Gaussian patches, retaining relative mass."""
    result = np.zeros(len(points_xy))
    for patch in patches:
        cov = np.asarray(patch['covariance_mm2'])
        delta = points_xy - patch['centre_mm']
        distance = np.einsum('ij,jk,ik->i', delta, np.linalg.inv(cov), delta)
        result += patch['weight']/(2*np.pi*np.sqrt(np.linalg.det(cov))) * np.exp(-.5*distance)
    return result

def _field_loss(p, goal, background, scale):
    """The exact shared objective for continuous and discrete phase search."""
    amplitude = np.sqrt(abs(p)**2+1e-24)
    loss = np.mean((amplitude-goal)**2)/scale**2
    if len(background):
        q = abs(background)**2/scale**2
        loss += .15*np.mean(q+q*q) + .15*(logsumexp(8*q)-np.log(len(q)))/8
    return float(loss)


def image_loss(phase, h, goal, background_h, scale=.35):
    """Fit nonuniform amplitudes; keep fixed per-channel amplitude.

    This scale normalizes the loss and fixes the background penalty; it is
    not the requested peak. Keep it fixed as the target peak is increased.
    Background uses a separate scalar scale, so variable target amplitudes
    cannot accidentally broadcast onto the background samples.
    """
    w = np.exp(1j*phase)
    p = h @ w
    amplitude = np.sqrt(abs(p)**2+1e-24)
    error = amplitude-goal
    b = background_h @ w
    loss = _field_loss(p, goal, b, scale)
    adj = h.T @ (2*error*np.conj(p)/(len(p)*scale**2*amplitude))
    if len(background_h):
        q = abs(b)**2/scale**2
        lse = logsumexp(8*q)
        coefficient = 2/scale**2*(.15*(1+2*q)/len(q)+.15*np.exp(8*q-lse))
        adj += background_h.T @ (coefficient*np.conj(b))
    return float(loss), -np.imag(w*adj)


def refine_quantized_phases(codes, h, goal, background_h, *, steps=64, max_sweeps=2, scale=.35):
    """Bounded deterministic coordinate descent on the phases hardware can emit.

    Try the two adjacent codes for each channel, updating complex fields with
    one propagator column rather than recomputing a full matrix product. A
    move needs a strict objective improvement above floating-point roundoff.
    No change is made to per-channel amplitude or hardware calibration.
    """
    codes = np.asarray(codes)
    h = np.asarray(h, dtype=complex)
    goal = np.asarray(goal, dtype=float)
    background_h = np.asarray(background_h, dtype=complex)
    if (type(steps) is not int or not 2 <= steps <= 256
            or type(max_sweeps) is not int or not 0 <= max_sweeps <= 8
            or not isinstance(scale, (int, float, np.integer, np.floating))
            or isinstance(scale, (bool, np.bool_)) or not np.isfinite(scale) or scale <= 0
            or codes.ndim != 1 or not len(codes) or codes.dtype.kind not in 'iu'
            or np.any(codes < 0) or np.any(codes >= steps)
            or h.ndim != 2 or h.shape[1] != len(codes) or not len(h)
            or goal.shape != (len(h),) or not np.isfinite(goal).all() or np.any(goal < 0)
            or background_h.ndim != 2 or background_h.shape[1] != len(codes)
            or not np.isfinite(h).all() or not np.isfinite(background_h).all()):
        raise ValueError('Invalid quantized refinement parameters')
    result = codes.astype(int, copy=True)
    phases = np.exp(2j*np.pi*result/steps)
    field, background = h @ phases, background_h @ phases
    initial_loss = current_loss = _field_loss(field, goal, background, scale)
    history = [current_loss]
    sweeps = improvements = 0
    for _ in range(max_sweeps):
        sweeps += 1
        changed = False
        for channel in range(len(result)):
            best_code, best_phase, best_loss = result[channel], phases[channel], current_loss
            best_field, best_background = field, background
            for direction in (-1, 1):
                code = (result[channel] + direction) % steps
                phase = np.exp(2j*np.pi*code/steps)
                delta = phase-phases[channel]
                candidate_field = field + h[:, channel]*delta
                candidate_background = background + background_h[:, channel]*delta
                candidate_loss = _field_loss(candidate_field, goal, candidate_background, scale)
                tolerance = 1e-12*max(1., abs(best_loss))
                if candidate_loss < best_loss-tolerance:
                    best_code, best_phase, best_loss = code, phase, candidate_loss
                    best_field, best_background = candidate_field, candidate_background
            if best_code != result[channel]:
                result[channel], phases[channel] = best_code, best_phase
                field, background, current_loss = best_field, best_background, best_loss
                improvements += 1
                changed = True
                history.append(current_loss)
        if not changed:
            break
    # Report the exact full-matrix score for the returned wire codes, never
    # the continuous optimum or an accumulated-field approximation.
    final_loss = image_loss(2*np.pi*result/steps, h, goal, background_h, scale)[0]
    if final_loss > initial_loss + 1e-12*max(1., abs(initial_loss)):
        raise ArithmeticError('Quantized refinement increased its objective')
    history[-1] = final_loss
    return result, dict(initial_quantized_loss=initial_loss, refined_loss=final_loss,
                        sweeps=sweeps, improvements=improvements, loss_history=history)

@dataclass(frozen=True)
class PressureFrame:
    config: Config
    array: ArraySpec
    raw_kpa: np.ndarray
    raw_edges: tuple
    axis_mm: np.ndarray
    target: np.ndarray
    field: np.ndarray
    mapping: dict
    metrics: dict


def compile_pressure(raw_kpa, array, *, z_mm=50., span_mm=40., level=DEFAULT_OUTPUT_LEVEL,
                     mod_hz=BUTTON_OUTPUT_SETTINGS['mod_hz'], starts=4, maxiter=300,
                     target_peak=DEFAULT_TARGET_PEAK, refinement_sweeps=2, mapping=None):
    array.validate()
    if array.mapping != 'ROW_MAJOR_XY' or not 1 <= array.count <= 256:
        raise ValueError('首版压力输出仅支持平面规则阵列')
    if (type(starts) is not int or not 1 <= starts <= 32
            or type(maxiter) is not int or not 1 <= maxiter <= 5000
            or type(refinement_sweeps) is not int or not 0 <= refinement_sweeps <= 8):
        raise ValueError('起点数须为1～32、迭代数1～5000、量化修正遍数0～8的整数')
    if mapping is not None:
        if not isinstance(mapping, PressureMapping):
            raise ValueError('Expected a PressureMapping')
        span_mm = mapping.validate().span_mm
    if not np.isfinite([z_mm, span_mm]).all() or not 20 <= z_mm <= 300 or not 5 <= span_mm <= 80:
        raise ValueError('高度须为 20～300 mm，接触范围须为 5～80 mm')
    if not np.isfinite(target_peak) or not 0 < target_peak <= 1:
        raise ValueError('相对目标峰值须大于 0 且不超过 1')
    config = Config(shape='POINT', radius_um=0, z_um=round(z_mm*1000), level=level, mod_hz=mod_hz).validate()
    raw = np.array(raw_kpa, dtype=float, copy=True)
    patches, mapping_info, edges = extract_patches(raw, span_mm=span_mm, mapping=mapping)
    emitters = array_coordinates(array)
    half = max(40., span_mm)
    axis, points = plane_points(half, 1., z_mm)
    _, train = plane_points(half, 2., z_mm)
    peak = patch_density(points[:,:2], patches).max()
    target = target_peak*np.sqrt(patch_density(points[:,:2], patches)/peak)
    goals = target_peak*np.sqrt(patch_density(train[:,:2], patches)/peak)
    fg, bg = goals >= target_peak*.15, goals < target_peak*.08
    if not fg.any() or not bg.any():
        raise ValueError('接触区域过小或过大，请调整映射范围')
    centres = np.array([p['centre_mm']+[z_mm] for p in patches])
    centre_goal = target_peak*np.sqrt(patch_density(centres[:,:2], patches)/peak)
    with threadpool_limits(limits=1):
        reference = abs(propagator([[0,0,z_mm]], emitters)).sum()
        h = propagator(train, emitters)/reference
        seeds = propagator(centres, emitters)/reference
        rng = np.random.default_rng(20261030)
        best = None
        for trial in range(starts):
            angles = np.zeros(len(patches)) if trial == 0 else rng.uniform(-np.pi,np.pi,len(patches))
            initial = np.angle(seeds.conj().T @ (centre_goal*np.exp(1j*angles)))
            result = minimize(image_loss, initial, args=(h[fg],goals[fg],h[bg]), jac=True,
                              method='L-BFGS-B', options=dict(maxiter=maxiter,ftol=1e-12,gtol=1e-7))
            if np.isfinite(result.fun) and (best is None or result.fun < best.fun):
                best = result
        if best is None:
            raise ValueError('未能生成有限的相位解')
        codes, refinement = refine_quantized_phases(quantize_phase(best.x), h[fg], goals[fg], h[bg],
                                                    max_sweeps=refinement_sweeps)
        values = abs(propagator(points, emitters) @ np.exp(2j*np.pi*codes/64))/reference
    from dataclasses import replace
    config = replace(config, phase_table=bytes(codes.astype('uint8')).hex().upper()).validate()
    metrics = dict(converged=bool(best.success), loss=refinement['refined_loss'], continuous_loss=float(best.fun),
                   **refinement,
                   target_peak=float(target_peak), field_peak=float(values.max()),
                   background_peak=float(values[target < target_peak*.08].max()),
                   whole_plane_rmse=float(np.sqrt(np.mean((values-target)**2))),
                   patches=len(patches), model='isotropic', calibration_applied=False)
    shape=(len(axis),len(axis))
    return PressureFrame(config,array,raw,edges,axis,target.reshape(shape),values.reshape(shape),mapping_info,metrics)
