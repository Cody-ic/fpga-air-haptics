`timescale 1ns/1ps

// FPGA has one switch. MG24 -> PC transport is configured only in MG24 firmware.
`ifndef FPGA_CALIBRATION_ENABLE
`define FPGA_CALIBRATION_ENABLE 0
`endif
`ifndef FPGA_CALIBRATION_SOURCE
`define FPGA_CALIBRATION_SOURCE 0
`endif
// 1: standalone U102 (channel 3) 40 kHz / 50% test; overrides normal/calibration.
// Rebuild and download after changing. UART control is inactive in this mode.
`ifndef FPGA_DEBUG_U102
`define FPGA_DEBUG_U102 0
`endif

// 板级顶层：把「芯片里该有的逻辑」接到「板子上真实的引脚和器件」上。
// 逻辑功能都在 hap2_top 里；这一层只做几件**必须**在真板子上做的事：
//
//   1. 上电复位（por_reset）：明确配置初值，保持复位几毫秒后同步释放。
//   2. 复位标识 boot：协议要求「每次复位都变、同一会话里不变」。
//      用一个**不受复位影响**的自由计数器当熵源，复位放开那一拍锁存。
//   3. 把板上按键（低有效、需要内部上拉）取反成逻辑里的高有效，并交给 hap2_local 消抖。
//   4. 把 8 路移位数据、4 根控制线、串口、状态灯引到引脚（引脚号在 fpga/board/tang_mega_138k_pro.cst 里绑）。
//
// 目标板：Sipeed Tang MEGA 138K Pro（GW5AST-LV138FPG676A，676 球 FCPBG676A 封装）。
// 板上 50 MHz 在 P16，和固件默认参数一致，所以**不需要锁相环**。
module board_top #(
    // Explicit build-time reuse of a measured table; no new acoustic validation.
    parameter integer CAL_RESTORE_ENABLE = 0,
    parameter integer CAL_NULL_SCALED_QC = 0,
    parameter integer BUTTON_FOCUS_ENABLE = 0,
    parameter integer BUTTON_FOCUS_MS = 3000,
    parameter integer BUTTON_FOCUS_Z_UM = 100000,
    parameter integer BUTTON_FOCUS_LEVEL = 1,
    parameter integer BUTTON_FOCUS_SHAPE = 0,
    parameter integer BUTTON_FOCUS_RADIUS_UM = 0,
    parameter integer BUTTON_FOCUS_REPEAT_MILLIHZ = 500,
    parameter [511:0] CAL_RESTORE_OFFSETS = 512'd0,
    parameter integer CAL_Z_UM     = 70000,        // fixed receiver: nominal (0,0,70) mm
    parameter integer CAL_LEVEL    = 30,
    parameter integer CLK_HZ        = 50_000_000,   // 板上晶振频率（138K Pro 的 P16 = 50 MHz）
    parameter integer BAUD          = 115200,
    // R2 8x8 / 10.3 mm, eight parallel SN74LV595A data lanes.
    parameter integer HW_ROWS       = 8,
    parameter integer HW_COLS       = 8,
    parameter integer HW_PITCH_UM   = 10300,
    parameter integer PH_PIPE       = 8,
    parameter integer OUT_DEAD_CYC  = 0,            // 互补输出的死区拍数；单端驱动填 0
    parameter integer POR_MS        = 10,           // 上电复位按住多少毫秒
    parameter integer KEY_DEB_MS    = 20,           // 按键消抖时间（毫秒）
    parameter integer LOC_RADIUS_UM = 20000,        // 本地按键演示用的半径（微米）
    parameter integer STATE_MS      = 500,          // 主动上报状态的间隔（毫秒，0 = 关掉）
    // 设备声明的工作空间（微米）：发给电脑，同时也是「越界检查」的依据
    parameter integer WS_X_MIN_UM   = -100000,
    parameter integer WS_X_MAX_UM   =  100000,
    parameter integer WS_Y_MIN_UM   = -100000,
    parameter integer WS_Y_MAX_UM   =  100000,
    parameter integer WS_Z_MIN_UM   =   20000,
    parameter integer WS_Z_MAX_UM   =  300000
) (
    input  wire clk,          // 板上 50 MHz 晶振（P16）
    input  wire key_rst_n,    // 复位键（K16），低有效；没有按键就接 1'b1
    input  wire key0_n,       // F15: next shape; BUTTON_FOCUS build: stop
    input  wire key1_n,       // G15: play/pause; BUTTON_FOCUS build: bounded test / stop
    input  wire uart_rx_pin,  // 电脑 → 板子（N16，接板载调试器 BL616 的 TX）
    output wire uart_tx_pin,  // 板子 → 电脑（P15，接板载调试器 BL616 的 RX）
    output wire [7:0] sr_data, // H1 pins 7..14 = DATA0..DATA7
    output wire sr_clk,        // H1 pin 3, SRCLK
    output wire sr_latch,      // H1 pin 5, RCLK
    output wire sr_oe_n,       // H1 pin 4, high = disabled
    output wire sr_clear_n,    // H1 pin 6, low clears shift register only
    // 状态灯：输出开 / 正在扫描 / 走步器在跑（干净脚不够，只留最有用的三颗；
    // 本地模式、串口状态在状态帧里都看得到）
    output wire [2:0] led
);

    generate
        if (HW_ROWS != 8 || HW_COLS != 8 || CLK_HZ != 50000000 || OUT_DEAD_CYC != 0) begin : invalid_shift_board
            ERROR_R2_REQUIRES_8X8_50MHZ_SINGLE_ENDED invalid_board();
        end
        if ((`FPGA_CALIBRATION_ENABLE != 0 && `FPGA_CALIBRATION_ENABLE != 1) ||
            (`FPGA_CALIBRATION_SOURCE != 0 && `FPGA_CALIBRATION_SOURCE != 1) ||
            (`FPGA_DEBUG_U102 != 0 && `FPGA_DEBUG_U102 != 1)) begin : invalid_macros
            ERROR_MACROS_MUST_BE_ZERO_OR_ONE invalid_configuration();
        end
    endgenerate

    // ---------------- 1. 上电复位 ----------------
    wire rst_n;
    por_reset #(
        .CLK_HZ  (CLK_HZ),
        .HOLD_MS (POR_MS)
    ) u_por (
        .clk       (clk),
        .ext_rst_n (key_rst_n),
        .rst_n     (rst_n)
    );

    // ---------------- 2. 复位标识 boot ----------------
    // 自由计数器故意**不复位**：上电后它一直数。复位放开那一拍把当前值锁进 boot_q。
    // 用于区分同次上电期间的复位；计数器会回绕，断电重启也可能重复。
    // 这不是随机数，也不保证跨断电的启动标识唯一。
    reg [31:0] entropy_q = 32'd0;
    always @(posedge clk) entropy_q <= entropy_q + 32'd1;

    reg [31:0] boot_q = 32'd0;
    reg        rst_n_d = 1'b0;
    always @(posedge clk) begin
        rst_n_d <= rst_n;
        if (rst_n && !rst_n_d) boot_q <= entropy_q;   // 只在复位放开那一拍更新
    end

    // ---------------- 3. 按键：低有效 → 高有效 ----------------
    // 板上按键在 .cst 里开了内部上拉；
    // 不按时被上拉成高电平，按下去接地变低电平。所以这里取反，
    // 逻辑里统一成「1 = 按下」，消抖模块就不用关心板子怎么接的。
    wire key_next = ~key0_n;
    wire key_play = ~key1_n;
    // 板上还有第 4 个用户键（G16，官方资料里当复位/重配置用），本地"停止"先不接它
    // （播放键本身既能暂停也能继续）。要接的话在 .cst 里加一行，再把这里改成 ~key2_n。
    wire key_stop = 1'b0;

    // ---------------- 4. 主逻辑 ----------------
    wire       output_on, scan_on, walk_running, mode_local;
    wire       normal_uart_tx;
    wire [63:0] array_pos;
    /* verilator lint_off UNUSEDSIGNAL */
    wire signed [20:0] focus_x, focus_y;
    wire [5:0]  stroke_index;
    wire        beat_pulse;
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_top #(
        .CAL_ENABLE    (`FPGA_CALIBRATION_ENABLE),
        .CAL_SOURCE    (`FPGA_CALIBRATION_SOURCE),
        .CAL_RESTORE_ENABLE (CAL_RESTORE_ENABLE),
        .CAL_NULL_SCALED_QC(CAL_NULL_SCALED_QC),
        .BUTTON_FOCUS_ENABLE(BUTTON_FOCUS_ENABLE),
        .BUTTON_FOCUS_MS(BUTTON_FOCUS_MS),
        .BUTTON_FOCUS_Z_UM(BUTTON_FOCUS_Z_UM),
        .BUTTON_FOCUS_LEVEL(BUTTON_FOCUS_LEVEL),
        .BUTTON_FOCUS_SHAPE(BUTTON_FOCUS_SHAPE),
        .BUTTON_FOCUS_RADIUS_UM(BUTTON_FOCUS_RADIUS_UM),
        .BUTTON_FOCUS_REPEAT_MILLIHZ(BUTTON_FOCUS_REPEAT_MILLIHZ),
        .CAL_RESTORE_OFFSETS(CAL_RESTORE_OFFSETS),
        .CAL_Z_UM      (CAL_Z_UM),
        .CAL_LEVEL     (CAL_LEVEL),
        .CLK_HZ        (CLK_HZ),
        .BAUD          (BAUD),
        .HW_ROWS       (HW_ROWS),
        .HW_COLS       (HW_COLS),
        .HW_PITCH_UM   (HW_PITCH_UM),
        .PH_PIPE       (PH_PIPE),
        .OUT_DEAD_CYC  (OUT_DEAD_CYC),
        .KEY_DEB_MS    (KEY_DEB_MS),
        .LOC_RADIUS_UM (LOC_RADIUS_UM),
        .STATE_MS      (STATE_MS),
        .WS_X_MIN_UM   (WS_X_MIN_UM),
        .WS_X_MAX_UM   (WS_X_MAX_UM),
        .WS_Y_MIN_UM   (WS_Y_MIN_UM),
        .WS_Y_MAX_UM   (WS_Y_MAX_UM),
        .WS_Z_MIN_UM   (WS_Z_MIN_UM),
        .WS_Z_MAX_UM   (WS_Z_MAX_UM)
    ) u_top (
        .clk         (clk),
        .rst_n       (rst_n),
        .uart_rx_pin (uart_rx_pin),
        .uart_tx_pin (normal_uart_tx),
        .boot_id     (boot_q),
        .key_next    (key_next),
        .key_play    (key_play),
        .key_stop    (key_stop),
        .mode_local  (mode_local),
        .focus_x     (focus_x),
        .focus_y     (focus_y),
        .scan_on     (scan_on),
        .stroke_index(stroke_index),
        .output_on   (output_on),
        .beat_pulse  (beat_pulse),
        .walk_running(walk_running),
        .array_pos   (array_pos),
        /* verilator lint_off PINCONNECTEMPTY */
        .array_neg   ()          // 单端驱动不用；要半桥就照上面的说明把端口引出来
        /* verilator lint_on PINCONNECTEMPTY */
    );

    generate if (`FPGA_DEBUG_U102 == 1) begin : debug_u102
        // Separate, fixed-rate latch timing avoids the normal 380 ns frame
        // quantization. Unused normal logic is removed during synthesis.
        hap2_debug_u102 u_debug (
            .clk(clk), .rst_n(rst_n),
            .sr_data(sr_data), .sr_clk(sr_clk), .sr_latch(sr_latch),
            .sr_oe_n(sr_oe_n), .sr_clear_n(sr_clear_n)
        );
    end else begin : normal_shift
      hap2_shift8 u_shift (
        .clk(clk), .rst_n(rst_n), .enable(output_on), .waves(array_pos),
        .sr_data(sr_data), .sr_clk(sr_clk), .sr_latch(sr_latch),
        .sr_oe_n(sr_oe_n), .sr_clear_n(sr_clear_n)
      );
    end endgenerate

    assign uart_tx_pin = `FPGA_DEBUG_U102 ? 1'b1 : normal_uart_tx;

    // ---------------- 5. 状态灯 ----------------
    // 3 颗灯分别是：正在驱动换能器、正在扫描、走步器在跑。
    // 上板时一眼就能看出板子走到哪一步了。
    // ⚠️ 板上用户灯是**低电平点亮**（官方 LED 例程：assign led = ~led_reg），
    //    所以这里取反——逻辑 1 对应"灯亮"，不用在示波器前反着想。
    assign led = `FPGA_DEBUG_U102 ? {2'b11, sr_oe_n} : ~{walk_running, scan_on, output_on};

endmodule
