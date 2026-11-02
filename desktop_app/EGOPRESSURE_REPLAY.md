# EgoPressure 三维回放

日期：2026-10-29。

## 本轮实现

默认首页是独立三维回放工作区：浅灰场景、蓝色手部顶点、压力板、右侧实测热图和底部时间轴。使用 Tk 内嵌 OpenGL，保留原有掌心绘图、设备播放与实验页面。

内置官方 `p_001_press_palm_low_x5_right`：366 帧，30 帧/秒，318 帧有手部标注。时间轴最后一帧为 12.167 秒，含全部帧的采样时长为 12.2 秒。初始停在总接触力最大的有效帧，方便观察；点击播放后由当前位置继续，末帧点击播放会从头开始。离开回放页自动暂停。

- 左键拖动旋转，右键拖动平移，滚轮缩放，双击或“复位视角”恢复；提供俯视视角。
- 播放、暂停、上一帧、下一帧、拖动时间轴与 0.25/0.5/1/2 倍速度。
- 原始 778 个手部拟合顶点，21 个关节，连线可关闭。没有 MANO 三角面模型，所以当前不是完整表面渲染，也没有贴手压力纹理。
- 接触压力实测显示在板上和右侧热图中，颜色固定为 0–20 kPa，超过 20 kPa 饱和但右侧数值仍显示真实峰值。总接触力单位 N。
- 导入选择官方 `annotation.parquet` 或 `pressure.parquet`，两者必须在同一目录；也支持本程序的 `.npz` 回放格式。读取失败显示原因并保留原序列。

## 数据与设备边界

数据读取使用独立后台线程，渲染在界面线程。回放组件不接收设备会话，不调用串口、蓝牙或 FPGA 指令。切换回放页不会修改草图，也不会开始硬件输出。已有硬件会话的停止输出仍由顶部按钮控制。

2026-10-30 增加独立的“发送当前帧压力…”入口：按钮暂停回放，并将当前帧副本交给单帧窗口。只有用户在该窗口明确点击发送，才经主程序会话提交静态相位表；普通回放操作仍不发送指令。操作及验证见 [压力单帧说明](PRESSURE_SINGLE_FRAME.md)。

手部显示的是数据集参与者的拟合动作，不是实时追踪用户。压力来自接触传感器，不是超声声压或触觉强度。原有实际轨迹执行和相位计算仍由 FPGA 承担。

使用原始帧编号对齐两个分片；缺失手部时清空顶点，缺失压力时使用灰色板面和空数值，不使用上一帧冒充。双流均缺失的中间帧也明确保留。播放时间依据原始编号 / 30，不按文件行数压缩。

原始坐标单位为米。仅对显示做绕 x 轴 180 度旋转 `(x,y,z) → (x,-y,-z)`，保持右手坐标系，原始数据不变。压力板标定范围为 240×137.5 mm，网格 105×185；列从 −x 到 +x，行从原始 −y 到 +y。counts / 1736 得到 N，再除以 1.25 mm 单元面积并换算为 kPa。显示转换与原始数据依据官方资料，不使用相机图像猜测配准。

## 运行与验证

```powershell
python -m desktop_app
python -m desktop_app.tests.replay_gui_smoke --scale 1
python -m desktop_app.tests.replay_gui_smoke --scale 1.25
python -m desktop_app.tests.replay_gui_smoke --scale 1.5
python -m desktop_app.tests.replay_gui_smoke --scale 2
```

打包版提供 `--replay-self-test <输出目录>`，验证真实数据、内嵌渲染、播放操作、缺失帧、相机、导航暂停、草图保留与零设备命令。报告及截图保存于指定目录。源码结果默认在 `.runtime/replay-ui/`。自动缩放检查通过 Tk 字体缩放模拟，不等于跨显示器热切换测试。未连接实板，未进行声学测量。

## 来源及许可

- 官方数据与许可：https://huggingface.co/datasets/eth-siplab/EgoPressure
- 字段、坐标及单位：https://github.com/eth-siplab/EgoPressure/blob/master/docs/DATA.md
- MANO 文件要求：https://github.com/eth-siplab/EgoPressure/blob/master/mano_models/README.md

内置数据遵守 CC BY-NC-SA 4.0，MANO 衍生标注同时受其单独许可约束。转换说明、完整作者署名、官方数据卡和源文件校验值在 `assets/egopressure/`。未附带单独授权的 MANO 模型文件。
