# 8×8 发射板 R2：10.3 mm 中心距、12 V DC

更新于 **2026-10-14**。基于同学提供的 `Haptics_8x8_R1_signal_optimized_20261014_checked.epro2` 修改，原输入和仓库 R1 的 13 mm 工程保留。

## 1. 本版变化

64 颗 MA40S4S 的中心距由 13 mm 改为 **10.3 mm**，外侧阵元中心跨度为 **72.1 × 72.1 mm**。板框仍为 **141 × 151 mm**；修正版已将逻辑铜层和物理叠层统一为顶层、Inner1、Inner2、底层。保留接口附近时钟阻尼电阻、寄存器旁源端 100 Ω 电阻及公共时钟回流优化；受影响的驱动器、去耦器件和走线按明确人工拐点调整，未使用自动布线或路径搜索。

10.3 mm 来自论文第 8 页 §2.6 的声学模型；§2.5 确认实物为 64 颗 MA40S4S 的 8×8 矩形阵列，但没有另报实物测得的中心距。改变间距不等于复现论文声压或触觉效果。

## 2. 文件

| 文件 | 用途 |
|---|---|
| `Haptics_8x8_R2_10p3mm_checked.epro2` | 嘉立创 EDA 专业版原理图、PCB 与器件库 |
| `BOM.csv` / `Positions.csv` | 物料表与实际 CAD 装配坐标 |
| `transducer_map.json` | 64 路坐标、驱动器、寄存器与串行位映射 |
| [接口与通道映射.md](接口与通道映射.md) | 可打印的 H1 接口及 64 路寄存器、驱动器、换能器引脚表 |
| [project_validation.json](project_validation.json) | 绑定最终工程 SHA-256 的原理图/PCB、阵列、叠层和焊膏参数检查结果 |
| `verify_project.py` | 工程审计入口：只读取 epro2，核对位号、引脚网络、阵列、通道、叠层和 R/C 焊膏参数 |
| `final_engineering_review.json` | 原生重导出后的独立复核记录：线路、过孔、元件、叠层与原生 DRC |
| `SHA256SUMS` | 本目录交付文件的校验值 |
| `audit_source.py` / `verify_release.py` | 原理图解析与工程源核对函数；后者另含可选制造文件检查 |

坐标表由 `export_tables.py` 从最终工程提取，不生成或改变走线。本次交付重点为原理图和 PCB 工程本身；工程检查直接核对源参数和连接关系。

## 3. 坐标与通道

PCB 顶视图以板框左上为原点，X 向右、Y 向下；阵列中心为 **(83.5, 65.5) mm**。第 `r` 行、第 `c` 列（从 0 开始）的中心为：

```text
pcb_x = 47.45 + 10.3*c
pcb_y_down = 29.45 + 10.3*r
array_x = pcb_x - 83.5
array_y_up = 65.5 - pcb_y_down
```

声学坐标名义发射面 Z=0，朝向阵列正前方；第一行 Y 为 +36.05 mm。电气通道按行从上到下、列从左到右编号 `8*r+c`，保持原板 DATA 行和寄存器输出位对应关系。此表不是上位机 `haptics-array-1` 导入文件；通用 `ROW_MAJOR_XY` 第一行 Y 为负，软件适配须明确视角和坐标变换。未校准实际高度或相位。

## 4. 接口与供电

H1 为底面 2×7 接口，按方形焊盘标识的 1 脚及工程编号接线；底视图相对于顶视图镜像。

| 奇数脚 | 信号 | 偶数脚 | 信号 |
|---|---|---|---|
| 1 | GND | 2 | GND |
| 3 | SRCLK，经 R135 33 Ω | 4 | OE_N，高电平关闭 |
| 5 | RCLK，经 R136 33 Ω | 6 | SRCLR_N，低电平清移位寄存器 |
| 7 | DATA0，第 0 行 | 8 | DATA1，第 1 行 |
| 9 | DATA2，第 2 行 | 10 | DATA3，第 3 行 |
| 11 | DATA4，第 4 行 | 12 | DATA5，第 5 行 |
| 13 | DATA6，第 6 行 | 14 | DATA7，第 7 行 |

