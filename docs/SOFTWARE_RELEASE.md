# 软件正式候选与发行说明

更新日期：2026-11-04。候选版本为 **`1.0.0rc1`**，使用Python兼容的预发行版本格式；它不是已经完成整机验收的正式版本。

## 当前发行状态

**默认候选采用`startup`：完整板内启动校准与理论显示。** 镜像保留四步求解、质量判断和真实复测，先在CAL2端点等待测量；达到DONE并收到RELEASE后才进入HAP3普通业务。没有旧补偿文件不阻止这一算法镜像的构建，但构建完成不代表已经完成本轮声学校准或整机验收。

`restore`为单独的旧表恢复路径：目前所有已核对Git分支中未找到指定的完整真实补偿文件，因此该路径仍受阻。既有文档记录不等于数据本身，不能从模拟向量或零表重新拼出“实测补偿”。`uncalibrated`不包含启动校准求解，仅作零补偿数字联调。桌面离线功能和独立MG24工具可以使用；屏幕显示与整机声学效果分别验收，触控尚未实现。

## 固定的软件组合

| 组件 | 候选配置 | 兼容标识与范围 |
| --- | --- | --- |
| Tang发射与显示 | 8×8、10.3 mm、64通道；默认保留启动校准，普通控制与只读理论显示 | startup先CAL2，DONE＋RELEASE后HAP3、`HAP3_STATIC_1`、`cal_profile=1`；restore为`cal_profile=2`；均为`preview_only=0` |
| XIAO MG24接收 | `MG24_USE_BLUETOOTH=0`；USB有线、115200；400 kS/s目标采样率、200点短窗 | MG24_RX1，独立于HAP3；保留原码、采样率、请求编号和质量标志 |
| 桌面整合版 | `python -m desktop_app`默认UserApp；`--developer`工程界面 | 图形、静态相位、数字回读、理论与导入／采集数据分析；新功能按能力声明开放 |

桌面静态相位发送要求`STATIC_PHASE`；读取逐通道修正要求`CAL_READBACK`。只看到`fw_id`不能证明运行的是某一个具体位流，须连同源码提交及产物校验值确认。

MG24目前通过[独立采样／校准工具](../firmware/xiao_mg24/README.md)使用，主界面没有第二个MG24串口会话。主界面原有波形采集要求其当前HAP3设备声明`adc_capture=1`，当前Tang业务镜像没有该能力。屏幕理论场、接收电压、数据集接触压力以及人体触觉不是同一种数据。

启动校准默认采用ADC来源、50 mm测量平面、等级20、参考通道0。独立工具连接Tang的CAL2端点与MG24，按真实窗口完成四步测量、板内求解及复测；成功释放后关闭校准会话，再连接桌面HAP3。仅下载镜像或看到CAL2等待，不表示已经生成有效修正。新测量应保留位置、驱动条件、原码及64项回读。

## 构建入口与容易混淆的产物

[release/software.json](../release/software.json)保存机器可读配置，[tools/build_final_software.py](../tools/build_final_software.py)为正式候选编排入口。先查看其参数：

```powershell
python tools/build_final_software.py --help
python tools/build_final_software.py --calibration-mode startup --validate-only
python tools/build_final_software.py --calibration-mode restore --calibration-file "D:/measurements/原记录.json" --validate-only
```

统一入口默认`--calibration-mode startup`。该模式校验启动校准配置，不要求旧表；`restore`才要求指定真实文件通过完整测量条件与固定SHA-256校验。找到原记录后可用`--calibration-file`指定其备用位置，不能借此绕过哈希检查。`--validate-only`不运行综合，`--prepare-only`只准备对应模式源码；二者均不产生声学测量结果。

已完成的同模式构建可用`--existing-manifest <manifest.json>`打包，重新核对位流、全部暂存源码及校准条件，不重复综合。该选项不能把零补偿产物重标为完整校准镜像。本轮实际Flash与软件验证见[验证记录](VERIFICATION_2026-11-04.md)。

完整固件构建可追加`--gowin "D:/tools/Gowin-1.9.12.04/IDE/bin/gw_sh.exe" --output-root "D:/FPGADev/final-software"`。成功后交付目录为`<输出根目录>/1.0.0rc1/delivery/`，包含带版本名的位流、`manifest.json`及对应模式说明；restore另保留原始补偿文件，startup不能伪造这一附件。桌面包与MG24固件按下表分别构建，当前脚本不宣称已统一生成三者。

| 入口 | 产物用途 | 发布规则 |
| --- | --- | --- |
| `tools/build_final_software.py --calibration-mode startup` | 默认完整启动校准＋控制＋理论显示候选 | 保留四步求解和复测；待真实测量DONE＋RELEASE才进入HAP3，不依赖旧表 |
| 同一统一入口`--calibration-mode restore` | 恢复指定实测补偿的候选 | 严格核对原文件哈希及条件，不执行新校准 |
| `fpga/preview/build_display_restore.py --startup-calibration adc --calibration-z-mm 50 --calibration-level 20` | 启动校准的底层构建 | 保留CAL2完整算法，默认参考通道0；scope来源仅供明确选择的人工测量 |
| 同一底层脚本`--calibration-file …` | 旧表恢复＋控制＋理论显示 | 校验表、高度、参考通道和测量等级；只恢复数据，不能重新校准 |
| 同一底层脚本`--uncalibrated` | 零补偿控制与显示联调 | 不包含启动校准求解，不能将其描述为只缺一张表的完整校准版本 |
| `fpga/preview/build_preview.py`／`--link-test` | 独立理论预览／静态视频测试 | 阵列恒关闭；静态测试不提供串口业务控制 |
| `fpga/board/build.tcl` | 仅控制的`board_top` | 无合并视频、默认无恢复补偿，不是完整整机包 |
| `fpga/board/build_calibration.py` | 校准与专用诊断 | 默认`all`会生成多种配置，按用途明确选取；不混装正式包 |
| `desktop_app/build_user_windows.ps1` | 当前体验台及便携包 | 保留`_internal`；记录构建信息与版本 |
| `desktop_app/build_windows.ps1` | 旧工程包路径 | 保留兼容，不作为当前统一分发入口 |
| `firmware/xiao_mg24/build.ps1 -Mode usb` | MG24有线采集固件 | 默认脚本可构建usb/ble两份，正式组合只选择usb |

