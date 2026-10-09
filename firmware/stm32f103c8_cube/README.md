# STM32F103C8T6 验证工程

更新于 **2026-10-09**。此工程用于替代 F411 做 4×4 阵列的串口、相位输出与接收 ADC 验证。最终控制平台仍为 Tang Mega 60K。

## 1. 打开工程

仓库工程位于 `firmware/stm32f103c8_cube/`，CubeIDE 工程名称为 `haptics_f103c8`。本机工作区副本位于 `D:\STM32Dev\haptics_f103c8`；其他电脑可直接导入克隆仓库中的工程目录。

在 CubeIDE 中选择 **File → Import → General → Existing Projects into Workspace**，选择该目录。不要勾选复制到工作区。选中工程后可直接 Run，或自行 Build 后使用 Debug；无需单独使用 HEX。

双击 `haptics_f103c8.ioc`，或用独立 STM32CubeMX 打开它，可查看引脚和时钟。当前配置为 STM32F103C8T6、**8 MHz 外部晶振、64 MHz 系统时钟**；若实际板卡晶振不同，应先调整配置。未启用 USB。

## 2. 下载与串口

| 接口 | F103 引脚 | 用途 |
|---|---|---|
| ST-LINK SWDIO | PA13 | 下载与调试 |
| ST-LINK SWCLK | PA14 | 下载与调试 |
| ST-LINK GND | GND | 共地 |
| ST-LINK NRST（建议连接） | NRST | 复位与复位下连接 |
| USB-UART RX | PA2 / USART2_TX | 接收 MCU 发出的数据 |
| USB-UART TX | PA3 / USART2_RX | 向 MCU 发送数据 |
| USB-UART GND | GND | 共地 |

串口使用 **115200、8N1、无流控、3.3 V 逻辑**。独立 ST-LINK V2 通常没有虚拟串口，上位机通信需另接 USB-UART。板卡电源只选一个来源；不要将多个供电输出直接并联。

**快捷烧录（绿色 Run）**：使用 **Run → Run Configurations → C/C++ Application → haptics_f103c8**。第一次选择并 Run 后，工具栏绿色 Run 按钮会复用它。该配置先增量编译 Debug，再由 OpenOCD 下载、回读校验、复位运行并释放 ST-LINK；无需额外手动转换 HEX/BIN。控制台出现 `Verified OK` 表示校验通过；`<terminated> (exit value: 0)` 表示下载工具正常退出，板上程序继续运行。源码调试会占用 ST-LINK，应先结束调试再 Run。

本机源码调试使用 **GDB Hardware Debugging → OpenOCD (pipe)**，与 Run 共用 SWD、100 kHz 和软件复位，不依赖 NRST 接线。在 **Run → Debug Configurations → GDB Hardware Debugging** 中选择：

- **haptics_f103c8 OpenOCD Debug**：下载已编译的 `Debug/haptics_f103c8.elf`，复位并运行至 `main` 断点。关闭自动编译，由你先自行 Build。
- **haptics_f103c8 Connection Check**：只连接、复位并暂停，读取寄存器；不下载程序、不自动编译。板上原程序没有加载符号时，出现“找不到源码”属于预期现象。

`haptics_f103c8.launch` 已替换为上述快捷烧录配置，避免再次进入报 `Could not verify ST device` 的 ST 专用流程。F11 用于调试；应先选定 **haptics_f103c8 OpenOCD Debug**，与绿色 Run 的下载运行用途不同。

换电脑或更新 CubeIDE 后，在工程目录生成本机初始化文件，再刷新工程：

```powershell
python configure_ide.py --cubeide 'D:\STM32Dev\STM32CubeIDE_2.1.0'
```

将路径改为实际安装目录。生成的 `haptics_openocd.local.gdb` 和 `haptics_run.local.cfg` 保存本机工具路径，已排除出 Git；Run 和 Debug 会自动启动 OpenOCD，无需手动启动服务器。Run 从本次 ELF 生成带 `0xff` 填充的临时镜像，限制在 63 KB 应用区内并保留末尾启动日志页；路径含空格也可使用。

