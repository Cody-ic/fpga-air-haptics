# 仓库地图与集成关系

更新日期：2026-11-04。当前软件候选为`1.0.0rc1`，发行状态见[软件发行说明](SOFTWARE_RELEASE.md)；配置以[版本清单](../release/software.json)为准。

## 正式候选源码

| 路径 | 职责 | 主要入口 |
| --- | --- | --- |
| `desktop_app/` | 使用者工作台、草图与路径、数据回放、压力帧求解、设备状态及声场分析 | `python -m desktop_app`；`--developer`打开工程工具 |
| `fpga/rtl/` | Tang板级控制、协议、轨迹、相位、校准及8×8移位输出 | `board_top.v`，由合并构建包装 |
| `fpga/preview/` | 理论声场计算、显示观察器与视频输出；合并完整启动校准或旧表恢复 | `build_display_restore.py --startup-calibration adc`；旧表恢复另选`--calibration-file` |
| `firmware/xiao_mg24/receiver/` | 独立接收采样与窗口分析 | `receiver.ino`，正式候选使用`MG24_USE_BLUETOOTH=0` |
| `release/`与`tools/` | 版本配置、正式候选预检及构建编排 | `software.json`与`build_final_software.py` |

正式发射目标为8×8、10.3 mm间距。默认startup镜像包含完整四步校准，先CAL2等待新测量，DONE＋RELEASE后才进入HAP3；没有旧表不阻止算法构建。restore只恢复匹配条件和哈希的历史实测值，不重新校准；uncalibrated裁掉启动校准求解，仅作零补偿数字联调。当前候选的算法构建、下载、实测校准和整机验收分别记录。

## 测量、校准与验证工具

- [MG24采样与校准](../firmware/xiao_mg24/README.md)：`receiver_client.py`读原始窗口，`fpga_calibration_gui.py`与`calibrate_fpga.py`连接发射和接收设备，组织逐通道测量、修正与复测。
- [FPGA校准协议](../fpga/CALIBRATION_PROTOCOL.md)及[构建器](../fpga/board/build_calibration.py)：人工、自动、恢复和单通道诊断是不同配置，不能凭文件名相近混用。
- [完整启动校准与显示](../fpga/preview/build_display_restore.py)：`--startup-calibration adc`保留板内求解和复测；经独立工具转发MG24真实窗口后释放进入普通业务。它与`--calibration-file`恢复旧数据、`--uncalibrated`无求解联调是三个独立选项。
- `fpga/tb/`：控制、扫描、相位、移位时序、停止及静态相位的逻辑仿真；`desktop_app/tests/`与`firmware/xiao_mg24/tests/`验证对应软件。
- `fpga/preview/build_preview.py --link-test`：静态视频链路诊断，物理阵列输出恒禁用。普通独立预览同样不是业务发射镜像。

桌面主界面的HAP3采样能力与独立MG24_RX1连接不同。目前主界面没有第二个MG24串口会话；不能把独立工具已有采集成绩写成主界面已经直连。

## 手部数据与数值处理

| 路径 | 已实现范围 |
| --- | --- |
| `desktop_app/egopressure.py`、`replay_panel.py` | 原始帧号连接、缺失标记、数据源元信息、离线回放与单帧选择 |
| `desktop_app/hand_datasets.py` | ContactPose原始关节注释导入；米制物体坐标、左右手选择与`hTo`逆变换；无压力和网格 |
| `desktop_app/hand_pose.py` | 从21关节建立正交掌坐标系、局部关节与几何弯曲特征，不是实时识别或临床测量 |
| `desktop_app/pressure_sequence.py` | 整段压力共用固定映射、手姿／压力分析、最多24帧的离线静态相位计划 |
| `desktop_app/pressure.py`、`pressure_panel.py` | 接触区域简化、连续相位求解、64档量化后离散优化与显式单帧提交 |
| `desktop_app/tests/test_hand_datasets.py`、`test_hand_sequence.py`、`test_pressure.py` | 官方数据数值节选、变换、掌坐标不变性、固定映射与实际相位码目标函数检查 |

ContactPose导入只使用公开关节注释，不加载MANO模型或物体接触贴图；不把0～1接触值换算为kPa。EgoPressure随附数据的[许可与来源](../desktop_app/assets/egopressure/NOTICE.md)独立于程序许可。完整真实注释的本地抽查记录属于验证产物，不自动成为内置样本或发行附件。

[手部数据工作流](HAND_DATA_WORKFLOW.md)提供界面操作、官方样本下载、序列分析及离散优化对照的实际入口。

## 历史原型与研究实验

| 路径 | 定位 |
| --- | --- |
| `firmware/nucleo_f411re/`、`firmware/stm32f103c8_cube/` | MCU先行验证与调试，保留原工程，不参加当前正式软件组合 |
| `mobile_app/`、桌面BLE适配及MG24蓝牙模式 | 手机／无线辅助路线，各自验证边界见目录说明 |
| `hardware/Haptics_4x4_*/`、`hardware/arrays/` | 历史小阵列PCB、几何示例与原型配置；不替代8×8实际接线 |
| `experiments/static_field/`、`fpga/board/build_edm_*.py`及带日期实验文档 | 离线算法研究与限定条件实验；不能将预测改善直接写成实测效果 |
| `hardware/`、`hardware/mechanical/` | 电路与结构源文件、打印及装配资料；按各自版本维护 |
| `docs/proposals/`、`设计思路.md`及原始选题调研 | 架构背景、提案与历史决策，不是当前实现清单 |

源码暂不物理迁移。Python导入、工程约束、数据路径和已分享链接继续使用原路径；通过导航和发布清单区分用途。

## 分支与待合并请求

本表记录2026-11-04本轮核对的集成关系，不将`main`自动视为最新业务代码。

| 分支／请求 | 已核对基线 | 处理方式 |
| --- | --- | --- |
| 远端`main` | `deb3fa1`，2026-10-14历史集成 | 旧入口；不能覆盖后续业务集成 |
| `feat/mg24-receiver` | `822335a` | 当前桌面、静态压力与校准能力的业务基线 |
| `codex/desktop-integrated`／[PR #22](https://github.com/Cody-ic/fpga-air-haptics/pull/22) | `b732414`，包含上述基线 | 当前使用者体验台整合路线，尚待合并 |
| `codex/fpga-display-recovery` | 从`b732414`继续 | 本轮校准与显示、手部数据处理及发行候选；验证见[本轮记录](VERIFICATION_2026-11-04.md) |
| [PR #17](https://github.com/Cody-ic/fpga-air-haptics/pull/17)、[PR #18](https://github.com/Cody-ic/fpga-air-haptics/pull/18)、[PR #19](https://github.com/Cody-ic/fpga-air-haptics/pull/19) | 并行的较早集成路线 | 先比较差异与依赖，提取仍适用的改动，不能整包覆盖当前源码 |

个人下载、聊天附件、局部未提交文件和`.runtime/`不属于GitHub版本清单。整理仓库不删除这些本地材料，也不把它们自动加入提交。
