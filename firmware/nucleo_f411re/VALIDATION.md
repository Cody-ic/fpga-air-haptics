# 软件验证记录

日期：2026-09-27。此记录不包含实板烧录或示波器测试。

## CubeIDE 工程复验（2026-09-28）

- 使用本机 STM32CubeIDE 2.1.0 的原生 headless builder，将 `.project` 导入独立临时工作区，编译 `haptics_f411re/Debug`：0 错误、0 警告。项目源码保留在仓库中文路径中。
- IDE 直接管理 C / 汇编编译及链接，没有调用 Python 构建脚本。目标为 STM32F411RE、硬浮点、`-O2 -g3`，使用现有启动代码及保留 Sector 7 的链接脚本。
- IDE 产物 `Debug/haptics_f411re.elf` 转为二进制后为 52,280 B，SHA-256 为 `e5a4056afefe27acad4ee5ad9a89816ff362ad83a3591581f459fd0823884bcb`，与既有命令行产物逐字节一致；Flash / RAM 占用相同。
- 已提供板载 ST-LINK / SWD 的共享 Debug 配置，启动停在 `main`，未连接实板验证下载和断点。`.elf` 内的调试路径与命令行构建不同，不能用整个 ELF 的哈希判断固件机器码是否相同。
- 本次只增加 IDE 配置和文档，固件 C / 汇编源码未变；沿用以下已完成的软件测试记录。

## 已执行

- Arm GNU 14.3.1 编译通过，`-Wall -Wextra -Werror`，链接无警告。
- 固件原生 C / Python 交叉测试：11 项通过。
- 现有桌面单元测试：65 项通过。
- Tk GUI smoke 与模拟 BLE GUI 流程通过；未连接真实串口或 BLE。
- 原生 GCC `-fanalyzer` 对协议/几何源码检查通过。

## 检查范围

测试覆盖 CRC/分片/超长行恢复、错误配置原子拒绝、阵列不匹配、握手后重新配置、暂停继续、心跳超时、本地控制、故障停止及恢复、所有基本图形、长路径、量化相位、GPIO 位掩码、调制和跨段关闭输出。真实 `desktop_app.controller.Session(demo=False)` 在软件传输夹具中按 115200 回传带宽完成 256 点配置及启停确认。几何与电脑参考值在微米取整误差范围内一致，单精度相位在舍入边界允许差 1 级。

## 资源与待验证项

- Flash：52,280 B / 可用 384 KiB；另保留 Sector 7 的 128 KiB 启动序号日志。
- RAM 链接占用：127,640 B / 128 KiB，包含已预留的 8 KiB 栈与 1 KiB 堆。新增功能必须重新核算 RAM；链接器有越界断言。
- 尚需实板确认：40 kHz 频率误差、两组 GPIO 的相对偏差、最大图形下的 DMA 供数余量、ST-LINK 串口连接、断线与按键停止。
- 尚无电流、温升、声压和触觉效果数据。

## 当前构建文件

| 文件 | 字节数 | SHA-256 |
|---|---|---|
| `haptics_f411re.elf` | 560212 | `cbedab67413a73e5ff04ac5205dc3aefb5540d6016c93e2ff9c347281facb47b` |
| `haptics_f411re.bin` | 52280 | `e5a4056afefe27acad4ee5ad9a89816ff362ad83a3591581f459fd0823884bcb` |
| `haptics_f411re.hex` | 147002 | `e9502252ef488136566752da3a83a7e14f3627af117ae6c654a4e796cb13a09e` |
