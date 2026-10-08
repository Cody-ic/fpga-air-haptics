# 8×8 发射板 R1：12 V DC

更新于 **2026-10-08**。基于同学提供的 `ProPrj_haptics_8_8_2026-10-07.epro2`，完成原理图核对、手工布局布线与制造文件检查。原文件未覆盖。

本版为 **141 × 151 mm、四层、13 mm 阵列间距**，使用 64 个 MA40S4S、32 个 TC4427A 和 8 个 SN74LV595A。按行设置走线通道，控制干线集中于左侧，驱动器放在相应换能器附近。最终走线路径由明确的手工拐点确定，未使用自动布线或路径搜索。

## 文件

| 文件 | 用途 |
|---|---|
| [Haptics_8x8_R1_release.epro2](Haptics_8x8_R1_release.epro2) | 嘉立创 EDA 专业版可编辑工程，包含原理图、PCB 与器件库 |
| [Haptics_8x8_R1_release_Gerber.zip](Haptics_8x8_R1_release_Gerber.zip) | 四层制造文件，毫米 4:6 坐标，含钻孔 |
| [BOM.csv](BOM.csv) / [Positions.csv](Positions.csv) | 324 个器件的物料与装配坐标；坐标原点为板框左上，Y 向下，角度为 CAD 原值 |
| [transducer_map.json](transducer_map.json) | 64 路位置、驱动器、移位寄存器与串行位映射；不是上位机图形文件 |
| [routing_geometry.json](routing_geometry.json) | 从最终工程提取的线段和过孔，便于逐条审查；不生成走线 |
| [核验报告.md](核验报告.md) / [validation.json](validation.json) | 检查结果、修改与待实测事项 |
| [SHA256SUMS.txt](SHA256SUMS.txt) | 交付文件校验值 |

## 接口与软件

H1 为底面 2×7 接口。**按方形焊盘的 1 脚及原生工程编号接线**；从底面看时位置相对于顶视图镜像。

| 奇数脚 | 信号 | 偶数脚 | 信号 |
|---|---|---|---|
| 1 | GND | 2 | GND |
| 3 | SRCLK 输入，经 R135 33 Ω | 4 | OE_N，高电平关闭 |
| 5 | RCLK 输入，经 R136 33 Ω | 6 | SRCLR_N，低电平清移位寄存器 |
| 7 | DATA0，第 0 行 | 8 | DATA1，第 1 行 |
| 9 | DATA2，第 2 行 | 10 | DATA3，第 3 行 |
| 11 | DATA4，第 4 行 | 12 | DATA5，第 5 行 |
| 13 | DATA6，第 6 行 | 14 | DATA7，第 7 行 |

逻辑电平为 3.3 V；发射板从 XT30 输入 12 V DC，自带逻辑降压。控制器与发射板共地，H1 不向控制器供电。时钟与数据线应短，并随线提供地参考。

每次更新同时在 8 根 DATA 线上移入 8 位，各行先发 bit7、最后发 bit0，然后统一锁存。清零顺序为 `OE_N=1 → 移入全零 → RCLK 锁存 → 按需使能`；SRCLR_N 不清输出锁存器。

**现有 F103/F411 直接 GPIO 固件及 FPGA 原型不能直接驱动本接口。** 需要新增并验证 8 路并行移位输出。40 kHz、64 相位级时，仅移位需要至少 20.48 MHz 时钟，锁存与建立时间还需预算，详见报告。

## 检查与预览

原生 DRC 为 0；原生网表对比无差异，独立检查确认 324 个器件、214 个有效网络一致。尚未制作实板，输出时序、功耗、温升、声场及触觉均未实测。

在仓库根目录复核文件与连接：

```powershell
python hardware/Haptics_8x8_R1_12VDC/verify_release.py
```

脚本只读文件，不修改或布线；不替代原生 DRC。`export_tables.py` 可重新提取表格；`render_previews.py` 需 PyGerber 2.4.3 与 Pillow，只渲染 Gerber。

以下为实际导出文件渲染，底面已翻转为从背面观看。颜色仅用于区分铜、阻焊开窗和丝印，钻孔以 DRL 为准。

![顶面 Gerber](previews/gerber_top.png)

![底面 Gerber](previews/gerber_bottom.png)

四层顺序为顶层 → 完整 GND 内层 → 3.3 V/12 V 分区内层 → 底层。下单使用四层普通全通孔工艺；旧 4×4 机械定位件的板框、安装孔和阵列间距不能直接套用本板。
