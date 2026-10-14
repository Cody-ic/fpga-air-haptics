# 4×4 超声驱动板 R5：首轮打样包

核验日期：2026-09-26。该版本面向 Tang Mega 60K 的 4×4、16 路单端超声驱动原型；它用于先验证 40 kHz 输出、阵列装配和声场测量，不宣称已经验证空中触觉效果。

## 已完成

- 12 V DC 经 XT30PW-M20.G.Y 公头、F1 0454.500MR（0.5 A）和 R33 AC03000002208JAC00（2.2 Ω / 3 W 脉冲电阻）进入 VCC；D1 SMBJ13A-13-F 接在 VCC 对地，用于小能量瞬态抑制。
- MA40S4S 按 4×4、11 mm 间距布置。焊孔按 Murata 手册的 0.64 mm 方形引脚和装配余量放大为约 1.20 mm；焊盘和孔位已在导出文件中复核。
- 19 个底面测试点：VIN、VCC、GND，以及 T00–T15（对应 OUT0–OUT15）；全部测试点无锡膏开口。
- 原有 32 个盲孔已经全部改为通孔；最终钻孔包只有 PTH、NPTH 和 PTH_Via 三类，未再生成盲孔钻孔文件。

## 复核结果

- 嘉立创 EDA Pro 重新打开最终 EPRO 后，原理图 DRC 与 PCB DRC 均为 0 条错误。
- 78 个器件的原理图/PCB 位号集合一致；R33 的网络为 VIN_FUSED → VCC。
- Gerber 包包含 4 层铜箔、阻焊、丝印、板框、钻孔和飞针测试文件。板框仍为 80 × 88 mm，四层叠层使用工程中已有的 JLC04161H-7628 配置。
- 保险丝数据手册给出 0.5 A、125 V、额定熔断 I²t 0.240 A²s；Vishay AC03 手册给出 2.2 Ω / 3 W 器件的尺寸为 Lmax 13.0 mm、Dmax 4.8 mm、引线 0.8 mm。MA40S4S 手册给出 40 kHz、20 Vp-p 最大输入和 2.55 nF（±20%）电容；阵列实际电流、温升和声压仍需上板测量。

## 上板检查顺序

使用限流的 12 V 电源；先不插换能器检查 VIN、VCC 和静态电流，再逐路检查 40 kHz 波形、幅度和过冲。确认驱动电压和温升后再焊装换能器。首次不要接 24 V，也不要把 TVS 当作完整浪涌或反接保护。

## 文件

- [可编辑 EPRO 工程](Haptics_4x4_R5_12VDC.epro)
- [Gerber 与钻孔文件](Haptics_4x4_R5_12VDC_Gerber.zip)
- [BOM](BOM.csv)
- [贴片坐标](Positions.csv)
- [板面预览](板面预览.png)

## 参考资料（核验日期：2026-09-26）

- Murata，2015-04-30，MA40S4S Reference Specification：40 kHz、20 Vp-p、2.55 nF 和机械尺寸。
- Vishay Draloric，2020-12-07，AC/AC-AT（Document 28730）：AC03 外形和脉冲能力曲线。
- Littelfuse，2018-01-18，452/454 Series Fuse：0454.500MR 的 0.5 A、125 V 与 I²t 参数。
- Microchip，DS20001423J：TC4427A 的 4.5–18 V 工作范围和 22 V 绝对最大值。
- Diodes，DS19002 Rev.20-2：SMBJ13A 参数。TVS 仅作小能量瞬态抑制，实际过冲需示波器确认。
