# 手部数据与触觉内容编译

本流程处理公开离线数据，不连接设备、不估计现场手部位置。目的在于把同一动作的手形、接触区域和可执行相位关联起来，供内容设计与算法比较。

## 图形界面

启动`python -m desktop_app`，进入“三维回放”。内置样本支持播放、逐帧和关节显示；“导入序列”支持EgoPressure的NPZ／Parquet及ContactPose的单个`annotations.json`。双手抓握会弹出左右手选择；ContactPose仅显示真实关节，不显示压力或压力板。

下方“导出动作分析…”保存21关节形成的正交掌坐标系、局部关节坐标、各指几何弯曲量和缺失标记。EgoPressure额外保存接触力、压力峰值和映射后的压力中心。几何弯曲量不是临床关节活动度。

有压力数据时，“发送当前帧压力…”打开三图预览。默认“保持整段动作的位置与大小”，使用整段有效压力的加权1%～99%边界拟合固定原点与尺度；逐帧使用同一变换，避免自动居中抹去滑动。该映射不等于把真实手掌配准到阵列。

“导出动作相位计划…”均匀选择最多12个有效压力帧，逐帧提取最多两个接触区域并生成64路相位。计划包含原始帧号、压力校验值、映射、手姿特征、完整相位表和数值指标；无接触帧明确跳过。此文件不是硬件连续播放协议。单帧发送仍需用户主动点击，且发送不启动输出。

## 可重复的命令行流程

从仓库根目录执行，先安装`desktop_app/requirements.txt`。

```powershell
# 内置样本：整段分析、6帧独立静态相位计划
python -m desktop_app.pressure_sequence --output .runtime/palm-analysis.json
python -m desktop_app.pressure_sequence --compile-frames 6 --output .runtime/palm-phases.json

# 另一段真实动作：先按固定哈希下载官方注释与压力
python tools/fetch_hand_sample.py
python -m desktop_app.pressure_sequence --input .runtime/datasets/p_001_index_press_pull_x5_right --output .runtime/index-analysis.json

# 只比较量化后优化，不涉及传输或声学测量
python tools/benchmark_hand_pipeline.py --output .runtime/pressure-comparison.json
```

内置掌心样本366帧中318帧有有效手姿；新增食指按压滑动样本445帧中387帧有有效手姿。缺失标注不插值为测量。两个样本来自同一公开数据集，不构成跨人群泛化验证。

## 算法与指标解释

连续相位求解后，按实际64档相位码逐通道尝试相邻两档，最多两轮。每次只接受共同目标函数的严格下降，并重算最终相位表的损失；连续解、直接量化基线、接受次数和独立细网格误差均保留。

2026-11-04内置样本6帧对照中，目标函数下降0.0416%～0.1100%，每帧接受5～14次码值调整。细网格误差有1帧略增，说明优化目标改善不保证所有指标改善。该结果未验证真实声压或人体触觉增强。比较脚本保留相位表和参数，可在不同环境复算；单次耗时不作为加速结论。

原始接触压力单位为kPa；目标场及预测场为相对声压幅值；接收板采集为电压。三者不直接等值转换。数据来源、许可与医学应用依据见[研究说明](research/HAND_DATASETS_2026-11-04.md)。
