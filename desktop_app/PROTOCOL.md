# HAP3 设备协议草案（串口／BLE）

更新日期：2026-09-30。本文是 `protocol.py`、`controller.py`、`demo.py` 当前使用的草案，尚未与 FPGA 端共同定稿。已有 [NUCLEO-F411RE 串口验证固件](../firmware/nucleo_f411re/README.md)，完成编译及宿主机交叉测试，尚未实板联调；BLE 模块也尚未选定。后续协议调整需同步修改两端及测试。

F411RE 固定 16 路、40 kHz、64 级相位，默认 4×4、11 mm 平面；也可编入逐路三维坐标表。不支持的参数整包拒绝。它要求每次 REMOTE 握手后重新 CONFIG，才允许 START；错误码包含 `CONFIG_REQUIRED`、`SCAN_TOO_FAST`、`DMA_UNDERRUN` 等及可选 `drive_on` 状态字段，具体时序量化与限制见固件说明。上位机保持对其他合法 HAP3 设备的兼容。

HAP3 在实际坐标基础上增加多段路径和关闭输出的跳转，拒绝 HAP1／HAP2 固件，避免旧设备将断开轮廓连起来。旧 JSON 文件可迁移；串口不自动降级。

## 1. 传输与帧格式

串口默认 115200 baud、8N1、无流控；BLE 传输映射见 [BLE 对接说明](BLE.md)，不使用 COM 口和电脑端波特率。两种连接暂共用以下应用层草案：ASCII 文本，一帧一行，最大 8192 字节（含 CRC 和换行）：

```text
HAP3 KIND SEQ VERB key=value key=value*CCCC\n
HAP3 CMD 1 HELLO*F6AD\n
```

以上 `\n` 表示一个 LF 字节。发送用 LF，接收兼容 CRLF。字段以单个空格分隔，不允许重复字段名；字段名符合 `[a-z][a-z0-9_]*`，值使用 `[A-Za-z0-9_,.?:+|\-]+`，顺序不影响含义。

- `KIND`：`CMD`、`ACK`、`ERR`、`TEL`。
- 命令 `SEQ` 为 1～65535；ACK/ERR 原样回显命令序号和动词。遥测为 `TEL 0 STATE`。
- CRC16-CCITT-FALSE：多项式 `0x1021`、初值 `0xFFFF`、不反射、异或输出 `0x0000`；覆盖从 `HAP3` 到最后一个字段的原始 ASCII 字节，不含 `*`、CRC 或换行。输出四位大写十六进制。校验向量 `123456789 → 29B1`。
- 接收端重组分片，超长帧整行丢弃至下一个 LF。校验失败、截断或解析失败不得应用部分配置。客户端不自动重发控制命令。

## 2. HELLO：能力、实际阵列与命令范围

HELLO 返回 ACK 后发送一帧完整 STATE。以下为 ACK 字段示例，实际传输需合成一帧并附 CRC：

```text
proto=3 device=FPGA boot=boot_id simulated=0 hb_ms=3000
caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,CUSTOM_XY,SCAN_PATHS
max_rows=16 max_cols=16 max_channels=256 max_nodes=64 max_scan_points=256 max_strokes=32
hw_rows=4 hw_cols=4 hw_pitch_um=10000 mapping=ROW_MAJOR_XY
x_min_um=-100000 x_max_um=100000 y_min_um=-100000 y_max_um=100000
z_min_um=20000 z_max_um=300000
```

`simulated=0` 为真实固件；Demo 固定为 1。客户端拒绝来源与连接模式不符的设备。`boot` 每次复位改变，同一会话保持不变。客户端要求 `hb_ms ≥ 2000`，Demo 使用 3000。

`max_*` 表示容量，`hw_*` 表示实际接线阵列，两者不能混用。行列各 1～16、名义间距 1000～30000 µm。默认 `ROW_MAJOR_XY` 表示完整规则平面矩形，通道 `r*hw_cols+c` 的坐标为：

```text
x = (c - (hw_cols-1)/2) * hw_pitch_um
y = (r - (hw_rows-1)/2) * hw_pitch_um
z = 0
```

阵列中心为原点，列向 +x、行向 +y、发射方向为 +z。半间距位置可能包含 0.5 µm，板端定点实现需正确表示。

