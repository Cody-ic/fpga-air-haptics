# 启动校准、补偿恢复与理论显示合并工程

## 当前候选：完整启动校准与显示（2026-11-04）

正式候选默认采用`startup`路线：保留板内四步相位求解、质量判定、修正后复测和整表提交，先在CAL2等待真实测量；全部成功达到DONE、接收匹配的RELEASE并发完应答后，才释放HAP3业务。缺少旧64项修正文件不影响编译校准算法，但此镜像不能标记为已经校准。旧表恢复路线继续严格验证原始实测文件，不以零表或仿真夹具替代。

构建器的三种模式互斥：

| 参数 | 板内设置 | 上电与后续行为 |
| --- | --- | --- |
| `--startup-calibration adc`或`scope` | `CAL_ENABLE=1`、`CAL_RESTORE_ENABLE=0`；来源分别为1／0 | 上电CAL2静默等待；真实校准与复测成功、RELEASE后进入HAP3，`cal_profile=1` |
| `--calibration-file …` | `CAL_ENABLE=1`、`CAL_RESTORE_ENABLE=1` | 恢复文件中的实测修正，直接HAP3待机；不执行新的启动校准，`cal_profile=2` |
| `--uncalibrated` | `CAL_ENABLE=0`、`CAL_RESTORE_ENABLE=0`、修正为零 | 直接HAP3待机；启动校准求解分支已裁掉，`cal_profile=0`，仅用于数字联调 |

`adc`由电脑桥接XIAO MG24的真实采样，将40 kHz分量的功率和质量信息送回FPGA；它不表示接收板已直接连到Tang内置ADC。`scope`由操作者读取示波器幅值并通过同一电脑工具提交。两种来源的通道编排、四步求解、重复性检查、复测和提交均在FPGA内；来源必须与编译配置一致。

统一候选使用50 mm参考高度、等级20、参考通道0。底层命令明确写出这些条件；通用工具仍保留历史30级默认值。`--calibration-z-mm`同时适用于启动校准默认位置和恢复表验证，CAL2 SETUP还可明确设置本轮实际测量条件。启动校准所得表只保留到复位／断电；Flash固化的是算法镜像，不会自动把本轮测量写入Flash。

```powershell
python tools/build_final_software.py --calibration-mode startup --prepare-only
python fpga/preview/build_display_restore.py --startup-calibration adc --calibration-z-mm 50 --calibration-level 20 --calibration-reference 0 --output-root D:/FPGADev/display-build --gowin D:/tools/Gowin-1.9.12.04/IDE/bin/gw_sh.exe
python fpga/preview/check_display_restore.py --startup-calibration adc --calibration-z-mm 50 --calibration-level 20 --output-root D:/FPGADev/display-build --iverilog "<iverilog路径>"
python -m unittest discover -s fpga/preview -p test_display_build.py -v
```

底层构建可追加`--prepare-only`只生成源码、显示资源和脚本，不运行Gowin。产物目录分别为`startup_adc_z50/display_startup_adc/`、`startup_scope_z50/display_startup_scope/`、`z50/display_restore/`及`uncalibrated/display_uncalibrated/`，位流均位于其`impl/pnr/calibration_display_top.fs`；其他支持高度对应修改目录中的高度。默认输出根为`.runtime/display-restore/`，Windows建议使用ASCII工具和输出路径。

上电、STATUS／PING、SETUP、错误READY或提前RELEASE均不会发射。只有有效SETUP和来源匹配的READY才能开始校准；超时／取消只失败并关闭输出，不绕过测量进入业务。CAL2占用串口期间普通HAP3界面不能完成握手，须先使用独立校准工具。所有构建命令均不下载、不自动开始测量。

屏幕沿用原页面：蓝点表示CAL2等待／校准阶段，绿点表示实际校准输出；显示目标位置和高度，CAL2期间不把少数通道试音绘成完整理论场。RELEASE后恢复原HAP3理论观察，暂停为黄点、停止为灰点。新增路径只向显示端传递只读快照，没有放宽输出门控，也没有显示端控制信号回传。

