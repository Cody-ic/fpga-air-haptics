// Tang Mega 60K 时序约束
// 板上就一个时钟源：sys_clk（V22），50 MHz → 周期 20 ns，占空比 50%。
// 这个数字来自 Sipeed 官方例程的 sdc（create_clock ... -period 20），
// 和固件默认参数 CLK_HZ = 50_000_000 对得上，所以不需要锁相环（PLL）。
//
// 按键和串口属于异步输入，逻辑中经过两级同步，不能因事件慢就忽略跨时钟路径。
// 首次综合后需核查同步器布局、外部复位释放和首级异步路径的时序例外；
// 例外应针对综合后的首级寄存器，不能对整个输入逻辑笼统放宽约束。

create_clock -name sys_clk -period 20 -waveform {0 10} [get_ports {clk}]
