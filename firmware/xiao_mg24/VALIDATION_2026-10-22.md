# MG24 首次实板烧录与 USB 采样验证

日期：2026-10-22；分支：`feat/mg24-receiver`。用户明确授权烧录新接入的 XIAO MG24。当前运行 USB 接收版，`MG24_USE_BLUETOOTH=0`；没有改变 FPGA 固件或启动发射、校准。

## 1. 目标与烧录

- Windows 识别到 Seeed Studio XIAO MG24 (Sense)，设备标识 `VID_2886&PID_0062`，串口 **COM9**，板载下载接口正常。
- 下载工具进一步读到 MG24B220、1536 KiB 程序存储器；选择现有 Silicon Labs 5.0.0 的 MG24／BLE (Silabs) 编译配置。
- 烧录前读取并保存原程序存储器，大小 1572864 字节。没有执行整片擦除、修改安全配置或更新板载下载器固件。
- 使用板载下载接口写入应用 `receiver.ino.hex`，地址从 `0x08006000` 开始，保留原有引导程序。最终下载工具退出码 0，明确报告 `Programming Finished` 和 `Verified OK`，重启后应用能应答。

本机证据目录：`.runtime/mg24/hardware/2026-10-22/`。`before-upload.bin` 为原程序备份，`upload-usb-final.log` 为最终下载日志，`final-manifest.json` 为源码与两版产物校验清单。这些本机文件不提交仓库。

## 2. 首次接入发现并修复的问题

### 串口回复没有发送

旧固件虽已写入，但信息查询超时。通过板内调试确认程序在运行、接收到查询并生成回复，随后回复因发送无进展被清理。

锁定版本的 Silicon Labs `UARTClass` 没有重写 `availableForWrite()`，沿用 `Print` 默认的 0；其含义是写入可能阻塞，不能解释为禁止发送。旧逻辑只在返回正数时写入，因此始终没有发出回复。

新增 `usb_transport.h`，每次直接发送最多 32 字节；115200 波特率下约 2.8 毫秒，有限窗口采样及结束中断仍独立运行。使用同一发送函数的原生测试模拟默认返回 0 的串口类，先复现失败，再确认完整回复能分块发出。

### 本地定时器使用了错误的时钟分频

串口恢复后，初次 200 点窗口的时间戳差为 274～275 微秒，与配置声称的 400000 点/秒不符。这些中间结果仅用于排查，不能用于声学幅值或相位校准。

根据 EFR32xG24 手册，`IADC_TIMER` 使用 `CLK_SRC_ADC` 计数。旧计算额外除了一次转换时钟分频，使触发速率偏快。现改为按源时钟分频后的时钟计算定时周期与报告速率，不再除转换时钟分频。修复后重新编译、烧录并验证窗口耗时。

## 3. 最终结果

| 检查 | 结果 |
| --- | --- |
| USB 版目标编译 | 通过；程序存储统计 270164 字节，全局变量 32612 字节 |
| 蓝牙版目标编译 | 通过；程序存储统计 275448 字节，全局变量 32648 字节；本轮未烧录蓝牙版 |
| 软件测试 | 27 项通过，含新增串口发送回归及现有分析、协议、校准窗口启动检查 |
| COM9／115200 信息查询 | 成功，型号 `TouchSee-MG24-RX`、协议 `MG24_RX1`、来源 USB、非模拟数据 |
| 连续三个采样请求 | 各返回 200 个 12 位原码，编号正确递增，同次启动标识一致 |
| 板端时间戳差 | 518、519、519 微秒 |
| 配置推导采样率 | 400000 点/秒 |
| 量程标志 | 这三个窗口均未触及 0／4095 边界 |
| STOP | 正常应答，随后关闭电脑串口 |

最终 USB 应用校验值：

```text
receiver.ino.hex SHA-256 d52dd4ed3d99d770db6411be0545d8da85a0c2dac66baec5c0c54453d9420fc0
receiver.ino.bin SHA-256 858be98756f9e15d0b1807b7900dff730bfff473542aa4c0574a90c0ebb27f72
```

原始采样和信息回复在 `usb-final-captures.jsonl`。软件复现命令：

```powershell
python firmware/xiao_mg24/receiver_client.py info --port COM9
python firmware/xiao_mg24/receiver_client.py capture --port COM9 --raw --repeat 3
python -m unittest discover -s firmware/xiao_mg24/tests -v
```

## 4. 验证边界与下一步

本次未确认 A0 外部接线或输入信号，采样值只能证明采集与回传链路可用，不能当作已知声学输入。`valid=true` 只表示通过现有数据检查，不能证明接收模拟前端没有削顶。

时间戳包含启动及结束中断开销，518～519 微秒与目标 0.5 毫秒窗口基本一致，但不等于用外部仪器校准了振荡器。下一步仍需已知 40 千赫输入，核对采样间隔、幅值、直流偏置和前端量程；蓝牙广播、传输和重连尚未上板测试。

FPGA 此前下载的是示波器来源，不能仅在电脑窗口改选 MG24 就变为自动来源。进入自动校准前需要按原计划构建、下载 `FPGA_CALIBRATION_SOURCE=1` 的版本，重新核对 FPGA 串口及接收板接线，再执行一轮验证；本次没有做这一步。

## 9. 官方依据

- [Seeed XIAO MG24 入门](https://wiki.seeedstudio.com/xiao_mg24_getting_started/)：板型、板载下载接口、串口与引脚。
- [Silicon Labs Arduino Core 5.0.0](https://github.com/SiliconLabsSoftware/arduino/tree/5.0.0)：本机锁定的 `cores/gecko/Serial.cpp`、`Serial.h` 与 `api/Print.h` 是串口发送兼容性判断依据。
- [EFR32xG24 参考手册](https://www.silabs.com/documents/public/reference-manuals/efr32xg24-rm.pdf)：23.5.5 `IADC_TIMER` 明确定时周期以 `CLK_SRC_ADC` 计数。