构建器只生成文件，不自动下载、不擦写存储器，也不发送START。运行图形需要完整配置、匹配应答与状态回读，再执行独立启动操作。

## 实测补偿与产物身份

restore恢复校验覆盖8×8、10300 µm间距、原始测量位置、参考通道、测量等级、全部64项补偿、完整测量窗口和质量结果。当前校验器接受完整1140窗记录，或注明导入且已复验的384窗记录；模拟、不完整或条件不匹配的文件不能通过。startup在运行时形成新测量结果，构建清单必须注明“算法保留、等待测量”，不能预先标成已恢复或已测量。

restore清单指定的是[2026-10-29记录](../fpga/50mm_16V校准与按键圆形_2026-10-29.md)对应文件：参考高度50 mm、参考通道0、测量等级20、记录驱动电压16 V，并固定其SHA-256。该文件尚未入库；界面的默认播放等级100不能反推历史校准也使用100，更不能为使校验通过而修改原测量条件。startup默认复用50 mm、20级、参考通道0作为待测配置，不表示沿用或恢复了该历史记录。

发布清单应一起记录：

- 版本号、源码提交及构建时间；有未提交修改时如实记录，不能只写基线提交。
- Tang目标器件、顶层、位流SHA-256及建立／保持／恢复／移除时序检查结果。
- 构建模式及运行端点；startup保存新测量条件和原始窗口，restore保存原文件SHA-256、条件及恢复后的64项修正，不能混淆两者来源。
- MG24通信宏、工具包版本与固件校验值；桌面可执行文件、依赖和数据许可。

恢复旧表只证明使用了该记录，不代表本次重新完成声学测量。startup镜像完成下载也只说明算法已经在板，需待本轮真实测量通过。更换阵列、通道接线、驱动条件或工作位置后，须据实际条件复核补偿适用性。

## 验证与未实现边界

```powershell
python -m unittest discover -s desktop_app/tests -v
python -m unittest discover -s firmware/xiao_mg24/tests -v
python -m desktop_app.tests.user_gui_smoke
```

需要完整桌面依赖，包含回放数据使用的PyArrow；缺包不能记为该测试已通过。硬件逻辑仿真、视频原语模型及厂商时序检查见[FPGA导航](../fpga/README.md)。旧验证记录只覆盖其对应版本与测试条件。

restore路径找到清单指定的实测文件、准备匹配源码后，合并显示检查器也须使用同一测量条件。通用底层工具保留历史30级默认值，当前候选必须明确传入20，不能把后续播放等级当作校准等级：

```powershell
python fpga/preview/check_display_restore.py --calibration-file "原实测记录.json" --calibration-z-mm 50 --calibration-level 20 --calibration-reference 0 --output-root "D:/FPGADev/final-software/1.0.0rc1" --iverilog "iverilog.exe"
```

此检查器只做离线仿真，不启动硬件。高度、参考通道和测量等级同时用于校准文件验证；测试目标、源码目录、整阵列预期码和理论显示也使用对应高度。

startup路径还须验证上电CAL2等待、板内求解与复测逻辑保留、DONE后RELEASE切换HAP3，以及切换前输出关闭。源码／仿真检查与真实测量分别记账；运行端点是CAL2还是HAP3应写入验收记录。

本轮手部数据链路已实现：

- 21关节建立正交掌坐标系，计算局部关节坐标和各指几何弯曲特征；刚体旋转／平移不改变局部形态。缺失或退化标注拒绝求取掌坐标系。
- 压力序列以加权边界建立固定`PressureMapping`，避免逐帧重新居中／缩放抹去接触移动；支持分析与最多24帧的离线静态相位计划，不自动连接或播放设备。
- 连续求解后执行64档离散精修，最终损失按实际相位码重算，保留连续解、量化基线、优化遍数、接受次数和细网格指标。训练目标改善不保证独立细网格指标或实测触感改善。
- ContactPose原始`annotations.json`可导入单帧抓握，保留21关节、米制物体坐标和左右手。双手必须明确选择；正确反解移动手的`hTo`，不声明压力或网格存在，不把接触值换算为kPa。

2026-11-04对官方ContactPose公开样本中的`full39_use/wine_glass`和`full39_use/camera`完整注释进行了9组帧／左右手抽查，与独立四元数和4×4逆矩阵参考的最大坐标差为`6.36×10⁻⁹ m`，属于float32存储误差。自动测试包含官方关节数值节选、合成刚体变换、左右手歧义与缺失压力检查；来源、MIT数据许可链接及节选声明见[导入模块](../desktop_app/hand_datasets.py)和[测试](../desktop_app/tests/test_hand_datasets.py)。未加载或分发MANO模型，不将上述公开数据分析宣称为实时手跟踪、医学验证或本机声压测量。

本候选不包含屏幕USB触摸主机、触控翻页、接收端直接连Tang、实时手掌跟踪或触感反馈闭环。STM32原型、4×4实验、手机应用、蓝牙与窄焦斑研究仍按[仓库地图](REPOSITORY_MAP.md)作为辅助内容保留。

真实样本获取、界面操作和离线算法对照见[手部数据工作流](HAND_DATA_WORKFLOW.md)。