F103 的专用逐通道诊断固件可返回 `profile=CHANNEL_TEST`，仅声明 `STOP,STATE,PHASE,GEOMETRY` 能力，另以 `adc_capture=1` 声明采样。HELLO/PING/SNAP 不干预板上有限测试序列，STOP 取消整轮；CONFIG/MODE/START/PAUSE/CALIBRATION 等返回 `CHANNEL_TEST_ONLY`。等待、停顿和完成状态允许 `channel_mask=0`，同时 `output=0`。普通图形客户端仍要求业务能力，应明确拒绝此固件；使用 F103 的专用只读监视脚本，不将零掩码当作业务配置导入。

### 2.1 可选三维坐标扩展

三维设备使用 `mapping=EXPLICIT_XYZ geometry_id=<24位小写十六进制>`，声明 `GEOMETRY` 能力。`hw_rows*hw_cols` 仍为通道数，但行列与间距不能替代实际坐标。HELLO、CONFIG、STATE 均带同一标识，固件拒绝不匹配的 CONFIG。

命令 `GEOMETRY start=0 count=16` 分块读取，count 为 1–16，不改变输出。专用 ACK 包含 `geometry_id start count sound_speed_mm_s elements`；每个元素为六个十进制整数 `x_nm,y_nm,z_nm,nx_ppm,ny_ppm,nz_ppm`，元素之间用 `|`，按通道顺序连续排列。有效 count 不能越过通道数。

坐标范围 ±1 m，朝向模长约 1000000 ppm；声速范围 300000–380000 mm/s。标识为紧凑 JSON `[rows,cols,pitch_um,sound_speed_mm_s,elements]` 的 SHA-256 前 24 个十六进制字符，JSON 分隔符为 `,`、`:`，无空白。单块上限避免 256 路坐标挤爆 8192 字节帧。

电脑在坐标表完整、标识核验成功后才宣布 ready，读取期间继续心跳。STATE 只回传标识，不重复坐标。文件格式和机械坐标来源见[阵列与校准说明](ARRAY_CALIBRATION.md)。旧手机端会明确拒绝此映射，平面协议兼容不变。

坐标范围是固件接受命令的包围盒，不保证触觉清晰度。以上范围仅为 Demo 示例，实际固件应声明自身验证范围。客户端允许 x/y 边界在 ±400000 µm 内，z 边界在 20000～300000 µm 内，且每轴下界小于上界。

## 3. CONFIG：一次提交完整图形

所有数值字段为十进制整数。CONFIG 包含下表全部字段及 `hw_rows hw_cols hw_pitch_um mapping`，后四项必须与 HELLO 一致。

| 字段 | 范围／含义 |
|---|---|
| `carrier_hz` | 20000～80000；默认 40000，固件可拒绝自身不支持的取值 |
| `phase_steps` | 8、16、32、64、128、256；默认 64 |
| `cx_um cy_um` | 各 -100000～100000；图形整体平移 |
| `z_um` | 20000～300000；所有路径点共用高度 |
| `radius_um` | 0～80000；预设图形半径／半长，自定义图形忽略 |
| `repeat_millihz` | 10～200000；完整路径每秒循环次数乘 1000 |
| `mod_hz` | 0～1000；调制频率，0 表示无调制请求 |
| `level` | 0～100；归一化驱动等级，不是电压或声压 |
| `shape` | `POINT LINE_X LINE_Y CIRCLE SQUARE TRIANGLE ARROW CUSTOM` 之一 |
| `path_xy_um` | `NONE` 或最多 64 个 `x:y` 坐标对，以逗号分隔 |
| `path_closed` | 1 闭合循环；0 沿原路径往返 |
| `scan_paths` | `NONE` 或多段坐标；段内用逗号，段间用 `\|`；最多 32 段、合计 256 点 |
| `blank_us` | 100～100000；每段结束至下一段开始的关闭输出时间，默认 2000 µs |

坐标为相对图形中心的有符号整数微米，每个分量在 ±300000 µm 内。板端目标为 `(x+cx_um, y+cy_um, z_um)`。例如：

```text
shape=CUSTOM path_xy_um=-17321:-12456,18234:-11098,1234:21678 path_closed=1 scan_paths=NONE blank_us=2000
```

三个顶点定义一个三角形；它们不是阵元编号，也不是同时存在的三个焦点。CUSTOM 至少需要一个点，一个点表示静止焦点。相邻点不能相同，闭合路径不重复首点。非 CUSTOM 可以携带自定义草稿，播放时忽略该草稿。设备的 `max_nodes` 可低于 64，客户端下发前检查。

