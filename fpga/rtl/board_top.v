`timescale 1ns/1ps

// 板级顶层：把「芯片里该有的逻辑」接到「板子上真实的引脚和器件」上。
// 逻辑功能都在 hap2_top 里；这一层只做几件**必须**在真板子上做的事：
//
//   1. 上电复位（por_reset）：明确配置初值，保持复位几毫秒后同步释放。
//   2. 复位标识 boot：协议要求「每次复位都变、同一会话里不变」。
//      用一个**不受复位影响**的自由计数器当熵源，复位放开那一拍锁存。
//   3. 把板上按键（低有效、需要内部上拉）取反成逻辑里的高有效，并交给 hap2_local 消抖。
//   4. 把 16 路输出、串口、状态灯引到引脚（引脚号在 fpga/board/tang_mega_138k_pro.cst 里绑）。
//
// 目标板：Sipeed Tang MEGA 138K Pro（GW5AST-LV138FPG676A，676 球 FCPBG676A 封装）。
// 板上 50 MHz 在 P16，和固件默认参数一致，所以**不需要锁相环**。
module board_top #(
    parameter integer CLK_HZ        = 50_000_000,   // 板上晶振频率（138K Pro 的 P16 = 50 MHz）
    parameter integer BAUD          = 115200,
    // 阵列规模：4×4 = 16 路，分在 PMOD1（8 路）+ PMOD2（8 路）两个连接器上。
    // 改成 2 就退回 8 路调试（同时把 .cst 里 array_pos[8..15] 那几行注释掉）。
    parameter integer HW_ROWS       = 4,
    parameter integer HW_COLS       = 4,
    parameter integer HW_PITCH_UM   = 11000,        // 当前 4×4 R5 发射板：11 mm 阵元间距
    parameter integer PH_PIPE       = 4,            // 相位并行流水线条数（8×8 建议 8）
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
    input  wire key0_n,       // 用户按键 0（F15）→ 换下一个图形
    input  wire key1_n,       // 用户按键 1（G15）→ 播放／暂停
    input  wire uart_rx_pin,  // 电脑 → 板子（N16，接板载调试器 BL616 的 TX）
    output wire uart_tx_pin,  // 板子 → 电脑（P15，接板载调试器 BL616 的 RX）
    // 送给驱动电路的方波（单端驱动：每路一根线，多数半桥驱动芯片自己管死区）。
    // ⚠️ 半桥要互补输入时：把下面这行改成
    //     output wire [HW_ROWS*HW_COLS-1:0] array_pos, array_neg
    //   再在 .cst 里给 array_neg 也分配引脚（现在故意不引出：顶层端口没约束时，
    //   工具会把它们自动摆到随便哪个脚上，可能撞到 DDR/HDMI 那些不能乱动的脚）。
    output wire [HW_ROWS*HW_COLS-1:0] array_pos,
    // 状态灯：输出开 / 正在扫描 / 走步器在跑（干净脚不够，只留最有用的三颗；
    // 本地模式、串口状态在状态帧里都看得到）
    output wire [2:0] led
);

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
    /* verilator lint_off UNUSEDSIGNAL */
    wire signed [20:0] focus_x, focus_y;
    wire [5:0]  stroke_index;
    wire        beat_pulse;
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_top #(
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
        .uart_tx_pin (uart_tx_pin),
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

    // ---------------- 5. 状态灯 ----------------
    // 3 颗灯分别是：正在驱动换能器、正在扫描、走步器在跑。
    // 上板时一眼就能看出板子走到哪一步了。
    // ⚠️ 板上用户灯是**低电平点亮**（官方 LED 例程：assign led = ~led_reg），
    //    所以这里取反——逻辑 1 对应"灯亮"，不用在示波器前反着想。
    assign led = ~{walk_running, scan_on, output_on};

endmodule
