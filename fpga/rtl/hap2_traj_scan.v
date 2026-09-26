`timescale 1ns/1ps

// 多段草图子系统：把「从串口收到的一行文本」变成「焦点每 10 µs 走到哪里」。
//
// 里面装了三块，按顺序串起来：
//   1. hap2_scan_parse   解析草图文案 → 点表 + 段表
//   2. hap2_traj_plan    点表 + 段表 + 配置 → 节拍表（开方、除法都在这边）
//   3. hap2_traj_walk    节拍表 → 每拍的焦点坐标、扫描开关、笔号
// 另外顺手把草图的**原文**抄一份存起来：因为 STATE 回传里要带上 `scan_paths`
// 原样字符串（上位机靠它认图形），而行缓冲下一帧就被覆盖了。
//
// 时序上是「解析完毕 → 编译完毕 → 才开始走」，由命令层按三个脉冲依次触发：
// parse_start → （等 parse_ok）→ plan_start → （等 plan_done）→ walk_start。
module hap2_traj_scan #(
    parameter integer ADDR_W   = 13,     // 行缓冲地址位宽（8192 字节）
    parameter integer PT_BITS  = 21,     // 坐标位宽（0.5 µm 单位）
    parameter integer MAX_PTS  = 256,
    parameter integer MAX_STK  = 32,
    parameter integer MAX_TXT  = 4096,   // 草图原文最长多少字节
    parameter integer FRAC     = 16,
    parameter integer TICK_CYC = 500,
    // 原文缓冲：上下两半各 4 KB。解析时写「没在对外的」那一半，配置被接受了
    // 才把对外的那一半切成它（换一个指针而已，不用搬运字节）。这样被拒绝的
    // 配置不会污染回传里那份 scan_paths。
    parameter integer TXT_AW   = 13
) (
    input  wire                      clk,
    input  wire                      rst_n,

    // ---- 来自命令层 ----
    input  wire                      parse_start,  // 单拍：解析当前行缓冲里的草图
    /* verilator lint_off UNUSEDSIGNAL */
    // 行缓冲最多 8 KB，而草图文案有 4 KB 上限，src_off 的最高位用不到
    input  wire [ADDR_W:0]           src_off,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [ADDR_W:0]           src_len,
    input  wire                      plan_start,   // 单拍：把点表／段表编译成节拍表
    // 单拍：这份配置被接受了，可以把抄下来的原文公布出去（STATE 里要回传它）。
    // 被拒绝的配置不能公布，否则回传的 scan_paths 和实际生效的图形对不上。
    input  wire                      txt_commit,
    // 和 txt_commit 同时给出：这次生效的配置里**没有草图**（预设图形，或 NONE），
    // 那回传的 scan_paths 就应当老老实实写 NONE
    input  wire                      txt_none,
    // 节拍表要用的是**影子配置**里的这两个值（编译在配置生效之前发生）
    input  wire [31:0]               cfg_repeat_millihz,
    input  wire [31:0]               cfg_blank_us,
    output wire                      parse_busy,
    output wire                      parse_ok,     // 单拍
    output wire                      parse_bad,    // 单拍
    output wire                      is_none,      // 原文就是 NONE（这份配置没有草图）
    output wire                      plan_busy,
    output wire                      plan_done,    // 单拍：节拍表算好了
    output wire                      plan_fault,   // 单拍：这份图形／配置不能用
    output wire [8:0]                point_count,
    output wire [5:0]                stroke_count,

    // ---- 行缓冲读口（解析时用，同步读）----
    output wire [ADDR_W-1:0]         rd_addr,
    input  wire [7:0]                rd_data,

    // ---- 走步器控制 ----
    input  wire                      walk_start,
    input  wire                      walk_hold,
    input  wire                      walk_stop,
    output wire signed [PT_BITS-1:0] focus_x,
    output wire signed [PT_BITS-1:0] focus_y,
    output wire                      scan_on,
    output wire [5:0]                stroke_index,
    output wire                      beat_pulse,
    output wire                      walk_running,

    // ---- 草图原文（给应答模块回传 scan_paths）----
    // 地址只到「一半」：对外那份永远从这一半的开头数起，用不着知道在哪一半
    input  wire [TXT_AW-2:0]         txt_addr,
    output reg  [7:0]                txt_data,
    output wire [ADDR_W:0]           txt_len,
    output wire                      txt_valid
);

    // ---- 解析器 ----
    wire [ADDR_W-1:0]         parse_rd_addr;
    wire [4:0]                st_addr;
    wire [8:0]                st_start, st_len;
    wire signed [PT_BITS-1:0] pt_x, pt_y;
    reg  [7:0]                pt_addr;

    hap2_scan_parse #(
        .ADDR_W  (ADDR_W),
        .PT_BITS (PT_BITS),
        .MAX_PTS (MAX_PTS),
        .MAX_STK (MAX_STK),
        .MAX_TXT (MAX_TXT)
    ) u_parse (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (parse_start),
        .src_off      (src_off),
        .src_len      (src_len),
        .rd_addr      (parse_rd_addr),
        .rd_data      (rd_data),
        .busy         (parse_busy),
        .ok           (parse_ok),
        .bad          (parse_bad),
        .point_count  (point_count),
        .stroke_count (stroke_count),
        .is_none      (is_none),
        .pt_addr      (pt_addr),
        .pt_x         (pt_x),
        .pt_y         (pt_y),
        .st_addr      (st_addr),
        .st_start     (st_start),
        .st_len       (st_len)
    );

    assign rd_addr = parse_rd_addr;

    // ---- 顺手把原文抄一份 ----
    // 解析器读哪个字节，我们就存哪个字节：rd_data 比 rd_addr 晚一拍有效，
    // 所以地址也要跟着打一拍，两边才对得上（同一个字节会被写两次，值一样，无害）。
    reg [TXT_AW-2:0] copy_addr_q;
    reg [7:0]        txt_ram [0:(1 << TXT_AW)-1];
    reg [ADDR_W:0]   txt_len_r;
    reg              txt_valid_r;
    reg              txt_sel;      // 0 = 对外的是低 4 KB，1 = 高 4 KB

    always @(posedge clk) begin
        if (!rst_n) begin
            copy_addr_q <= {(TXT_AW-1){1'b0}};
            txt_len_r   <= {ADDR_W+1{1'b0}};
            txt_valid_r <= 1'b0;
            txt_sel     <= 1'b0;
            txt_data    <= 8'h00;
        end else begin
            copy_addr_q <= (parse_rd_addr - src_off[ADDR_W-1:0]);
            if (parse_busy) txt_ram[{~txt_sel, copy_addr_q}] <= rd_data;
            txt_data <= txt_ram[{txt_sel, txt_addr}];
            if (txt_commit) begin
                if (txt_none) begin
                    // 没有草图：把 NONE 写进「没在对外的」那一半，再切过去
                    txt_ram[{~txt_sel, 12'd0}] <= "N";
                    txt_ram[{~txt_sel, 12'd1}] <= "O";
                    txt_ram[{~txt_sel, 12'd2}] <= "N";
                    txt_ram[{~txt_sel, 12'd3}] <= "E";
                    txt_len_r  <= 13'd4;
                end else begin
                    txt_len_r <= src_len;
                end
                txt_valid_r <= 1'b1;
                txt_sel     <= ~txt_sel;
            end
        end
    end

    assign txt_len   = txt_len_r;
    assign txt_valid = txt_valid_r;

    // ---- 节拍表 ----
    wire [8:0]         rd_mv;
    wire [23:0]        mv_beats;
    wire signed [31:0] mv_step_x, mv_step_y;
    wire               mv_scan;
    wire [5:0]         mv_stroke;
    wire [8:0]         move_count;
    wire signed [PT_BITS-1:0] plan_x0, plan_y0;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [31:0]        laps_beats, blank_beats;   // 只在波形上看，暂时没人用
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_traj_plan #(
        .PT_BITS (PT_BITS), .MAX_PTS (MAX_PTS), .MAX_STK (MAX_STK), .FRAC (FRAC)
    ) u_plan (
        .clk                (clk),
        .rst_n              (rst_n),
        .point_count        (point_count),
        .stroke_count       (stroke_count),
        .pt_addr            (pt_addr),
        .pt_x               (pt_x),
        .pt_y               (pt_y),
        .st_addr            (st_addr),
        .st_start           (st_start),
        .st_len             (st_len),
        .cfg_repeat_millihz (cfg_repeat_millihz),
        .cfg_blank_us       (cfg_blank_us),
        .start              (plan_start),
        .busy               (plan_busy),
        .done               (plan_done),
        .fault              (plan_fault),
        .laps_beats         (laps_beats),
        .blank_beats        (blank_beats),
        .move_count         (move_count),
        .start_x            (plan_x0),
        .start_y            (plan_y0),
        .rd_mv              (rd_mv),
        .mv_beats           (mv_beats),
        .mv_step_x          (mv_step_x),
        .mv_step_y          (mv_step_y),
        .mv_scan            (mv_scan),
        .mv_stroke          (mv_stroke)
    );

    // ---- 走步器 ----
    // 没有有效节拍表时，走步器停在阵列中心（0,0）并且不开扫描，
    // 免得把上一份图形的表当成这一份继续走。
    // traj_ok 的意思是「表里这份节拍表对得上当前配置」：
    //   新配置开始解析 → 旧表作废；
    //   节拍表编译成功 → 有效；
    //   编译失败（图形做不出来）→ 作废。
    // 注意停止（walk_stop）只是停下走步器，不能让表作废——停下之后
    // 焦点要回到这张表的起点，而不是回到阵列中心。
    reg  traj_ok;
    always @(posedge clk) begin
        if (!rst_n)             traj_ok <= 1'b0;
        else if (parse_start)   traj_ok <= 1'b0;
        else if (plan_done)     traj_ok <= 1'b1;
        else if (plan_fault)    traj_ok <= 1'b0;
    end

    wire signed [PT_BITS-1:0] walk_x0 = traj_ok ? plan_x0 : {PT_BITS{1'b0}};
    wire signed [PT_BITS-1:0] walk_y0 = traj_ok ? plan_y0 : {PT_BITS{1'b0}};

    hap2_traj_walk #(
        .PT_BITS (PT_BITS), .FRAC (FRAC), .TICK_CYC (TICK_CYC)
    ) u_walk (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (walk_start),
        .hold         (walk_hold),
        .stop         (walk_stop),
        .start_x      (walk_x0),
        .start_y      (walk_y0),
        .move_count   (move_count),
        .rd_mv        (rd_mv),
        .mv_beats     (mv_beats),
        .mv_step_x    (mv_step_x),
        .mv_step_y    (mv_step_y),
        .mv_scan      (mv_scan),
        .mv_stroke    (mv_stroke),
        .focus_x      (focus_x),
        .focus_y      (focus_y),
        .scan_on      (scan_on),
        .stroke_index (stroke_index),
        .beat_pulse   (beat_pulse),
        .running      (walk_running)
    );

endmodule
