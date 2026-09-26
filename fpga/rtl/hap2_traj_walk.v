`timescale 1ns/1ps

// 轨迹走步器：把节拍表「放」出来——每 10 µs 走一步，输出焦点坐标。
//
// 这是**运行阶段**的模块，一秒要走十万步，所以里面**只有加法和减法**：
// 乘除和开方都已经在配置阶段由 hap2_traj_plan 算进表里了。
//
// 位置用定点小数累加：低 FRAC 位是小数部分，高 PT_BITS 位（含符号）是整数部分，
// 单位 0.5 µm。每拍在累加器上加一个步进，对外只取整数部分：
//   位置 = 起点 + 步进 × 已经走过的拍数
// 步进是四舍五入过的，所以一整段走完的累计误差不到一个 0.5 µm 单位；
// 而且每一行都从表里重新取步进，误差不会一行一行滚雪球。
//
// 输出：
//   focus_x / focus_y  当前焦点（0.5 µm 单位，相对阵列中心）
//   scan_on            这一刻是在扫描（1）还是在抬笔跳转（0）
//   stroke_index       这一刻属于第几笔
//   beat_pulse         单拍脉冲，标记「新的一个节拍开始了，焦点就是下面的坐标」
//
// 表是同步读的，而且要给足时间：地址发出去之后**第二拍**数据才有效
// （pf 从 1 数到 2 就是在等这一拍）。为了不浪费节拍，走步器在**这一行还在走**
// 的时候就把下一行取好（预取），所以换行那一拍不需要等待，
// 一圈的拍数一拍不多、一拍不少。
module hap2_traj_walk #(
    parameter integer PT_BITS  = 21,     // 坐标位宽
    parameter integer FRAC     = 16,     // 步进的小数位
    parameter integer TICK_CYC = 500     // 一拍多少个时钟（50 MHz ÷ 100 kHz = 500）
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire                      start,     // 单拍脉冲：从轨迹起点重新开始
    input  wire                      hold,      // 电平：暂停（位置冻结）
    input  wire                      stop,      // 单拍脉冲：停下并回到起点
    input  wire signed [PT_BITS-1:0] start_x,   // 轨迹起点（来自节拍表模块）
    input  wire signed [PT_BITS-1:0] start_y,
    input  wire [8:0]                move_count,
    // ---- 节拍表读口 ----
    output reg  [8:0]                rd_mv,
    input  wire [23:0]               mv_beats,
    input  wire signed [31:0]        mv_step_x,
    input  wire signed [31:0]        mv_step_y,
    input  wire                      mv_scan,
    input  wire [5:0]                mv_stroke,
    // ---- 输出 ----
    output wire signed [PT_BITS-1:0] focus_x,
    output wire signed [PT_BITS-1:0] focus_y,
    output reg                       scan_on,
    output reg  [5:0]                stroke_index,
    output reg                       beat_pulse,
    output reg                       running
);

    localparam integer ACC_W = PT_BITS + FRAC;   // 定点累加器位宽（21 + 16 = 37）

    localparam [1:0] W_IDLE = 2'd0;
    localparam [1:0] W_LOAD = 2'd1;   // 装第一行
    localparam [1:0] W_RUN  = 2'd2;

    reg [1:0]  wstate;
    reg [15:0] tick;                  // 一拍之内的时钟计数
    reg [23:0] beats_left;            // 这一行还剩几拍
    reg signed [31:0] step_x, step_y; // 这一行每拍走多少
    reg signed [ACC_W-1:0] acc_x, acc_y;

    // 预取：下一行的参数。pf 的含义
    //   0 = 没事干；1 = 地址刚发出去，等一拍；2 = 数据在位，可以收下来
    reg [23:0] n_beats;
    reg signed [31:0] n_step_x, n_step_y;
    reg        n_scan;
    reg [5:0]  n_stroke;
    reg [1:0]  pf;

    // 下一行的行号（到末尾绕回第 0 行）
    wire [8:0] rd_next = (rd_mv + 9'd1 == move_count) ? 9'd0 : rd_mv + 9'd1;

    // 走完这一拍之后的位置（对外的坐标直接由它取整数部分，和 beat_pulse 同一拍生效）
    wire signed [ACC_W-1:0] acc_x_run = acc_x + step_x;
    wire signed [ACC_W-1:0] acc_y_run = acc_y + step_y;

    assign focus_x = acc_x[ACC_W-1:FRAC];
    assign focus_y = acc_y[ACC_W-1:FRAC];

    always @(posedge clk) begin
        if (!rst_n) begin
            wstate       <= W_IDLE;
            tick         <= 16'd0;
            beats_left   <= 24'd0;
            step_x       <= 32'sd0;
            step_y       <= 32'sd0;
            acc_x        <= {(ACC_W){1'b0}};
            acc_y        <= {(ACC_W){1'b0}};
            n_beats      <= 24'd0;
            n_step_x     <= 32'sd0;
            n_step_y     <= 32'sd0;
            n_scan       <= 1'b0;
            n_stroke     <= 6'd0;
            pf           <= 2'd0;
            rd_mv        <= 9'd0;
            scan_on      <= 1'b0;
            stroke_index <= 6'd0;
            beat_pulse   <= 1'b0;
            running      <= 1'b0;
        end else if (stop) begin
            // 停下：位置回到起点，输出关掉，等下一次 start
            wstate       <= W_IDLE;
            tick         <= 16'd0;
            beats_left   <= 24'd0;
            step_x       <= 32'sd0;
            step_y       <= 32'sd0;
            acc_x        <= {start_x, {FRAC{1'b0}}};
            acc_y        <= {start_y, {FRAC{1'b0}}};
            rd_mv        <= 9'd0;
            pf           <= 2'd0;
            scan_on      <= 1'b0;
            stroke_index <= 6'd0;
            beat_pulse   <= 1'b0;
            running      <= 1'b0;
        end else begin
            beat_pulse <= 1'b0;

            case (wstate)
                W_IDLE: begin
                    running <= 1'b0;
                    if (start) begin
                        acc_x  <= {start_x, {FRAC{1'b0}}};   // 轨迹起点
                        acc_y  <= {start_y, {FRAC{1'b0}}};
                        rd_mv  <= 9'd0;                      // 取第 0 行
                        pf     <= 2'd1;
                        tick   <= 16'd0;
                        wstate <= W_LOAD;
                    end
                end

                // 等第 0 行的数据到位，装上它，再预取第 1 行
                W_LOAD: begin
                    if (pf == 2'd1) begin
                        pf <= 2'd2;
                    end else if (pf == 2'd2) begin
                        step_x       <= mv_step_x;
                        step_y       <= mv_step_y;
                        beats_left   <= mv_beats;
                        scan_on      <= mv_scan;
                        stroke_index <= mv_stroke;
                        rd_mv        <= rd_next;
                        pf           <= 2'd1;
                        tick         <= 16'd0;
                        running      <= 1'b1;
                        wstate       <= W_RUN;
                    end
                end

                default: begin   // W_RUN
                    // 预取推进：等一拍 -> 收下下一行的参数
                    if (pf == 2'd1) begin
                        pf <= 2'd2;
                    end else if (pf == 2'd2) begin
                        n_step_x <= mv_step_x;
                        n_step_y <= mv_step_y;
                        n_beats  <= mv_beats;
                        n_scan   <= mv_scan;
                        n_stroke <= mv_stroke;
                        pf       <= 2'd0;
                    end
                    // 节拍
                    if (!hold) begin
                        if (tick == TICK_CYC - 1) begin
                            tick       <= 16'd0;
                            beat_pulse <= 1'b1;
                            acc_x      <= acc_x_run;         // 走一步
                            acc_y      <= acc_y_run;
                            beats_left <= beats_left - 24'd1;
                            if (beats_left == 24'd1) begin
                                // 这一行走完了：整行换掉（参数早就预取好了，不用等）
                                step_x       <= n_step_x;
                                step_y       <= n_step_y;
                                beats_left   <= n_beats;
                                scan_on      <= n_scan;
                                stroke_index <= n_stroke;
                                rd_mv        <= rd_next;     // 接着预取下下行的后面一行
                                pf           <= 2'd1;
                            end
                        end else begin
                            tick <= tick + 16'd1;
                        end
                    end
                end
            endcase
        end
    end

endmodule
