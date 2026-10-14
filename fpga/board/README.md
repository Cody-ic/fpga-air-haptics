# 上板工程：怎么建、怎么烧、上电先看什么

更新日期：2026-10-13。

## ⚠️ 2026-10-14：板子型号定案（这条比下面所有内容都重要）

板子是 **Sipeed Tang MEGA 138K Pro**（丝印确认），芯片 **GW5AST-LV138FPG676A**，
封装 **FCPBG676A（676 球）**，Device Version **B 或 C**。

这推翻了之前两个版本：

| 曾经以为 | 实际 | 后果 |
|---|---|---|
| Tang Mega 60K（GW5AT-LV60PG484A，484 球） | ✗ | 60K 的比特流烧进去报 `ID code mismatch` |
| Tang Mega 138K（GW5AST-LV138PG484A，484 球） | ✗ | 器件名对了，但**封装不对**，球位毫无对应关系 |
| **Tang MEGA 138K Pro（FCPBG676A，676 球）** | ✓ | 现在的约束文件 `tang_mega_138k_pro.cst` 就是照它重排的 |

两条硬条件（都已核实）：

1. **必须用商业版 IDE**。官方写明「138K Pro 目前未被教育版支持，需要 V1.9.9 或更新版本的
   商业版 IDE」。实测：教育版 V1.9.11.03 执行 `create_project` 直接报
   `can't find device: GW5AST-LV138FPG676AC1/I0` —— 它的器件库里只有 484 球的封装，
   根本没有 676 球这一颗 ✗。授权不用买：Sipeed 提供免费在线 Float Lic，填
   `ip 106.55.34.119 / port 10559`（不通就试域名 `gowinlic.sipeed.com` 对应的 IP）。
2. **时钟在 P16，不是 V22**。这块板子上 P16 = 50 MHz（官方所有 Pro 例程都用它当 clk），
   V22 是另一个 70 MHz 晶振。60K 那版「V22 = 50 MHz」的结论在这块板子上是错的。

引脚来源（都在 `fpga/board/docs/sipeed_ref_pro/`，全是官方件）：

- `tang_mega_138k-pro_pins.cst` — Sipeed 官方「全引脚约束」
- `pro_dock_4071f3_schematic.pdf` — 底板原理图（据此定下 DEBUG 串口 = N16/P15、PMOD 连线）
- `Tang_MEGA_138K_Pro_Pinout_Track_Length_Table.xlsx` — 核心板↔底板 BTB 引脚表
- `key_led__src__key_led.cst`、`led__src__top.cst` — 官方按键/LED 例程

**当前状态**：约束文件和 build.tcl 已按 138K Pro 改好；综合还没跑过（本机只有教育版 IDE，
装好商业版后执行 `build.tcl` 即可）。下面第 0～6 节里凡是提到 60K / 484 球引脚的地方
都以本节为准。

## ⚠️ 烧录踩坑：下载器要选对"USB 位置"，不然一直卡 0%

2026-10-14 实测（商业版 IDE V1.9.12.04）。症状：板子开着机、流水灯在跑、两个电源灯都亮、
USB 插的是 `USB JTAG&UART` 口，电脑也认得下载器（设备管理器里两个 USB Serial Converter
＋两个 COM 口），但 Programmer 一烧就**卡在 0%**，命令行 `--scan` 也**永远等不到应答**；
换个电脑（队友的）用同一根线同一块板子却能烧。

原因不在板子也不在线，而在**下载器的两个 USB 接口**：这块板载调试器（BL616 芯片，
USB 侧模拟成 FTDI 双通道设备）在设备管理器里是两个 converter，对应两个"USB location"。
Gowin 工具默认打开的那个（本机是 **location 562**）接的是**串口通道**，JTAG 在**另一个**
（本机是 **location 561**）。所以现象就是"下载器认得到、JTAG 完全不应答"。

**怎么修（GUI）**：`Edit → Setting → Cable Setting` → 点 **Query** → 选 **USB location = 561**
那一项（不要选 562）→ **Save** → 再烧。

**怎么修（命令行，最稳）**：

```powershell
& "D:\OneDrive\Desktop\Gowin\Gowin_V1.9.12.04_x64\Programmer\bin\programmer_cli.exe" `
  --device GW5AST-138B --run 2 `
  --fsFile D:\OneDrive\Desktop\fpga-air-haptics\fpga\board\haptics138kpro\impl\pnr\board_top.fs `
  --cable-index 4 --location 561 --frequency 2.5MHz
