# NUCLEO-F411RE：4×4 阵列验证固件

更新日期：2026-09-27。用 STM32F411RE 暂代 FPGA，连接现有 R5 发射板及电脑上位机。已完成源码、ARM 编译和软件交叉测试，**尚未烧录实机，未验证声压、温升或触觉效果**。此版本不包含接收板 ADC 采集、BLE 或 FPGA RTL。

## 1. 功能与边界

- 16 路逻辑输出，固定 40 kHz、64 级相位；MCU 根据坐标计算相位并执行扫描。
- 点、直线、圆、方形、三角形、箭头；支持旧版 64 点路径及最多 32 段、256 点草图。
- HAP3 双向串口：握手、完整配置、启动、暂停、停止、心跳、状态和相位回读。
- 上电关闭输出；配置不完整或不匹配时整包拒绝；远程心跳超时、DMA 错误、供数超时或串口接收错误时关闭输出。
- 本机声明实际阵列 **4×4、11 mm 间距**。握手后上位机自动采用此几何，不接受伪装成 8×8 的配置。

其他载波频率和相位分辨率返回 `BAD_CONFIG`。该限制是本版 MCU 实现的边界，不改变上位机对其他设备的支持。

## 2. 编译与烧录

在仓库根目录执行：

```powershell
python firmware/nucleo_f411re/build.py
```

脚本自动查找本机 STM32CubeIDE 内的 Arm GNU 工具链；其他环境可设置 `ARM_GCC` 或传入 `--gcc <arm-none-eabi-gcc完整路径>`。构建不需要联网或 CubeMX 生成代码，`vendor/` 已包含所需 CMSIS 头文件及许可证。

产物位于 `build/`：`haptics_f411re.hex` 用于烧录，`.bin` 的加载地址为 `0x08000000`，`.elf` 用于调试；`build-info.json` 记录编译器和文件哈希。构建脚本不会自动烧录。

将板载 ST-LINK USB 接到电脑，在 STM32CubeProgrammer 中选择 ST-LINK、连接、下载 `.hex`、校验并复位。JP5 使用 USB 供电位置；保留板载 ST-LINK 与目标 MCU 的 SWD 跳帽。也可用 GNU Make 调用此目录的 `Makefile`。

Flash 最后 128 KB（Sector 7，`0x08060000–0x0807FFFF`）专门保存启动序号，不能放置程序或其他参数。每次启动仅写一个 32 位记录，不主动擦除，最多 32768 次；写入失败或用尽会拒绝输出。日常下载 HEX 保留此扇区；全片擦除会重置启动序号，须先断开上位机会话再重新连接。

## 3. 与上位机连接

1. USB 连接 Nucleo 的 **ST-LINK 接口**，在设备管理器找到 STLink Virtual COM Port。
2. 启动电脑程序，选择串口连接，选对应 COM 口，波特率 **115200**，8N1，无流控。不要选择 Demo。
3. 握手后应显示 `NUCLEO-F411RE`，实际阵列为 4×4、11 mm。
4. 选择或绘制图形，点击“发送到设备”；收到配置确认及同版本状态后，再点击“播放”。
5. 调试模式可查看焦点、相位和通信记录。先用固定点、`mod_hz=0`、`level=100` 检查连续载波；默认 `level=30`、200 Hz 调制会有周期性停波。

USART2 使用 PA2/TX、PA3/RX。标准板默认通过 SB13/SB14 接 ST-LINK，**不需另购 USB 转串口模块**。若改用外置 3.3 V USB-UART，须依板卡版本调整对应焊桥，断开 ST-LINK 的 UART 驱动后交叉连接 TX/RX 并共地，不能把两个 TX 并接。

## 4. 接线：Nucleo → R5 发射板

**从换能器正面看，XT30 位于下方，+x 向右、+y 向上，z 朝向手掌。** 逻辑通道按从下到上、每行从左到右排列。R5 的 CH 编号并非这个顺序，请按下表接线。`CN7/CN10` 是 Nucleo 插针，`CN1` 是 R5 的 2×10 接口，插针编号均以丝印为准。

| 逻辑通道 | MCU GPIO | Nucleo 插针 | R5 CN1 脚 | R5 信号 |
|---|---|---|---|---|
| 0 | PB0 | CN7-34 | 7 | CH4 |
| 1 | PB1 | CN10-24 | 8 | CH5 |
| 2 | PB2 | CN10-22 | 9 | CH6 |
| 3 | PB4 | CN10-27 | 10 | CH7 |
| 4 | PB5 | CN10-29 | 3 | CH0 |
| 5 | PB6 | CN10-17 | 4 | CH1 |
| 6 | PB7 | CN7-21 | 5 | CH2 |
| 7 | PB8 | CN10-3 | 6 | CH3 |
| 8 | PC0 | CN7-38 | 11 | CH12 |
| 9 | PC1 | CN7-36 | 12 | CH13 |
| 10 | PC2 | CN7-35 | 17 | CH14 |
| 11 | PC3 | CN7-37 | 18 | CH15 |
| 12 | PC4 | CN10-34 | 13 | CH8 |
| 13 | PC5 | CN10-6 | 14 | CH9 |
| 14 | PC6 | CN10-4 | 15 | CH10 |
| 15 | PC7 | CN10-19 | 16 | CH11 |
| GND | GND | CN7-20 | 1 | GND |

