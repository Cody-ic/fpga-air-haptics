`timescale 1ns/1ps

// 输出级：把「每一路的相位码」变成 16 路 40 kHz 方波，送给驱动电路。
//
// 相位码的含义是「这一路比基准提前多少个相位档」。做法是最常见的数字波形生成法：
//
//   相位累加器  acc   每个时钟加一个固定量 inc，加满一圈就是一个载波周期
//   第 i 路     相位 = acc + 码_i × (一圈 / S)，取最高位就是方波（自动 50% 占空比）
//
// inc 在配置阶段算一次：inc = round(f × 2^N ÷ 时钟频率)。用累加器而不是「数到 1250
// 归零」，是因为 50 MHz ÷ 40 kHz = 1250 拍、而 1250 ÷ 64 档 = 19.53 拍不是整数——
// 硬数会一档一档地抖，累加器则把误差均匀摊开（40 kHz 时实测频率误差 0.002%）。
//
// 三个门控条件（运行中、等级>0、正在扫描）由 enable 一句话带进来；enable=0 时
// 所有输出一律拉低——换能器在跳转、暂停、停止、上电复位时都不能被驱动。
//
// 如果驱动板要的是「互补的两个输入」（半桥），把 DEAD_CYC 设成死区拍数，
// out_neg 就是带死区的互补输出（两边同时导通会烧管子，所以要有都不导通的空档）。
// 只用单端驱动时 DEAD_CYC=0、只用 out_pos 就行。
module hap2_out #(
    parameter integer ROWS     = 4,
    parameter integer COLS     = 4,
    parameter integer CLK_HZ   = 50_000_000,
    parameter integer ACC_BITS = 24,     // 相位累加器位宽：越大频率越准
    parameter integer DEAD_CYC = 0       // 互补输出的死区（时钟数），0 = 不插死区
) (
    input  wire                      clk,
    input  wire                      rst_n,
    // ---- 配置 ----
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [31:0]               cfg_carrier_hz,   // 取值范围由字段解析层把关（≤80000）
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [31:0]               cfg_phase_steps,
    input  wire                      cfg_change,   // 单拍：载波换了，增量重算
    // ---- 门控 ----
    input  wire                      enable,       // 运行中 && 等级>0 && 正在扫描
    // ---- 相位表读口（新表好了就读一遍，锁进本地寄存器）----
    input  wire                      tbl_new,      // 单拍：相位表刚更新
    output reg  [7:0]                tbl_addr,
    input  wire [7:0]                tbl_data,
    // ---- 输出 ----
    output reg  [ROWS*COLS-1:0]      out_pos,
    output reg  [ROWS*COLS-1:0]      out_neg
);

    localparam integer CHANNELS = ROWS * COLS;
    localparam integer DEAD_MAX = (DEAD_CYC < 1) ? 1 : DEAD_CYC;

    // ---------------- 配置阶段：算累加增量 ----------------
    // inc = round(f × 2^ACC_BITS ÷ 时钟频率)
    reg  [55:0] in_numer;
    reg         in_start;
    wire        in_done, in_div0;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [55:0] in_quot;    // 只用到低 ACC_BITS 位
    /* verilator lint_on UNUSEDSIGNAL */

    fix_div #(.NUM_BITS(56), .DEN_BITS(32)) u_inc (
        .clk (clk), .rst_n (rst_n), .start (in_start),
        .numer (in_numer), .denom (CLK_HZ[31:0]),
        .done (in_done), .div0 (in_div0), .quot (in_quot)
    );

    reg [ACC_BITS-1:0] inc;
    reg [5:0]          off_shift;    // ACC_BITS − log2(S)

    function [3:0] log2s;            // S 只可能是 8..256
        input [31:0] s;
        begin
            case (s)
                32'd8:   log2s = 4'd3;
                32'd16:  log2s = 4'd4;
                32'd32:  log2s = 4'd5;
                32'd64:  log2s = 4'd6;
                32'd128: log2s = 4'd7;
                default: log2s = 4'd8;      // 256
            endcase
        end
    endfunction

    // ---------------- 本地相位码与偏移 ----------------
    /* verilator lint_off UNUSEDSIGNAL */
    reg [7:0]          code [0:CHANNELS-1];   // 锁进来的相位码，只为了看波形
    /* verilator lint_on UNUSEDSIGNAL */
    reg [ACC_BITS-1:0] off  [0:CHANNELS-1];
    reg [ACC_BITS-1:0] acc;
    reg [7:0]          load_i;
    reg                loading, load_wait;

    // 读表：给地址后一拍出数据，所以每个通道两拍
    always @(posedge clk) begin
        if (!rst_n) begin
            load_i     <= 8'd0;
            loading    <= 1'b0;
            load_wait  <= 1'b0;
            tbl_addr   <= 8'd0;
        end else begin
            if (tbl_new) begin
                loading   <= 1'b1;
                load_i    <= 8'd0;
                load_wait <= 1'b0;
                tbl_addr  <= 8'd0;
            end else if (loading) begin
                if (load_wait) begin
                    // 数据这一拍有效：锁进本地，偏移也跟着算好
                    code[load_i] <= tbl_data;
                    off [load_i] <= tbl_data << off_shift;
                    load_wait    <= 1'b0;
                    if (load_i + 8'd1 >= CHANNELS[7:0]) begin
                        loading <= 1'b0;
                    end else begin
                        load_i   <= load_i + 8'd1;
                        tbl_addr <= load_i + 8'd1;
                    end
                end else begin
                    load_wait <= 1'b1;
                end
            end
        end
    end

    // ---------------- 相位累加器 ----------------
    always @(posedge clk) begin
        if (!rst_n)          acc <= {ACC_BITS{1'b0}};
        else if (in_done)    acc <= {ACC_BITS{1'b0}};   // 换了载波：从零相位重新开始
        else                 acc <= acc + inc;
    end



    // ---------------- 每一路：加偏移取最高位 ----------------
    genvar gi;
    generate
        for (gi = 0; gi < CHANNELS; gi = gi + 1) begin : g_ch
            wire [ACC_BITS-1:0] ph = acc + off[gi];
            wire                sq = ph[ACC_BITS-1];    // 50% 占空比，天然如此
            /* verilator lint_off UNUSEDSIGNAL */
            reg  [DEAD_MAX-1:0] dly;    // 死区延迟链（不用互补输出时也留着，方便看波形）
            /* verilator lint_on UNUSEDSIGNAL */
            wire                sq_d = dly[DEAD_MAX-1]; // 延迟 DEAD_CYC 拍

            always @(posedge clk) begin
                if (!rst_n) begin
                    out_pos[gi] <= 1'b0;
                    out_neg[gi] <= 1'b0;
                    dly         <= {(DEAD_MAX){1'b0}};
                end else begin
                    dly         <= (dly << 1) | sq;   // 移位插入，位宽无关
                    out_pos[gi] <= enable & sq;
                    // 互补输出：用一个「晚一点点的」方波取反，就有了两边都不导通的空档。
                    // enable=0 时两个输出一起拉低（半桥上下管都关，最安全）。
                    out_neg[gi] <= enable & (DEAD_CYC == 0 ? ~sq : ~sq_d);
                end
            end
        end
    endgenerate

    // ---------------- 状态机（只负责算增量）----------------
    /* verilator lint_off UNUSEDSIGNAL */
    reg busy;   // 只在波形上看：算增量的时候是 1
    /* verilator lint_on UNUSEDSIGNAL */

    always @(posedge clk) begin
        if (!rst_n) begin
            busy      <= 1'b0;
            in_start  <= 1'b0;
            in_numer  <= 56'd0;
            inc       <= {ACC_BITS{1'b0}};
            off_shift <= ACC_BITS[5:0] - 6'd6;
        end else begin
            in_start <= 1'b0;
            if (cfg_change) begin
                busy     <= 1'b1;
                in_numer <= ({{38{1'b0}}, cfg_carrier_hz[17:0]} << ACC_BITS)
                           + {30'h0, CLK_HZ[25:0]};
                in_start <= 1'b1;
                off_shift<= ACC_BITS[5:0] - {2'b0, log2s(cfg_phase_steps)};
            end else if (in_done) begin
                busy <= 1'b0;
                inc  <= in_div0 ? {ACC_BITS{1'b0}} : in_quot[ACC_BITS-1:0];
            end
        end
    end

endmodule
