"""Offline, phase-only static-field reference. Never opens hardware interfaces.

Run: python -m experiments.static_field.reference --out .runtime/static-field
All fields are coherent simultaneous fields, not scan-time averages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import j1
from scipy.special import logsumexp
from scipy.spatial import cKDTree

from desktop_app.model import ArraySpec, array_coordinates


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


def circle_points(count, radius_mm=20, z_mm=50, offset_rad=0):
    theta = np.arange(count) * 2*np.pi/count + offset_rad
    return np.column_stack((radius_mm*np.cos(theta), radius_mm*np.sin(theta), np.full(count, z_mm)))


def plane_points(span_mm, step_mm, z_mm):
    axis = np.arange(-span_mm, span_mm + step_mm/2, step_mm)
    xx, yy = np.meshgrid(axis, axis)
    return axis, np.column_stack((xx.ravel(), yy.ravel(), np.full(xx.size, z_mm)))


def target_distance(points, targets, ring=False, radius_mm=20):
    if ring:
        return np.abs(np.linalg.norm(points[:, :2], axis=1) - radius_mm)
    return cKDTree(targets[:, :2]).query(points[:, :2])[0]


def loss_gradient(phase, target_h, dark_h, goal, dark_weight=.15, peak_weight=.15):
    """Amplitude fitting plus dark-zone energy and hotspot penalties.

    J=mean((|Ht*w|/goal-1)^2) + lambda*mean(q+q^2)
      + peak_weight*log(mean(exp(8*q)))/8.
    q=|Hd*w|^2/goal^2, |w_n|=1. Exact analytic phase gradient.
    Smooth maximum prevents isolated bright peaks hiding in a dark-zone mean.
    """
    weights = np.exp(1j*phase)
    p = target_h @ weights
    amplitude = np.sqrt(np.abs(p)**2 + 1e-24)
    error = amplitude / goal - 1
    loss = np.mean(error**2)
    coefficient = 2 * error / (goal * amplitude * len(p))
    adjoint = target_h.T @ (coefficient * np.conj(p))
    if len(dark_h):
        dark = dark_h @ weights
        power = np.abs(dark)**2 / goal**2
        loss += dark_weight * np.mean(power + power**2)
        coefficient = 2 * dark_weight * (1+2*power) / (goal**2 * len(dark))
        lse = logsumexp(8*power)
        loss += peak_weight * (lse-np.log(len(dark))) / 8
        coefficient += 2 * peak_weight * np.exp(8*power-lse) / goal**2
        adjoint += dark_h.T @ (coefficient * np.conj(dark))
    return float(loss), -np.imag(weights * adjoint)


def solve(target_h, dark_h, goal, *, seed=20261030, starts=6, maxiter=350):
    """Multi-start phase-only local optimisation; no global-optimum claim."""
    rng = np.random.default_rng(seed)
    trials, best = [], None
    for start in range(starts):
        desired = np.ones(len(target_h), dtype=complex) if start == 0 else np.exp(1j*rng.uniform(-np.pi, np.pi, len(target_h)))
        initial = np.angle(target_h.conj().T @ desired)
        initial_loss = loss_gradient(initial, target_h, dark_h, goal)[0]
        result = minimize(loss_gradient, initial, args=(target_h, dark_h, goal),
                          method='L-BFGS-B', jac=True,
                          options=dict(maxiter=maxiter, ftol=1e-12, gtol=1e-7))
        trials.append(dict(start=start, initial_loss=initial_loss, loss=float(result.fun),
                           iterations=int(result.nit), converged=bool(result.success),
                           message=str(result.message)))
        if best is None or result.fun < best.fun:
            best = result
    return best.x, trials


def metrics(target_values, field_values, dark_mask, centre_value):
    mean = float(np.mean(target_values))
    return dict(target_mean=mean, target_min=float(np.min(target_values)),
                target_max=float(np.max(target_values)),
                target_cv=float(np.std(target_values)/max(mean, 1e-15)),
                target_min_max=float(np.min(target_values)/max(np.max(target_values), 1e-15)),
                centre=float(centre_value),
                off_target_peak=float(np.max(field_values[dark_mask])),
                off_target_peak_over_target_mean=float(np.max(field_values[dark_mask])/max(mean, 1e-15)),
                plane_peak=float(np.max(field_values)))


def run(out, *, starts=6, maxiter=350):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from threadpoolctl import threadpool_limits

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    emitters = array_coordinates(ArraySpec(8, 8, 10300))
    z_mm, radius_mm = 50., 20.
    cases = [('point', np.array([[0., 0., z_mm]]), False, 1.),
             ('two_points', np.array([[-10., 0., z_mm], [10., 0., z_mm]]), False, .6),
             ('four_points', circle_points(4, 15, z_mm, np.pi/4), False, .42),
             ('eight_points', circle_points(8, radius_mm, z_mm), False, .28),
             ('ring', circle_points(64, radius_mm, z_mm), True, .23)]
    axis, evaluation = plane_points(45, .5, z_mm)
    _, training = plane_points(45, 2.5, z_mm)
    # Independent dense angular evaluation prevents fitting only the 64 controls.
    ring_evaluation = circle_points(720, radius_mm, z_mm, np.pi/720)
    records, maps = [], {}
    metadata = dict(schema='static-field-reference-1', simulated=True,
                    date='2026-10-30', array=dict(rows=8, cols=8, pitch_mm=10.3),
                    height_mm=z_mm, radius_mm=radius_mm, frequency_hz=40000,
                    sound_speed_mm_s=343000, phase_steps=64, unit_amplitude_per_channel=True,
                    calibration='ideal identical channels; no measured offsets applied',
                    normalization='one continuous-phase on-axis focus at z=50 mm per propagation model',
                    evaluation=dict(span_mm=45, step_mm=.5, dark_exclusion_mm=6, ring_samples=720),
                    solver=dict(starts=starts, maxiter=maxiter, seed=20261030, dark_weight=.15, peak_weight=.15, peak_sharpness=8),
                    versions=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__),
                    reference_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with threadpool_limits(limits=1):
        for model, piston_radius in [('isotropic', 0.), ('piston_assumed_10mm', 5.)]:
            def h(points):
                return propagator(points, emitters, piston_radius_mm=piston_radius)
            centre_h = h(np.array([[0., 0., z_mm]]))
            reference = np.abs(centre_h).sum()
            field_h = h(evaluation) / reference
            train_h = h(training) / reference
            for name, targets, ring, goal in cases:
                start_time = time.perf_counter()
                target_h = h(targets) / reference
                dark_h = train_h[target_distance(training, targets, ring, radius_mm) >= 6]
                if name == 'point':
                    phase, trials = -np.angle(target_h[0]), []
                else:
                    phase, trials = solve(target_h, dark_h, goal, starts=starts, maxiter=maxiter)
                codes = quantize_phase(phase)
                quantized = codes * 2*np.pi/64
                evaluated_targets = ring_evaluation if ring else targets
                eval_h = h(evaluated_targets)/reference
                dark_mask = target_distance(evaluation, targets, ring, radius_mm) >= 6
                variants = {}
                for variant, ph in [('continuous', phase), ('quantized_64', quantized)]:
                    w = np.exp(1j*ph)
                    values = np.abs(field_h @ w)
                    tv = np.abs(eval_h @ w)
                    variants[variant] = metrics(tv, values, dark_mask, np.abs((centre_h/reference @ w)[0]))
                    if variant == 'quantized_64':
                        maps[model, name] = values.reshape(len(axis), len(axis))
                        np.savez_compressed(out/f'{model}_{name}_field.npz', axis_mm=axis,
                                            amplitude=maps[model, name], target_xyz_mm=evaluated_targets,
                                            target_amplitude=tv, phase_codes=codes, phase_rad=phase)
                row = dict(model=model, case=name, target_count=len(targets), goal=goal,
                           seconds=time.perf_counter()-start_time, metrics=variants, trials=trials,
                           source_coordinates_mm=emitters.tolist(), target_coordinates_mm=targets.tolist(),
                           phase_codes_64=codes.tolist(), phase_rad=phase.tolist())
                records.append(row)
                print(model, name, json.dumps(variants['quantized_64']), flush=True)
                (out/f'{model}_{name}_phases.json').write_text(json.dumps(dict(
                    simulated=True, ready_for_hardware=False, phase_convention='exp(+j*(k*r+phase)); modulo delay code',
                    calibration_applied=False, **row), indent=2), encoding='utf-8')

    fig, axes = plt.subplots(2, 5, figsize=(18, 7.8), constrained_layout=True)
    labels = ['One focus', 'Two simultaneous', 'Four simultaneous', 'Eight simultaneous', 'Static ring (64 controls)']
    for i, model in enumerate(['isotropic', 'piston_assumed_10mm']):
        for j, (name, targets, ring, _) in enumerate(cases):
            ax = axes[i, j]
            im = ax.imshow(maps[model, name], origin='lower', extent=[-45,45,-45,45],
                           vmin=0, vmax=1, cmap='magma')
            if ring:
                ax.add_patch(plt.Circle((0,0), radius_mm, fill=False, color='cyan', lw=.7, ls='--'))
            else:
                ax.scatter(targets[:,0], targets[:,1], marker='+', c='cyan', s=25, linewidths=.8)
            ax.set_title(labels[j], fontsize=11)
            ax.set_xlabel('X (mm)')
            ax.set_ylabel(('Isotropic\n' if i == 0 else 'Assumed 10 mm piston\n') + 'Y (mm)' if j == 0 else 'Y (mm)')
    fig.colorbar(im, ax=axes, shrink=.8, label='Relative pressure amplitude |p| / single-focus reference')
    fig.suptitle('STATIC coherent fields | 8x8, pitch 10.3 mm, z=50 mm, 40 kHz, 64 phase steps\n'
                 'SIMULATION ONLY - each panel has the SAME scale; not measured pressure or membrane displacement', fontsize=13)
    fig.savefig(out/'comparison.png', dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(15,4.5), constrained_layout=True)
    for model, color in [('isotropic', '#2f81f7'), ('piston_assumed_10mm', '#e67e22')]:
        ring_data = np.load(out/f'{model}_ring_field.npz')
        axes[0].plot(np.arange(720)*.5+.25, ring_data['target_amplitude'], label=model, color=color)
        axes[1].plot(axis, maps[model, 'ring'][len(axis)//2], label=model, color=color)
        selected = [r for r in records if r['model']==model]
        axes[2].plot(range(5), [r['metrics']['quantized_64']['target_mean'] for r in selected], 'o-', color=color, label=model)
        axes[2].plot(range(5), [r['metrics']['quantized_64']['off_target_peak'] for r in selected], 'x--', color=color)
    axes[0].set(xlabel='Angle around ring (degrees)', ylabel='Relative pressure amplitude', title='Ring: independent 720-point evaluation', ylim=(0,1))
    axes[1].set(xlabel='X (mm), Y=0', ylabel='Relative pressure amplitude', title='Static ring: centre cross section', ylim=(0,1))
    axes[2].set(xticks=range(5), xticklabels=['1','2','4','8','Ring'], ylabel='Relative pressure amplitude', title='Solid: target mean; dashed: off-target peak', ylim=(0,1.05))
    for ax in axes:
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.savefig(out/'profiles.png', dpi=180)
    plt.close(fig)
    (out/'results.json').write_text(json.dumps(dict(metadata=metadata, results=records), indent=2), encoding='utf-8')
    report = ['# 静态多点与圆环参考仿真', '', '2026-10-30。纯数值模拟，没有连接或启动硬件。', '',
              '## 参数与判读', '',
              '8×8、间距10.3 mm、高度50 mm、40 kHz、声速343 m/s、64档相位、各通道等幅。',
              '每种传播模型都用该模型中心单焦点的理想相干幅值归一化；同一模型不同图形不单独调亮。',
              '下表均为量化后结果。圆环沿720个独立角度评估；其他图形在指定目标点评估。',
              '杂散峰在±45 mm平面内、距目标点或圆周至少6 mm处寻找，不代表整个三维空间。',
              '10 mm均匀活塞只是指向性敏感性假设，未经器件实测。两模型均假设通道已经理想校准。', '',
              '| 模型 | 图形 | 目标平均幅值 | 最小/最大 | 图外最高幅值 | 图外峰/目标均值 | 圆心幅值 |',
              '|---|---|---:|---:|---:|---:|---:|']
    for r in records:
        m = r['metrics']['quantized_64']
        report.append(f"| {r['model']} | {r['case']} | {m['target_mean']:.3f} | {m['target_min_max']:.3f} | {m['off_target_peak']:.3f} | {m['off_target_peak_over_target_mean']:.3f} | {m['centre']:.3f} |")
    report += ['', '## 输出与限制', '',
               '- comparison.png：统一色标的二维声压幅值图；profiles.png：圆周均匀性及截面。',
               '- results.json：参数、软件版本、源码哈希、各次优化收敛记录、连续/量化指标。',
               '- *_field.npz：数值场；*_phases.json：64路模拟相位，未添加真实校准修正，不能直接发送。',
               '- 局部多起点优化不保证全局最优。目标幅值是优化设定，不是实际达到值。',
               '- |p|²仅可作为同一模型下的相对声压平方指标，不能直接称为触觉强度或薄膜位移。',
               '- 未包含反射、膜耦合、通道残差、真实指向性、方波谐波、驱动电压和移位输出时序误差。',
               '- 计算耗时是本机Python求解时间，不是板端预计延迟。',
               '', '详见仓库 experiments/static_field/README.md 中的算法和验证说明。']
    (out/'report.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='.runtime/static-field-20261030')
    parser.add_argument('--starts', type=int, default=6)
    parser.add_argument('--maxiter', type=int, default=350)
    args = parser.parse_args()
    if args.starts < 1 or args.maxiter < 1:
        parser.error('starts and maxiter must be positive')
    run(args.out, starts=args.starts, maxiter=args.maxiter)
