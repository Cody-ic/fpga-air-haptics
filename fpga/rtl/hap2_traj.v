`timescale 1ns/1ps

// 轨迹通路的顶层：节拍表（配置阶段算） + 走步器（运行阶段放）。
//
// 两个模块的接口正好是一对：节拍表的「移动表读口」直接接到走步器的读口上。
// 这样分开的好处是职责清楚：
//   配置阶段（收到 CONFIG、机器停着）—— 开方、除法，算一份节拍表，慢一点没关系；
//   运行阶段（START 之后）—— 只有加减法，每秒十万步也来得及。
//
// 注意：重新编译节拍表会一行一行改写表，所以**必须机器停着的时候才允许 CONFIG**。
// 协议本来就是这么规定的（CONFIG 只在 IDLE 允许），命令层负责把关。
module hap2_traj #(
    parameter integer PT_BITS  = 21,
    parameter integer MAX_PTS  = 256,
    parameter integer MAX_STK  = 32,
    parameter integer FRAC     = 16,
    parameter integer TICK_CYC = 500
) (
    input  wire                      clk,
    input  wire                      rst_n,
    // ---- 来自命令层 ----
    input  wire                      plan_start,   // 单拍脉冲：把当前配置编译成节拍表
    // 节拍表是双缓冲的：读口读 tbl_sel 那一半，编译写另一半。
    // 编译到一半失败也不会破坏正在用的表，所以被拒绝的配置不改变已生效的东西。
    input  wire                      tbl_sel,
    input  wire                      walk_start,   // 单拍脉冲：从头开始走
    input  wire                      walk_hold,    // 电平：暂停
    input  wire                      walk_stop,    // 单拍脉冲：停下并回到起点
    // 走步器「停下时回到哪里」：由调用方给（没有可用节拍表时就给阵列中心）。
    // 不能直接用节拍表刚算出来的起点：编译到一半失败时那个值是半成品。
    input  wire signed [PT_BITS-1:0] walk_x0,
    input  wire signed [PT_BITS-1:0] walk_y0,
    input  wire [8:0]                walk_moves,   // 走步器按几行循环（同样是「已发布」的那份）
    // ---- 点表 / 段表（直接接到 hap2_scan_parse）----
    input  wire [8:0]                point_count,
    input  wire [5:0]                stroke_count,
    output wire [7:0]                pt_addr,
    input  wire signed [PT_BITS-1:0] pt_x,
    input  wire signed [PT_BITS-1:0] pt_y,
    output wire [4:0]                st_addr,
    input  wire [8:0]                st_start,
    input  wire [8:0]                st_len,
    // ---- 配置 ----
    input  wire [31:0]               cfg_repeat_millihz,
    input  wire [31:0]               cfg_blank_us,
    // ---- 节拍表状态（给命令层报错 / 决定能不能 START）----
    output wire                      plan_busy,
    output wire                      plan_done,
    output wire                      plan_fault,
    output wire [31:0]               laps_beats,
    output wire [23:0]               blank_beats,
    output wire [8:0]                move_count,
    output wire signed [PT_BITS-1:0] traj_x0,
    output wire signed [PT_BITS-1:0] traj_y0,
    // ---- 走步器输出（给相位计算和 STATE 回传）----
    output wire signed [PT_BITS-1:0] focus_x,
    output wire signed [PT_BITS-1:0] focus_y,
    output wire                      scan_on,
    output wire [5:0]                stroke_index,
    output wire                      beat_pulse,
    output wire                      walk_running
);

    wire [8:0]         rd_mv;
    wire [23:0]        mv_beats;
    wire signed [31:0] mv_step_x, mv_step_y;
    wire               mv_scan;
    wire [5:0]         mv_stroke;

    hap2_traj_plan #(
        .PT_BITS (PT_BITS), .MAX_PTS (MAX_PTS), .MAX_STK (MAX_STK), .FRAC (FRAC)
    ) u_plan (
        .clk               (clk),
        .rst_n             (rst_n),
        .tbl_sel           (tbl_sel),
        .point_count       (point_count),
        .stroke_count      (stroke_count),
        .pt_addr           (pt_addr),
        .pt_x              (pt_x),
        .pt_y              (pt_y),
        .st_addr           (st_addr),
        .st_start          (st_start),
        .st_len            (st_len),
        .cfg_repeat_millihz(cfg_repeat_millihz),
        .cfg_blank_us      (cfg_blank_us),
        .start             (plan_start),
        .busy              (plan_busy),
        .done              (plan_done),
        .fault             (plan_fault),
        .laps_beats        (laps_beats),
        .blank_beats       (blank_beats),
        .move_count        (move_count),
        .start_x           (traj_x0),
        .start_y           (traj_y0),
        .rd_mv             (rd_mv),
        .mv_beats          (mv_beats),
        .mv_step_x         (mv_step_x),
        .mv_step_y         (mv_step_y),
        .mv_scan           (mv_scan),
        .mv_stroke         (mv_stroke)
    );

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
        .move_count   (walk_moves),
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
