`timescale 1ns/1ps

// 二进制 → 十进制 ASCII。
//
// 硬件里没有现成的「数字转字符串」函数，得自己算。这里用「双 dabble」
// （shift-and-add-3）：把二进制数一位一位地移进 BCD 寄存器，每移一位之前，
// 任何一个大于等于 5 的十进制位先加 3。移完 32 位，BCD 寄存器里就是
// 这串十进制数字了。
//
// 一拍移一位，32 拍出结果。串口发一个字节要 87 µs，所以这点时间毫无压力。
//
// 用法：start 拉高一拍 → busy 变高 → 若干拍后 done 给一拍脉冲，
// 此时 bcd 有效（每 4 位一个十进制数字，越靠高位越是高位数字）。
module dec_ascii #(
    parameter integer BITS   = 32,   // 输入位宽
    parameter integer DIGITS = 10    // 最多输出几位十进制
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    start,
    input  wire signed [BITS-1:0]  value,
    output reg                     busy,
    output reg                     done,
    output reg                     negative,   // 输入是负数
    output reg  [4*DIGITS-1:0]     bcd        // 绝对值转换后的十进制各位
);

    reg [BITS-1:0]       shift;      // 还没移进来的二进制位
    reg [4*DIGITS-1:0]   acc;
    reg [5:0]            count;

    integer              i;
    reg [4*DIGITS-1:0]   adjusted;

    // 组合修正：每个十进制位 >= 5 就先加 3
    always @* begin
        adjusted = acc;
        for (i = 0; i < DIGITS; i = i + 1) begin
            if (adjusted[4*i +: 4] >= 4'd5) begin
                adjusted[4*i +: 4] = adjusted[4*i +: 4] + 4'd3;
            end
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            shift    <= {BITS{1'b0}};
            acc      <= {4*DIGITS{1'b0}};
            count    <= 6'd0;
            busy     <= 1'b0;
            done     <= 1'b0;
            negative <= 1'b0;
            bcd      <= {4*DIGITS{1'b0}};
        end else begin
            done <= 1'b0;

            if (start) begin
                negative <= value[BITS-1];
                // 负数先取绝对值（二进制补码取反加一）
                shift    <= value[BITS-1] ? (~value + 1'b1) : value;
                acc      <= {4*DIGITS{1'b0}};
                count    <= 6'd0;
                busy     <= 1'b1;
            end else if (busy) begin
                if (count == BITS) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    bcd  <= acc;
                end else begin
                    // 先修正、再把 {BCD, 二进制} 整体左移一位
                    acc   <= {adjusted[4*DIGITS-2:0], shift[BITS-1]};
                    shift <= {shift[BITS-2:0], 1'b0};
                    count <= count + 6'd1;
                end
            end
        end
    end

endmodule
