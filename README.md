# 触见：超声触觉图形显示器

面向基础图形与方向信息的非视觉表达，以 Tang 上的 FPGA（现场可编程门阵列）执行轨迹、相位计算和阵列同步输出，配合接收采样与电脑工作台开展验证。

> **当前候选版本：1.0.0rc1，更新于2026-11-04。** 正式目标为 **8×8、10.3 mm间距**。默认构建保留完整板内启动校准算法，先等待真实测量，校准完成并释放后进入普通业务；无需先找回旧表才能构建。旧实测表恢复属于另一条严格校验的路径。当前候选尚待本轮声学校准与整机验收，不能将算法已构建等同于已获得实测补偿。

## 从这里开始

- [软件版本、构建与验收](docs/SOFTWARE_RELEASE.md)：正式候选的三个软件组成、补偿要求和交付边界。
- [仓库地图与分支关系](docs/REPOSITORY_MAP.md)：区分正式候选、测量工具、历史原型与研究实验。
- [桌面工作台](desktop_app/README.md)：使用者入口、绘图、三维回放、静态压力帧与声场对比。
- [FPGA工程](fpga/README.md)：8×8控制与显示、构建配置、协议及仿真导航。
- [独立接收采集](firmware/xiao_mg24/README.md)：XIAO MG24接收固件、接线、原始窗口与校准工具。

## 当前软件组成

| 部分 | 当前职责 | 关键边界 |
| --- | --- | --- |
| Tang控制与显示 | 完整四步启动校准、板内轨迹、8路并行相位求解、64路同步发布与移位输出；屏幕显示理论场和状态 | 默认先CAL2测量，DONE＋RELEASE后才进入HAP3；理论显示不等于实测声场 |
| MG24接收固件 | 独立接收板采样、200点窗口、40 kHz分量与质量标记 | 默认USB有线；协议为MG24_RX1，不是HAP3发射控制器 |
| 桌面整合版 | 草图编译、完整配置与回读、三维数据回放、压力帧求解及测量分析 | 默认使用者界面；MG24直连采样／校准目前使用独立工具 |

电脑提交完整图形或静态相位表，Tang自主执行输出。单点接收窗口用于测量和阶段性校准；当前没有手掌位置跟踪或面向触感的实时闭环。屏幕触摸主机、触控翻页及MG24直接连接Tang尚未实现。

固件分为三种：`startup`保留板内四步求解和复测，等待新测量；`restore`嵌入已核验的旧修正值，不重新校准；`uncalibrated`不包含启动校准求解，仅作零补偿联调。详细流程见[发行说明](docs/SOFTWARE_RELEASE.md)。

## 离线手部数据与触觉目标

手部数据处理已实现21关节掌坐标系、几何弯曲特征、整段压力的固定位置／尺度映射，以及独立静态相位帧导出。压力求解同时约束目标区域与背景，并在量化后进行64档离散精修；指标按实际发送的相位码重算。动作导出是离线计划，不是设备端连续动作播放器。

EgoPressure用于真实手姿和接触压力回放；ContactPose支持导入原始`annotations.json`中的单帧抓握关节，双手需明确选择，不将接触图解释为压力。两者的坐标、数据缺失与来源标记分别保留。功能、导入方式及验证范围见[桌面手部数据说明](desktop_app/README.md#2026-11-04-手姿与压力序列处理)。这些算法和公开数据验证不等于实测触觉增强或医学效果。

实际操作、官方样本下载与可重复对照见[手部数据工作流](docs/HAND_DATA_WORKFLOW.md)。

## 运行与构建

在仓库根目录、安装完整依赖的Python环境中运行：

```powershell
python -m pip install -r desktop_app/requirements.txt
python -m desktop_app
python -m desktop_app --demo
python -m desktop_app --developer
```

默认不连接设备；Demo不自动播放。正式候选构建入口及所需参数查看：

```powershell
python tools/build_final_software.py --help
```

发布配置以 [release/software.json](release/software.json) 为准。桌面便携包的直接构建入口是 `desktop_app/build_user_windows.ps1`；构建和打包均不自动下载固件或启动发射。不同用途的FPGA产物详见[构建选择表](docs/SOFTWARE_RELEASE.md#构建入口与容易混淆的产物)。

## 开发与验证

```powershell
python -m unittest discover -s desktop_app/tests -v
python -m unittest discover -s firmware/xiao_mg24/tests -v
python -m desktop_app.tests.user_gui_smoke
```

硬件逻辑仿真和Gowin构建见[FPGA说明](fpga/README.md)。模拟设备、数字回读、理论计算、接收电压和触觉辨识是不同证据；各项历史验证记录只证明其注明的版本和条件。

## 硬件与历史资料

- [PCB导航](hardware/README.md)：发射、接收与降压电路板；[机械设计](hardware/mechanical/README.md)保留结构与打印资料。
- [设计思路](设计思路.md)、[研究提案](docs/proposals/2026-10-14-position-aware-haptics-calibration.md)与[原始选题调研](FPGA赛道选题建议%281%29.md)保留设计背景，历史计划不代表当前实现。
- [STM32F103原型](firmware/stm32f103c8_cube/README.md)、[F411原型](firmware/nucleo_f411re/README.md)和[Android测试应用](mobile_app/README.md)属于辅助路线，不列入当前正式整机软件。
- 贡献约定见 [AGENTS.md](AGENTS.md)。保留原源码路径及历史命名，避免破坏导入、工程约束和已有链接。
