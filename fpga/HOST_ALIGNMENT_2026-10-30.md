# 2026-10-30 上位机协议对齐与压力单帧板端实现

后续实板阶段：用户另行授权后已完成临时下载和真实串口验证，最终停止输出，详见 [2026-10-30 实板记录](BOARD_PRESSURE_VALIDATION_2026-10-30.md)。下文“未连接硬件”指本文件原始代码与仿真阶段。

## 范围

按最后确认的范围，仅修改 FPGA（Field-Programmable Gate Array，现场可编程门阵列）代码、板端仿真和说明。上位机本轮改动已撤回，保留原有工作区修改。不连接串口、不下载、不驱动真实阵列。

既有绘图继续使用 HAP3 的 CONFIG / MODE / START / PAUSE / STOP / SNAP，保留原有字段和错误码。新增压力单帧是板端完整相位表接口；**现有上位机尚无这项新接口的生成、发送和静态显示入口**。此次用仿真报文验证它，不将完成板端接口描述为上位机整套压力功能已完成。

后续更新：用户随后授权补齐上位机入口；现已加入当前数据帧的离线生成、预览、发送及静态回读识别。该后续改动及验证见 [上位机压力单帧说明](../desktop_app/PRESSURE_SINGLE_FRAME.md)，以上范围描述保留为板端阶段记录。

## 板端变化

- HELLO 增加 `fw_id=HAP3_STATIC_1`、`STATIC_PHASE,CAL_READBACK` 能力、`cal_profile`、`preview_only`、`carrier_fixed`、`steps_fixed`。心跳期限从实际参数传入。
- `cal_profile=0/1/2` 分别为无补偿、本次启动校准、恢复已保存补偿。校准版本要求 40000 Hz / 64 相位级；完整补偿表由 STATE 的 `phase_offsets` 回报。具体校准文件及摘要仍记录于构建清单，不把恢复表称为本次重新测量。
- 接收行容量提升到 8192 字节，草图仍为最多 32 段、256 点、4096 字节路径文本。实际阵列保持 8×8、10.3 mm；不增加硬件通道。
- STATE 发送前复制路径文本和相位快照，避免发送期间新配置替换原文，造成旧版本号配上新路径。
- STOP 在图形编译期间也有效；即使与编译提交同拍，也撤销本地自动播放，并保留 STOP 与 CONFIG 各自的应答。
- 恢复构建增加显式 `--calibration-level`，使交接中的等级 20 测量记录可以通过完整验证；默认仍为历史等级 30。未修改测量文件，也未放宽质量检查。
- 压力模式不使用运动焦点相位计算结果覆盖输出表。显示观察接口关闭单焦点理论场的有效标志，避免板载预览显示假焦点。
- 校准配置兼容性比较寄存一拍，缩短通向外部输出使能引脚的组合路径；配置仅在待机时替换，暂停/停止/段间关闭仍直接门控。

## 压力单帧协议

先 HELLO 并检查 `STATIC_PHASE`，然后发送完整 CONFIG，并增加 `phase_table`。旧固件会忽略未知字段，因此没有能力声明时绝不能尝试直接发送。

| 字段 | 约定 |
|---|---|
| `phase_table` | 每路两位大写十六进制；64 路共 128 字符；每字节 00～3F |
| 排序 | 首字节为通道 0；沿用 `ROW_MAJOR_XY`，与现有阵列行列一致 |
| 相位含义 | 未加入硬件校准的名义相位；板端只叠加一次补偿并对 64 取模 |
| 必需配置 | `shape=POINT`、`carrier_hz=40000`、`phase_steps=64`、两个路径字段均为 NONE |
| 阵列校验 | `hw_rows/hw_cols/hw_pitch_um/mapping` 必须匹配板端 |
| 工作状态 | 仅 REMOTE + IDLE 接受；完整验证和提交成功后才 ACK、增加 rev |
| 后续操作 | CONFIG 不启动；START 开启，PAUSE 关闭且保留表，START 恢复，STOP 关闭 |
| 切回绘图 | 发送不含 `phase_table` 的普通 CONFIG；本地切换图形也退出静态模式 |

STATE 保留全部旧字段，新增 `static_mode=0/1`、`phase_table=NONE/完整表`；`phases` 始终是应用补偿后的数字相位，`phase_offsets` 为独立补偿表。读取者必须根据 `static_mode` 区分静态分布与单焦点扫描，不能从静态模式的 `shape=POINT` 推断输出为单焦点。

