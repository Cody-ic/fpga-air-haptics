// Tang MEGA 138K Pro 时序约束
// 板上我们只用一个时钟源：clk = P16，50 MHz → 周期 20 ns，占空比 50%。
// 这个数字来自 Sipeed 官方 Pro 例程的 sdc（key_led 里 sys_clk 写 period 20），
// 和固件默认参数 CLK_HZ = 50_000_000 对得上，所以不需要锁相环（PLL）。
// （同一块板上 V22 是另一个 70 MHz 晶振，我们不用；要用的话记得连 sdc 一起改。）
//
// 按键和串口属于异步输入，逻辑中经过两级同步，不能因事件慢就忽略跨时钟路径。
// 首次综合后需核查同步器布局、外部复位释放和首级异步路径的时序例外；
// 例外应针对综合后的首级寄存器，不能对整个输入逻辑笼统放宽约束。

create_clock -name sys_clk -period 20 -waveform {0 10} [get_ports {clk}]

// Bound register-to-pad latency to 10 ns for the source-synchronous shift bus.
// Data changes a whole 20 ns cycle before/after SRCLK rises; RCLK follows
// the last SRCLK rising edge by 40 ns. PCB/cable skew still needs measurement.
set_output_delay -clock sys_clk -max 10 [get_ports {sr_data[*] sr_clk sr_latch}]
// OE/clear are asynchronous controls, not bits sampled on every shift edge.
// Budget one system cycle (20 ns) from source clock to pad. Clear is released
// two cycles before first SRCLK; OE enables two cycles after a fresh latch.
set_output_delay -clock sys_clk -max 0 [get_ports {sr_oe_n sr_clear_n}]
set_output_delay -clock sys_clk -min 0 [get_ports {sr_data[*] sr_clk sr_latch sr_oe_n sr_clear_n}]
