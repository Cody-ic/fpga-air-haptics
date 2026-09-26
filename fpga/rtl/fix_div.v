`timescale 1ns/1ps

// 无符号整数除法：给定被除数和除数，算出商和余数。
//
// 用「移位减」（恢复余数法），每拍定出商的一位：
//   余数先左移一位、接上被除数的下一位；
//   够减就减掉除数，这一位商定成 1；不够减就定成 0。
// 被除数多少位就多少拍（42 位 = 42 拍）。
//
// 为什么需要它：算轨迹时要算「每一小段占多少拍」和「每一拍走多远」；
// 后面算相位量化时还要做类似换算，所以这个单元会被复用。
//
// 除数为 0 时不做除法，只把 div0 拉高（调用方应当当成配置非法）。
// 商一定放得下：被除数和除数都没有超出各自位宽时，商不会超过被除数位宽。
module fix_div #(
    parameter integer NUM_BITS = 42,    // 被除数位宽（也是商的位宽）
    parameter integer DEN_BITS = 32     // 除数位宽
) (
    input  wire                clk,
    input  wire                rst_n,
    input  wire                start,
    input  wire [NUM_BITS-1:0] numer,
    input  wire [DEN_BITS-1:0] denom,
    output reg                 busy,
    output reg                 done,
    output reg                 div0,      // 除数为 0
    output reg  [NUM_BITS-1:0] quot,
    output reg  [NUM_BITS-1:0] rem
);

    // 余数本身一定小于除数（DEN_BITS 位够），移位之后才可能多一位，
    // 所以移位用 DEN_BITS+1 位算，存回去只存低 DEN_BITS 位。
    localparam integer RW = DEN_BITS + 1;

    reg [NUM_BITS-1:0] num_lat;
    reg [DEN_BITS-1:0] den_lat;
    reg [DEN_BITS-1:0] r;          // 运行中的余数
    reg [NUM_BITS-2:0] q;          // 运行中的商（最后一位在收尾时拼上）
    reg [NUM_BITS-1:0] cnt;        // 还剩几拍

    wire [RW-1:0]       r_shift = {r, num_lat[cnt]};
    wire [RW-1:0]       den_ext = {1'b0, den_lat};
    wire                enough  = (r_shift >= den_ext);
    wire [RW-1:0]       r_next  = enough ? (r_shift - den_ext) : r_shift;
    wire [NUM_BITS-1:0] q_next  = {q, enough};

    always @(posedge clk) begin
        if (!rst_n) begin
            busy    <= 1'b0;
            done    <= 1'b0;
            div0    <= 1'b0;
            num_lat <= {NUM_BITS{1'b0}};
            den_lat <= {DEN_BITS{1'b0}};
            r       <= {RW{1'b0}};
            q       <= {(NUM_BITS-1){1'b0}};
            cnt     <= {NUM_BITS{1'b0}};
            quot    <= {NUM_BITS{1'b0}};
            rem     <= {NUM_BITS{1'b0}};
        end else begin
            done <= 1'b0;

            if (start) begin
                num_lat <= numer;
                den_lat <= denom;
                r       <= {DEN_BITS{1'b0}};
                q       <= {(NUM_BITS-1){1'b0}};
                cnt     <= NUM_BITS[15:0] - 1'b1;   // 从最高位开始，共 NUM_BITS 拍
                div0    <= (denom == {DEN_BITS{1'b0}});
                busy    <= 1'b1;
            end else if (busy) begin
                // r_next 的最高位始终是 0（余数一定小于除数），所以只存低位
                r <= r_next[DEN_BITS-1:0];
                if (cnt == 0) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    quot <= q_next;
                    rem  <= {{(NUM_BITS-RW){1'b0}}, r_next};
                end else begin
                    q   <= q_next[NUM_BITS-2:0];
                    cnt <= cnt - 1'b1;
                end
            end
        end
    end

endmodule
