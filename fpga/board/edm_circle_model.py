"""Offline, constant-amplitude EDM projection for the 70 mm / r20 circle.

No hardware access. Calibration cancels assumed intrinsic phase error and is
not added to the ideal acoustic model. This is not full amplitude+phase EDM.
"""
import os
for _key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[_key] = '1'
import json
import math
from pathlib import Path
import numpy as np
from scipy.linalg import eigh
from scipy.special import j1

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / '.runtime/fpga-edm-circle-ab'
AXIS = (np.arange(8)-3.5)*10.3
SX, SY = np.meshgrid(AXIS, AXIS)
SOURCES = np.c_[SX.ravel(), SY.ravel(), np.zeros(64)]
K = 2*np.pi*40000/343000
ANGLES = np.arange(128)*2*np.pi/128
UNIT = np.rint(16384*np.c_[np.cos(ANGLES), np.sin(ANGLES)]).astype(int)
TARGETS = np.rint(40000*np.c_[np.cos(ANGLES), np.sin(ANGLES)]).astype(int)


def baseline(hx, hy, z=70000):
    coeff = ((40000 << 39)+171500000)//343000000
    mask = (1 << 40)-1
    out = []
    for ch in range(64):
        dx = (2*(ch % 8)-7)*10300-int(hx)
        dy = (2*(ch//8)-7)*10300-int(hy)
        r = math.isqrt(dx*dx+dy*dy+(2*z)**2)
        u = (((r*coeff & mask)+(1 << 33)) & mask) >> 34
        out.append((-u) & 63)
    return np.array(out, dtype=int)


def green(points, diameter=0):
    d = np.atleast_2d(points)[:, None, :] - SOURCES[None, :, :]
    r = np.linalg.norm(d, axis=-1)
    direct = np.ones_like(r)
    if diameter:
        a = K*diameter/2*np.linalg.norm(d[:, :, :2], axis=-1)/r
        nz = abs(a) > 1e-10
        direct[nz] = 2*j1(a[nz])/a[nz]
    return direct*np.exp(1j*K*r)/r


def quant_codes(q):
    return (np.floor(np.remainder(np.angle(q)/(2*np.pi), 1)*64+.5).astype(int) % 64)


def optimized(hx, hy, radius=6.5, alpha=8.912509381337459):
    p = np.array([hx/2000, hy/2000, 70.])
    th = np.arange(8)*2*np.pi/8
    dark = p + np.c_[radius*np.cos(th), radius*np.sin(th), np.zeros(8)]
    g = green(p)[0]
    gd = green(dark)
    h = np.outer(g.conj(), g)-alpha*(gd.conj().T @ gd)/8
    eig, vec = eigh(h, subset_by_index=[63, 63], check_finite=False)
    q = vec[:, 0]
    b = np.exp(2j*np.pi*baseline(hx, hy)/64)
    q *= np.exp(1j*(np.angle(g @ b)-np.angle(g @ q)))
    residual = np.linalg.norm(h @ q-eig[0]*q)/(np.linalg.norm(h)+1e-30)
    assert residual < 1e-10
    # Deliberately discard amplitudes: existing hardware has one global level.
    return quant_codes(q)


def index(hx, hy):
    return int(np.argmax(UNIT[:, 0]*int(hx)+UNIT[:, 1]*int(hy)))


def width(x, p):
    mid = len(x)//2
    half = p[mid]/2
    distances = []
    for xx, pp in ((x[mid:], p[mid:]), (-x[:mid+1][::-1], p[:mid+1][::-1])):
        ids = np.flatnonzero(pp < half)
        if not len(ids): return float('nan')
        i = ids[0]
        distances.append(float(np.interp(half, pp[i-1:i+1][::-1], xx[i-1:i+1][::-1])))
    return sum(distances)


def generate():
    FOLDER.mkdir(parents=True, exist_ok=True)
    deltas = np.array([(optimized(x,y)-baseline(x,y)) % 64 for x,y in TARGETS])
    assert deltas.shape == (128,64)
    data = dict(targets_half_um=TARGETS.tolist(), unit_q14=UNIT.tolist(),
                phase_deltas=deltas.tolist(), alpha=8.912509381337459,
                dark_radius_mm=6.5, phase_only=True, acoustic_validation=False)
    # Evaluate all anchors and quarter-segment positions, including ROM boundary
    # approximation, in both monopole and assumed 10 mm piston models.
    xx = np.linspace(-20,20,801)
    metrics = []
    for j in range(128):
        for fraction in (0., .25, .5, .75):
            xy = np.rint((1-fraction)*TARGETS[j]+fraction*TARGETS[(j+1)%128]).astype(int)
            idx = index(*xy)
            p = np.r_[xy/2000,70.]
            b = np.exp(2j*np.pi*baseline(*xy)/64)
            q = np.exp(2j*np.pi*((baseline(*xy)+deltas[idx]) % 64)/64)
            radial = np.r_[xy/np.linalg.norm(xy),0.]
            tang = np.array([-radial[1],radial[0],0.])
            for diameter in (0,10):
                g = green(p,diameter)[0]
                widths=[]
                for direction in (radial,tang):
                    gg = green(p+xx[:,None]*direction,diameter)
                    widths.append([width(xx,abs(gg@b)),width(xx,abs(gg@q))])
                metrics.append(dict(anchor=j,fraction=fraction,index=idx,diameter=diameter,
                    pressure_ratio=float(abs(g@q)/abs(g@b)),widths_radial_tangential_mm=widths))
        if j % 32 == 0: print('MODEL',j,'/128',flush=True)
    summary={}
    for d in (0,10):
        records=[m for m in metrics if m['diameter']==d]
        wr=np.array([m['widths_radial_tangential_mm'] for m in records])
        ratios=wr[:,:,1]/wr[:,:,0]
        pr=[m['pressure_ratio'] for m in records]
        summary[str(d)]=dict(pressure_ratio_min=min(pr),pressure_ratio_max=max(pr),
            radial_width_ratio_range=[float(ratios[:,0].min()),float(ratios[:,0].max())],
            tangential_width_ratio_range=[float(ratios[:,1].min()),float(ratios[:,1].max())],
            finite=bool(np.isfinite(ratios).all()))
    data['summary']=summary
    assert all(s['finite'] for s in summary.values())
    # Gate the candidate: at every sampled position both principal cuts narrow.
    assert all(max(s['radial_width_ratio_range'])<1 and max(s['tangential_width_ratio_range'])<1
               for s in summary.values()), 'Candidate does not narrow around the whole circle'
    (FOLDER/'model.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    (FOLDER/'model_metrics.json').write_text(json.dumps(metrics),encoding='utf-8')
    (FOLDER/'delta.hex').write_text('\n'.join(f'{v:02x}' for v in deltas.ravel())+'\n',encoding='ascii')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__': generate()
