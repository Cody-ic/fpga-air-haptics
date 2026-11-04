# 手部状态与接触数据的接入依据

更新日期：2026-11-04。本文用于触见的离线触觉内容重构与数据接入，不将公开数据中的参与者、采集设备或识别模型记为本项目自行完成。

## 当前主数据源

EgoPressure 同步记录手部姿态和接触压力，适合研究“同一动作的手形、接触区域与刺激目标如何对应”。项目已经接入官方序列的手部顶点、关节和压力网格，按原始帧号对齐，保留缺帧有效标记。压力数据来自接触传感器，属于内容源；超声输出的预测场与接收端的电压测量分别记录。[官方论文](https://openaccess.thecvf.com/content/CVPR2025/html/Zhao_EgoPressure_A_Dataset_for_Hand_Pressure_and_Pose_Estimation_in_CVPR_2025_paper.html)

| 数据源 | 可用信息 | 本项目可使用的任务 | 接入与授权要点 |
| --- | --- | --- | --- |
| EgoPressure | 手部网格、21关节、平面压力、同步帧号 | 掌平面与手形描述、动作回放、压力区域提取、序列触觉素材编译 | 官方 Parquet 或项目 NPZ；数据 CC BY-NC-SA 4.0，MANO 派生注释另受 MANO 许可约束 |
| ContactPose | 已接入21关节与物体变换；数据集另含接触分布、RGB-D | 读取抓握手形、统一左右手和物体坐标，检验姿态处理；接触图为后续扩展 | 注释等非模型数据和工具MIT；接触值为无量纲热接触观测，不能当作 kPa；物体模型各有许可 |
| HOT3D | 多视角手部与物体三维姿态、模型、时间戳 | 验证手部坐标规范化、姿态覆盖与序列处理；研究离线动作条件 | 完整数据需按官方流程登记下载；工具 Apache 2.0，数据有单独许可；没有接触压力标签 |

EgoPressure已接入姿态与压力，ContactPose本轮已完成官方`annotations.json`的关节导入和回放，HOT3D目前为明确用途的扩展数据源。只有导入器、真实样本与验证记录一起存在时，才将其列为已接入功能。[EgoPressure 官方数据卡](https://huggingface.co/datasets/eth-siplab/EgoPressure)、[ContactPose 官方项目](https://contactpose.cc.gatech.edu/)、[HOT3D 官方项目](https://facebookresearch.github.io/hot3d/)

## 数据语义与坐标

- EgoPressure 的 `pressure.parquet` 含原始105×185网格，`annotation.parquet` 含778×3顶点及21×3关节。原始计数除以1736得到牛顿；单元间距1.25 mm。世界坐标以压力板为基准，手部几何采用米；进入阵列求解前显式换为毫米并记录变换。不要用显示纹理外框重新推算单元受力面积。[官方数据格式](https://github.com/eth-siplab/EgoPressure/blob/master/docs/DATA.md)
- ContactPose 的物体网格顶点颜色编码0至1接触值；零值可能表示热像未观测，不能一律解释为“没有接触”。该信息适合接触权重，不提供绝对力或压力标定。[官方格式说明](https://github.com/facebookresearch/ContactPose/blob/main/docs/doc.md)
- HOT3D 提供手部与物体轨迹，其工具支持完整VRS格式与Webdataset片段。接入时需要选择同一坐标系和时间戳，不能由手部与物体接近直接伪造实测压力。[官方工具与下载流程](https://github.com/facebookresearch/hot3d)

原始数据、变换参数、有效标记、目标场和量化后相位应保存在同一次离线计划中。回放与导出不能自动触发阵列；硬件提交仍由现有完整相位表协议执行。

ContactPose导入器按手相对物体的运动状态处理：静止手保留原关节坐标，运动手通过`hTo`逆变换转到米制物体坐标，显式选择左手或右手。该入口只提供真实关节标注，记录`pressure_valid=False`和`vertices_available=False`；不会构造压力或假定手部表面网格。官方完整注释与独立4×4矩阵参考的约6.36×10⁻⁹ m差异属于浮点实现一致性，不是人体测量精度。[官方许可说明](https://github.com/facebookresearch/ContactPose/blob/main/README.md#licensing)、[MIT许可全文](https://github.com/facebookresearch/ContactPose/blob/main/LICENSE.txt)

## 小规模真实样本复查

除内置的 `p_001_press_palm_low_x5_right` 掌心轻压序列外，可使用同一参与者的 `p_001_index_press_pull_x5_right` 检查接触位置随时间移动的处理。两份文件来自官方数据集，下载总量约13.8 MB，无需下载视频。原始帧号用于关联姿态和压力；缺失标注保留有效性，不插值为实测数据。

| 官方文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| [annotation.parquet](https://huggingface.co/datasets/eth-siplab/EgoPressure/resolve/main/data/p_001/p_001_index_press_pull_x5_right/annotation.parquet) | 12,620,091 | `c6081d34de38202d7c6cfa3d566aac5c3d8b85ec3482daf0934aac8b376fa5ec` |
| [pressure.parquet](https://huggingface.co/datasets/eth-siplab/EgoPressure/resolve/main/data/p_001/p_001_index_press_pull_x5_right/pressure.parquet) | 1,166,658 | `ab7063adb72a986d0e3594c0a85ccd849b29eb216fe5df791b054aa10728c97c` |

压力编译为整段动作拟合固定原点与尺度，避免逐帧自动居中消除滑动位移。手部特征独立记录掌平面和几何弯曲量；相位计划保留原始压力校验值、源帧与64路相位，便于复算。数据仍遵守原始非商业共享许可，MANO相关模型不随项目再分发。

## 医学场景的研究价值

无菌操作区中的影像浏览具有明确的非接触交互需求：操作者需要切换图像、调整视图，同时避免直接接触普通键鼠等非无菌控制表面。已有研究在贴近手术室的任务中评估了无接触影像交互的可用性，表明这类任务需要同时考虑手势负担、误触发和反馈清晰度。[外科医生可用性研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC6989265/)、[随机交叉比较研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC4833285/)

触见可据此研究空间选择、方向引导和操作确认的空中触觉编码。公开手部数据用于构造可重复的姿态与接触条件，阵列模型用于生成反馈目标，接收测量用于检查实际输出。此路径可支撑非接触人机交互和医学教学研究；临床使用仍需另行验证无菌流程、声学暴露、可靠性和具体任务效果，不宣称已经减少感染或改善治疗结果。

## 对外表述

推荐使用“面向灵巧交互的多关节手部状态与接触压力映射”“基于公开手部数据的触觉内容重构”。这能准确表达数据、算法和阵列执行之间的关系。项目当前没有实体机器人灵巧手，也没有实时医学手势识别系统；报告应按实际接入、离线验证和研究应用分别陈述。
