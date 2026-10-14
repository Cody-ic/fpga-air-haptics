`timescale 1ns/1ps

// 整数开方：给定一个无符号整数，算出它的平方根（向下取整）。
//
// 用「逐位逼近」法，每拍定出结果的一位：
//   把被开方数按两位一组，从最高那一组开始；
//   每拍把余数左移两位、接上当前这一组，再拿 (当前根<<2)|1 去试减；
//   够减就把这一位定成 1，否则定成 0。
// 42 位的输入需要 21 拍，正好对应 21 位结果。
//
// 为什么需要它：算轨迹时要求每一小段的长度 √(dx²+dy²)；
// 后面算相位时还要再算一次 √(dx²+dy²+dz²)，所以这个单元会被复用。
//
// 用法：start 拉高一拍 → busy 变高 → 若干拍后 done 给一拍脉冲，此时 result 有效。
module fix_sqrt #(
    parameter integer IN_BITS  = 42,    // 输入位宽（平方和），必须是偶数
    parameter integer OUT_BITS = 21     // 输出位宽（平方根），等于 IN_BITS/2
) (
    input  wire                clk,
    input  wire                rst_n,
    input  wire                start,
    input  wire [IN_BITS-1:0]  value,
    output reg                 busy,
    output reg                 done,
    output reg  [OUT_BITS-1:0] result
);

    // 余数本身比被开方数小得多（实测最大约 4.2×10^6，22 位就够），
    // 但移位后要拼满 IN_BITS 位，所以留 IN_BITS-2 位，移位时正好补齐。
    reg [IN_BITS-3:0]  rem;
    reg [OUT_BITS-1:0] root;       // 已经定出来的那部分根
    reg [OUT_BITS-1:0] bit_idx;    // 这次处理第几组（从高到低）
    reg [IN_BITS-1:0]  value_lat;

    // 被开方数里对应的那一组两位
    wire [IN_BITS-1:0] pair  = {{(IN_BITS-2){1'b0}}, value_lat[2*bit_idx +: 2]};
    // 余数左移两位，接上这一组
    wire [IN_BITS-1:0] rsh   = {rem, 2'b00} | pair;
    // 试减值 (当前根<<2)|1
    wire [IN_BITS-1:0] trial = {{(IN_BITS-OUT_BITS-2){1'b0}}, root, 2'b01};
    // 这一位定成 1 还是 0，以及定完之后的新根
    wire               bit_one   = (rsh >= trial);
    wire [OUT_BITS-1:0] root_next = {root[OUT_BITS-2:0], bit_one};
    /* verilator lint_off UNUSEDSIGNAL */
    // 余数的高位恒为 0：实测最大约 4.2×10^6（22 位），这里留 40 位已经很宽裕。
    // 所以只把低位存回去，高位不参与后续运算。
    wire [IN_BITS-1:0]  rem_full  = bit_one ? (rsh - trial) : rsh;
    wire [IN_BITS-3:0]  rem_next  = rem_full[IN_BITS-3:0];
    /* verilator lint_on UNUSEDSIGNAL */

    always @(posedge clk) begin
        if (!rst_n) begin
            busy      <= 1'b0;
            done      <= 1'b0;
            rem       <= {(IN_BITS-2){1'b0}};
            root      <= {OUT_BITS{1'b0}};
            bit_idx   <= {OUT_BITS{1'b0}};
            value_lat <= {IN_BITS{1'b0}};
            result    <= {OUT_BITS{1'b0}};
        end else begin
            done <= 1'b0;

            if (start) begin
                value_lat <= value;
                rem       <= {(IN_BITS-2){1'b0}};
                root      <= {OUT_BITS{1'b0}};
                bit_idx   <= OUT_BITS[OUT_BITS-1:0] - 1'b1;
                busy      <= 1'b1;
            end else if (busy) begin
                rem  <= rem_next;
                root <= root_next;

                if (bit_idx == 0) begin
                    busy   <= 1'b0;
                    done   <= 1'b1;
                    // 最后一位已经拼进 root，下一拍它就是最终结果
                    result <= root_next;
                end else begin
                    bit_idx <= bit_idx - 1'b1;
                end
            end
        end
    end

endmodule