CUSTOM 按路径长度匀速插值。闭合时补最后一点到首点的线段；往返时沿原线段返回，不添加斜向闭合边。SQUARE 以 `radius_um` 为半边长，从左下角向右沿四边循环；预设图形定义以 `model.py::trajectory_point` 为参考。不支持某图案的固件须明确拒绝 CONFIG。配置须同时通过参数范围、实际路径包围盒和阵列匹配检查，完整验证后原子替换，`rev` 加一，再发 `ACK applied=1 rev=N` 和完整 STATE；拒绝时返回 ERR 并保留原配置。

上位机只有在本次连接内发送 CONFIG、收到 ACK 及其后配置内容与版本一致的 STATE 后，才允许点击播放。新连接默认图形相同、只有 ACK、编辑后的草稿未发送等情况均不能解锁播放。

容量是传输和执行选择，不是触觉像素数；1 µm 坐标精度不代表物理定位精度。没有多焦点或手部跟踪。

### 3.1 多段草图执行

`path_xy_um` 与 `scan_paths` 不得同时非 NONE。`scan_paths` 非 NONE 时覆盖旧路径执行逻辑，`path_closed` 不生效，每段只沿显式相邻坐标前进；闭合段必须把首点显式重复在末尾。例：

```text
shape=CUSTOM path_xy_um=NONE path_closed=1 scan_paths=0:0,10000:0,10000:10000,0:0|30000:0,40000:0 blank_us=2000
```

电脑保留草图几何、尺寸和关系，并在发送前按轮廓顺序减去已经呈现的公共部分、离散曲线；板端收到的是完整有序路径，不执行 CAD 约束求解。FPGA 仍须独立完成线段插值、实时相位计算与同步输出。

设段数为 N、整幅重复周期为 T=`1000/repeat_millihz` 秒，单次关闭输出时间 B=`blank_us/1e6` 秒，须满足 `T>N*B`。剩余 `T-N*B` 按各段长度分配；单点或长度小于 0.1 mm 的段按 0.1 mm 权重分配。每段结束后将扫描开关设为 0，目标在 B 内从本段末点移到下段首点，再开启扫描；最后一段返回第一段也适用。关闭期间换能器输出必须关闭，不能输出连接线。`repeat_millihz` 表示所有段加跳转完成一圈。

STATE 必须同时回传实际扫描开关、当前段序号及位置，PC 不根据动画猜测开关。设备声明 `SCAN_PATHS`、`max_scan_points`、`max_strokes`，客户端在下发前核对容量；Demo 校验完整配置后才原子替换。不足以容纳跳转的重复频率会拒绝。默认 2000 µs 仅为软件参数，实际相位切换、声场消散及触觉效果须硬件验证。

## 4. 控制命令与模式

| 命令 | 条件与动作 |
|---|---|
| `HELLO` | 握手并读状态，不启动输出 |
| `PING` | 刷新主机心跳，返回 ACK |
| `CONFIG` | 仅 REMOTE 且 IDLE；更新配置并将路径进度归零 |
| `MODE value=LOCAL/REMOTE` | 仅 IDLE；切换控制来源 |
| `START` | 仅 REMOTE 且 IDLE/PAUSED；开始／继续 |
| `PAUSE` | 仅 REMOTE 且 RUNNING；冻结路径进度，关闭输出 |
| `STOP` | 两种模式均可；关闭输出，回到 IDLE 并将进度归零 |
| `SNAP` | 请求完整状态，不改变输出 |

除 HELLO / CAPTURE / GEOMETRY 的专用应答外，成功命令返回 `ACK applied=1 rev=N`；除 PING 外紧跟 STATE。ERR 格式为 `ERR SEQ VERB code=...`，示例码包括 `BUSY`、`LOCAL_CONTROL`、`BAD_CONFIG`、`OUT_OF_WORKSPACE`、`HARDWARE_MISMATCH`、`NOT_RUNNING`、`BAD_MODE`、`UNKNOWN_COMMAND`。非法命令不改变已生效配置或运行状态。

Demo 的本地 NEXT/PLAY/STOP 为模拟物理按键，不是串口命令。NEXT 只在 LOCAL+IDLE 生效，校验新图形范围后 `rev` 加一；本地停止键在两种模式均有效。

电脑端“电脑控制”操作可组合现有命令：LOCAL 非 IDLE 时先 STOP，仅在该请求 ACK 后收到更新的 IDLE、output=0 状态才发送 `MODE value=REMOTE`。失败、超时或用户停止会取消后续切换；最终仍等待 MODE 的 ACK 和状态，不隐含 CONFIG/START。线上命令及 MODE 的 IDLE 限制不变。