2026-10-02 本机检查：CubeProgrammer 和 OpenOCD 均识别到 Device ID `0x410`、64 KB Flash。芯片 ROM 返回的厂商字段为 `jedec=1, con=8, id=0x0e`，不符合 CubeIDE 的 ST 校验规则；仅凭此尚未确定芯片品牌或下载器真伪。通用配置已在 CubeIDE 内成功下载并停在 `main`；未修改 ST 插件或厂商校验。

同日烧录验证发现并修复看门狗初始化顺序：先启动 IWDG，使 LSI 时钟工作，再配置并等待更新标志清零，避免启动卡住；等待增加 100 ms 超时。修复后重新编译无错误、无警告，使用 OpenOCD 烧录并通过回读校验，保留末尾 1 KB 启动日志页。源码断点确认进入 `app_f103_poll()`，系统时钟为 64 MHz，`boot_ok=true`；运行时间增长至 2536 ms 时仍为 `IDLE`、无待处理故障、GPIOB 输出全低。已释放下载连接并保持程序运行；串口联调、输出波形与声学效果尚未验证。

2026-10-04 本机验证：Run 配置对话框和工具栏绿色 Run 按钮均完成增量编译（0 错误、0 警告）、ST-LINK V2 烧录、`Verified OK` 回读校验和复位运行，下载工具正常退出。启动断点与 2 秒运行检查确认 64 MHz、`boot_ok=true`、`IDLE`、无待处理故障、输出关闭；验证后恢复运行并释放 ST-LINK。

## 3. 阵列接线

以下映射保留 [R5 驱动板](../../hardware/Haptics_4x4_R5_12VDC/README.md) 原来的物理通道顺序。2026-10-04 按实物 Blue Pill 照片调整：逻辑通道 2 从未引出的 PB2/BOOT1 改到 **PA8**，其余 15 路及协议通道编号不变。

| MCU 引脚 | 逻辑通道 | 驱动板 CN1 引脚 | 驱动板通道 |
|---|---|---|---|
| PB0 | 0 | 7 | 4 |
| PB1 | 1 | 8 | 5 |
| **PA8** | 2 | **9** | 6 |
| PB3 | 3 | 10 | 7 |
| PB4 | 4 | 3 | 0 |
| PB5 | 5 | 4 | 1 |
| PB6 | 6 | 5 | 2 |
| PB7 | 7 | 6 | 3 |
| PB8 | 8 | 11 | 12 |
| PB9 | 9 | 12 | 13 |
| PB10 | 10 | 17 | 14 |
| PB11 | 11 | 18 | 15 |
| PB12 | 12 | 13 | 8 |
| PB13 | 13 | 14 | 9 |
| PB14 | 14 | 15 | 10 |
| PB15 | 15 | 16 | 11 |

CN1-1 接 GND；CN1-2/19/20 不接。驱动板 XT30 单独接 **12 V DC**，不能接 MCU 电源脚。换能器连接驱动板输出，不能由 MCU 引脚直接驱动。

照片方向为 USB 在下、SWD 四针在上时，**PA8 是右侧从下往上的第 5 个排针，位于 B15 与 A9 之间**。只将 CN1-9 接到这个 A8 排针；不要从 BOOT1 跳帽取驱动信号。标准 Blue Pill 原理图中，BOOT1 跳帽经 100 kΩ 电阻连接 PB2，实物阻值仍应以板卡为准。

其余引脚检查：

| 引脚 | 板上关联功能 | 当前处理 |
|---|---|---|
| PB2 / BOOT1 | 启动跳帽，两侧无 B2 排针 | 不用于阵列，保持浮空输入；两个 BOOT 跳帽保持 0 |
| PB3、PB4 | 默认 JTAG 调试引脚 | `HAL_MspInit()` 关闭 JTAG，释放为阵列输出 |
| PA13、PA14 | 顶部 SWD 接口 | 保留给 ST-LINK，不接阵列 |
| PC13 | 板载状态 LED | 保留给 LED，不接阵列 |
| PA11、PA12 | 板载 USB 数据线 | 不用于阵列；未启用 USB 串口 |
| PC14、PC15、PD0、PD1 | 板载晶振 | 不用于阵列 |
| PA0、PA2/PA3、PA5–PA7 | ADC、串口和按键 | 均有侧边排针；PA3 增加内部上拉，避免未接 USB-UART 时接收端悬空 |

