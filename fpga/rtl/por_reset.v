`timescale 1ns/1ps

// 上电复位（POR，Power-On Reset）。
//
// 为什么必须有这个模块：真板子上电、FPGA 配置完成之后，里面寄存器的值是**随机的**，
// 不是仿真里那样全都是 0（仿真工具默认把寄存器初始化成 0，真芯片不会）。
// 所以必须自己产生一个复位，把整条链路按住一小段时间再放开，
// 否则状态机会从一堆随机值开始跑——这是上板最经典的翻车点，仿真里永远看不到。
//
// 做法是数字电路里通行的「异步拉低、同步放开」：
//   - 外部复位键一按（低电平）**立刻**拉低复位，不用等时钟；
//   - 放开的时候跟 clk 对齐，避免复位释放那一瞬间电路处在亚稳态（metastability）。
//
// 说明：HOLD_MS 受 24 位计数限制，50 MHz 下最多约 335 ms。
module por_reset #(
    parameter integer CLK_HZ  = 50_000_000,   // 系统时钟频率（Hz）
    parameter integer HOLD_MS = 10            // 上电后按住多少毫秒
) (
    input  wire clk,
    input  wire ext_rst_n,   // 外部复位键，低有效；没有就接 1'b1
    output wire rst_n        // 给整条链路用的复位，低有效
);

    localparam [23:0] HOLD_CYC = (CLK_HZ / 1000) * HOLD_MS;

    reg [23:0] cnt;
    reg        released;

    always @(posedge clk or negedge ext_rst_n) begin
        if (!ext_rst_n) begin
            // 按键一按：立刻回到「还没放开」的状态，重新数
            cnt      <= 24'd0;
            released <= 1'b0;
        end else if (cnt != HOLD_CYC) begin
            cnt      <= cnt + 24'd1;
            released <= 1'b0;
        end else begin
            released <= 1'b1;
        end
    end

    assign rst_n = ext_rst_n & released;

endmodule
