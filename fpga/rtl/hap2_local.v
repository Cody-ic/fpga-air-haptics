`timescale 1ns/1ps

// 本地按键：不接电脑时也能自己选图形、播放、停止。
//
// 板子上的按键是机械触点，按下去的那一瞬间电平会抖动好几毫秒（示波器上看是一串毛刺），
// 直接拿来做「按一下换一个图形」会一次按出七八个动作。所以每个键都过一遍消抖：
// **连续 DEB_MS 毫秒都是同一个电平，才认为键真的动了**；而且只在「按下」的那一刻
// 给一个单拍脉冲——按住不放不会一直重复触发，松开再按才算第二次。
//
// 三个键：
//   key_next  换下一个预设图形（POINT → LINE_X → LINE_Y → CIRCLE → SQUARE → TRIANGLE → ARROW → …）
//   key_play  播放／暂停（待机时开始，运行中按下暂停，暂停中按下继续）
//   key_stop  停止并回到起点
//
// 注意：sel 是「本次按下之后应该用的图形」，在 next_pulse 那一拍就已经是新值，
// 下游同一拍采样即可，不用再等一拍（否则会差一个图形）。
module hap2_local #(
    parameter integer CLK_HZ   = 50_000_000,
    parameter integer DEB_MS   = 20,        // 消抖时间（毫秒）
    parameter [2:0]   SEL_INIT = 3'd3       // 上电默认选中的图形（3 = CIRCLE）
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       key_next,     // 高有效（板上按键是低有效，在板级顶层取反）
    input  wire       key_play,
    input  wire       key_stop,
    output wire       next_pulse,   // 单拍：换下一个图形
    output wire       play_pulse,   // 单拍：播放／暂停
    output wire       stop_pulse,   // 单拍：停止
    output wire [2:0] sel           // 当前选中的图形
);

    localparam [23:0] DEB_CYC = (CLK_HZ / 1000) * DEB_MS;   // 24 位够到 335 ms @50 MHz

    // 三个键各一份：raw[i] 是同步进来的电平，stable[i] 是消抖之后的电平
    wire [2:0] raw = {key_stop, key_play, key_next};
    reg  [2:0] stable;
    reg  [2:0] pulse;                 // 单拍：某个键刚被确认按下
    reg  [23:0] cnt [0:2];            // 每个键各自的「已经连续多久是新电平」

    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            stable <= 3'b000;
            pulse  <= 3'b000;
            for (i = 0; i < 3; i = i + 1) cnt[i] <= 24'd0;
        end else begin
            pulse <= 3'b000;
            for (i = 0; i < 3; i = i + 1) begin
                if (raw[i] == stable[i]) begin
                    cnt[i] <= 24'd0;               // 和现状一样：计数清零
                end else if (cnt[i] == DEB_CYC) begin
                    stable[i] <= raw[i];           // 连续 DEB_MS 毫秒都是新电平 → 认定它变了
                    if (raw[i]) pulse[i] <= 1'b1;  // 只在「按下」那一刻出脉冲
                    cnt[i] <= 24'd0;
                end else begin
                    cnt[i] <= cnt[i] + 24'd1;
                end
            end
        end
    end

    assign next_pulse = pulse[0];
    assign play_pulse = pulse[1];
    assign stop_pulse = pulse[2];

    // ---- 图形选择：按一次下一个，7 个预设图形循环 ----
    reg  [2:0] sel_q;
    wire [2:0] sel_inc = (sel_q == 3'd6) ? 3'd0 : (sel_q + 3'd1);

    always @(posedge clk) begin
        if (!rst_n)        sel_q <= SEL_INIT;
        else if (pulse[0]) sel_q <= sel_inc;
    end

    assign sel = pulse[0] ? sel_inc : sel_q;

endmodule
