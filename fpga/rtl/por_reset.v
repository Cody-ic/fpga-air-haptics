`timescale 1ns/1ps

// 上电复位（POR，Power-On Reset）。
//
// 计数器必须有明确的配置初值，才能在没有按复位键时产生上电复位。
// 四态仿真中未初始化的寄存器是 X；综合后需核查器件的 INIT/GSR 设置。
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

    reg [23:0] cnt = 24'd0;
    reg        released = 1'b0;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ext_sync = 2'b00;

    // 外部复位异步置低，同步进入计数器后再开始释放计时。
    always @(posedge clk or negedge ext_rst_n) begin
        if (!ext_rst_n) ext_sync <= 2'b00;
        else            ext_sync <= {ext_sync[0], 1'b1};
    end

    always @(posedge clk or negedge ext_rst_n) begin
        if (!ext_rst_n) begin
            // 按键一按：立刻回到「还没放开」的状态，重新数
            cnt      <= 24'd0;
            released <= 1'b0;
        end else if (!ext_sync[1]) begin
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
