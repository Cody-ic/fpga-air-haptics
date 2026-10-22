# XIAO MG24 接收采样固件

日期：2026-10-22。用于现有接收板的固定测点采样，属于独立接收设备；不实现发射控制。USB 版已烧录到实板，并在 COM9 完成信息查询与三次 200 点回传，见 [首次实板验证](VALIDATION_2026-10-22.md)。双模式校准代码见 [FPGA 校准说明](../../fpga/CALIBRATION.md)，人工模式不需要 MG24，自动模式由电脑转发测量值。已知输入幅值、外部采样时序精度、声学效果及蓝牙链路仍待验证。

## 1. 通信模式

只修改 [receiver/config.h](receiver/config.h) 顶部：

```cpp
#define MG24_USE_BLUETOOTH 0
```

| 值 | 使用方式 |
| --- | --- |
| `0`（默认） | 开发板自带 USB 连接电脑，串口 115200；不启动蓝牙广播 |
| `1` | BLE（Bluetooth Low Energy，低功耗蓝牙）连接电脑；USB 仍用于供电和烧录，不混入协议日志 |
| 其他 | 编译报错 |

修改后必须重新编译、烧录。两种模式均选择 `Seeed Studio XIAO MG24 (Sense)` 和 `BLE (Silabs)` 协议栈。仓库的 `build.ps1` 会分别在临时副本中指定 0、1，保留源码中的手动设置。

## 2. D 盘环境

- Arduino IDE（Integrated Development Environment，集成开发环境）2.3.10：`D:\tools\ArduinoIDE`。
- Silicon Labs 开发板包固定 5.0.0；ArduinoJson 固定 7.4.2。
- 工具包：`D:\ArduinoData\packages-data`；第三方库：`D:\ArduinoData\sketchbook\libraries`；下载：`D:\ArduinoData\downloads`。
- 编译缓存及中间文件：`D:\ArduinoData\build-cache`、`D:\ArduinoData\build`；日志：`D:\ArduinoData\logs`。
- Windows 用户目录 `.arduinoIDE/arduino-cli.yaml` 只保留指向 D 盘的配置；如已有配置，安装脚本先备份为 `.before-mg24`。IDE 自身的小型用户状态可能仍保留在用户目录。

从仓库根目录运行（需要 Python 3.11+ 和网络）：

```powershell
powershell -ExecutionPolicy Bypass -File firmware/xiao_mg24/setup_environment.ps1
python -m pip install -r firmware/xiao_mg24/requirements.txt
powershell -ExecutionPolicy Bypass -File firmware/xiao_mg24/build.ps1
```

安装从官方地址下载，开发板包及依赖由 Arduino 包管理器校验。大文件下载支持分段恢复。脚本和编译均不会上传固件，也不会烧录引导程序或擦除开发板。

## 3. 接线

```text
现有接收板 U4 的 ADCV_P（模拟输出） ─── MG24 A0 / D0 / PC00
现有接收板 GND                     ─── MG24 GND
MG24 USB-C                         ─── 电脑（有线模式）或供电
```

接收板继续使用其既有 **5 V 电源**，保留 R13/R17 分压和现有增益、滤波电路。不要将接收板 5 V 电源接入 MG24 的模拟输入或 3V3 引脚。ADCV_P 的历史名义范围约 0～0.97 V，接线前按实物版本用示波器核对；MG24 输入按 3.3 V 供电域配置。测量参考点是外接接收头，不使用 Sense 板载麦克风。

先关闭发射检查背景，再低增益测量。前级模拟削顶不一定触及 MG24 的原码边界，软件的 `adc_rail=false` 不能证明接收前端没有削顶。

## 4. 采样与指标

使用 MG24 的 IADC（Incremental Analog-to-Digital Converter，增量式模数转换器）本地硬件定时器触发单次转换，通过 DMA（Direct Memory Access，直接内存访问）保存有限窗口；不使用普通循环 `analogRead()` 或默认连续采样速率。

- A0 / PC00，12 位单端输入，名义参考 3300 mV，1 倍模拟增益。
- 目标采样率 400000 点/秒，每次 200 点，名义窗口 0.5 ms。
- 独立 FSRCO（Fast Startup RC Oscillator，快速启动阻容振荡器）时钟经源时钟分频后驱动本地定时器；转换时钟另有分频，不能重复用于本地定时周期。`sample_hz` 从源时钟频率、源分频和定时周期计算。它是配置推导值，不是示波器实测校准值。
- DMA 结束回调关闭采样触发；主循环完成分析和发送，避免在中断内执行 JSON 格式化或蓝牙发送。
- 40 kHz 分量使用“常数项＋正弦＋余弦”的最小二乘拟合，按实际配置采样率计算，避免非整周期窗口中的直流偏置干扰结果。
- `mean_adc` 是原码均值；`rms_adc` 是去直流后的 RMS（Root Mean Square，均方根）；`peak40_adc` 是 40 kHz 分量峰值。后两项再按名义参考电压换算为 mV，不表示绝对声压。
- `start_us/end_us` 是本次采样启动的软件时间与 DMA 完成时间，采用板端单调微秒时间；不等于电脑收包时间，也不是逐样本时间戳。

上电等待请求，不持续采集或发送；采样超时为 10 ms。采样溢出或超时返回错误，不补零、不返回假成功。

## 5. 接收端协议 MG24_RX1

USB 和蓝牙使用相同 UTF-8 JSON（JavaScript Object Notation，结构化数据格式）行，以 `LF` 换行分帧。请求上限 255 字节，回复上限 2047 字节；蓝牙的一次写入/通知可以只是半行，必须重组。未知命令或类型错误返回错误。