完整构建才生成`manifest.json`。启动ADC模式为`mode=display_startup_adc`、`calibration_applied=false`，并明确记录`solver_on_fpga=true`、`measurement_required=true`、`verification_required=true`、入口CAL2、释放后HAP3及易失性修正。这里的`false`表示没有在构建时嵌入已测修正，不表示校准算法缺失。

### 本轮物理构建与离线验证

2026-11-04的启动ADC镜像已完成Gowin V1.9.12.04完整综合、布局布线及严格时序检查，目标为GW5AST-138B。文件位于`.runtime/startup-calibration-release/startup_adc_z50/`，位流SHA-256为`035099cc1e69a91cbe0e824a0fd3e1edbe9a95ebe4289f69199a0ef88557dbe3`，生成源码与清单逐项哈希一致。

| 项目 | 本轮结果 |
| --- | --- |
| 时钟／Fmax | 系统50 MHz／51.644 MHz；像素49 MHz／51.553 MHz |
| 建立／保持 | 132369条分析路径、82792个端点；违例均0；最小报告裕量分别+0.637／+0.181 ns |
| OSER10复位 | 四个末级FF就近固定；恢复／移除最小裕量+0.890／+0.079 ns，无负裕量 |
| 逻辑／寄存器 | 38332／138240（28%）；24200／139140（18%） |
| BSRAM／DSP | 107／340（32%）；220.5／298（74%） |

245 MHz串行时钟没有普通寄存器路径可报告Fmax，不能把这理解为未检查串化器复位；其恢复／移除路径仍正常约束并通过，未添加规避该检查的假路径。

新增CAL2显示仿真覆盖：上电和按键按住静音、真实STATUS、提前RELEASE拒绝、SETUP目标跨域观察、错误来源READY拒绝；在仿真中提交有效READY及三次背景窗口后，实际参考路输出对应绿点且无虚构声场，ABORT和复位均恢复静音／等待。测试日志位于`.runtime/startup-calibration-release/startup-adc-tests/`。原有`tb_calibrate`、`tb_cal_modes`、`tb_cal_startup`、`tb_cal_apply`、`tb_cal_link`回归通过，覆盖四步求解、64路人工来源、真实UART、复验、回滚与交接。恢复分支另用明确标记为数字测试的合成夹具检查64路相位、STOP、心跳和复位；它不是实测校准文件，也未编入交付。

上述证据说明完整算法、启动门控、显示观察和物理实现通过离线检查，不等于本轮真实声学校准或触觉效果验证。烧录及只读CAL2回读另以实际日志为准。

> 以下保留2026-10-26至28的恢复镜像与下载记录。其默认值、位流及当时验收结论属于原日期，不代替本轮启动校准候选。

## 2026-10-28 15:08 重新下载

屏幕已到，用户要求重新下载显示版。扫描下载器位置561、器件ID `0x0001081B`，核对下文既有合并位流SHA256一致后，15:07运行内存下载成功（User Code `0xDCAB`、Status `0x26230`），未写Flash。日志 `.runtime/display-restore/z50/download-20261028_150713.log` 及同名JSON。

下载后通过正常串口验证64路50 mm修正后相位全部匹配，最终中心(0,0,50) mm、等级1、无调制、REMOTE/IDLE/output=0。未发送START，本次不是恢复此前LOCAL/等级100的薄膜观察设置。回读日志 `.runtime/calibration/probe_50mm_20261028_150754.jsonl`。视频使用板上DVI TX接口，实际屏幕点亮待用户观察，不能把下载与相位回读成功等同屏幕点亮。

2026-10-26新增。原`theory_preview_top`仍然是独立仅预览、物理输出强制关闭的版本。本工程另生成`calibration_display_top`，复用经过验证的发射控制，并在构建时嵌入一份完整实测校准。构建脚本不下载、不发送发射命令。

## 结构与边界

