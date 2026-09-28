"""Bounded ADC windows; voltage estimates, never calibrated acoustic pressure."""

from dataclasses import dataclass
import math


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

    @classmethod
    def parse(cls, fields):
        for key in ("simulated", "tx_running"):
            if fields[key] not in ("0", "1"):
                raise ValueError("采样来源或输出标志无效")
        if not fields["boot"] or fields["pin"] != "PA0":
            raise ValueError("采样设备或引脚无效")
        integers = ("rev", "uptime_ms", "fs_hz", "bits", "n")
        if any(not fields[k].isascii() or not fields[k].isdecimal() for k in integers):
            raise ValueError("采样参数须为非负整数")
        rev, uptime, fs, bits, n = (int(fields[k]) for k in integers)
        if fs != 400000 or bits != 12 or n != 200:
            raise ValueError("不支持的采样窗口")
        tokens = fields["raw"].split(",")
        if len(tokens) != n or any(not t.isascii() or not t.isdecimal() for t in tokens):
            raise ValueError("采样数量或数据格式错误")
        raw = tuple(map(int, tokens))
        if any(v > 4095 for v in raw):
            raise ValueError("ADC 数值越界")
        return cls(fields["boot"], rev, uptime, fields["simulated"] == "1",
                   fields["tx_running"] == "1", fields["pin"], fs, bits, raw)

    def analyze(self, vref_mv=3300.0, divider=3.4):
        if not math.isfinite(vref_mv) or not 3000 <= vref_mv <= 3600:
            raise ValueError("ADC 参考电压请输入 3000～3600 mV")
        if not math.isfinite(divider) or not 1 <= divider <= 10:
            raise ValueError("分压还原倍数请输入 1～10")
        mv = tuple(v * vref_mv / 4095 for v in self.raw)
        mean = sum(mv) / len(mv)
        ac = tuple(v - mean for v in mv)
        angle = 2 * math.pi * 40000 / self.fs_hz
        cosine = 2 * sum(v * math.cos(angle*i) for i, v in enumerate(ac)) / len(ac)
        sine = 2 * sum(v * math.sin(angle*i) for i, v in enumerate(ac)) / len(ac)
        peak = math.hypot(cosine, sine)
        return dict(mv=mv, ac_mv=ac, mean_mv=mean, pp_mv=max(mv)-min(mv),
                    rms_mv=math.sqrt(sum(v*v for v in ac)/len(ac)), peak40_mv=peak,
                    stage2_peak40_mv=peak*divider,
                    filtered_mv=tuple(cosine*math.cos(angle*i)+sine*math.sin(angle*i)
                                      for i in range(len(ac))),
                    adc_rail=any(v <= 2 or v >= 4093 for v in self.raw))