### 4.1 CAPTURE：可选 ADC 短窗口扩展

2026-09-28 新增，F411、F103C8 与电脑 Demo 已实现；手机仅验证兼容，不提供波形页面。HELLO 可加独立字段 `adc_capture=1`，缺省表示不支持；基础 `caps` 和 STATE 不变。旧客户端忽略该可选字段，新电脑端仅在声明能力后启用采集。

命令为 `CMD SEQ CAPTURE`，不带参数。成功时只发一帧延迟 ACK，窗口完成前不提前确认，也不触发完整 STATE。下面是应答字段示意，`raw` 实际必须包含 200 个逗号分隔整数，不能使用省略号：

```text
boot=boot_id rev=1 uptime_ms=1234 simulated=0 tx_running=1
pin=PA0 fs_hz=400000 bits=12 n=200 raw=<200个0至4095的十进制整数>
```

帧仍按第 1 节格式及 CRC 编码，ACK 原样返回请求序号。`rev/uptime_ms/tx_running` 在采样启动时记录；运行状态不是整个窗口的输出门控，也不用于改变播放状态。`boot/simulated` 必须与会话一致。客户端严格校验版本固定的引脚、位数、采样率、样本数及数值范围，拒绝坏帧，不能以历史波形填充新结果。

采样可在待机、播放或本地模式请求，不要求 CONFIG，不改变图形，也不刷新心跳。单个窗口 200 点、400 kS/s，占 0.5 ms；F411 同时最多一个未完成请求，启动间隔至少 200 ms，电脑连续查看间隔至少 500 ms。窗口外不采样，不能当作连续示波器或整幅图形测量。

F411 采样外设等待上限 20 ms；结果等串口发送队列空闲再回传，不阻塞 STOP/按键处理。错误码包括 `UNSUPPORTED`、`BAD_FIELDS`、`ADC_BUSY`、`ADC_RATE_LIMIT`、`ADC_START_FAILED`、`ADC_ERROR`、`ADC_TIMEOUT`。新 HELLO 取消旧会话采样；STOP 关闭发射，已开始的采样仍可返回并保留启动时元数据。客户端既有 ACK/STATE 超时仍有效。

ADC 原码按 `code × Vref / 4095` 换算，默认 Vref=3300 mV，允许手工校准；去均值后估计交流 RMS、峰峰值及 40 kHz 正交投影峰值。默认 ×3.4 仅近似还原原接收板末级分压前的交流幅值，不能增加有效分辨率或恢复削顶。数据没有声压单位、位置坐标或校准相位。Demo 同格式返回 `simulated=1` 的确定性合成波形。

### 4.2 CALIBRATION：可选逐通道相位修正

设备须声明 `CALIBRATION` 能力。命令为 `CALIBRATION action=trial/store geometry_id=<标识> mask=<十进制位掩码> offsets=<逐路逗号分隔相位码>`，四个字段必须齐全。平面设备标识按第 2.1 节的规则生成平面坐标后计算，不能省略；相位码为正向附加量 `0..phase_steps-1`，与名义聚焦相位相加取模。bit 0 对应通道 0，屏蔽通道实际保持 LOW。

仅 REMOTE、IDLE、无采样请求在途可更改。`trial` 还要求已配置 POINT、mod_hz=0、level=100；它设置临时相位与非零掩码，不立即输出，仍需 START。`store` 要求全部通道使能，仅写 RAM，并同时替换全部通道修正。成功后 rev 递增并回传 STATE。

STOP/本地停止/心跳停止/REMOTE 新握手撤销临时 trial、恢复已有修正并要求重新 CONFIG；CONFIG 总是恢复全通道和已保存修正。临时 trial 存在时不允许切换 LOCAL。断电清空保存值；试验中收到错误不能当作修正成功。主机完整核验 ACK、STATE、采样元数据及复测结果后才发送 store。错误码包含 `BAD_CALIBRATION`、`STEADY_POINT_REQUIRED`、`BUSY`；无效修改不部分生效。

STATE 可选字段 `channel_mask`（十进制）与 `phase_offsets`（逐路码）说明实际门控与修正；缺省按全通道、零修正处理。`phases` 必须是叠加修正后的实际相位，不能再次由客户端加修正。详见[校准流程及边界](ARRAY_CALIBRATION.md)。

