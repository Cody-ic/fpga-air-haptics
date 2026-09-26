`timescale 1ns/1ps

// 串口接收：115200 8N1（8 个数据位、无校验、1 个停止位），位中点采样。
//
// 工作方式：空闲时线是高电平；检测到下降沿后，先等半个位时间，
// 在起始位正中间确认它确实是低电平（防毛刺），之后每个位时间采一次，
// 正好都落在每一位的中间。第 10 位（停止位）必须是高电平，否则报帧错。
//
// 分频：CLKS_PER_BIT = 系统时钟 ÷ 波特率。50 MHz 时为 434，100 MHz 时为 868。
module uart_rx #(
    parameter integer CLK_HZ = 50_000_000,   // 系统时钟频率（Hz）
    parameter integer BAUD   = 115200        // 波特率
) (
    input  wire       clk,
    input  wire       rst_n,      // 低电平复位（同步复位，与 clk 对齐）
    input  wire       rx_line,    // 串口接收引脚
    output reg  [7:0] rx_data,    // 收到的字节
    output reg        rx_valid,   // 单拍脉冲：rx_data 有效
    output reg        rx_error    // 单拍脉冲：停止位不是高电平
);

    // 位时间用多少个时钟，四舍五入
    localparam integer CLKS_PER_BIT = (CLK_HZ + (BAUD / 2)) / BAUD;
    localparam integer HALF_BIT     = CLKS_PER_BIT / 2;

    localparam [1:0] S_IDLE  = 2'd0;
    localparam [1:0] S_START = 2'd1;
    localparam [1:0] S_DATA  = 2'd2;
    localparam [1:0] S_STOP  = 2'd3;

    reg [1:0]  state;
    reg [15:0] cnt;        // 位内计数
    reg [2:0]  bit_idx;    // 正在收第几位数据
    reg [7:0]  shifter;    // 移位寄存器，先收到的是最低位

    always @(posedge clk) begin
        if (!rst_n) begin
            state    <= S_IDLE;
            cnt      <= 16'd0;
            bit_idx  <= 3'd0;
            shifter  <= 8'h00;
            rx_data  <= 8'h00;
            rx_valid <= 1'b0;
            rx_error <= 1'b0;
        end else begin
            rx_valid <= 1'b0;   // 默认拉低，只在需要时置一拍
            rx_error <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (rx_line == 1'b0) begin     // 看到起始位
                        cnt   <= HALF_BIT;         // 等半个位，采到中间
                        state <= S_START;
                    end
                end

                S_START: begin
                    if (cnt == 16'd0) begin
                        if (rx_line == 1'b0) begin // 确认不是毛刺
                            cnt     <= CLKS_PER_BIT - 1;
                            bit_idx <= 3'd0;
                            state   <= S_DATA;
                        end else begin
                            state   <= S_IDLE;     // 是毛刺，重新等
                        end
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                S_DATA: begin
                    if (cnt == 16'd0) begin
                        shifter[bit_idx] <= rx_line;   // 低位先到
                        cnt <= CLKS_PER_BIT - 1;
                        if (bit_idx == 3'd7) begin
                            state <= S_STOP;
                        end else begin
                            bit_idx <= bit_idx + 3'd1;
                        end
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                S_STOP: begin
                    if (cnt == 16'd0) begin
                        if (rx_line == 1'b1) begin
                            rx_data  <= shifter;
                            rx_valid <= 1'b1;
                        end else begin
                            rx_error <= 1'b1;      // 停止位不对，丢弃
                        end
                        state <= S_IDLE;
                    end else begin
                        cnt <= cnt - 16'd1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