R5 的 CN1-2、19、20 留空。R5 从 XT30 单独输入限流 12 V；Nucleo 从 USB 供电，两板共地。MCU 引脚只接驱动板逻辑输入，不能接 OUT 测试点或 12 V。

PC0/PC1 使用默认模拟脚路由：SB51/SB56 接通，SB46/SB52 断开；若板子以前改过 I²C 焊桥，应先恢复。PB3/SWO、PA13/PA14/SWD 均未占用。本工程未使用 F411RE 封装没有引出的 PB11。

## 5. 本地按键

- B1（蓝色 USER/PC13）：短按停止，所有模式有效；按住约一秒，在停止状态切换 LOCAL/REMOTE。
- 可选外接按钮：PA6（CN10-13）到 GND 为下一图形；PA7（CN10-15）到 GND 为播放/暂停，内部上拉。
- 外接按钮仅 LOCAL 有效。上电默认 REMOTE 且输出关闭；长按 B1 切到 LOCAL 后可脱离电脑操作。参数不保存到 Flash，重启恢复默认值。
- LD2 表示 RUNNING 状态；它不是实际声场检测指示灯。

## 6. 输出与回传的含义

HSI 经 PLL 设置到标称 64 MHz，TIM1 每 25 个时钟产生一个相位槽：`64 MHz / 25 / 64 = 40 kHz`。实际频率随 HSI 误差变化。DMA2 Stream5/Channel6 由 TIM1_UP 请求写 GPIOB，Stream1/Channel6 由 TIM1_CH1 请求写 GPIOC，两个请求相差一个定时器时钟；实际端口间偏差和总线竞争需用示波器检查。

DMA 双存储地址配合第三个准备缓冲区，每块 1 ms，每 250 µs 更新一次焦点，目标更新节拍 4 kHz。未完成下一块时提前停机，返回 `DMA_UNDERRUN`，不循环播放过期数据。可在调试器观察 `render_max_cycles`；必须明显低于 64000 周期才有调度余量。

跨轮廓转移关闭输出。若 250 µs 区间可能碰到转移，整个区间留空，因此实际空白可能比 `blank_us` 多不到 500 µs；单个轮廓有效时间少于 500 µs 返回 `SCAN_TOO_FAST`，应降低重复频率或减少轮廓。

`level` 以整载波周期的脉冲密度实现，`mod_hz` 为 50% 占空比门控调制。它们不是校准后的电压、声压或触觉强度。STATE 的 `output` 表示扫描输出使能；额外字段 `drive_on` 表示采样到的这一载波周期是否被调制放行。焦点和相位从当前 DMA 缓冲区取出，属于**数字执行状态，不是传感器测量**。

完整 STATE 根据帧长约 1–10 Hz 回传，为控制应答预留带宽。串口回传频率与板内焦点更新频率无关。

## 7. 测试与首板验证

```powershell
python firmware/nucleo_f411re/tests/test_firmware.py
python -m unittest discover -s desktop_app/tests -v
```

固件测试需原生 GCC，可通过 `HOST_CC` 指定；本机可自动找到 STEdgeAI 自带的 MinGW。测试把相同的 C 协议、几何和波形代码编译到宿主机，使用 Python 上位机编解码及 Session 交叉检查，并以 115200 串口的回传速率验证最长路径流程。它不模拟 STM32 外设或证明 DMA 在实板达标。

首次烧录先不接 12 V：检查握手、配置确认、16 个 MCU 引脚的频率/相位，以及 STOP、暂停、B1 和拔线后停止。再接限流供电的 R5，检查各 OUT 的电压和波形。尤其检查两组 GPIO 的偏差、最大路径时是否出现供数超时，再进行声学测试。

## 8. 文件组织

- `src/haptics.c`：HAP3 帧、原子配置、状态机与回传。
- `src/geometry.c`：轨迹、相位计算、门控和 DMA 数据生成。
- `src/board.c`：时钟、USART2、双路 DMA、启动序号、按键和看门狗。
- `src/startup.S`、`stm32f411re.ld`：启动向量、内存布局；`src/main.c`：前台调度。
- `tests/`：仅宿主机测试，不编入 MCU；`build/`、`.runtime/` 不提交 Git。

## 9. 技术资料

资料核对日期：2026-09-27。

- [ST UM1724 Rev 17（2025-09）](https://www.st.com/resource/en/user_manual/um1724-stm32-nucleo64-boards-mb1136-stmicroelectronics.pdf)：板载 ST-LINK/VCP、焊桥、Table 29 Morpho 引脚。
- [ST RM0383](https://www.st.com/resource/en/reference_manual/dm00119316-stm32f411xce-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)：RCC、TIM1、DMA2 请求映射、GPIO、USART 和 Flash 控制器。
- [ST STM32F411xC/E 数据手册](https://www.st.com/resource/en/datasheet/stm32f411re.pdf)：芯片封装、存储容量及电气/时钟约束。
- [ST MB1136 C04 原理图](https://www.st.com/resource/en/schematic_pack/mb1136-default-c04_schematic.pdf)：供电、B1、ST-LINK 与目标 MCU 的连接。
- [本仓库 R5 板](../../hardware/Haptics_4x4_R5_12VDC/README.md)及其 EPRO：11 mm 阵列间距、CN1 与 OUT 空间顺序。
- [HAP3 草案](../../desktop_app/PROTOCOL.md)：上位机应用层协议；FPGA 端仍待实现与共同定稿。
- [第三方源码清单](vendor/SOURCES.md)：CMSIS 固定版本、来源和许可证。
