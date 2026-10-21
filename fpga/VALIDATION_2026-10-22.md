# 双模式校准验证记录（2026-10-22）

分支：`feat/mg24-receiver`。目标板：Tang MEGA 138K Pro，GW5AST-138B / FCPBG676A。以下软件验证不代表实板声学测量通过。

## 构建

使用已安装的 Gowin V1.9.12.04，执行 `python fpga/board/build_calibration.py --mode all`。三个配置均完成综合、布局布线，50 MHz 约束下建立与保持时序的总负裕量和违例端点均为零。

| 配置 | 校准开关／来源 | Fmax (MHz) | 产物 |
| --- | --- | --- | --- |
| normal | 0 / 0 | 61.855 | `.runtime/fpga-calibration/normal/calibration_normal/impl/pnr/board_top.fs` |
| scope | 1 / 0 | 55.970 | `.runtime/fpga-calibration/scope/calibration_scope/impl/pnr/board_top.fs` |
| adc | 1 / 1 | 55.765 | `.runtime/fpga-calibration/adc/calibration_adc/impl/pnr/board_top.fs` |

对应 SHA-256：

```text
normal 794601900f26e54774728fee2d3b0736c0fe101dda4e92c19e80f176147c5f93
scope  565c5c3e4ec652413f7900c8f7b44a063f39aa5001106be3fa00f671278a8872
adc    72f2ba530cda336909bce3550076e966e99438411614a6e509b0706a67885034
```

每个配置目录内的 `manifest.json` 记录宏值、产物及 RTL 源码校验值；`build.log` 和工程内的 `board_top.tr` 保存完整构建和时序报告。非法宏值经过编译拒绝检查。现有部分位宽和时钟布线警告仍保留，最终建立、保持时序通过；未为通过构建添加时序豁免。

## 主机与仿真

- `python -m unittest discover -s firmware/xiao_mg24/tests -v`：24 项通过，包含现有接收客户端与新增校准测试。覆盖分包、CRC、两种来源、编号、旧结果、取消、重启、错误文件和保存失败阻止交还控制。
- `python firmware/xiao_mg24/tests/calibration_gui_smoke.py`：真实 Tk 窗口使用合成端点完成输入、保存、取消及恢复；截图检查窗口内容可见。生成文件标记为模拟数据，实测导入入口拒绝它们。
- 新增 RTL 测试 `tb_calibrate`、`tb_cal_modes`、`tb_cal_startup`、`tb_cal_apply`、`tb_cal_link` 均通过：64 档、16 路、非零参考通道、三次读数、原子提交／失败保留旧表、导入复验、120 秒人工超时、心跳断线、编号／来源／重复检查、实际输出与状态表修正。
- 原有 `tb_board`、`tb_review_edges`、`tb_focus_propagation`、`tb_hap2_rx`、`tb_fix_units`、`tb_traj`、`tb_shape`、`tb_phase`、`tb_out`、`tb_local` 通过。
- 原有 `tb_top8`、`tb_hap2_top` 使用 1 Mbaud 仿真副本均通过，合计 17 套 RTL 测试通过。原始 115200 波特率下的这两个完整顶层仿真因耗时中止，没有标为通过。

加速只改变 `.runtime` 中测试台副本的串口波特率；保留 50 MHz 时钟、40 kHz 载波和原断言，交付固件串口仍为 115200。板级串口与接收模块已按原波特率测试。重现命令：

```powershell
python fpga/tb/run_tests.py --iverilog <iverilog.exe路径>
python fpga/tb/run_tests.py --iverilog <iverilog.exe路径> --bench tb_hap2_top --bench tb_top8 --uart-baud 1000000
```

日志位于 `.runtime/calibration/tests/<测试名>/test.log`。完整顶层测试中原本未初始化的边沿统计计数器已显式初始化，防止未知值掩盖失败。

本轮未修改 MG24 固件公共采样或通信代码，复用其已有 USB／蓝牙构建验证。

## USB 接入检查

用户授权识别成功后下载示波器配置。首次检查 Windows 发现一个 `USB\\VID_0000&PID_0002` 设备，硬件标识 `USB\\DEVICE_DESCRIPTOR_FAILURE`，问题码 43，位于 `Port_#0004.Hub_#0002`。当时仅有的 COM3、COM4 均为蓝牙串口，Gowin 下载器扫描未返回可用下载器。

这说明当时存在 USB 枚举失败，尚不能据此认定只缺串口驱动，也不能仅凭未知设备标识确认具体板型。需通过拔插对应关系、已知数据线、直连其他 USB 口和正常供电排查；设备正常识别后再核对 JTAG 芯片及串口。实板采样率、接收输入、相位方向、实际幅值和削顶等仍待硬件验证。

用户重新连接并要求复查后，USB 已正常枚举为 `VID_0403&PID_6010`，Converter A/B 状态正常，新增 COM8（A）／COM7（B）。下载器位置 529 可读到 `0x0001081B`；位置 530 的扫描无响应，已终止该扫描。识别码不能单独区分 B/C，沿用此前成功下载并串口验证的 B 版工程。

已按授权将上述 `scope` 产物下载至 SRAM（断电失效），未擦写外部 Flash：

```powershell
& 'D:\Gowin\Gowin_V1.9.12.04_x64\Programmer\bin\programmer_cli.exe' --device GW5AST-138B --run 2 --fsFile 'D:\pld\fpga-air-haptics\.runtime\fpga-calibration\scope\calibration_scope\impl\pnr\board_top.fs' --cable-index 4 --location 529 --frequency 2.5MHz
```

工具退出码 0，进度 100%，`Finished`；User Code `0x0000B8D1`，Status Code `0x00026230`。工具实际传输时报告频率更新为 15 MHz，耗时 23.21 秒。随后在 COM7／115200 只读监听 3 秒，收到 30 个有效 CAL2 状态包，CRC 错误为零；来源为示波器（0）、4×4／11000 µm、IDLE、输出关闭、未开始校准。未发送任何开始或测量命令。串口检查证据在 `.runtime/calibration/hardware/2026-10-22-after-sram-download.json`。至此确认下载与校准串口可用，尚未进行声学实测。
