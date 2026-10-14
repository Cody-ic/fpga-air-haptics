`timescale 1ns/1ps

// 轨迹点表 / 段表：一份双口存储，写口给「谁在产生这张表」，读口给轨迹节拍表模块。
//
// 为什么要单独拎出来：填这张表的有两个来源——草图文案解析（hap2_scan_parse）、
// 预设图形生成（hap2_shape）。两个来源各自用一份表，读口用多路选择器挑一份，
// 这样两个模块互不干扰，也不用改动已经测过的解析器。
//
// 点表是同步读（给地址后一拍出数据），段表是组合读（同拍出数据，因为里面只有
// 几十项，用寄存器实现更省事）。
module hap2_pt_table #(
    parameter integer PT_BITS = 21,
    parameter integer MAX_PTS = 256,
    parameter integer MAX_STK = 32
) (
    input  wire                      clk,
    input  wire                      rst_n,
    // ---- 写口 ----
    input  wire                      wr_en,
    input  wire [7:0]                wr_addr,
    input  wire signed [PT_BITS-1:0] wr_x,
    input  wire signed [PT_BITS-1:0] wr_y,
    input  wire                      st_wr_en,
    input  wire [4:0]                st_wr_addr,
    input  wire [8:0]                st_wr_start,
    input  wire [8:0]                st_wr_len,
    input  wire                      counts_en,
    input  wire [8:0]                npt_in,
    input  wire [5:0]                nst_in,
    // ---- 读口 ----
    input  wire [7:0]                rd_pt_addr,
    output reg  signed [PT_BITS-1:0] rd_pt_x,
    output reg  signed [PT_BITS-1:0] rd_pt_y,
    input  wire [4:0]                rd_st_addr,
    output wire [8:0]                rd_st_start,
    output wire [8:0]                rd_st_len,
    output reg  [8:0]                point_count,
    output reg  [5:0]                stroke_count
);

    reg signed [PT_BITS-1:0] pt_xram [0:MAX_PTS-1];
    reg signed [PT_BITS-1:0] pt_yram [0:MAX_PTS-1];
    reg [8:0]                st_startram [0:MAX_STK-1];
    reg [8:0]                st_lenram   [0:MAX_STK-1];

    assign rd_st_start = st_startram[rd_st_addr];
    assign rd_st_len   = st_lenram  [rd_st_addr];

    always @(posedge clk) begin
        if (!rst_n) begin
            point_count  <= 9'd0;
            stroke_count <= 6'd0;
        end else begin
            if (wr_en) begin
                pt_xram[wr_addr] <= wr_x;
                pt_yram[wr_addr] <= wr_y;
            end
            if (st_wr_en) begin
                st_startram[st_wr_addr] <= st_wr_start;
                st_lenram  [st_wr_addr] <= st_wr_len;
            end
            if (counts_en) begin
                point_count  <= npt_in;
                stroke_count <= nst_in;
            end
        end
    end

    // 点表同步读：给地址后一拍出数据
    always @(posedge clk) begin
        rd_pt_x <= pt_xram[rd_pt_addr];
        rd_pt_y <= pt_yram[rd_pt_addr];
    end

endmodule
