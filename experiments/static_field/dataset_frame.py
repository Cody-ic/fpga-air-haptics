"""One built-in EgoPressure frame -> two patches -> static coherent field.

Offline only. No transport/session imports, hardware commands, or GUI changes.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import label
from scipy.optimize import minimize
from scipy.special import logsumexp
from threadpoolctl import threadpool_limits

from desktop_app.egopressure import load_sequence, sample_path, counts_to_kpa
from desktop_app.model import ArraySpec, array_coordinates
from experiments.static_field.reference import propagator, quantize_phase, plane_points


def extract_patches(pressure, threshold_kpa=1., span_mm=40.):
    """Keep two largest connected components ranked by integrated pressure.

    Coordinates match existing replay's 240x137.5 mm texture extent, rotated
    into display coordinates (+Y up, no X mirror). Cell area for force remains
    the dataset's 1.25 mm squared; texture extent is NOT used to recalculate N.
    """
    pressure = np.asarray(pressure, dtype=float)
    if pressure.shape != (105, 185) or not np.isfinite(pressure).all() or np.any(pressure < 0):
        raise ValueError('Expected a finite, nonnegative 105x185 pressure frame')
    mask = pressure > threshold_kpa
    if not mask.any():
        raise ValueError('No contact above threshold; no output target generated')
    x_edges = np.linspace(-120, 120, 186)
    y_edges = np.linspace(68.75, -68.75, 106)
    xx, yy = np.meshgrid((x_edges[:-1]+x_edges[1:])/2, (y_edges[:-1]+y_edges[1:])/2)
    xy = np.stack([xx, yy], axis=-1)
    active_xy = xy[mask]
    low, high = active_xy.min(0), active_xy.max(0)
    origin = (low+high)/2
    scale = span_mm / max(high-low)
    regions, count = label(mask)  # Explicit 4-neighbour components.
    ranks = sorted(range(1, count+1), key=lambda k: float(pressure[regions==k].sum()), reverse=True)[:2]
    if len(ranks) != 2:
        raise ValueError('This reference requires two distinct contact regions')
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


def image_loss(phase, h, goal, background_h, scale=.35):
    """Fit nonuniform amplitudes; keep fixed per-channel amplitude.

    Background uses a separate scalar scale, so variable target amplitudes
    cannot accidentally broadcast onto the background samples.
    """
    w = np.exp(1j*phase)
    p = h @ w
    amplitude = np.sqrt(abs(p)**2+1e-24)
    error = amplitude-goal
    loss = np.mean(error**2)/scale**2
    adj = h.T @ (2*error*np.conj(p)/(len(p)*scale**2*amplitude))
    if len(background_h):
        b = background_h @ w
        q = abs(b)**2/scale**2
        lse = logsumexp(8*q)
        loss += .15*np.mean(q+q*q) + .15*(lse-np.log(len(q)))/8
        coefficient = 2/scale**2*(.15*(1+2*q)/len(q)+.15*np.exp(8*q-lse))
        adj += background_h.T @ (coefficient*np.conj(b))
    return float(loss), -np.imag(w*adj)


def run(out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    plt.rcParams['font.family'] = font_manager.FontProperties(fname='C:/Windows/Fonts/msyh.ttc').get_name()
    plt.rcParams['axes.unicode_minus'] = False
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    source = sample_path()
    seq = load_sequence(source)
    valid = seq.hand_valid & seq.pressure_valid
    index = int(np.nanargmax(np.where(valid, seq.total_force_n, np.nan)))
    raw = counts_to_kpa(seq.force[index])
    patches, mapping, edges = extract_patches(raw)
    emitters = array_coordinates(ArraySpec(8,8,10300))
    axis, points = plane_points(40, .5, 50)
    _, train = plane_points(40, 2, 50)
    # Fixed normalisation shared by both models and both target/field panels.
    # This is a single-frame encoding, not an absolute contact-to-acoustic law.
    density_peak = float(patch_density(points[:,:2], patches).max())
    target = .35*np.sqrt(patch_density(points[:,:2], patches)/density_peak)
    train_target = .35*np.sqrt(patch_density(train[:,:2], patches)/density_peak)
    foreground = train_target >= .35*.15
    background = train_target < .35*.08
    evaluation_bg = target < .35*.08
    evaluation_fg = target >= .35*.15
    # Include both geometric patch centres as explicit diagnostic points.
    centres = np.array([p['centre_mm']+[50.] for p in patches])
    centre_goal = .35*np.sqrt(patch_density(centres[:,:2], patches)/density_peak)
    records = []
    with threadpool_limits(limits=1):
        for model, radius in [('isotropic',0.), ('piston_assumed_10mm',5.)]:
            def h(p):
                return propagator(p, emitters, piston_radius_mm=radius)
            reference = abs(h([[0,0,50]])).sum()
            train_h = h(train)/reference
            fg_h, bg_h = train_h[foreground], train_h[background]
            goals = train_target[foreground]
            rng = np.random.default_rng(20261030)
            trials, best = [], None
            # Use region-centre seeds, not arbitrary pixel-by-pixel phases.
            seeds_h = h(centres)/reference
            for trial in range(10):
                phases = np.zeros(2) if trial == 0 else rng.uniform(-np.pi,np.pi,2)
                initial = np.angle(seeds_h.conj().T @ (centre_goal*np.exp(1j*phases)))
                result = minimize(image_loss, initial, args=(fg_h,goals,bg_h), jac=True,
                                  method='L-BFGS-B', options=dict(maxiter=500,ftol=1e-12,gtol=1e-7))
                trials.append(dict(loss=float(result.fun), converged=bool(result.success),
                                   iterations=int(result.nit), message=str(result.message)))
                if best is None or result.fun < best.fun:
                    best = result
            codes = quantize_phase(best.x)
            w = np.exp(1j*codes*2*np.pi/64)
            values = abs(h(points)@w)/reference
            actual_centres = abs(h(centres)@w)/reference
            metrics = dict(target_centre_amplitude=centre_goal.tolist(), predicted_centre_amplitude=actual_centres.tolist(),
                           target_centre_ratio=float(centre_goal[0]/centre_goal[1]),
                           predicted_centre_ratio=float(actual_centres[0]/actual_centres[1]),
                           foreground_rmse_over_peak=float(np.sqrt(np.mean((values[evaluation_fg]-target[evaluation_fg])**2))/.35),
                           background_peak=float(values[evaluation_bg].max()),
                           plane_peak=float(values.max()),
                           whole_plane_correlation=float(np.corrcoef(target,values)[0,1]))
            np.savez_compressed(out/f'{model}.npz', axis_mm=axis, raw_pressure_kpa=raw,
                                target_amplitude=target.reshape(len(axis),-1),
                                field_amplitude=values.reshape(len(axis),-1), phase_codes=codes,
                                raw_x_edges_mm=edges[0],raw_y_edges_mm=edges[1])
            records.append(dict(model=model, metrics=metrics, trials=trials,
                                phase_codes_64=codes.tolist(), best_phase_rad=best.x.tolist(),
                                calibration_applied=False, ready_for_hardware=False))
            # Exact same mapped axes for all panels. Contact and acoustic units
            # are distinct; target/field use identical fixed colour limits.
            fig, axes = plt.subplots(1,3,figsize=(15.4,5.8),layout='constrained')
            im0=axes[0].pcolormesh(edges[0],edges[1],raw,cmap='inferno',vmin=0,vmax=25,shading='flat')
            axes[0].set_title('① 原始接触压力\n保留原数值，位置统一缩放',fontsize=13)
            axes[0].text(0,-30,'灰色：传感器范围之外，无数据',ha='center',color='#555555',fontsize=9)
            im1=axes[1].imshow(target.reshape(len(axis),-1),origin='lower',extent=[-40,40,-40,40],cmap='inferno',vmin=0,vmax=.4)
            axes[1].set_title('② 简化目标：两个平滑受压区域\n范围与相对强弱 → 声压幅值目标',fontsize=13)
            im2=axes[2].imshow(values.reshape(len(axis),-1),origin='lower',extent=[-40,40,-40,40],cmap='inferno',vmin=0,vmax=.4)
            axes[2].set_title('③ 模拟声场：64路固定相位\n同时发射，非逐点扫描',fontsize=13)
            for ax in axes:
                ax.set(xlim=(-40,40),ylim=(-40,40),xlabel='映射后的 X / 毫米',ylabel='映射后的 Y / 毫米',aspect='equal')
                ax.set_facecolor('#dddddd')
                for number,p in enumerate(patches,1):
                    x,y=p['centre_mm']
                    ax.plot(x,y,'+',color='#5de5ef',ms=7)
                    ax.text(x+2,y+2,str(number),color='#5de5ef',fontsize=11)
            fig.colorbar(im0,ax=axes[0],shrink=.75,label='接触压力 / 千帕')
            fig.colorbar(im2,ax=axes[1:],shrink=.75,label='相对声压幅值（中心单焦点 = 1）')
            model_cn='各向同性点声源' if radius==0 else '假设10毫米活塞指向性（未实测）'
            fig.suptitle(f'EgoPressure 内置样本 · 第{seq.frames[index]}帧 / {seq.seconds[index]:.2f}秒\n'
                         f'8×8阵列 · 间距10.3毫米 · 高度50毫米 · 40千赫 · 64档相位 · {model_cn}',fontsize=14)
            fig.supxlabel(f'位置等比缩小至 {mapping["scale"]:.3f} 倍；青色十字为区域中心。'
                           '接触压力不等于超声声压；仅模拟，不预测实际手感。',fontsize=10)
            fig.savefig(out/f'comparison_{model}.png',dpi=170)
            plt.close(fig)
            print(model,json.dumps(metrics),flush=True)
    metadata = dict(simulated=True,hardware_connected=False,source=str(source.resolve()),
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    sequence=seq.name,index=index,frame=int(seq.frames[index]),seconds=float(seq.seconds[index]),
                    frame_selection='maximum total force among frames with valid hand and pressure',
                    total_contact_force_n=float(seq.total_force_n[index]),peak_contact_kpa=float(raw.max()),
                    patches=patches,mapping=mapping,amplitude_mapping='0.35*sqrt(two-Gaussian density / density peak); heuristic only',
                    array=dict(rows=8,cols=8,pitch_mm=10.3),height_mm=50,carrier_hz=40000,
                    speed_mm_s=343000,phase_steps=64,equal_unit_channel_amplitudes=True,
                    limitations=['Gaussian patches extrapolate beyond sampled contact support, including sensor boundary',
                                 'Two patches preserve integrated relative weights and moments, not all local peaks',
                                 'Not a calibrated contact-pressure to acoustic-force transformation'],
                    normalization='per-model ideal on-axis single-focus amplitude at 50 mm',
                    seed=20261030,starts=10,maxiter=500,training_step_mm=2,evaluation_step_mm=.5,
                    target_masks='foreground >= 0.15 of target peak; background < 0.08 of target peak',
                    results=records)
    (out/'results.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
    print('mapping',json.dumps(mapping),flush=True)
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default='.runtime/dataset-static-frame-20261030')
    run(parser.parse_args().out)
