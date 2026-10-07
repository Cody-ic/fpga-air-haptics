// Tang Mega 60K 时序约束
// 板上就一个时钟源：sys_clk（V22），50 MHz → 周期 20 ns，占空比 50%。
// 这个数字来自 Sipeed 官方例程的 sdc（create_clock ... -period 20），
// 和固件默认参数 CLK_HZ = 50_000_000 对得上，所以不需要锁相环（PLL）。
//
// 说明：板上按键、串口都是「人按一下 / 电脑发一帧」级别的事件，
// 相对 20 ns 的时钟慢了好几个数量级，不必单独约束。

create_clock -name sys_clk -period 20 -waveform {0 10} [get_ports {clk}]
