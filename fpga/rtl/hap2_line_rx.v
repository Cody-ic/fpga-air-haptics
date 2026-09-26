`timescale 1ns/1ps

// 行组装：把串口来的字节流攒成“一整行”，供后面做 CRC 校验和字段解析。
//
// 协议规定的三条规则都在这里落实：
//   1. 只有收到 LF（0x0A）才算一行结束；
//   2. 协议允许 CRLF，所以行末的 CR（0x0D）要在送出长度前剥掉；
//   3. 一行超过上限还没等到 LF，就整行丢弃（不是丢前缀，也不是拿半行去解析）。
//
// 数据存在一块 RAM 里（MAX_LINE 字节），下游通过 rd_addr / rd_data 读它。
// 读是同步读：这一拍给出 rd_addr，下一拍 rd_data 才有效。
module hap2_line_rx #(
    parameter integer MAX_LINE = 4096,   // 一帧最多多少字节（含 CRC 与换行）
    parameter integer ADDR_W   = 13      // 地址位宽；MAX_LINE 必须等于 2^ADDR_W（8192）
) (
    input  wire            clk,
    input  wire            rst_n,
    // 来自 uart_rx 的字节流
    input  wire            rx_valid,
    input  wire [7:0]      rx_data,
    // 行就绪通知
    output reg             line_ready,   // 单拍脉冲：缓冲里有一整行可读
    output reg  [ADDR_W:0] line_len,     // 行长（不含 LF，也不含被剥掉的 CR）
    output reg             line_dropped, // 单拍脉冲：本行超长，已整行丢弃
    // 下游读缓冲的端口
    input  wire [ADDR_W-1:0] rd_addr,
    output reg  [7:0]        rd_data
);

    // 一帧最长 4096 字节（含换行），所以正文最多 4095 字节
    localparam [ADDR_W:0] MAX_CONTENT = MAX_LINE - 1;

    reg [7:0]        mem [0:MAX_LINE-1];
    reg [ADDR_W:0]   wr_ptr;      // 已写入多少字节
    reg              dropping;    // 正在丢弃一整行，等 LF 收尾
    reg [7:0]        last_byte;   // 上一个写入的字节，用来判断行末 CR

    always @(posedge clk) begin
        if (!rst_n) begin
            wr_ptr       <= 0;
            dropping     <= 1'b0;
            last_byte    <= 8'h00;
            line_ready   <= 1'b0;
            line_len     <= 0;
            line_dropped <= 1'b0;
            rd_data      <= 8'h00;
        end else begin
            line_ready   <= 1'b0;   // 两个通知都是单拍脉冲
            line_dropped <= 1'b0;
            rd_data      <= mem[rd_addr];   // 同步读，下一拍有效

            if (rx_valid) begin
                if (dropping) begin
                    // 已判定超长：一路丢到 LF 为止，中途的字节全部不要
                    if (rx_data == 8'h0A) begin
                        dropping     <= 1'b0;
                        line_dropped <= 1'b1;
                    end
                end else if (rx_data == 8'h0A) begin
                    // 行结束
                    if (wr_ptr != 0 && last_byte == 8'h0D) begin
                        line_len <= wr_ptr - 1'b1;   // 剥掉行末 CR
                    end else begin
                        line_len <= wr_ptr;
                    end
                    line_ready <= 1'b1;
                    wr_ptr     <= 0;
                    last_byte  <= 8'h00;
                end else if (wr_ptr == MAX_CONTENT) begin
                    // 正文已经满 4095 字节，再来一个字节就超长
                    dropping <= 1'b1;
                    wr_ptr   <= 0;
                end else begin
                    mem[wr_ptr[ADDR_W-1:0]] <= rx_data;
                    wr_ptr     <= wr_ptr + 1'b1;
                    last_byte  <= rx_data;
                end
            end
        end
    end

endmodule