- 发射、串口、按键、相位表及移位输出保留在50 MHz域；视频和声场计算在49 MHz像素域，串化器245 MHz。显示不向发射控制返回暂停、等待或数据，停止操作不等待绘图结束。
- `board_top`新增只读观察端口，导出焦点、平面高度、载波、相位档数、等级、配置编号和运行状态。默认生产构建不接这些端口，未使用逻辑可被裁掉。
- `preview_observer_cdc`通过请求/应答切换位和各两级同步器传递189位完整快照。源端锁存后保持数据，目的端看到同步后的应答才接收；不能逐位独立同步整个坐标总线。
- 对握手的首级同步器数据引脚设置时序例外；数据快照路径约束最长20 ns，并仅豁免该总线的同拍保持检查：接收使能必须等待应答经过两级同步，源端也必须等待下一次请求返回才能更新数据，因此实际锁存不会与源端更新同时发生。最大延迟的建立检查仍保留。其他跨域路径、串化器复位、发射引脚延迟仍做时序检查。不对两个时钟域整体设置假路径。
- 理论显示仍是理想等幅点源模型，按当前目标重新计算理想相位；恢复模式的真实输出使用理论聚焦相位加实测修正，未校准模式使用零补偿。不会把补偿阵元本征误差的相位偏置直接当成理想声源额外整形，也不声称显示的是实测声场。
- 当前不包含70 mm窄焦斑圆周专用修正，也不包含触摸、接收板直连或实测热图。正常协议控制与原有按键逻辑保留；不自动启动声音。
- 新增停止时高度标签跟随当前配置，避免50 mm配置未播放时仍显示默认70 mm。画面有效性与数值更新仍分开处理。

## 构建与检查

```powershell
python fpga/preview/build_display_restore.py --calibration-file D:/pld/output/calibration/8x8_50mm_calibration_20261026_1324.json --calibration-z-mm 50
python fpga/preview/check_display_restore.py --calibration-file D:/pld/output/calibration/8x8_50mm_calibration_20261026_1324.json
python fpga/preview/check_preview.py --part integration
```

构建从真实测量文件校验阵列、位置、等级、来源、1140个有效窗口及完整相位表；拒绝模拟或不完整文件。所有生成内容位于`.runtime/display-restore/z50/`，源码生成采用精确模板匹配，模板变化不符合预期则失败。

测试证据在`.runtime/display-restore/tests/`：

- 不同时钟下连续变化的计数/反码/校验组合，检查快照不撕裂、顺序及重复复位。
- 恢复真实50 mm校准表，模拟正常串口配置和启动，逐项核对64路输出相位；独立核对显示的理想相位、高度和位置。
- 停止、等待未完成图像结束及复位后，确认物理输出关闭、旧场不恢复；显示模块本身的输出始终关闭。
- 原独立预览的自定义多段路径、移动帧、暂停/继续/停止及物理静音回归。

首次物理构建在约束解析阶段失败：源码寄存器名与综合网表原语实例名不同，同步器实际名带`_s0`后缀。已修正首级同步器约束到实际数据引脚，后续构建不得通过省略此错误或整体豁免跨域来绕过检查。

最终状态（2026-10-26 21:53）：逻辑回归通过，完整布局布线时序检查通过，已临时下载并回读64路50 mm修正后相位，输出关闭。未发送START，未写Flash，屏幕实际点亮尚未验证。

第二次构建暴露157个握手数据总线同拍保持违例；握手协议实际禁止同拍采样更新。按官方SUG940 5.4.1仅对held_data→dest_data添加`-hold`例外，继续保留20 ns最大延迟。第三次报告建立/保持违例均0，最大延迟约束仍为Actived，其他时钟路径继续检查。仿真检查跨时钟原子传输、重复复位、真实相位恢复与停止输出全部通过。

最终系统Fmax 52.128 MHz、像素Fmax 51.398 MHz；串化时钟没有可报告Fmax的普通路径。逻辑27229/138240（20%）、寄存器15801（12%）、BSRAM 101/340（30%）、DSP 170.5/298（58%）。位流SHA256 `628cc4f92a75eaf0e98d1d69fd39b8e0f936dedf24e8a92f1fe0b2eda1884bf6`。构建清单、下载日志均在`.runtime/display-restore/z50/`。正常协议回读日志为`.runtime/calibration/probe_50mm_20261026_215321.jsonl`，首次连接失败也保留，详见`../50mm校准记录_2026-10-26.md`。

原普通本地按键逻辑被保留，但本工程没有启用此前70 mm双按键普通/窄焦斑圆形的专用控制，不能套用那一版S1/S2说明。
