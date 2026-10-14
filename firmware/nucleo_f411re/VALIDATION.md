# 软件验证记录

日期：2026-09-28。此记录不包含实板烧录、ADC 电气采样或示波器测试。

## 构建与资源

- STM32CubeIDE 2.1.0 原生 headless 构建 `haptics_f411re/Debug`：0 错误、0 警告；工程保留在仓库中文路径，包含新增 `receiver.c`。IDE 直接构建和使用 ELF，经板载 ST-LINK 下载调试，无须手动操作 HEX。
- Arm GNU 14.3.1 命令行构建通过，`-Wall -Wextra -Werror`。IDE ELF 提取的可加载二进制与命令行 BIN 逐字节一致，53,576 B。
- Flash：53,576 B / 可用 384 KiB；Sector 7 的 128 KiB 仍只用于启动序号。
- RAM 链接占用：128,096 B / 128 KiB，包含 8 KiB 栈和 1 KiB 堆的预留，剩余 2,976 B。相比接收功能加入前增加 456 B，其中 ADC 数据缓存 400 B。此数字不等于实测栈峰值或 DMA 性能。
- GCC `-fanalyzer` 检查协议和几何源码通过。

## 已通过的测试

- 固件原生 C / Python：14 项。覆盖几何相位、输出位掩码、配置原子性、心跳、启停、本地控制，以及 ADC 完成、错误、超时、限频和新握手取消。
- 桌面 Python unittest：71 项。新增电压/直流/40 kHz 幅值分析、异常窗口、CRC 分片、启动标识/来源校验、Demo 和停止请求队列测试。
- `Session(demo=False)` 对真实 C 协议核心的传输夹具，按 115200 回传带宽运行 32 段 / 256 点图形，并在 RUNNING 中采样、随后暂停和停止。它是软件夹具，不是实串口结果。
- 实际 ADC 驱动在寄存器替身上验证 PA0 模式、TIM2 触发、ADC 分频/采样时间、DMA 有限长度，以及完成、错误和取消；不模拟真实 STM32 总线仲裁。
- 接收页 Tk GUI：待机/播放采样、连续查看、Demo 标识、200 行 CSV、旧固件按钮禁用和断线历史标识通过，截图已人工查看。
- 原有 Tk 与模拟 BLE GUI smoke 通过。新 Windows EXE 完整打包后 `--self-test` 退出码 0；图形编辑、连接、播放、状态反馈和模拟 BLE 流程通过。
- 手机 Dart 协议/会话：13 项通过；Python 参考夹具含可选 HELLO 字段和 CAPTURE 帧。手机暂无采样页面，本次不发布新 APK。

## 实板仍需验证

ST-LINK 下载/串口、16 路输出频率和相位、双 GPIO 偏差、最大图形供数余量，以及 ADC 与发射并发的丢样/停机表现均未实测。需用已知直流和带正偏置的 40 kHz 小信号比对 ADC，再接接收板检查前级削顶。默认 ADC 参考 3300 mV 未校准；×3.4 不会恢复削顶、量程损失或混叠，窗口幅值不能冒充声压。

没有电流、温升、声压或触觉效果数据。详细接线与首次验证见 [README](README.md)，板卡修改见 [接收板建议](../../hardware/receiver/PCB调整建议.md)。构建输出、截图和运行日志保留在忽略目录，不提交 Git。

## 当前命令行构建文件

ELF 含调试路径，IDE 与命令行 ELF 文件哈希可不同；比较机器码应使用提取后的 BIN。

| 文件 | 字节数 | SHA-256 |
|---|---|---|
| `haptics_f411re.elf` | 568792 | `7f792b5d99993a65d5c6fee2a64858aceeeb917c6260cdfb52cd1c6ce7ca5254` |
| `haptics_f411re.bin` | 53576 | `6e5cf68fdcc27c8593f95ddb8fa1d497c9b59f4eb09098b77ed3611403b862e0` |
| `haptics_f411re.hex` | 150647 | `309cf253f83cecf44ab392eeb8c9efa0f4e3f8bafa72aecca2f3f3936f8bf545` |
