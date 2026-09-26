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
// 现在还没做的：相位计算（`phases=` 先回全 0）、输出级、预设图形
// （CIRCLE 等形状的轨迹；多段草图已经能用）、以及上板约束。
module hap2_top #(
    parameter integer        CLK_HZ       = 50_000_000,
    parameter integer        BAUD         = 115200,
    parameter integer        ADDR_W       = 13,        // 行缓冲 8192 字节
    parameter integer        MAX_LINE     = 4096,
    parameter integer        HW_ROWS      = 4,
    parameter integer        HW_COLS      = 4,
    parameter integer        HW_PITCH_UM  = 10000,
    parameter [31:0]         BOOT_ID      = 32'hA1B2C3D4,
    parameter integer        HB_MS        = 3000,
    // 没收到命令时每隔多少毫秒主动回一份状态快照。
    // 上位机每 0.8 秒探活一次、状态超过 2.5 秒没到就判过期，所以 500 毫秒足够；
    // 草图很长时一帧状态本身就要几百毫秒，可以把这里调大。
    // 0 表示关掉（测试台用 0，免得打乱「这一条命令该回几帧」的核对）。
    parameter integer        STATE_MS     = 500,
    parameter integer        TICK_CYC     = 500        // 50 MHz 下 10 µs = 500 拍
) (
    input  wire clk,
    input  wire rst_n,
    input  wire uart_rx_pin,
    output wire uart_tx_pin,
    // 调试／观测：当前焦点与扫描开关（相位计算做出来之后这里接阵列）
    output wire signed [20:0] focus_x,
    output wire signed [20:0] focus_y,
    output wire               scan_on,
    output wire [5:0]         stroke_index,
    output wire               output_on,
    output wire               beat_pulse,
    output wire               walk_running
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
    wire        traj_parse_ok, traj_parse_bad, traj_none;
    wire        traj_plan_busy, traj_plan_done, traj_plan_fault;
    wire [ADDR_W:0] traj_src_off, traj_src_len;
    wire [31:0] traj_repeat_millihz, traj_blank_us;
    wire        walk_start, walk_hold, walk_stop;
    wire [8:0]  traj_pts;
    wire [5:0]  traj_stk;
    wire        traj_ready;
    wire        connected;
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_cmd #(
        .ADDR_W (ADDR_W)
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
        .txt_commit         (traj_txt_commit),
        .txt_none           (traj_txt_none),
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
        .DIGITS (10)
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
        .boot_id          (BOOT_ID),
        .state_push       (state_push),
        .revision         (cmd_rev),
        .mode             (cmd_mode),
        .run_state        (cmd_run),
        .reason           (cmd_reason),
        .output_on        (output_on),
        .hw_rows          (HW_ROWS[7:0]),
        .hw_cols          (HW_COLS[7:0]),
        .hw_pitch_um      (HW_PITCH_UM),
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
        .cfg_blank_us     (cfg_blank_us),
        // 焦点的单位是 0.5 µm，回传时换成整数微米（丢掉那半个微米）
        .fx_um            ($signed(focus_x) >>> 1),
        .fy_um            ($signed(focus_y) >>> 1),
        .fz_um            (cfg_z_um),
        .scan_on          (scan_on),
        .stroke_index     (stroke_index),
        .phase_addr       (phase_addr),
        // 相位计算还没做：先回全 0。协议允许 0..phase_steps-1，0 是合法的。
        .phase_data       (8'd0),
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
