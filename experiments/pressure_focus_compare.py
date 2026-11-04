"""Reproduce the frame-325 region/focus comparison without opening hardware."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from threadpoolctl import threadpool_limits

from desktop_app.egopressure import counts_to_kpa, load_sequence, sample_path
from desktop_app.model import ArraySpec, array_coordinates
from desktop_app.pressure import compile_pressure, propagator, plane_points


def main():
    output = Path('.runtime/pressure-focus')
    output.mkdir(parents=True, exist_ok=True)
    raw = counts_to_kpa(load_sequence(sample_path()).force[324])
    array = ArraySpec(8,8,10300)
    emitters = array_coordinates(array)
    results = [compile_pressure(raw,array,centre_spacing_mm=35,mode=mode) for mode in ('regions','focus')]
    centres = np.array(results[0].mapping['centres_mm'])
    axis, plane = plane_points(80,1,50)
    distance = np.min(np.linalg.norm(plane[:,None,:2]-centres[None,:,:],axis=-1),axis=1)
    reference = abs(propagator([[0,0,50]],emitters)).sum()
    line_xy = centres[0]+(centres[1]-centres[0])*np.linspace(0,1,201)[:,None]
    line = np.column_stack((line_xy,np.full(201,50)))
    reports, curves = [], []
    with threadpool_limits(limits=1):
        for result in results:
            phasors = np.exp(2j*np.pi*np.array(result.config.static_phases(array))/64)
            values = abs(propagator(plane,emitters)@phasors)/reference
            curve = abs(propagator(line,emitters)@phasors)/reference
            curves.append(curve)
            sensitivity = []
            for z,radius in ((45,0),(50,0),(55,0),(50,5)):
                sample = np.column_stack((centres,np.full(2,z)))
                ref = abs(propagator([[0,0,z]],emitters,piston_radius_mm=radius)).sum()
                vals = abs(propagator(sample,emitters,piston_radius_mm=radius)@phasors)/ref
                sensitivity.append(dict(z_mm=z,assumed_piston_radius_mm=radius,centre_amplitudes=vals.tolist()))
            reports.append(dict(mode=result.metrics['mode'],metrics=result.metrics,
                                outside_8mm_peak_within_80mm_plane=float(values[distance>=8].max()),
                                midpoint_amplitude=float(curve[100]),sensitivity=sensitivity))
    (output/'focus-comparison.json').write_text(json.dumps(dict(frame=325,height_mm=50,spacing_mm=35,
        level=100,mod_hz=0,model='isotropic; piston=5mm is sensitivity only, not measured',
        hardware_connected=False,reports=reports),indent=2),encoding='utf-8')
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False})
    fig,axes = plt.subplots(1,3,figsize=(13,4.5),layout='constrained')
    for ax,result,title in zip(axes,results,('原算法：宽区域','双点增强：集中焦点')):
        image = ax.imshow(result.field,origin='lower',extent=[-40,40,-40,40],vmin=0,vmax=.7,cmap='inferno')
        ax.plot(centres[:,0],centres[:,1],'+',color='#5eead4')
        ax.set(title=title,xlabel='x / mm',ylabel='y / mm')
    fig.colorbar(image,ax=list(axes[:2]),location='bottom',shrink=.65,label='同一色标：相对声压幅值')
    for curve,title in zip(curves,('原算法','双点增强')):
        axes[2].plot(np.linspace(0,35,201),curve,label=title)
    axes[2].set(title='两中心连线上的模拟幅值',xlabel='从第一个中心起 / mm',ylabel='相对理想中心单焦点')
    axes[2].legend(); axes[2].grid(alpha=.2)
    fig.suptitle('第325帧 · 高度50 mm · 间距35 mm · 仅理论预测，不是保鲜膜实测')
    fig.savefig(output/'focus-comparison.png',dpi=150)
    plt.close(fig)
    print(json.dumps(reports,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
