`timescale 1ns/1ps

// 一帧的格式与 CRC 检查。
//
// 输入：hap2_line_rx 缓冲里的一整行（长度 len，不含 LF 和 CR）。
// 输出：frame_ok（格式正确且 CRC 对）或 frame_bad（其余情况）。
//
// 判据（对应协议第 1 节）：
//   - 行里必须有一个 '*'；
//   - '*' 前面是从 "HAP2" 到最后一个字段的正文，CRC 只覆盖这一段，不含 '*'；
//   - '*' 后面必须正好是 4 位十六进制校验码，多一个字符都算错。
//
// 时序：本模块每拍处理一个字节，读 RAM 有一拍延迟，所以用 idx 记录
// “现在 rd_data 上是第几个字节”，而 rd_addr 始终保持在 idx+1，见下面的状态机。
module hap2_frame_check #(
    parameter integer ADDR_W = 13         // 与 hap2_line_rx 的地址位宽一致
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              start,       // 单拍脉冲：开始检查当前行
    input  wire [ADDR_W:0]   len,         // 行长（不含 LF/CR）
    output reg  [ADDR_W-1:0] rd_addr,     // 送缓冲的读地址
    input  wire [7:0]        rd_data,     // 一拍之后有效的数据
    output reg               busy,        // 检查进行中
    output reg               frame_ok,    // 单拍脉冲：通过
    output reg               frame_bad,   // 单拍脉冲：不通过
    output reg  [15:0]       crc_value,   // 本帧算出来的 CRC（调试对照用）
    output reg  [15:0]       crc_expected // 报文里写的 CRC（调试对照用）
);

    localparam [1:0] S_IDLE  = 2'd0;
    localparam [1:0] S_PRIME = 2'd1;   // 等一拍，让第一次读的数据到位
    localparam [1:0] S_SCAN  = 2'd2;
    localparam [1:0] S_CHECK = 2'd3;

    localparam [7:0] CH_STAR = 8'h2A;   // '*'

    reg [1:0]        state;
    reg [ADDR_W:0]   idx;          // rd_data 当前对应第几个字节
    reg              star_seen;
    reg [2:0]        hex_cnt;      // 已收到几位十六进制校验码
    reg [15:0]       hex_val;
    reg              format_bad;   // 格式问题（非法字符、多字符等）

    // 给 CRC 模块的接口
    reg              crc_init;
    reg              crc_valid;
    reg [7:0]        crc_data;
    wire [15:0]      crc_now;

    crc16_ccitt u_crc (
        .clk   (clk),
        .rst_n (rst_n),
        .init  (crc_init),
        .valid (crc_valid),
        .data  (crc_data),
        .crc   (crc_now)
    );

    // 十六进制字符转 4 位数值；第 5 位（bit4）为 1 表示字符非法
    function [4:0] hex_digit;
        input [7:0] ch;
        begin
            if (ch >= "0" && ch <= "9") begin
                hex_digit = {1'b0, ch[3:0]};
            end else if (ch >= "A" && ch <= "F") begin
                hex_digit = {1'b0, ch[3:0] + 4'd9};
            end else if (ch >= "a" && ch <= "f") begin
                hex_digit = {1'b0, ch[3:0] + 4'd9};
            end else begin
                hex_digit = 5'b1_0000;
            end
        end
    endfunction

    wire [4:0] digit_now;
    assign digit_now = hex_digit(rd_data);

    always @(posedge clk) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            idx          <= 0;
            rd_addr      <= 0;
            star_seen    <= 1'b0;
            hex_cnt      <= 3'd0;
            hex_val      <= 16'h0000;
            format_bad   <= 1'b0;
            busy         <= 1'b0;
            frame_ok     <= 1'b0;
            frame_bad    <= 1'b0;
            crc_value    <= 16'h0000;
            crc_expected <= 16'h0000;
            crc_init     <= 1'b0;
            crc_valid    <= 1'b0;
            crc_data     <= 8'h00;
        end else begin
            frame_ok  <= 1'b0;   // 两个结果都是单拍脉冲
            frame_bad <= 1'b0;
            crc_init  <= 1'b0;
            crc_valid <= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        if (len == 0) begin
                            frame_bad <= 1'b1;      // 空行，直接判错
                        end else begin
                            busy       <= 1'b1;
                            idx        <= 0;
                            rd_addr    <= 0;        // 先摆地址 0
                            star_seen  <= 1'b0;
                            hex_cnt    <= 3'd0;
                            hex_val    <= 16'h0000;
                            format_bad <= 1'b0;
                            crc_init   <= 1'b1;
                            state      <= S_PRIME;
                        end
                    end
                end

                S_PRIME: begin
                    // 这一拍 rd_addr=0 已经喂给 RAM，下一拍数据才到
                    rd_addr <= 1;
                    state   <= S_SCAN;
                end

                S_SCAN: begin
                    // 本拍 rd_data 对应的就是第 idx 个字节
                    if (!star_seen) begin
                        if (rd_data == CH_STAR) begin
                            star_seen <= 1'b1;      // 正文结束，CRC 就到此为止
                        end else begin
                            crc_valid <= 1'b1;      // 正文字节喂给 CRC
                            crc_data  <= rd_data;
                        end
                    end else if (hex_cnt < 3'd4) begin
                        if (digit_now[4]) begin
                            format_bad <= 1'b1;     // 校验码里有非十六进制字符
                        end
                        hex_val <= {hex_val[11:0], digit_now[3:0]};
                        hex_cnt <= hex_cnt + 3'd1;
                    end else begin
                        format_bad <= 1'b1;         // '*' 后面多于 4 个字符
                    end

                    if (idx == len - 1'b1) begin
                        state <= S_CHECK;
                    end else begin
                        idx     <= idx + 1'b1;
                        rd_addr <= rd_addr + 1'b1;  // rd_addr 始终保持在 idx+1
                    end
                end

                S_CHECK: begin
                    busy         <= 1'b0;
                    crc_value    <= crc_now;
                    crc_expected <= hex_val;
                    if (format_bad || !star_seen || hex_cnt != 3'd4) begin
                        frame_bad <= 1'b1;
                    end else if (crc_now == hex_val) begin
                        frame_ok <= 1'b1;
                    end else begin
                        frame_bad <= 1'b1;
                    end
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