```json
{"id":1,"cmd":"INFO"}
{"id":2,"cmd":"CAPTURE","raw":false}
{"id":3,"cmd":"CAPTURE","raw":true}
{"id":4,"cmd":"STOP"}
```

`id` 为 0～4294967295 的整数。回复包含 `proto="MG24_RX1"`、相同 `id`、随机启动标识 `boot`、`simulated=false` 和 `ok`；不能把它作为 HAP3 发射设备连接。

- `INFO`：清理旧采样及待发结果，开启干净的逻辑会话，回复型号、通信方式、采样设置及接收能力。USB 经板载桥接，不依赖虚拟串口控制信号识别断线；电脑每次连接都必须先请求 INFO。
- `CAPTURE`：一条命令获取一个新窗口，完成后仅返回一条结果；`raw` 默认 false，true 时附加 200 个原码。包含采样起止、速率、量程信息、均值、均方根、40 kHz 峰值及 `valid/adc_rail`。
- `STOP`：停止窗口。若原窗口尚未交付，为原采样编号返回 `CANCELLED`，再对 STOP 编号确认；已完成结果可能已在链路上，电脑必须按编号核对。
- 并行 CAPTURE 返回 `BUSY`。其他错误包括 `BAD_REQUEST`、`UNKNOWN_COMMAND`、`FRAME_TOO_LONG`、`FRAME_TIMEOUT`、`ADC_ERROR`、`ADC_TIMEOUT`、`ADC_OVERFLOW`。
- `adc_rail` 表示窗口出现原码 0 或 4095，此时返回数据但 `valid=false`。不触轨的结果也仍需要检查模拟前端、稳定性和信噪比。
- 回复队列最多四条；传输连续 3 秒没有进展或缓冲溢出时清理采样与队列，蓝牙模式关闭连接。断线/取消订阅清除未完成和待发结果，重连必须重新请求。

蓝牙名称 `TouchSee-MG24-RX`，使用 Nordic UART Service（北欧串口服务），单客户端：

| 项目 | UUID（Universally Unique Identifier，通用唯一标识符） | 方向 |
| --- | --- | --- |
| 服务 | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | 接收设备 |
| RX | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | 电脑写入命令 |
| TX | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | 设备通知结果 |

电脑先订阅通知，再发 INFO。支持 20 字节小包；设备发送端按协商长度分包，资源不足时等待，不忙循环丢弃后续帧。

## 6. 电脑测试与记录

```powershell
# 有线：将 COM5 替换为实际 MG24 串口
python firmware/xiao_mg24/receiver_client.py info --port COM5
python firmware/xiao_mg24/receiver_client.py capture --port COM5 --raw --repeat 3 --output firmware/xiao_mg24/.runtime/usb.jsonl

# 蓝牙：先烧录宏=1版本，扫描后使用真实地址
python firmware/xiao_mg24/receiver_client.py scan
python firmware/xiao_mg24/receiver_client.py capture --ble "设备地址" --raw --repeat 3 --output firmware/xiao_mg24/.runtime/ble.jsonl

python -m unittest discover -s firmware/xiao_mg24/tests -v
```

记录保存原始回复和电脑接收时间；板端与电脑时钟不能直接比较。电脑校验协议、启动标识、请求编号、窗口长度和质量字段；失败直接报错，不用旧结果替代。

首次烧录：打开 `receiver/receiver.ino`，选 MG24、BLE (Silabs) 和真实端口，确认已有固件用途后再上传。官方首次引导程序操作会擦除内容，不应在仅编译验证时执行。

## 7. 验证边界

环境与最初离线范围见 [2026-10-21 离线验证记录](VALIDATION_2026-10-21.md)，后续烧录、串口兼容性与定时修复、USB 实板结果见 [2026-10-22 验证记录](VALIDATION_2026-10-22.md)。

原生 C++ 检查运行固件同一份协议与分析代码；合成信号测试不是实板采样。两种模式须分别通过目标编译。上板后仍需完成：已知 40 kHz 信号的采样率与幅值核对、两种传输结果一致性、前级削顶检查、蓝牙断线与重连、长时间空闲后的新采样，以及与 FPGA 的后续测量编号关联。

接收固件保持独立；双模式校准工具另行接入 FPGA，现有发射页面和接收模拟电路保持不变。不自动烧录，不声明已完成声学校准。

## 9. 官方参考资料

- [Seeed XIAO MG24 入门与引脚](https://wiki.seeedstudio.com/xiao_mg24_getting_started/)：A0/PC00、USB 与板卡接口。
- [Silicon Labs Arduino Core 5.0.0](https://github.com/SiliconLabsSoftware/arduino/tree/5.0.0)：锁定的工具包版本；本实现使用独立硬件定时采样，不把默认连续 ADC 速率当成 400 kHz。
- [官方 IADC 硬件定时与 DMA 示例](https://github.com/SiliconLabsSoftware/peripheral_examples_gsdk/tree/master/series2/iadc/iadc_single_low_current)：本地定时触发与 DMA 外设接口依据。
- [Silicon Labs BLE 服务示例](https://github.com/SiliconLabsSoftware/arduino/blob/5.0.0/libraries/SiliconLabs/examples/ble_spp/ble_spp.ino)：动态服务、通知、连接事件接口依据；本项目另定义 Nordic 服务与接收协议。
- [Arduino 目录配置](https://docs.arduino.cc/arduino-cli/configuration/)：D 盘数据、库及缓存路径。
- [EFR32xG24 参考手册](https://www.silabs.com/documents/public/reference-manuals/efr32xg24-rm.pdf)：本地采样定时器按 `CLK_SRC_ADC` 计数。