照片确认其余 15 个 PB 阵列引脚均有侧边排针。GPIO 复用和内部上拉属于配置检查，尚未测量实板各引脚波形。

## 4. 接收板与按键

接收板 U4-2 `ADCV_P` 接 PA0，U4-3/4 接 GND，接收板按其设计独立供 5 V。PA0 输入须处于 0 至 VDDA 范围；保留原接收前端与分压，上位机按 3.4 倍还原前端电压。

**业务版本上电自动启动**：初始化完成后等待 3 秒，再进入本地模式，开始输出内置默认圆形。2026-10-09 的源码增加原型调试期限：这次自动运行最多 10 秒，到期关闭输出，进入 `IDLE/AUTO_RUN_LIMIT`，不会自动重试。显式按键或串口 START 保留原来的运行行为。倒计时期间阵列输出保持低电平，PC13 状态灯灭；启动后状态灯亮。默认参数为半径 20 mm、焦点高度 150 mm、输出等级 30%、200 Hz 调制和 40 kHz 载波，实际触觉效果尚未测量。无需串口模块或启动按键。红色板载 RESET 会重启 MCU，并重新进行这次倒计时。

倒计时在主循环中执行，不阻塞看门狗、串口或按键。只尝试自动启动一次；停止、暂停或故障后不会再次自动播放。倒计时期间收到串口数据或按键操作会取消自动启动；初始化故障也会阻止启动。已自动启动后，电脑可先发 STOP，再发 `MODE value=REMOTE`，然后配置和开始远程播放；协议帧格式见 HAP3。F411 工程的默认启动行为不受此改动影响。

F103 在初始化入口记录并清除 RCC 复位标志。若上一次为 IWDG/WWDG 看门狗复位，保持 `LOCAL/FAULT/WATCHDOG_RESET`，取消自动启动；HELLO 不解除该故障，须显式 STOP 后再切换 REMOTE、CONFIG 和 START。启动日志或 ADC 初始化故障保留自身原因。停止、暂停、故障或新启动都撤销旧的自动运行期限，避免它干扰后续手动运行。

