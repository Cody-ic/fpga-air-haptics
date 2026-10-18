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
    // 阵列规模：4×4 = 16 路。默认接 J23；旧 PMOD1+PMOD2 可由构建脚本选择。
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
    output wire [2:0] led,
    input wire adc_sdata,
    output wire adc_sclk,
    output wire adc_cs_n
);

    // 共用控制逻辑；只有此层的端口会分配到物理引脚。
    board_core #(
        .CLK_HZ(CLK_HZ),
        .BAUD(BAUD),
        .HW_ROWS(HW_ROWS),
        .HW_COLS(HW_COLS),
        .HW_PITCH_UM(HW_PITCH_UM),
        .PH_PIPE(PH_PIPE),
        .OUT_DEAD_CYC(OUT_DEAD_CYC),
        .POR_MS(POR_MS),
        .KEY_DEB_MS(KEY_DEB_MS),
        .LOC_RADIUS_UM(LOC_RADIUS_UM),
        .STATE_MS(STATE_MS),
        .WS_X_MIN_UM(WS_X_MIN_UM),
        .WS_X_MAX_UM(WS_X_MAX_UM),
        .WS_Y_MIN_UM(WS_Y_MIN_UM),
        .WS_Y_MAX_UM(WS_Y_MAX_UM),
        .WS_Z_MIN_UM(WS_Z_MIN_UM),
        .WS_Z_MAX_UM(WS_Z_MAX_UM)
    ) u_core (
        .clk(clk),
        .key_rst_n(key_rst_n),
        .key0_n(key0_n),
        .key1_n(key1_n),
        .uart_rx_pin(uart_rx_pin),
        .uart_tx_pin(uart_tx_pin),
        .array_pos(array_pos),
        .led(led),
        .adc_sdata(adc_sdata), .adc_sclk(adc_sclk), .adc_cs_n(adc_cs_n),
        .output_fault(1'b0),
        /* verilator lint_off PINCONNECTEMPTY */
        .sample_tick(), .drive_enable(), .board_rst_n()
        /* verilator lint_on PINCONNECTEMPTY */
    );
endmodule
