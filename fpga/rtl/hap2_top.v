`timescale 1ns/1ps

// HAP3 固件顶层：一根串口线进、一根串口线出，中间是整条链路。
//
//   uart_rx_pin → 收字节 → 攒行 → 校验码 → 解析字段 ─┬→ 命令裁决
//                                                    │      ├→ 多段草图：文本 → 点表/段表
//                                                    │      │              → 节拍表 → 走步器
//                                                    │      └→ 应答组装 → 校验码 → uart_tx_pin
//                                                    └→ 心跳看门狗
//
// 数据流上有一条关键的分工：
//   电脑只发一次图形，**慢**；
//   板子每秒自己算十万次相位、走十万步，**快**，全程不碰串口；
//   板子每秒回几十份状态快照，**慢**，只是报告，不参与实时输出。
//
// 已经做完并仿真验证过的：串口收发与协议、命令裁决、多段草图与预设图形的轨迹、
// 相位计算、输出级（含等级/调制）、本地按键、工作空间越界检查。
// 还需要上板确认的：相位符号、死区取值、驱动板与真实换能器。
// 板级封装（上电复位、boot 标识、引脚约束）见 rtl/board_top.v 和 fpga/board/。
module hap2_top #(
    parameter integer        CLK_HZ       = 50_000_000,
    parameter integer        BAUD         = 115200,
    parameter integer        ADDR_W       = 13,        // 行缓冲 8192 字节
    parameter integer        MAX_LINE     = 4096,
    parameter integer        HW_ROWS      = 4,
    parameter integer        HW_COLS      = 4,
    parameter integer        HW_PITCH_UM  = 10000,
    // ---- 设备声明的工作空间（微米）----
    // 握手时发给电脑，命令层也拿同一组值去查「图形有没有越界」。
    // 上板时按真实阵列的有效口径改小一点更安全。
    parameter integer        WS_X_MIN_UM  = -100000,
    parameter integer        WS_X_MAX_UM  =  100000,
    parameter integer        WS_Y_MIN_UM  = -100000,
    parameter integer        WS_Y_MAX_UM  =  100000,
    parameter integer        WS_Z_MIN_UM  =   20000,
    parameter integer        WS_Z_MAX_UM  =  300000,
    // 本地按键选中预设图形时的半径（微米）
    parameter integer        LOC_RADIUS_UM = 20000,
    // 本地按键消抖时间（毫秒）
    parameter integer        KEY_DEB_MS    = 20,
    parameter integer        HB_MS        = 3000,
    // 没收到命令时每隔多少毫秒主动回一份状态快照。
    // 上位机每 0.8 秒探活一次、状态超过 2.5 秒没到就判过期，所以 500 毫秒足够；
    // 草图很长时一帧状态本身就要几百毫秒，可以把这里调大。
    // 0 表示关掉（测试台用 0，免得打乱「这一条命令该回几帧」的核对）。
    parameter integer        STATE_MS     = 500,
    parameter integer        PH_PIPE      = 4,         // 相位并行几条开方流水线
    parameter integer        OUT_DEAD_CYC = 0,         // 互补输出的死区拍数（单端驱动填 0）
    parameter integer        TICK_CYC     = 500        // 50 MHz 下 10 µs = 500 拍
) (
    input  wire clk,
    input  wire rst_n,
    input  wire uart_rx_pin,
    output wire uart_tx_pin,
    // 本次启动的标识（协议要求「每次复位都变、同一会话不变」）。
    // 板级顶层用「不受复位影响的自由计数器」在复位放开那一刻锁一个值进来。
    input  wire [31:0] boot_id,
    // ---- 面板按键（高有效，已经由板级顶层把板上的低有效按键取反）----
    input  wire        key_next,
    input  wire        key_play,
    input  wire        key_stop,
    // 当前是不是本地控制模式（给状态灯用）
    output wire        mode_local,
    // 调试／观测：当前焦点与扫描开关（相位计算做出来之后这里接阵列）
    output wire signed [20:0] focus_x,
    output wire signed [20:0] focus_y,
    output wire               scan_on,
    output wire [5:0]         stroke_index,
    output wire               output_on,
    output wire               beat_pulse,
    output wire               walk_running,
    // ---- 送给驱动电路的 16 路方波 ----
    // 半桥驱动要互补输入时用 array_neg（带死区）；单端驱动只用 array_pos
    output wire [HW_ROWS*HW_COLS-1:0] array_pos,
    output wire [HW_ROWS*HW_COLS-1:0] array_neg
);

    // ---------------- 接收通路 ----------------
    /* verilator lint_off UNUSEDSIGNAL */
    // 这些信号暂时只接出来看波形（或者在诊断时用），不参与逻辑判断
    wire              line_ready, line_dropped;
    wire [ADDR_W:0]   line_len;
    wire              frame_ok, frame_bad;
    wire [3:0]        err_code;
    wire [4:0]        bad_field;
    wire              header_ok;
    wire [1:0]        kind;
    wire [15:0]       seq;
    wire [3:0]        verb;
    wire              verb_unknown;
    wire              field_valid;
    wire [4:0]        field_id;
    wire [31:0]       field_value;
    wire [2:0]        field_type, field_enum;
    wire [ADDR_W:0]   field_off, field_size;
    wire [18:0]       seen_mask;
    wire [15:0]       crc_value, crc_expected;
    wire              rx_valid, rx_error;
    /* verilator lint_on UNUSEDSIGNAL */

    // 行缓冲读口：草图解析要借一下
    wire [ADDR_W-1:0] scan_rd_addr;
    wire [7:0]        buf_data;
    wire              scan_parse_busy;

    hap2_rx #(
        .CLK_HZ   (CLK_HZ),
        .BAUD     (BAUD),
        .MAX_LINE (MAX_LINE),
        .ADDR_W   (ADDR_W)
    ) u_rx (
        .clk          (clk),
        .rst_n        (rst_n),
        .uart_rx_pin  (uart_rx_pin),
        .ext_rd_addr  (scan_rd_addr),
        .ext_rd_busy  (scan_parse_busy),
        .buf_data_out (buf_data),
        .line_ready   (line_ready),
        .line_len     (line_len),
        .line_dropped (line_dropped),
        .frame_ok     (frame_ok),
        .frame_bad    (frame_bad),
        .err_code     (err_code),
        .bad_field    (bad_field),
        .header_ok    (header_ok),
        .kind         (kind),
        .seq          (seq),
        .verb         (verb),
        .verb_unknown (verb_unknown),
        .field_valid  (field_valid),
        .field_id     (field_id),
        .field_value  (field_value),
        .field_type   (field_type),
        .field_enum   (field_enum),
        .field_off    (field_off),
        .field_size   (field_size),
        .seen_mask    (seen_mask),
        .crc_value    (crc_value),
        .crc_expected (crc_expected),
        .rx_valid     (rx_valid),
        .rx_error     (rx_error)
    );

    // ---------------- 命令裁决 ----------------
    wire        reply_valid;
    wire [1:0]  reply_kind;
    wire [3:0]  reply_verb;
    wire [15:0] reply_seq;
    wire [3:0]  reply_code;
    wire        reply_want_state;
    wire [1:0]  cmd_mode, cmd_run;
    wire [2:0]  cmd_reason;
    wire [15:0] cmd_rev;
    /* verilator lint_off UNUSEDSIGNAL */
    // 这些同样只为了看波形：配置变更脉冲、节拍表状态、走步器状态等
    wire        config_changed;
    wire [31:0] cfg_carrier_hz, cfg_phase_steps, cfg_cx_um, cfg_cy_um, cfg_z_um;
    wire [31:0] cfg_radius_um, cfg_repeat_millihz, cfg_mod_hz, cfg_level;
    wire [2:0]  cfg_shape;
    wire [31:0] cfg_path_closed, cfg_blank_us;
    wire        heartbeat_lost;

    // 轨迹子系统的接口
    wire        traj_parse_start, traj_plan_start;
    wire        traj_txt_commit;
    wire        traj_txt_none;
    wire        traj_shape_start, traj_preset, traj_shape_done, traj_shape_busy;
    wire [2:0]  traj_shape_kind;
    wire [31:0] traj_radius_um, traj_cx_um, traj_cy_um;
    wire        traj_parse_ok, traj_parse_bad, traj_none;
    wire        traj_plan_busy, traj_plan_done, traj_plan_fault;
    wire [ADDR_W:0] traj_src_off, traj_src_len;
    wire [31:0] traj_repeat_millihz, traj_blank_us;
    wire        walk_start, walk_hold, walk_stop;
    wire [8:0]  traj_pts;
    wire [5:0]  traj_stk;
    wire        traj_ready;
    wire        connected;
    wire        state_capture;      // 发送模块开始拼 STATE 的那一拍
    // 图形包围盒（0.5 µm）与本地按键
    wire        bb_vld;
    wire signed [20:0] bb_xmin, bb_xmax, bb_ymin, bb_ymax;
    wire        loc_next, loc_play, loc_stop;
    wire [2:0]  loc_shape;
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_cmd #(
        .ADDR_W      (ADDR_W),
        .WS_X_MIN_UM (WS_X_MIN_UM),
        .WS_X_MAX_UM (WS_X_MAX_UM),
        .WS_Y_MIN_UM (WS_Y_MIN_UM),
        .WS_Y_MAX_UM (WS_Y_MAX_UM),
        .WS_Z_MIN_UM (WS_Z_MIN_UM),
        .WS_Z_MAX_UM (WS_Z_MAX_UM),
        .LOC_RADIUS_UM (LOC_RADIUS_UM)
    ) u_cmd (
        .clk              (clk),
        .rst_n            (rst_n),
        .frame_ok         (frame_ok),
        .frame_bad        (frame_bad),
        .header_ok        (header_ok),
        .kind             (kind),
        .seq              (seq),
        .verb             (verb),
        .verb_unknown     (verb_unknown),
        .field_valid      (field_valid),
        .field_id         (field_id),
        .field_value      (field_value),
        .field_enum       (field_enum),
        .field_off        (field_off),
        .field_size       (field_size),
        .seen_mask        (seen_mask),
        .array_rows       ({24'h0, HW_ROWS[7:0]}),
        .array_cols       ({24'h0, HW_COLS[7:0]}),
        .array_pitch_um   (HW_PITCH_UM),
        .heartbeat_lost   (heartbeat_lost),
        .traj_parse_start (traj_parse_start),
        .traj_plan_start  (traj_plan_start),
        .traj_txt_commit  (traj_txt_commit),
        .traj_txt_none    (traj_txt_none),
        .traj_shape_start (traj_shape_start),
        .traj_preset      (traj_preset),
        .traj_shape_done  (traj_shape_done),
        .traj_shape_kind  (traj_shape_kind),
        .traj_radius_um   (traj_radius_um),
        .traj_cx_um       (traj_cx_um),
        .traj_cy_um       (traj_cy_um),
        .bb_vld           (bb_vld),
        .bb_xmin          (bb_xmin),
        .bb_xmax          (bb_xmax),
        .bb_ymin          (bb_ymin),
        .bb_ymax          (bb_ymax),
        .loc_next         (loc_next),
        .loc_play         (loc_play),
        .loc_stop         (loc_stop),
        .loc_shape        (loc_shape),
        .traj_parse_ok    (traj_parse_ok),
        .traj_parse_bad   (traj_parse_bad),
        .traj_none        (traj_none),
        .traj_plan_done   (traj_plan_done),
        .traj_plan_fault  (traj_plan_fault),
        .traj_src_off     (traj_src_off),
        .traj_src_len     (traj_src_len),
        .traj_repeat_millihz (traj_repeat_millihz),
        .traj_blank_us    (traj_blank_us),
        .walk_start       (walk_start),
        .walk_hold        (walk_hold),
        .walk_stop        (walk_stop),
        .traj_ready       (traj_ready),
        .reply_valid      (reply_valid),
        .reply_kind       (reply_kind),
        .reply_verb       (reply_verb),
        .reply_seq        (reply_seq),
        .reply_code       (reply_code),
        .reply_want_state (reply_want_state),
        .mode             (cmd_mode),
        .run_state        (cmd_run),
        .reason           (cmd_reason),
        .revision         (cmd_rev),
        .config_changed   (config_changed),
        .connected_out    (connected),
        .cfg_carrier_hz   (cfg_carrier_hz),
        .cfg_phase_steps  (cfg_phase_steps),
        .cfg_cx_um        (cfg_cx_um),
        .cfg_cy_um        (cfg_cy_um),
        .cfg_z_um         (cfg_z_um),
        .cfg_radius_um    (cfg_radius_um),
        .cfg_repeat_millihz (cfg_repeat_millihz),
        .cfg_mod_hz       (cfg_mod_hz),
        .cfg_level        (cfg_level),
        .cfg_shape        (cfg_shape),
        .cfg_path_closed  (cfg_path_closed),
        .cfg_blank_us     (cfg_blank_us)
    );

    // ---------------- 本地按键（不接电脑时的面板操作）----------------
    // 消抖、边沿识别都在 hap2_local 里；这里只是把三个单拍脉冲和「当前选中的图形」
    // 转给命令层。本地动作只在 LOCAL 模式下生效，不需要电脑握手。
    hap2_local #(
        .CLK_HZ (CLK_HZ),
        .DEB_MS (KEY_DEB_MS)
    ) u_local (
        .clk        (clk),
        .rst_n      (rst_n),
        .key_next   (key_next),
        .key_play   (key_play),
        .key_stop   (key_stop),
        .next_pulse (loc_next),
        .play_pulse (loc_play),
        .stop_pulse (loc_stop),
        .sel        (loc_shape)
    );

    // 当前是不是本地控制模式（LOCAL = 0，和协议里的枚举一致）
    assign mode_local = (cmd_mode == 2'd0);

    // ---------------- 多段草图子系统 ----------------
    wire [7:0]  txt_data;
    wire [11:0] txt_addr;
    wire [12:0] txt_len;
    wire        txt_valid;

    hap2_traj_scan #(
        .ADDR_W   (ADDR_W),
        .PT_BITS  (21),
        .FRAC     (16),
        .TICK_CYC (TICK_CYC)
    ) u_traj (
        .clk                (clk),
        .rst_n              (rst_n),
        .parse_start        (traj_parse_start),
        .src_off            (traj_src_off),
        .src_len            (traj_src_len),
        .plan_start         (traj_plan_start),
        // 预设图形这条支路
        .preset             (traj_preset),
        .shape_start        (traj_shape_start),
        // 注意用的是**影子配置**里的形状参数：生成发生在配置生效之前，
        // 用已生效的旧配置会生成出上一种图形
        .shape              (traj_shape_kind),
        .cfg_radius_um      (traj_radius_um),
        .cfg_cx_um          (traj_cx_um),
        .cfg_cy_um          (traj_cy_um),
        .shape_busy         (traj_shape_busy),
        .shape_done         (traj_shape_done),
        .bb_vld             (bb_vld),
        .bb_xmin            (bb_xmin),
        .bb_xmax            (bb_xmax),
        .bb_ymin            (bb_ymin),
        .bb_ymax            (bb_ymax),
        .txt_commit         (traj_txt_commit),
        .txt_none           (traj_txt_none),
        .traj_ready_in      (traj_ready),
        .cfg_repeat_millihz (traj_repeat_millihz),
        .cfg_blank_us       (traj_blank_us),
        .parse_busy         (scan_parse_busy),
        .parse_ok           (traj_parse_ok),
        .parse_bad          (traj_parse_bad),
        .is_none            (traj_none),
        .plan_busy          (traj_plan_busy),
        .plan_done          (traj_plan_done),
        .plan_fault         (traj_plan_fault),
        .point_count        (traj_pts),
        .stroke_count       (traj_stk),
        .rd_addr            (scan_rd_addr),
        .rd_data            (buf_data),
        .walk_start         (walk_start),
        .walk_hold          (walk_hold),
        .walk_stop          (walk_stop),
        .focus_x            (focus_x),
        .focus_y            (focus_y),
        .scan_on            (scan_on),
        .stroke_index       (stroke_index),
        .beat_pulse         (beat_pulse),
        .walk_running       (walk_running),
        .txt_addr           (txt_addr),
        .txt_data           (txt_data),
        .txt_len            (txt_len),
        .txt_valid          (txt_valid)
    );

    // ---------------- 输出使能 ----------------
    // 协议规定三件事同时成立才允许输出：运行中、等级大于 0、正在扫描。
    assign output_on = (cmd_run == 2'd1) && (cfg_level != 32'd0) && scan_on;

    // ---------------- 相位计算 ----------------
    // 每拍更新一次：把焦点坐标变成每一路的相位码。载波换了要重算常数。
    /* verilator lint_off UNUSEDSIGNAL */
    wire        ph_busy, ph_done;   // 只在波形上看
    wire [8:0]  ph_cnt;
    /* verilator lint_on UNUSEDSIGNAL */
    wire        ph_pub_sel;
    wire [7:0]  ph_rd_addr, ph_rd_data;
    wire [7:0]  ph_rd2_addr, ph_rd2_data;   // 第二个读口：给输出级拿相位码
    wire signed [20:0] ph_pub_fx, ph_pub_fy;
    wire [31:0] ph_pub_fz_um;

    hap2_phase #(
        .ROWS (HW_ROWS), .COLS (HW_COLS), .PITCH_UM (HW_PITCH_UM), .PIPE (PH_PIPE)
    ) u_phase (
        .clk                (clk),
        .rst_n              (rst_n),
        .cfg_carrier_hz     (cfg_carrier_hz),
        .cfg_phase_steps    (cfg_phase_steps),
        .cfg_z_um           (cfg_z_um),
        .cfg_change         (config_changed),
        .focus_x            (focus_x),
        .focus_y            (focus_y),
        .start              (beat_pulse),
        .busy               (ph_busy),
        .done               (ph_done),
        .sweep_cnt          (ph_cnt),
        .pub_sel            (ph_pub_sel),
        .rd_sel             (snap_sel),
        .rd_addr            (ph_rd_addr),
        .rd_data            (ph_rd_data),
        .rd2_addr           (ph_rd2_addr),
        .rd2_data           (ph_rd2_data),
        .pub_fx             (ph_pub_fx),
        .pub_fy             (ph_pub_fy),
        .pub_fz_um          (ph_pub_fz_um)
    );

    // ---------------- 状态快照 ----------------
    // ---------------- 输出级 ----------------
    // 用相位表里那张表生成 16 路方波。enable 就是「运行中 + 等级>0 + 正在扫描」，
    // 不满足时 16 路全部拉低——跳转、暂停、停止、上电复位时换能器都不能被驱动。
    hap2_out #(
        .ROWS (HW_ROWS), .COLS (HW_COLS), .CLK_HZ (CLK_HZ),
        .ACC_BITS (32), .DEAD_CYC (OUT_DEAD_CYC)
    ) u_out (
        .clk                (clk),
        .rst_n              (rst_n),
        .cfg_carrier_hz     (cfg_carrier_hz),
        .cfg_phase_steps    (cfg_phase_steps),
        .cfg_change         (config_changed),
        .cfg_level          (cfg_level),
        .cfg_mod_hz         (cfg_mod_hz),
        .enable             (output_on),
        .tbl_new            (ph_done),
        .tbl_addr           (ph_rd2_addr),
        .tbl_data           (ph_rd2_data),
        .out_pos            (array_pos),
        .out_neg            (array_neg)
    );

    // 一帧 STATE 要发几十毫秒，期间焦点一直在动、配置也可能被换掉。协议要求这一帧里
    // 的配置、坐标、相位表来自同一瞬间，所以发送模块一开始拼 STATE，就把它们锁存下来，
    // 相位表再复制一份；复制时把「读哪一半」钉住，这边翻指针也不会读串。
    reg  [7:0]  snap_ph [0:255];
    reg  [7:0]  snap_idx;
    reg         snap_wait, snap_copy;
    reg         snap_sel;
    reg  [8:0]  snap_count;

    reg [31:0] sn_carrier, sn_steps, sn_cx, sn_cy, sn_z, sn_radius;
    reg [31:0] sn_repeat, sn_mod, sn_level, sn_closed, sn_blank;
    reg [2:0]  sn_shape;
    reg [1:0]  sn_mode, sn_run;
    reg [2:0]  sn_reason;
    reg [15:0] sn_rev;
    reg        sn_scan;
    reg [5:0]  sn_stroke;

    assign ph_rd_addr = snap_idx;

    always @(posedge clk) begin
        if (!rst_n) begin
            snap_idx  <= 8'd0;
            snap_wait <= 1'b0;
            snap_copy <= 1'b0;
            snap_sel  <= 1'b0;
            snap_count<= 9'd0;
            sn_carrier<= 32'd40000; sn_steps <= 32'd64;  sn_cx <= 32'd0;
            sn_cy     <= 32'd0;     sn_z     <= 32'd150000;
            sn_radius <= 32'd20000; sn_repeat<= 32'd500;
            sn_mod    <= 32'd200;   sn_level <= 32'd30;
            sn_closed <= 32'd1;     sn_blank <= 32'd2000;
            sn_shape  <= 3'd3;      sn_mode  <= 2'd1;
            sn_run    <= 2'd0;      sn_reason<= 3'd0;
            sn_rev    <= 16'd0;     sn_scan  <= 1'b0;
            sn_stroke <= 6'd0;
        end else begin
            if (state_capture) begin
                // 锁存这一瞬间：配置、状态、扫描开关
                sn_carrier <= cfg_carrier_hz; sn_steps <= cfg_phase_steps;
                sn_cx      <= cfg_cx_um;      sn_cy    <= cfg_cy_um;
                sn_z       <= cfg_z_um;       sn_radius<= cfg_radius_um;
                sn_repeat  <= cfg_repeat_millihz; sn_mod <= cfg_mod_hz;
                sn_level   <= cfg_level;      sn_closed<= cfg_path_closed;
                sn_blank   <= cfg_blank_us;   sn_shape <= cfg_shape;
                sn_mode    <= cmd_mode;       sn_run   <= cmd_run;
                sn_reason  <= cmd_reason;     sn_rev   <= cmd_rev;
                sn_scan    <= scan_on;        sn_stroke<= stroke_index;
                // 相位表：钉住现在对外的哪一半，从头复制一遍
                snap_sel   <= ph_pub_sel;
                snap_idx   <= 8'd0;
                snap_wait  <= 1'b0;
                snap_copy  <= 1'b1;
                snap_count <= HW_ROWS * HW_COLS;
            end else if (snap_copy) begin
                if (snap_wait) begin
                    snap_ph[snap_idx] <= ph_rd_data;
                    snap_wait <= 1'b0;
                    if ({1'b0, snap_idx} + 9'd1 >= snap_count) snap_copy <= 1'b0;
                    else                                       snap_idx  <= snap_idx + 8'd1;
                end else begin
                    snap_wait <= 1'b1;      // 地址这一拍已经发出，等数据回来
                end
            end
        end
    end

    // 发送模块读相位快照（同步读，一拍出数据）
    reg [7:0] snap_rd_data;
    always @(posedge clk) snap_rd_data <= snap_ph[phase_addr];

    // ---------------- 应答组装与发送 ----------------
    /* verilator lint_off UNUSEDSIGNAL */
    wire [7:0] phase_addr;      // 相位表读口：相位计算做出来之前没人读
    wire       tx_busy;
    /* verilator lint_on UNUSEDSIGNAL */
    reg        state_push;      // 单拍：主动插一份状态快照
    reg [31:0] state_ms;

    // 主动上报：上位机就靠它刷新界面。没握手之前不发，免得对着空气广播。
    always @(posedge clk) begin
        if (!rst_n) begin
            state_push <= 1'b0;
            state_ms   <= 32'd0;
        end else if (STATE_MS == 0) begin
            state_push <= 1'b0;
        end else if (connected && state_ms >= (STATE_MS * (CLK_HZ / 1000)) - 1) begin
            state_ms   <= 32'd0;
            state_push <= 1'b1;
        end else if (connected) begin
            state_ms   <= state_ms + 1'b1;
            state_push <= 1'b0;
        end else begin
            state_ms   <= 32'd0;
            state_push <= 1'b0;
        end
    end

    hap2_tx #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD),
        .DIGITS (10),
        // 工作空间：和命令层用同一组参数，保证「声明的范围」和「实际拦的范围」一致
        .WS_X_MIN_UM (WS_X_MIN_UM),
        .WS_X_MAX_UM (WS_X_MAX_UM),
        .WS_Y_MIN_UM (WS_Y_MIN_UM),
        .WS_Y_MAX_UM (WS_Y_MAX_UM),
        .WS_Z_MIN_UM (WS_Z_MIN_UM),
        .WS_Z_MAX_UM (WS_Z_MAX_UM)
    ) u_tx (
        .clk              (clk),
        .rst_n            (rst_n),
        .tx_line          (uart_tx_pin),
        .busy             (tx_busy),
        .reply_valid      (reply_valid),
        .reply_kind       (reply_kind),
        .reply_verb       (reply_verb),
        .reply_seq        (reply_seq),
        .reply_code       (reply_code),
        .reply_want_state (reply_want_state),
        .boot_id          (boot_id),
        .state_push       (state_push),
        .state_capture    (state_capture),
        .revision         (cmd_rev),
        .revision_report  (sn_rev),
        // 下面这些一律来自**快照**：一份状态帧里的数据必须来自同一瞬间
        .mode             (sn_mode),
        .run_state        (sn_run),
        .reason           (sn_reason),
        .output_on        (sn_run == 2'd1 && sn_level != 32'd0 && sn_scan),
        .hw_rows          (HW_ROWS[7:0]),
        .hw_cols          (HW_COLS[7:0]),
        .hw_pitch_um      (HW_PITCH_UM),
        .cfg_carrier_hz   (sn_carrier),
        .cfg_phase_steps  (sn_steps),
        .cfg_cx_um        (sn_cx),
        .cfg_cy_um        (sn_cy),
        .cfg_z_um         (sn_z),
        .cfg_radius_um    (sn_radius),
        .cfg_repeat_millihz (sn_repeat),
        .cfg_mod_hz       (sn_mod),
        .cfg_level        (sn_level),
        .cfg_shape        (sn_shape),
        .cfg_path_closed  (sn_closed),
        .cfg_blank_us     (sn_blank),
        // 焦点与相位表来自同一次计算（相位引擎把那次用的坐标一并给出来），
        // 单位从 0.5 µm 换成整数微米
        .fx_um            ($signed(ph_pub_fx) >>> 1),
        .fy_um            ($signed(ph_pub_fy) >>> 1),
        .fz_um            (ph_pub_fz_um),
        .scan_on          (sn_scan),
        .stroke_index     (sn_stroke),
        .phase_addr       (phase_addr),
        .phase_data       (snap_rd_data),
        .txt_addr         (txt_addr),
        .txt_data         (txt_data),
        .txt_len          (txt_len),
        .txt_valid        (txt_valid)
    );

    // ---------------- 主机心跳 ----------------
    hap2_watchdog #(
        .CLK_HZ (CLK_HZ),
        .HB_MS  (HB_MS)
    ) u_wd (
        .clk        (clk),
        .rst_n      (rst_n),
        .host_frame (frame_ok),
        .mode       (cmd_mode),
        .run_state  (cmd_run),
        .timeout    (heartbeat_lost)
    );

endmodule