```

`--run 2` 是 SRAM 编程（断电即失）；要固化到 flash 再另说。
`--location` 一给，`--channel` 就被忽略（手册 4.4 节）——这也是当初 `--channel 0/1` 都试不通的原因。

**怎么确认链路通**（不用烧，只读芯片）：

```powershell
& "...\programmer_cli.exe" --scan --cable-index 4 --location 561
```

通了会打印 `ID: 0x0001081B` —— 对照官方文档，`0x0001081B` 就是 **GW5AST-138B**，
所以**器件版本选 B 是对的**，不用改 C。换 USB 口之后 location 号可能变，重新跑一次
`--scan-cables` 看两个口分别是多少即可。

2026-10-12 审查补充：串口和按键已加入两级输入同步；上电复位与启动标识有明确初值。
第一次综合仍须核查初始化设置和同步器路径。当前 `boot` 仅用于同次上电期间区分复位，
断电重启可能重复，跨断电唯一标识仍待设计。数字仿真通过不代表已经完成上板验证。

这一份是**板子到手之后照着做**的清单。逻辑本身在 `fpga/rtl/` 里，这里只讲工具那边
怎么把这些文件变成一个能烧进板子的工程。

## 0. 板卡与工具

| 项目 | 值 | 来源 |
|---|---|---|
| 开发板 | Sipeed **Tang MEGA 138K Pro** | 板子丝印 |
| FPGA 型号 | **GW5AST-LV138FPG676A**，封装 **FCPBG676A**（676 球），Device Version **B 或 C** | 官方 Pro 例程的 cst 头 |
| 板上时钟 | **50 MHz**，引脚 **P16** | 官方 Pro 例程的 sdc：`create_clock ... -period 20` |
| 用户按键 | K16、F15、G15（低有效，3.3 V，**必须开内部上拉**） | 官方全引脚约束 |
| 软件 | **商业版**高云云源软件（Gowin EDA）；教育版不支持这颗，见文首说明 | 

器件版本 B 还是 C：看 FPGA 表面**镭雕印记第三行第 5 个字符**（官方说明：是 B 就是版本 B，
是 C 就是 C）。先按 **B** 建工程；如果 Programmer 报 `ID code mismatch`，把 `build.tcl` 里的
`-device_version B` 改成 **C** 重跑。

板子上有两个 USB 口：**`USB JTAG&UART`**（板载下载器 + 一路串口，烧录和联调都用这个；
串口对应 FPGA 的 N16/P15，不需要外接 USB-TTL）和「自定义 USB/软接」（我们不用）。

因为板上就是 50 MHz，和固件默认的 `CLK_HZ = 50_000_000` 一致，**不需要锁相环（PLL）**：
串口分频 434、每 10 µs 一拍（500 个时钟）都是按 50 MHz 算好的。将来要跑更快的时钟
再例化 Gowin 的 PLL IP，同时把 `CLK_HZ` 和 `.sdc` 一起改。

## 1. 新建工程

### 1.1 命令行一键跑（推荐，已实测通过）

仓库里带了 `build.tcl`，命令行就能建工程、综合、布局布线、出比特流：

```powershell
cd D:\OneDrive\Desktop\fpga-air-haptics\fpga\board
& "D:\OneDrive\Desktop\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe" build.tcl
```

（路径是**商业版**那套装在哪儿就写哪儿；教育版没有这颗 676 球的器件库，跑不起来。）

跑完看到 `=== BUILD-DONE ===` 就是成功了，产物都在 `fpga\board\haptics138kpro\impl\`：

- 比特流：`impl\pnr\board_top.fs`
- 综合资源报告：`impl\gwsynthesis\board_top_syn.rpt.html`
- 引脚报告：`impl\pnr\board_top.pin.html`（哪个信号落到哪个脚，上板前照它对线）
- 时序报告：`impl\pnr\board_top.tr.html` 和 `board_top.timing_paths`

这套工程目录已经被 `.gitignore` 忽略，不会污染仓库。

### 1.2 图形界面（第一次想看清报错用这个）

1. Gowin EDA → New Project → 器件选 **GW5AST-138B / FCPBG676A / B**（选错了烧不进去：ID code 对不上）。
2. 添加设计文件：把 `fpga/rtl/` 下的**全部 `.v`** 加进去（不要加 `fpga/tb/` 里的测试台）。
3. 顶层模块填 **`board_top`**。
4. 添加物理约束 `fpga/board/tang_mega_138k_pro.cst`、时序约束 `fpga/board/tang_mega_138k_pro.sdc`。
5. 综合 → 布局布线。第一次跑请把**时序报告**和**资源报告**都看一眼（见第 4 节）。
6. 用板载下载器（USB JTAG）烧到 SRAM 或 Flash。板子电源要长按 PWR 键 2 秒才开。

## 2. 上板前必须自己核实的四件事

这四件事在电脑上没有答案，只能在板子/原理图上确认：

1. **串口引脚**。`.cst` 现在把串口临时放在排针上的两个 3.3 V 普通脚：
   `uart_rx_pin = T18`、`uart_tx_pin = P16`（都在左边那排排针上）。拿一根 USB-TTL 接上去
   就能调：电脑的 TX → T18，电脑的 RX → P16，两边地要接一起。板载 `JTAG&UART` 那一路
   串口对应哪两个 FPGA 引脚，只在官方的全引脚表里（网上没有公开文本），核实到之后再改
   这两行。**注意别把 TX/RX 接反。**
2. **通道引脚**。**16 路（4×4）已经能布局布线并生成比特流** ✓

   关键是 `build.tcl` 里这两条（不是可选项）：

   ```tcl
   set_option -use_cpu_as_gpio   1
   set_option -use_sspi_as_gpio  1
   ```

   138K 上有一半引脚是**配置复用脚**，不放开会在布局阶段直接报
   `PR2017 ... dedicated pin (CPU/SSPI)`：

   | 引脚 | 器件文件里的 CFG 字段 | 要哪个开关 |
   |---|---|---|
   | P19 | D06/SSPI_CLK | CPU + SSPI |
   | R19 | D07/SSPI_WPN | CPU + SSPI |
   | U21 | D05/SI | CPU + SSPI |
   | T20 | D08/SO | CPU + SSPI |
   | Y21 | SSPI_CS_N（**只属于 SSPI**） | 只要 SSPI |
   | T21 | D04（纯 CPU） | 只要 CPU |

   ⚠️ 60K 上这几个脚**没有** SSPI 这层别名，所以以前只开 CPU 就够了；换板子之后
   必须**两条都开**才能布局。官方 138K 例程（pmod_led）的工程配置里 CPU 和 SSPI
   两项也都是 true，两边一致。换成 138K Pro（676 球）之后这两条先原样留着——
   在 676 球的新器件文件里到底哪几个脚是复用脚，要等商业版 IDE 装好、能建工程之后再核。

   ⚠️ 走过弯路，记一下：**改文件没用**。`IDE\share\config\device.cfg`（工具模板）改了
   不生效；工程里的 `impl\pnr\device.cfg` 每次跑都会被重新生成，改了会被覆盖回去。
   IDE 里那个 Multipurpose/复用引脚的对话框我们也一直没找到，
   最后是**命令行开关**解决的。

   切换 8 路/16 路：`board_top` 的 `HW_ROWS`（4 = 16 路，2 = 8 路），
   同时 `.cst` 里把 `array_pos[8..15]` 那几行注释/取消注释。

  我们用的脚现在全部来自官方 Pro 资料，电平统一写 `IO_TYPE=LVCMOS33 / BANK_VCCIO=3.3`：

  | 用途 | 引脚 | 来源 |
  |---|---|---|
  | 时钟 50 MHz | P16 | 官方 Pro 例程的 sdc |
  | 按键 ×3 | K16（复位）、F15、G15 | 官方全引脚约束 |
  | 串口 | N16（收）、P15（发） | 底板原理图（BL616 的 UART 经 0 Ω 电阻接这两脚） |
  | 16 路输出 | PMOD1 八个 + PMOD2 八个 | 官方全引脚约束 + 底板原理图，见 `.cst` 里的逐脚清单 |
  | 状态灯 ×3 | R26、J14、L20（**低电平点亮**） | 官方 LED 例程（`assign led = ~led_reg`） |

  一个坑记一下：**不同板子的 bank 编号和电压完全不是一套**，照抄会翻车。60K 上那三个
  按键在 1.5 V 的 bank 9 里（只能写 LVCMOS15），而 138K（非 Pro）上它们在 3.3 V 的
  Bank 5 里；当时照抄了 60K 的 LVCMOS15，那一行就把整个 bank 锁成 1.5 V，同 bank 的
  另一个脚再写 LVCMOS33 就报 `CT1136 ... vccio(1.5) is locked` ✗。现在所有脚的电平
  都按对应板子的官方资料写。
3. **驱动拓扑**。单端驱动 → 用 `array_pos`、`OUT_DEAD_CYC` 填 0（**当前工程就是这样**）；
   半桥要互补输入 → 先把 `board_top` 那行端口改成同时引出 `array_neg`，在 `.cst` 里给
   `array_neg[0..15]` 分配一组自己的引脚（不能和通道重脚），再把 `OUT_DEAD_CYC` 填成
   死区拍数（4 拍 @50 MHz = 80 ns，够不够要看驱动芯片手册的上升/下降时间）。
   `array_neg` 现在故意没引出：顶层端口没有约束时工具会自动乱摆引脚，可能撞到 DDR/HDMI。
4. **相位符号**。参考实现的约定是「相位码越大越提前」，固件按这个实现；但真实声场取决于
   换能器和驱动级的极性，**必须用麦克风在一个已知位置上量一次**。量反了焦点会跑到镜像点。

## 3. 上电第一件事：先不接阵列，只接串口

顺序很重要，一次只加一样东西，出了问题才知道是谁的问题：

1. 只接串口和下载线，阵列先不接。烧进去之后用上位机「连接」。
2. 应当收到 HELLO 应答：`device=FPGA`、`simulated=0`、`hw_rows=4`、`boot=` 是一个非 0 的数。
3. 板上 5 颗状态灯的含义（从左到右）：输出正在驱动 / 正在扫描 / 走步器在跑 /
   本地模式 / 串口在发。
4. 按下面板上的「播放」键：灯亮、状态帧变成 `state=RUNNING`（注意上位机默认是远程模式，
   要用按键得先 `MODE value=LOCAL`，或者干脆不接电脑、板子自己跑）。
5. 示波器/逻辑分析仪先量空载波形：频率 40 kHz、16 路之间有相位差、暂停时全部拉低。
6. 再接驱动板 + 一个换能器，量电压、电流、温升；最后才接整个 4×4 阵列。

## 4. 综合时看什么

### 4.1 最近一次跑的结果（2026-10-13 实测，138B，命令行 build.tcl）

| 项目 | 实测 | 说明 |
|---|---|---|
| 器件 | **GW5AST-138B / FCPBG676A（676 球）** | 2026-10-14 商业版 IDE 实测 |
| 比特流 | 生成成功（`board_top.fs`，**36.4 MB**） | `board/haptics138kpro/impl/pnr/` |
| 引脚落位 | 25 个脚全部按 `.cst` 落位（P16 时钟 / K16 F15 G15 按键 / N16 P15 串口 / PMOD1+PMOD2 十六路 / R26 J14 L20 灯） | 见 `board_top.rpt.txt` 的引脚表 |
| 逻辑单元（LUT+ALU） | 12341 / 138240（**9%**） | 余量很大 |
| 寄存器 | 7373 / 139140（6%） | — |
| BSRAM | 32 / 340（10%） | 大表（行缓冲、节拍表、相位表）都进了块存储 ✓ |
| DSP | 56 / 298（19%） | 除法/开方/乘法用掉的多 |
| 时序 | **0 条违例**；最差 setup slack **+2.912 ns**、最差 hold slack **+0.185 ns** | 报告列了 50 条最差路径，全部为正 |

⚠️ **结论**：138K Pro 这颗上综合、布局布线、时序全部通过，比特流已生成，**可以烧板子了**。
但**还没有上板实测过任何东西**（没量过波形、没量过声场、没接驱动板）。
「综合通过」只说明工具认为电路能在 50 MHz 下跑，不等于板子上行为正确。

历史记录（60K 时期的两处时序问题，都已修完，留档）：

**第一处（已修）**：配置阶段的关键路径原来是「`blank_us` → 除以 10 → 乘笔数 → 再乘 `repeat` → 比较」，
全挤在一个时钟周期里，36.9 ns，slack −13.5 ns。`hap2_traj_plan` 里把它拆成三级寄存器
（`budget_q`/`blank_beats_q` → `budget_full`/`blank_total_q` → 判定），多花两拍，
最差 slack 从 −13.5 ns 变成 −1.19 ns。语义一字未改（同一个乘积、同一个比较）。

**第二处（已修）**：剩下的违例只有 −1.0～−1.2 ns，而且**散落在输出级、轨迹编译、串口发送
等互不相关的模块上**——这种"到处都差一点点"的形态，配合布局布线时的告警
`PR1014: Generic routing resource will be used to clock signal 'clk_d' ... may lead to
excessive delay or skew`，说明瓶颈是**时钟没有走全局时钟资源**（走了普通布线，
引入了额外偏斜），而不是某个模块逻辑太长。后来是**分四处把真正的长路径拆短**解决的：
`fix_div` 的倒计数器缩到 6 位、`hap2_out` 的占空比门限改成寄存器、
`hap2_traj_plan` 的拍数计算拆成三拍并改走共享除法器、`hap2_field_parse` 的键名比较打一拍。

⚠️ 那个 `PR1014` 告警在 484 球那一轮出现过 1 次。它是告警不是错误，时序分析显示所有
路径都过；含义是「时钟走了普通布线，可能带来额外偏斜」。上板后用示波器量 40 kHz 波形时
留意有没有明显抖动；真有问题再回头处理（加 PLL / 时钟缓冲原语）。

**存储**（按代码里的表算出来的，是估算不是综合报告）：

| 表 | 大小 |
|---|---|
| 串口行缓冲 | 4096 B = 32 Kbit |
| 草图原文缓冲（上下两半） | 8192 B = 64 Kbit |
| 点表 × 2（草图 + 预设图形） | 各约 11 Kbit |
| 节拍表（双缓冲，每半 512 行 × 95 位） | 约 97 Kbit |
| 第一趟的中间表（长度、dx、dy） | 约 25 Kbit |
| 相位表 + 状态快照 | 约 6 Kbit |
| 应答模板（常量 ROM） | 约 7 Kbit |
| **合计** | **约 250 Kbit ≈ 32 KB** |

按 36 Kbit 一块的 BSRAM 算大约 8～10 块；实际综合出来用了 **32 块 BSRAM（10%）**，
对 138K 级别的器件很宽松。

**算术单元**（这些是占 LUT/DSP 的大头，具体数字以综合报告为准）：

- 相位计算里有 `PH_PIPE` 条 42 位开方流水线（默认 4 条），每条是一个逐位逼近的移位减法器；
- 56 位除法器 3 个（轨迹编译、输出级算载波增量、相位计算算常数）；
- 21 位开方 1 个（轨迹编译算段长）；
- 节拍表、相位表都是「同步读」的寄存器堆，综合成 BSRAM 还是分布式 RAM 由工具决定。

想省资源就把 `PH_PIPE` 调小（4→2），代价是相位表扫一遍的时间变长——4×4 时
PIPE=4 大约 100 个时钟扫完，离一个节拍的 500 个时钟（10 µs @50 MHz）还有很大余量。

## 5. 换 8×8（64 路）时要动什么

1. `board_top` 的参数：`HW_ROWS = 8`、`HW_COLS = 8`，`PH_PIPE` 建议 8。
2. `.cst` 里补 `array_pos[16..63]` 的引脚（需要 64 个通道，两个 PMOD 已经不够，
   那时候驱动板应该有自己的接口定义了）。
3. 复位重启一次（`boot` 会变），上位机会重新握手，握手应答里的 `hw_rows/hw_cols` 会变成 8。
4. 仿真里已经先跑过一遍 8×8（`tb_phase.v` 的 8×8 用例 + `tb_top8.v` 的端到端），
   上板前可以先看那两条有没有过。

## 6. 还没做的（上板相关的）

- 半桥互补输出的引脚（现在只留了 `array_neg` 端口，没有分配引脚）；
- `path_xy_um` 旧点列的解析（协议里那条老路，固件目前不声明 `CUSTOM_XY`）；
- 本地按键的第三个键（停止）借用相机口一个脚，等面板定型后再定；
- 烧板子之后的真机验证（`.fs` 已经生成，但还没烧进板子跑过第一次）。