逻辑电平 3.3 V，XT30 输入 12 V DC；控制器与发射板共地，H1 不向控制器供电。每次在 8 根 DATA 上并行移入 8 位，先 bit7、后 bit0，再统一锁存。清零采用 `OE_N=1 → 移入全零 → RCLK 锁存`；SRCLR_N 不清输出锁存器。

## 5. 软件适配边界

现有 F103/F411 固件及 FPGA 板级原型使用直接 GPIO 输出，**不能直接驱动本板的移位接口**。后续需实现并验证 8 路并行移位、锁存、使能及其建立时间。仅把 RTL 行列改为 8×8、间距改为 10300 µm 不足以完成适配。当前 4×4、11 mm 上板配置保留。

## 6. 装配尺寸

Murata 数据表规定 MA40S4S 外径 **9.9 ± 0.3 mm**。10.3 mm 中心距对应**名义壳体间隙 0.4 mm**；按外径公差最坏情况，两颗相邻器件均为 10.2 mm 时仅余 **0.1 mm**，尚未计入器件偏位和装配误差。装配前核对实际外径与定位件间隙；国产替代型号需按其尺寸重新确认，避免强行夹紧壳体。旧 4×4 定位件不能直接用于本板。

## 7. 验证状态

**最终原生工程的工程核验通过，原生 DRC 为 0 项。** 交付对象为 `Haptics_8x8_R2_10p3mm_checked.epro2`，SHA-256 为 `b36760dca47a7697b3a980386de4d648fc3ddd5fa24f5ec0fef5ea73369ac2a1`；`project_validation.json` 绑定同一文件。原生重铺后 5 块当前铺铜均有填充，独立工程源检查全部通过，`pending_corrections` 为空。

324 个元件、970 个焊盘、214 个有效网络的原理图/PCB 引脚集合一致；64 路电气通道和 10.3 mm 名义坐标通过；逻辑铜层与物理铜层顺序均为 1/15/16/2。211 个 R/C 贴片元件的 422 个异常焊盘已修正，C21 的 2 个正常焊盘继续继承工程默认零扩展；全部 424 个 R/C 贴片焊盘的有效焊膏扩展为零。C77/R33 为通孔器件。脚本按工程默认规则解析 `None`，不要求所有焊盘显式填写 0。

同时核对 C1 两个焊盘的原生焊膏参数均为 0；表格重新提取为 324 个装配位置、21 组 BOM、64 路通道。工程核验不替代实板测试：8 路并行移位输出时序、声压、温升、功耗、器件装配及触觉效果仍待验证。后续修改工程时，应重新重铺铜、运行原生 DRC 并生成绑定新 SHA-256 的报告。

## 8. 复核命令

在仓库根目录运行，不要求 Gerber 或额外几何依赖；`--output` 仅保存 JSON 检查报告：

```powershell
python hardware/Haptics_8x8_R2_10p3mm_12VDC/verify_project.py
python hardware/Haptics_8x8_R2_10p3mm_12VDC/verify_project.py --project "需要检查的工程.epro2" --output "project_audit.json"
```

发现工程参数异常时返回非零退出码，并列出 `pending_corrections`；不会改动工程或自动修补。报告中的 `not_covered` 明确区分工程源检查、原生 DRC 与实板工作验证。

## 9. 来源

- Kwak 等，*Tactile feedback in mid-air ultrasound haptics with adjustable spatial resolution*，Mechanical Systems and Signal Processing 249 (2026), 114056：[论文](https://www.sciencedirect.com/science/article/pii/S088832702600213X)。§2.5 说明实物型号、数量与校准；§2.6 明确模型中相邻阵元间距为 10.3 mm。
- Murata，MA40S4S/MA40S4R 数据表，DM-U16-483 Rev.2，2017-08：[官方 PDF](https://www.murata.com/-/media/webrenewal/products/sensor/ultrasonic/open/datasheet_maopn.ashx)。第 8 页 Fig.7 为机械尺寸；第 10 页说明底部焊料凸起可能影响 PCB 安装，实际安装方式需核对。
