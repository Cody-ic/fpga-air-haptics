# EgoPressure 样本来源与许可

作者：Yiming Zhao, Taein Kwon, Paul Streli, Marc Pollefeys, Christian Holz。
论文：EgoPressure: A Dataset for Hand Pressure and Pose Estimation in Egocentric Vision，CVPR 2025，27727–27738。

- 数据源：https://huggingface.co/datasets/eth-siplab/EgoPressure
- 原序列：`data/p_001/p_001_press_palm_low_x5_right/`
- 来源字段：annotation.parquet 的 frame、has_annotation、hand_side、vertices、joint_position；pressure.parquet 的 frame、force。
- 本地变换：按原始帧号连接，保留 float32 数值、缺失掩码，压缩为 NumPy NPZ。没有重建手部表面、生成新动作或修改压力。
- 原始数据与本目录内转换样本均按 **CC BY-NC-SA 4.0（署名—非商业性使用—相同方式共享）** 使用与分发：https://creativecommons.org/licenses/by-nc-sa/4.0/ ，法律文本：https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode 。
- 官方数据卡另外声明：MANO 衍生标注同时受 MANO 许可约束：https://mano.is.tue.mpg.de/license.html 。
- 此目录不包含单独授权的 MANO 模型、三角面或 UV 文件。应用程序源代码与数据许可分开。

下载日期：2026-10-29。随附 DATASET_README.md 保留官方数据卡和论文引用。source-manifest.json 记录原始分片与转换文件的 SHA-256 校验值。