错误长度、重复字段、非十六进制、超过 63、阵列不匹配、频率/相位级不合、静态表混入扫描路径均拒绝；生效表和版本号保持原值。部分数据不会写入运行输出表。

驱动等级仍控制既有全阵列占空比；`mod_hz` 仍控制包络。首版不提供逐路独立幅值、压力视频流、板上优化求解或绝对接触压力复现。静态目标高度只是生成名义相位时的几何参数；板端不会重新求解这张表。

## 仿真与构建复现

以下命令只运行本地模拟与编译。`--iverilog` 应替换为本机 Icarus Verilog 仿真器路径。

```powershell
python fpga/tb/gen_vectors.py
python fpga/board/check_rtl.py --iverilog <iverilog.exe>
python fpga/board/check_rtl.py --iverilog <iverilog.exe> tb_stop_commit
python fpga/board/check_alignment.py --iverilog <iverilog.exe>
python fpga/board/check_alignment.py --iverilog <iverilog.exe> --pressure-frame
```

`check_alignment.py` 使用现有上位机的编码和解析函数作为协议参考，把报文送入真实 RTL（Register Transfer Level，寄存器传输级）串口接收逻辑，再解析串口输出；不启动或测试上位机界面。

- 普通对齐测试：32 条命令、58 帧回读、256 点/32 段草图、3095 字节配置，静态表、非法配置、暂停/恢复/停止、模式切换和心跳超时。
- 压力向量：`tb/vectors/pressure_frame_325.json`，内置样本第 325 帧，数组索引 324，10.8 秒；来自此前离线参考算法，8×8、50 mm、40 kHz、64 级相位。记录数据集摘要；不含校准。
- 模拟补偿使用非均匀测试表，核对全部 64 路驱动相位与串口回读一致，避免零补偿掩盖错位或重复补偿问题。
- `tb_stop_commit` 专门检查 STOP 与本地/远程编译完成及编译失败同拍的竞争。
- 旧板级回归台从历史 4×4 直出接口调整为当前 R2 的 8×8 串化输出板；串口加速仅用于缩短仿真时间，生产波特率保持 115200。
- Icarus 执行两个控制端到端回归时，使用 `SIM_SCALE=10` 同比缩放模拟时钟、扫描节拍和观察时长；默认测试台参数仍为 50 MHz。输出频率及传播相位测试单独保持原始时钟，真实 50 MHz 的实现约束由布局布线检查。

结果保存在 `.runtime/alignment*/` 和 `.runtime/rtl-alignment/`。最终构建单独放在 `.runtime/host-alignment-build/restore_z50/`，使用 `8x8_50mm_16v_calibration_20261029_1652.json`，校准时等级为 20。具体源文件摘要、校准来源和时序结果以该目录 `manifest.json` 为准。生成位流不等于下载，数字仿真通过不等于真实声场测量通过。

```powershell
python fpga/board/build_calibration.py --mode restore `
  --calibration-file D:/pld/output/calibration/8x8_50mm_16v_calibration_20261029_1652.json `
  --calibration-z-mm 50 --calibration-level 20 `
  --output-root .runtime/host-alignment-build
```

## 最终验证结果

2026-10-30 完成，未连接硬件、未下载：

| 检查 | 结果 |
|---|---|
| FPGA 回归测试台 | 13 个全部通过，含停止/编译提交同拍竞争 |
| 最终源码协议对齐仿真 | 32 条命令、58 帧回读通过；最大草图 32 段、256 点 |
| 内置压力第 325 帧 | 10 条命令、18 帧回读通过；64 路补偿后驱动与回读一致 |
| 10 月 29 日真实 16 V 校准记录恢复仿真 | 通过；验证回读与不兼容配置拒绝，不作本次声学复测 |
| 完整综合、布局布线、位流生成 | 通过；50 MHz 约束下最高频率 53.862 MHz |
| 建立/保持时间检查 | 违例端点均为 0；最差建立时间裕量 1.434 ns |
| 构建一致性 | 暂存源码与当前源码及显式校准参数一致，位流摘要核验通过 |

最终位流 SHA-256：`0512311d50bc766ee813f80f3335c6f43829d5dc2369edf231387bb93a8dc481`。构建报告仍有派生时钟使用普通布线资源的工具警告；当前约束下的建立、保持检查均通过。真实阵列声场及现有上位机与板子的联调留待后续硬件阶段。
