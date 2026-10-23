"""Bounded ADC windows; voltage estimates, never calibrated acoustic pressure."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AdcProbe:
    valid: bool
    count: int
    minimum: int
    maximum: int
    mean: int
    vrefint: int

    @classmethod
    def parse(cls, fields):
        keys = ('probe_ok', 'direct_n', 'direct_min', 'direct_max', 'direct_mean', 'vrefint_raw')
        if not any(k in fields for k in keys):
            return None
        if any(k not in fields or not fields[k].isascii() or not fields[k].isdecimal() for k in keys):
            raise ValueError('ADC 对照读数格式错误')
        valid, count, minimum, maximum, mean, vrefint = (int(fields[k]) for k in keys)
        if valid not in (0, 1) or count != 8 or any(v > 4095 for v in (minimum, maximum, mean, vrefint)):
            raise ValueError('ADC 对照读数越界')
        if valid and not minimum <= mean <= maximum:
            raise ValueError('ADC 对照读数范围错误')
        return cls(bool(valid), count, minimum, maximum, mean, vrefint)


@dataclass(frozen=True)
class Capture:
    boot: str
    revision: int
    uptime_ms: int
    simulated: bool
    tx_running: bool
    pin: str
    fs_hz: int
    bits: int
    raw: tuple
    full_scale_mv: int = 3300
    probe: AdcProbe | None = None

    @classmethod
    def parse(cls, fields):
        for key in ("simulated", "tx_running"):
            if fields[key] not in ("0", "1"):
                raise ValueError("采样来源或输出标志无效")
        if not fields["boot"] or fields["pin"] not in ("PA0", "ADCS7476", "MG24_D0"):
            raise ValueError("采样设备或引脚无效")
        integers = ("rev", "uptime_ms", "fs_hz", "bits", "n")
        if any(not fields[k].isascii() or not fields[k].isdecimal() for k in integers):
            raise ValueError("采样参数须为非负整数")
        rev, uptime, fs, bits, n = (int(fields[k]) for k in integers)
        mg24 = fields['pin'] == 'MG24_D0'
        valid_rate = 200000 <= fs <= 1000000 if mg24 else fs == 400000
        if not valid_rate or bits != 12 or n != 200:
            raise ValueError("不支持的采样窗口")
        scale = fields.get('full_scale_mv', '3300')
        if not scale.isascii() or not scale.isdecimal():
            raise ValueError('采样满量程格式错误')
        scale = int(scale)
        if (mg24 and not 1000 <= scale <= 1250) or (not mg24 and scale != 3300):
            raise ValueError('采样来源与满量程不匹配')
        tokens = fields["raw"].split(",")
        if len(tokens) != n or any(not t.isascii() or not t.isdecimal() for t in tokens):
            raise ValueError("采样数量或数据格式错误")
        raw = tuple(map(int, tokens))
        if any(v > 4095 for v in raw):
            raise ValueError("ADC 数值越界")
        return cls(fields["boot"], rev, uptime, fields["simulated"] == "1",
                   fields["tx_running"] == "1", fields["pin"], fs, bits, raw, scale, AdcProbe.parse(fields))

    def analyze(self, vref_mv=None, divider=3.4):
        vref_mv = self.full_scale_mv if vref_mv is None else vref_mv
        low, high = (1000, 1250) if self.pin == 'MG24_D0' else (3000, 3600)
        if not math.isfinite(vref_mv) or not low <= vref_mv <= high:
            raise ValueError(f"采样满量程请输入 {low}～{high} mV")
        if not math.isfinite(divider) or not 1 <= divider <= 10:
            raise ValueError("分压还原倍数请输入 1～10")
        mv = tuple(v * vref_mv / 4095 for v in self.raw)
        mean = sum(mv) / len(mv)
        ac = tuple(v - mean for v in mv)
        angle = 2 * math.pi * 40000 / self.fs_hz
        # Fit DC + sine + cosine. MG24 clock division need not yield an integer
        # number of cycles per window; 2/N projection would then bias amplitude.
        cosines = tuple(math.cos(angle*i) for i in range(len(ac)))
        sines = tuple(math.sin(angle*i) for i in range(len(ac)))
        cm, sm = sum(cosines)/len(ac), sum(sines)/len(ac)
        c, s = tuple(v-cm for v in cosines), tuple(v-sm for v in sines)
        cc, ss, cs = sum(v*v for v in c), sum(v*v for v in s), sum(x*y for x, y in zip(c, s))
        xc, xs = sum(v*x for v, x in zip(ac, c)), sum(v*x for v, x in zip(ac, s))
        determinant = cc*ss-cs*cs
        if determinant <= 1e-9:
            raise ValueError('当前采样窗口不足以分析 40 kHz 分量')
        cosine = (xc*ss-xs*cs)/determinant
        sine = (xs*cc-xc*cs)/determinant
        peak = math.hypot(cosine, sine)
        return dict(mv=mv, ac_mv=ac, mean_mv=mean, pp_mv=max(mv)-min(mv),
                    fit_dc_mv=mean-cosine*cm-sine*sm,
                    rms_mv=math.sqrt(sum(v*v for v in ac)/len(ac)), peak40_mv=peak,
                    stage2_peak40_mv=peak*divider,
                    filtered_mv=tuple(cosine*math.cos(angle*i)+sine*math.sin(angle*i)
                                      for i in range(len(ac))),
                    adc_rail=any(v <= 2 or v >= 4093 for v in self.raw))