### 4.3 F103 测试模式识别

2026-10-10 新增可选 HELLO 字段：`fw_id` 标明固件标识，`firmware_mode=0/1/2/3` 分别为业务、逐通道、固定单路、固定多路；单路模式回传 `pin_channel`，多路模式回传起始 `group_first`、结束 `group_last`（首尾均包含）及程序自动计算的十进制 `group_mask`，两者均回传 `on_ms`（0 表示持续）。测试固件保留 `profile=CHANNEL_TEST`，仍使用 HELLO/PING/SNAP 和 STATE，不新增寄存器诊断命令。旧客户端忽略这些可选字段；仅允许单路的监视器不适用于多路模式。

`serial_diagnostics.py` 保存原始收发字节、带时间报文和包含 MCU/CN1/PCB 通道的状态 CSV；退出仅释放串口，不发送 STOP。REMOTE 业务会话中的 HELLO 仍遵守第 2 节会话规则，会停止旧控制会话，此工具主要用于本地测试。STATE 是数字状态回传，不能替代引脚波形或声压测量。

## 5. STATE：原子数字快照

STATE 包含完整 CONFIG 字段、实际阵列字段以及：

| 字段 | 含义 |
|---|---|
| `boot` | 本次设备启动标识 |
| `sample` | 每份完整快照递增，同一 boot 内不回绕 |
| `uptime_ms` | 本次启动的单调运行时间，毫秒 |
| `rev` | 已应用配置版本；配置或本地图形选择变化时递增 |
| `mode state` | LOCAL/REMOTE；IDLE/RUNNING/PAUSED/FAULT |
| `output` | 0/1；1 仅允许 RUNNING 且 level>0 |
| `scan_on` | 0/1；是否处于本段扫描区间，段间跳转为 0；output=1 必须同时 scan_on=1 |
| `stroke_index` | 从 0 开始的当前段序号；跳转时保留刚完成的段号，旧路径或预设为 0 |
| `simulated reason` | 数据来源；停止或异常原因，无原因用 NONE |
| `fx_um fy_um fz_um` | 与相位快照同一时刻的目标坐标 |
| `phases` | 按实际阵列通道顺序排列的逗号分隔相位码 |

相位数量严格等于 `hw_rows*hw_cols`，每项在 `0..phase_steps-1`。参考模型采用 `p=Σexp(j(kr+phase))/r`，发送相位为 `-kr` 的量化值；若 RTL 使用延迟码，需在接口层换算。固件必须锁存同一时刻的已应用寄存器、坐标及相位表，再异步发送，不能混用两帧数据。

客户端从 STATE 更新界面，ACK 单独到达不改变实际显示。旧 sample、倒退 uptime/rev 被丢弃；boot 或阵列改变要求重新握手。数字寄存器回读不等于测得换能器电压或声压。

## 6. 时序、断线与实现边界

Demo 运行时每 0.05 秒回传完整快照，其他状态每 0.5 秒回传，另外在控制命令后即时回传。Demo 使用内存传输，其运行回传量不作为串口带宽目标；真实固件应按波特率与帧长度安排回传，8N1 占用时间约为 `帧字节数×10/baud`。客户端每 0.8 秒发送 PING，ACK 超过 2 秒或状态超过 2.5 秒未到达则关闭会话；界面超过 1.6 秒标为过期，控制命令在 3.5 秒内没有后续匹配版本快照也会断开。低波特率可能无法在这些期限内传输复杂草图，必须缩短回传或使用足够的波特率。

REMOTE 运行或暂停期间，板端超过 `hb_ms` 未收到 PING，应关闭输出、回到 IDLE 并报告 `HEARTBEAT_TIMEOUT`。客户端断开 REMOTE 时尝试 STOP，但失联后不能保证命令送达。LOCAL 断开保留独立运行，不应用主机心跳停机规则。上电和复位必须关闭输出，重连不隐含 START。

PC 一次提交完整图形，由 FPGA 自己插值、相位求解和高速循环输出。`repeat_millihz` 是轮廓循环频率，不是相位更新率、载波或串口回传频率。当前协议未报告实际相位更新率，后续固件须补充测量与能力声明，不能从界面刷新推断性能。

Demo 默认 0.5 次/秒，用于看清路径；仅在取快照时计算当前理论位置和相位，未实现硬实时更新循环。`mod_hz` 被保存与回传，参考声场未模拟调制包络。高速扫描、多焦点、手部移动适应性及图形辨识均需真实硬件验证。
