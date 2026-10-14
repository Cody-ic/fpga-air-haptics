`timescale 1ns/1ps

// 串口发送：115200 8N1。
//
// 一帧是 10 个位时间：1 位起始位（低）→ 8 位数据（低位先发）→ 1 位停止位（高）。
// 空闲时线保持高电平。
//
// 用法：tx_start 拉高一拍，把 tx_data 交出去；之后 tx_busy 会是 1，
// 等 tx_busy 变回 0 就说明这一字节发完了（tx_done 会给一拍脉冲）。
module uart_tx #(
    parameter integer CLK_HZ = 50_000_000,   // 系统时钟频率（Hz）
    parameter integer BAUD   = 115200        // 波特率
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       tx_start,   // 单拍脉冲：开始发送 tx_data
    input  wire [7:0] tx_data,
    output reg        tx_line,    // 串口发送引脚
    output reg        tx_busy,    // 1 = 正在发送
    output reg        tx_done     // 单拍脉冲：刚发完一个字节
);

    localparam integer CLKS_PER_BIT = (CLK_HZ + (BAUD / 2)) / BAUD;

    localparam [1:0] S_IDLE  = 2'd0;
    localparam [1:0] S_START = 2'd1;
    localparam [1:0] S_DATA  = 2'd2;
    localparam [1:0] S_STOP  = 2'd3;

    reg [1:0]  state;
    reg [15:0] cnt;        // 当前位还剩几个时钟
    reg [2:0]  bit_idx;    // 正在发第几位数据
    reg [7:0]  shifter;    // 移位寄存器，先发最低位

    always @(posedge clk) begin
        if (!rst_n) begin
            state   <= S_IDLE;
            cnt     <= 16'd0;
            bit_idx <= 3'd0;
            shifter <= 8'h00;
            tx_line <= 1'b1;
            tx_busy <= 1'b0;
            tx_done <= 1'b0;
        end else begin
            tx_done <= 1'b0;

            case (state)
                S_IDLE: begin
                    tx_line <= 1'b1;
                    tx_busy <= 1'b0;
                    if (tx_start) begin
                        shifter <= tx_data;
                        tx_line <= 1'b0;                 // 起始位
                        tx_busy <= 1'b1;
                        cnt     <= CLKS_PER_BIT - 1;
                        state   <= S_START;
                    end
                end

                S_START: begin
                    if (cnt == 16'd0) begin
                        tx_line <= shifter[0];
                        shifter <= {1'b0, shifter[7:1]};
                        bit_idx <= 3'd0;
                        cnt     <= CLKS_PER_BIT - 1;
                        state   <= S_DATA;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                S_DATA: begin
                    if (cnt == 16'd0) begin
                        if (bit_idx == 3'd7) begin
                            tx_line <= 1'b1;             // 停止位
                            cnt     <= CLKS_PER_BIT - 1;
                            state   <= S_STOP;
                        end else begin
                            tx_line <= shifter[0];
                            shifter <= {1'b0, shifter[7:1]};
                            bit_idx <= bit_idx + 3'd1;
                            cnt     <= CLKS_PER_BIT - 1;
                        end
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                S_STOP: begin
                    if (cnt == 16'd0) begin
                        tx_busy <= 1'b0;
                        tx_done <= 1'b1;
                        state   <= S_IDLE;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
