`timescale 1ns/1ps

// CRC16-CCITT-FALSE 计算单元。
//
// 参数：初值 0xFFFF、多项式 0x1021、不反射、输出不异或。
// 自检向量：把 "123456789" 这 9 个 ASCII 字节依次喂进来，结果必须是 0x29B1。
//
// 每来一个字节做 8 次移位，综合出来就是 8 级异或/移位，属于纯组合逻辑。
// 串口一个字节要 86.8 µs（50 MHz 下是 4340 个时钟），所以这里完全不需要
// 流水线；如果哪天要一秒算几百万个字节，再按 README 里的办法切流水线。
module crc16_ccitt #(
    parameter [15:0] INIT = 16'hFFFF
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        init,      // 单拍脉冲：把 crc 复位成初值
    input  wire        valid,     // 单拍脉冲：吃掉 data 上的一个字节
    input  wire [7:0]  data,
    output reg  [15:0] crc
);

    // 组合函数：给定当前值和输入字节，算出下一个 CRC 值
    function [15:0] crc_next;
        input [15:0] cur;
        input [7:0]  din;
        integer      i;
        reg   [15:0] c;
        begin
            c = cur ^ {din, 8'h00};
            for (i = 0; i < 8; i = i + 1) begin
                if (c[15]) begin
                    c = (c << 1) ^ 16'h1021;
                end else begin
                    c = c << 1;
                end
            end
            crc_next = c;
        end
    endfunction

    always @(posedge clk) begin
        if (!rst_n) begin
            crc <= INIT;
        end else if (init) begin
            crc <= INIT;                   // init 优先于 valid
        end else if (valid) begin
            crc <= crc_next(crc, data);
        end
    end

endmodule
