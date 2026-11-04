# 2026-10-30 实板下载与压力单帧控制验证

用户在完成离线验证后明确要求“烧录板子然后测试”。本轮执行真实板卡临时下载和串口测试，未写入非易失存储。断电后本次程序失效。

## 下载

- 下载器扫描：USB Debugger A/0/561，器件识别码 `0x0001081B`，沿用已验证的 GW5AST-138B 配置；该识别码不能单独区分 B/C 版。
- 位流：`.runtime/host-alignment-build/restore_z50/calibration_restore/impl/pnr/board_top.fs`。
- SHA-256：`0512311d50bc766ee813f80f3335c6f43829d5dc2369edf231387bb93a8dc481`。下载前重新核对位流、归档源码、当前业务源码及校准文件摘要。
- 嵌入 2026-10-29 的 16 V、50 mm 校准表；其测量等级为 20。本轮没有重新校准，也没有重新测量供电电压。
- 下载器报告 `SRAM Program` 完成，耗时 23.1 秒，User Code `0x00002514`、Status `0x00026230`。SRAM（Static Random Access Memory，静态随机存取存储器）为掉电失效的运行存储。
- 下载日志：`.runtime/board-pressure-test/download.log`。

## 实板测试

通过现有上位机的真实 Session 会话连接 COM7 / 115200，握手返回 `simulated=0`、`fw_id=HAP3_STATIC_1`、8×8 / 10.3 mm、`STATIC_PHASE`、`CAL_READBACK`、`cal_profile=2`。测试不使用模拟设备。

22 个检查点通过：

- 握手、真实校准补偿表和 64 路中心相位核对。
- 圆形尺寸/高度/频率/调制参数完整回读；以等级 0 验证播放、暂停、继续、停止，输出始终关闭。
- 最大 32 段 / 256 点草图的配置、完整回读及上述播放控制；等级 0，未发射。
- 本地/电脑控制模式切换，均处于停止状态。
- 50 mm 中心固定点以等级 1 短时启动、暂停、继续、停止。
- 使用上位机打包版窗口生成的内置数据第 325 帧相位表，高度 50 mm、调制 200 Hz；只将驱动等级从 30 改为 1，短时启动、暂停、继续、停止。
- 静态名义相位、保存的补偿表和实际数字相位全部 64 路一致，补偿只叠加一次。
- 能切回普通绘图，再重新装载压力帧。

短时输出分为四段：固定点两段、压力帧两段；每段确认运行后等待 0.25 秒再发暂停或停止，实际使能时间另含串口命令/回读延迟。未进行持续输出或高等级试验。

最终完整回读为 `REMOTE / IDLE / output=0`，压力帧保留、驱动等级 1。停止已确认，串口会话已关闭释放，可由上位机重新连接。重新连接后需重新发送并确认配置才能从上位机播放。

## 证据和限制

- 结果：`.runtime/board-pressure-test/result_20261030_223027.json`，`passed=true`、`final_stop_confirmed=true`、`session_closed=true`。
- 完整收发和状态事件：`.runtime/board-pressure-test/test_20261030_223027.jsonl`。
- 手动执行脚本：[check_hardware_alignment.py](board/check_hardware_alignment.py)。默认输出等级 0；仅显式 `--run` 开启本次等级 1 的有界测试。
- 本轮未检测到此前的独立接收器 COM9，没有测量真实声场、接触压力、输出电压、供电电流或温升。`output=1` 是板端数字状态，不能替代仪器测量，也不能据此宣称两块压力区域已在手上重现。

```powershell
python fpga/board/check_hardware_alignment.py --port COM7 `
  --manifest .runtime/host-alignment-build/restore_z50/manifest.json `
  --pressure-config .runtime/pressure-frozen/config.json --run
```