新版电脑端点击“电脑控制”会自动执行停止确认和模式切换。调试模式的“实测数据 → 接收波形”可在 LOCAL/REMOTE 请求采样；“连续查看”以每秒最多两次请求 200 点、400 kS/s 的窗口，自动保存原码 JSONL 和电压分析 CSV，详见[上位机接收板调试](../../desktop_app/README.md#接收板调试)。采样仍由串口请求触发，不依赖外接按键，不会自动修改相位；没有供电时先用 Demo 验证流程。

外接按键为可选操作：一端接对应引脚，另一端接 GND，内部上拉、低电平有效：

- PA5：停止；长按 1.5 秒切换本地/远程模式。
- PA6：切换预设图形。
- PA7：暂停/继续；停止状态下播放所选图形。
- PC13：低电平点亮的状态 LED，仅适用于对应板卡电路。

## 5. 能力与限制

固件保持 [HAP3](../../desktop_app/PROTOCOL.md) 双向通信语义，支持配置确认、开始/暂停/停止、状态与相位回传、几何配置和 ADC 短窗口采集。F103 握手如实声明 **16 通道、最多 64 个坐标和 8 段路径**，兼容旧版 64 点导入；超限配置拒绝，不截断。复杂草图可能需要简化。

TIM1 更新请求通过 DMA1 Channel5 写 15 个 GPIOB 输出；内部 CH1 比较请求通过 DMA1 Channel2 写 GPIOA，将逻辑通道 2 输出到 PA8，并保持 GPIOA 其他输出锁存位（包括按键上拉）。PA8 为普通 GPIO，TIM1 CH1 使用 **Output Compare No Output**，不占用引脚复用。两类 DMA 请求来自同一个 TIM1，比较请求比更新请求晚 1 个定时器时钟；实际跨端口偏差仍需示波器测量。

目标载波保持 40 kHz、每周期 64 个相位槽，焦点目标更新率保持 1 kHz。为在 20 KB RAM 内容纳双端口波形，缓冲边界改为 250 μs，双端口双缓冲合计仍为 5120 字节。每个边界保留 25 μs 全低检查窗口，因此连续输出的检查窗口占比由 5% 变为 10%。以较后的 GPIOA DMA 边界回填两组缓冲；任一路 DMA 错误、缓冲欠载、两端口缓冲位置失配或串口错误均关闭全部 16 路。串口心跳失联关闭只适用于 REMOTE；拔掉串口线不能保证 LOCAL 停止。ADC1 仍由 TIM3 触发，以 400 kHz 采集 200 点。以上是配置与代码行为，实际频率、延迟和触觉效果尚未测量。

等级 30% 是载波周期的脉冲密度开关，不是将输出电压降至 30%。关闭 200 Hz 调制仍保留该门控；等级 100%、调制关闭时也保留每 250 μs 的保护空隙，不能当作无间断连续音。电脑生成的数字位图含音频范围电气分量，尚未测量实际声音频谱。详见 [4×4 板与程序蜂鸣核验](../../hardware/Haptics_4x4_R5_12VDC/蜂鸣核验_2026-10-09.md)。

2026-10-09 修复业务门控：每路只在自身正常上升沿决定是否输出该高脉冲，普通调制/密度关闭让已开始的高脉冲到自然下降沿结束。每个缓冲首周期仍全低，不继承旧相位或使能。此前直接在全局周期边界恢复高电平，会插入额外上升沿；新版数字测试消除了此机制。guard、STOP 和故障仍立即关闭，guard 可裁短末尾脉冲，调制仍会缺脉冲，示波器自动频率不保证恒为 40 kHz。业务 `drive_on` 表示当前 25 μs 周期内波形缓冲是否包含阵列高电平，不包括 GPIOA 的其他锁存位，也不是瞬时引脚或声学测量。

2026-10-07 优化后，DMA 中断只回填已计算好的波形；主循环提前准备四个焦点，每次后台服务完成一个焦点的 16 路相位。F103 使用整数距离求相位、缓存线段长度和单周期位图，并用固定大小的字拷贝填充缓冲，避免软浮点和逐字节大块拷贝挤占回填时间。只在 F103 启用这些配置，F411 保留原默认计算方式。共享协议/配置临时空间降低 RAM 占用；不改变坐标、驱动引脚或接收前端。

USART2 中断优先级为 0，波形 DMA 为 1，ADC DMA 为 2，均已同步到生成代码和 `.ioc`。输出故障保留正常接收到的串口命令；UART 接收错误才丢弃受损数据并重新同步。焦点未及时准备会报 `FOCUS_UNDERRUN` 并关闭输出，不会用旧焦点静默继续。

相位和状态回传属于数字执行反馈，不能证明手掌处声场或触觉效果。接收 ADC 属于测量链路，需要先核对接线、输入范围及校准。

## 6. CubeMX 再生成与维护

应用入口保存在 `main.c`、中断文件的 `USER CODE` 区域，专用逻辑在 `app_f103.c` / `wave_f103.c`；CubeMX 已验证能保留这些入口。生成时启用 **Keep User Code**。

若 CubeMX 重新生成 IDE 配置，在工程目录运行 `python configure_ide.py`，然后在 CubeIDE 刷新工程。它恢复 `-O2`、newlib-nano、`haptics_memory.ld`、源目录范围、ChannelTest/PinTest 编译配置及三种快捷 Run 配置；添加 `--cubeide` 可同时更新 Run/Debug 工具路径。不能改用默认 64 KB 链接脚本，否则会占用预留日志页。

仓库中 F411 的协议和几何核心为共享来源；修改后可执行：

```powershell
python firmware/stm32f103c8_cube/sync_core.py
python firmware/stm32f103c8_cube/sync_core.py --project 'D:\STM32Dev\haptics_f103c8'
```

### 6.1 逐通道调试版本

`Core/Inc/haptics.h` 中 `F103_CHANNEL_TEST` 默认为 0，编译时可覆盖。`app_f103.c` 使用 `#if F103_CHANNEL_TEST` 分隔诊断序列、静态载波与业务图形、动态波形，两者不会同时执行；串口解析和故障停机仍共用。

| CubeIDE 编译配置 | 宏 | 行为 |
|---|---|---|
| Debug / Release | `F103_CHANNEL_TEST=0` | 业务图形、按键和串口控制，自动运行有 10 秒期限 |
| ChannelTest | `F103_CHANNEL_TEST=1` | 等待 3 秒，逻辑通道 0→15 各输出 2 秒、全关 1 秒；约 51 秒后停止 |

调试版本关闭调制、等级 100%，每次仅一个掩码位有效。2026-10-09 的源码改为静态循环 DMA：TIM1 `PSC=0、ARR=799`，按 64 MHz 定时器时钟每 12.5 μs 交替写高、低两个字，目标为连续 40 kHz、50% 占空比；不再动态回填，也不插入每 250 μs 的 25 μs 全低保护。两路 DMA 只开启传输错误中断，错误、串口故障及 STOP 仍关闭全部输出。此改动尚未烧录，实际载波、占空比与声音变化需示波器验证。掩码对应逻辑编号，物理换能器顺序必须查第 3 节。

调试 ON 阶段 STATE 使用固定 `POINT(0,0,150 mm)` 作为协议参考，电气相位固定为 0，不求解聚焦声场；单个换能器不能据此认定形成触觉焦点。此时 `output=1、drive_on=1` 表示载波门控开启，正常低半周期不会改变它；`elapsed_us` 来自软件毫秒计时。这些是数字配置与状态回读，不能作为实际引脚波形或声压测量。业务模式仍使用独立的动态波形路径。

任意时刻串口 STOP、PA5 短按或故障都会取消整轮，停顿期间也有效；不会自动继续，重测需复位。看门狗复位与初始化错误禁止开始。HELLO/PING/SNAP 只观察，不取消序列；调试固件声明 `profile=CHANNEL_TEST`，允许 STOP、GEOMETRY、CAPTURE，图形 CONFIG/START/MODE 等返回 `CHANNEL_TEST_ONLY`。

串口格式化或发送跨过 2 秒截止时，后台服务先关闭输出，主循环随后进入 GAP；不会因为持续读取状态而延长 ON，也不会在发送回调中启动下一路。`.ioc` 保留业务 TIM1 配置，诊断的 `ARR=799` 由宏分支在启动静态 DMA 时设置；引脚、串口、ADC 和 CubeMX 配置不变。

在 **Run → Run Configurations → C/C++ Application** 选择 **haptics_f103c8 Channel Test**，该快捷方式先编译 ChannelTest，再下载其专属 ELF。原 **haptics_f103c8** 仍编译并下载 Debug 业务版本。不要用原 OpenOCD Debug 配置下载旧 Debug ELF 来测试逐通道版本。更新后刷新工程；若未出现 ChannelTest，运行 `configure_ide.py --cubeide <安装目录>` 恢复配置。

普通图形上位机要求业务控制能力，不能连接此只读诊断配置。使用 `channel_monitor.py --port COM4` 观察，记录逻辑/PCB 通道、掩码及状态，退出时发送 STOP 并确认输出关闭；默认不启用 ADC。接收板正确连接后才加 `--capture` 保存原码窗口与电压分析。脚本不会复位或启动输出，因此连接前已完成的通道不会自动补测。

### 6.2 固定引脚测试 PinTest

2026-10-09 新增独立 **PinTest** 编译配置和 **haptics_f103c8 Pin Test** 快捷 Run。原 Debug/Release 业务与 ChannelTest 逐通道序列保留原行为；PinTest 复用静态 40 kHz、50% 方波后端，默认持续测试指定的一路，不切换到其他通道。

默认 **PB11 → CN1-18 → PCB CH15**，对应软件通道 11、掩码 `2048`（`0x0800`）。初始化后等待 3 秒，进入 `RUNNING/PIN_TEST_ON` 并持续输出，直到 STOP、故障或断电；测试期间其余 15 路保持低。看门狗复位仍禁止自动启动。普通业务 Run 不会选择此固件；在 Run Configurations 中明确选择 **haptics_f103c8 Pin Test**。

编译、下载时使用 PinTest 配置及其 ELF；`Debug/haptics_f103c8.elf` 仍为有扫描与调制的业务固件。测 PB11 对 MCU GND 可先用 10 μs/格、1 V/格、DC 耦合和约 1.5 V 上升沿触发；目标周期 25 μs，高低各约 12.5 μs。

参数位于 `Core/Inc/pin_test_config.h`，修改后重新编译 PinTest：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `F103_PIN_TEST_CHANNEL` | `11` | 测试软件通道，允许 0–15；第 3 节接线表可查对应引脚 |
| `F103_PIN_TEST_ON_MS` | `0u` | `0` 表示持续输出；正数表示输出时长（ms），最大 `0x7fffffff` |

例如 `10` 选择 PB10/CN1-17/CH14，`2` 选择 PA8/CN1-9/CH6；不存在 PB2 输出。PinTest 使用 `F103_CHANNEL_TEST=1、F103_PIN_TEST=1`，其他配置的 `F103_PIN_TEST=0`。也可在 PinTest 的编译宏中覆盖上述参数，IDE 配置恢复脚本保留自定义参数。

```powershell
python firmware/stm32f103c8_cube/build.py --pin-test
python firmware/stm32f103c8_cube/build.py --pin-test --test-channel 2 --test-on-ms 1500
```

命令行产物独立保存到 `build/pin_test/`；CubeIDE 保存到 `PinTest/`，不会覆盖原版本。`channel_monitor.py --port COM4` 同样可观察并保存此模式，默认监听 65 秒后或按 Ctrl+C 退出时会发送 STOP 并确认关闭；这是工具发出的停止命令，固件本身不设持续模式截止。时长设置为正数时，到期进入 `IDLE/PIN_TEST_DONE`；发包跨截止也会关闭输出。方波频率与占空比仍需实测；参数选择不改变引脚或 `.ioc`。

本次检查：F103 22 项通过，原业务与逐通道回归保持通过；持续 PinTest 通过严格 Arm GNU 编译，Flash/RAM 为 37,640/13,464 字节，含原堆栈预留。回归覆盖 PB11 持续、PA8 持续、PB10/PA8 定时、1 ms 和最大有效时长、HAL tick 回绕、非法参数、单路隔离及 STOP/故障关闭。串口工具 11 项和 IDE 配置 1 项在新增 PinTest 时已通过，本次不改其配置。

2026-10-09 已通过 ST-LINK V2 下载持续 PB11 版本并校验成功。运行超过 3 分钟后读到 `PIN_TEST_ON`、掩码 `2048`、无待处理故障，TIM1 与 DMA 保持启用，`ARR=799`。这是数字运行状态核验，实际引脚的频率、幅度与占空比仍需示波器确认。

## 7. 软件验证

你可以在 CubeIDE 自行编译；可选命令行编译和原生检查：

```powershell
python firmware/stm32f103c8_cube/build.py
python firmware/stm32f103c8_cube/build.py --channel-test
python firmware/stm32f103c8_cube/build.py --pin-test
python firmware/stm32f103c8_cube/tests/test_f103.py
python firmware/stm32f103c8_cube/tests/test_ide_profiles.py
python firmware/stm32f103c8_cube/tests/test_channel_monitor.py
python firmware/nucleo_f411re/tests/test_firmware.py
```

命令行编译不会自动烧录。2026-10-07 优化版本通过 Arm GNU 编译（`-Wall -Wextra -Werror`）、16 项 F103 检查和 17 项 F411 回归检查。Flash 占用 39,452 / 64,512 字节，RAM 占用 19,184 / 20,480 字节，包含 2 KB 栈和 256 字节堆预留；栈的实际峰值仍需专项测量。自动启动延时由 `app_f103.h` 中的 `F103_AUTOSTART_MS` 设置，当前为 3000。

2026-10-09 新版的 F103 原生检查覆盖全部 64 种相位、密度/调制切换、完整 2 秒默认圆形数字流、反向换相、guard 裁剪及逐周期回读；静态诊断检查两路 DMA 请求/传输配置、全部单路掩码、串口发送跨截止和 tick 回绕。两种配置均使用 Arm GNU `-O2 -Wall -Wextra -Werror` 编译；最终资源与测试结果见本次 PR。RAM 含 2 KB 栈和 256 字节堆预留，未测实际栈峰值。命令行产物分别在 `build/` 和 `build/channel_test/`，不会覆盖。IDE 配置恢复、宏、独立编译目录与 Run 选择另有回归检查。

最终检查：F103 20 项、串口工具 mock 10 项、IDE 配置 1 项均通过。业务 Flash/RAM 为 39,784/19,720 字节，静态逐通道诊断为 37,820/13,464 字节，均在 63 KB Flash / 20 KB RAM 限制内；业务剩余静态 RAM 余量仅 760 字节，已包含上述堆栈预留。实际回填时间、栈峰值及带负载波形尚未验证。

本次还在波形准备完成后、启用 DMA 前增加原子故障复查，防止准备期间 UART 故障关闭输出后又被启动尾部打开；故障注入测试检查原中断屏蔽状态恢复。源码已同步本机 CubeIDE，尚未烧录；引脚及 `.ioc` 不变。上述测试属于电脑寄存器/命令模型，不能替代实板波形、时序或温升验证。

本机 CubeIDE 工程的源码及 `.ioc` 已同步，优化 ELF 已用 ST-LINK V2 下载并回读校验。真实 USB-UART 连接为 COM4、115200：九组图形的配置、播放、采样、暂停/继续和停止通过；上位机真实 `Session` 连续完成 30 个 ADC 窗口的解析与记录。压力测试使用输出等级 0，输入信号来源未确认，因此不作为超声或触觉验证。详细条件见[实板联调记录](VALIDATION_2026-10-07.md)。

检查覆盖 16 个逻辑通道的相位、PB2 禁用、PA8 映射、GPIOA 其他锁存位保持、双端口检查窗口与 1 kHz 焦点更新。`wave_driver_harness.c` 使用实际驱动源码和模拟 CMSIS 寄存器，检查双 DMA 启停、较后端口边界回填、任一路 DMA 错误、漏回填和缓冲失配关闭输出，以及倒计时完成启动、手动/串口取消、暂停/停止后不自动重启、初始化或输出故障阻止启动；这不代表已测量真实总线同步。

Flash 最后 1 KB（`0x0800FC00`）保留启动日志，共 512 次启动；用尽后禁止输出，不自动擦除。需要维护时应停止并断开驱动电源，手动擦除此页后重新启动；擦除整个芯片会同时删除程序。不应将清空日志作为常规连接操作。

## 8. 首次上板

若仅给 MCU 供电，先断开全部发射控制线，再用示波器/逻辑分析仪检查 PA8 与其余 15 个 PB 输出的载波片段、相位、停止和 REMOTE 失联关闭，特别检查跨端口同步。不能让未供电的 TC4427A 继续接收高电平，其输入绝对上限为 VDD＋0.3 V；驱动板关电前先停止输出，相关手册和短时测点见上述蜂鸣核验。默认 3 秒后会自动输出，串口联调时可在倒计时期间握手取消自动启动。调试器可查看 `render_max_cycles`（应低于 16,000 周期，即 250 μs）与 `prepare_max_cycles`；这些计数只反映本次运行观察值，不是最坏执行时间证明。之前的 PA8 自动启动版本已烧录并完成数字/串口联调；2026-10-09 的启动保护尚未烧录，输出波形、接收板校准、声场与触觉效果仍需实测。

## 9. 参考资料

- [ST STM32F103C8 官方产品页与数据手册入口](https://www.st.com/en/microcontrollers-microprocessors/stm32f103c8.html)：Flash/RAM、引脚与电气限制。
- [ST RM0008](https://www.st.com/resource/en/reference_manual/rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)：DMA 请求映射、ADC 外部触发、GPIO 与调试端口配置。
- [Blue Pill 原始核心板原理图](https://stm32-base.org/assets/pdf/boards/original-schematic-STM32F103C8T6-Blue_Pill.pdf)：2016-01-26 的板卡设计，PB2/BOOT1 经 R4 100 kΩ 接启动跳帽；用于解释未引出 B2 排针的原因，不作为实物变体阻值的测量结论。2026-10-04 核对用户板卡照片的排针、BOOT、SWD、USB 和晶振位置。
- `haptics_f103c8.ioc` 与 `Drivers/`：本工程使用 STM32CubeMX 6.17.0、STM32Cube F1 V1.8.7；ST HAL/CMSIS 许可证保留在各组件目录。
