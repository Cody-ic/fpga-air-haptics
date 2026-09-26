`timescale 1ns/1ps

// 相位计算：把「焦点现在在哪」翻译成「每一路比基准提前多少相位」。
//
// 参考实现是 desktop_app/model.py 的 focus_phases()：
//
//     r_i    = |第 i 路阵元坐标 − 焦点|                  （米）
//     code_i = floor( frac(−r_i·f/c)·S + 0.5 ) mod S     c = 343 m/s
//
// 物理含义很直白：声波从第 i 路走到焦点要花 r_i/c 秒，所以这一路得先把相位
// 往回拧这么多，各路的声波才会在焦点上同相叠加。c 是声速，S 是 phase_steps，
// 坐标一律用 0.5 微米为单位（阵元坐标可能是半个间距，正好是整数）。
//
// 三个化简点：
//   1. S 只可能是 8/16/32/64/128/256，全是 2 的幂，所以 mod S 就是按位与。
//   2. 只要「小数部分」，整数部分多大都无所谓，不必去看。
//   3. 「除以声速」搬到配置阶段：算一个常数 A = f/c × 0.5e-6（每个坐标单位
//      对应多少个周期），量化成 FRAC 位小数。之后每路只剩一次乘法加一次移位。
//
// 每拍（10 µs = 500 个时钟）更新一遍全阵列。4×4 共 16 路、4 条并行流水线分
// 4 组，一组约 24 个时钟，总共约 100 个时钟，余量很大。8×8 是 64 路 16 组、
// 约 400 个时钟；想更宽松就把 PIPE 调成 8（要求 PIPE ≤ COLS）。
//
// 相位码存在一块双缓冲的表里：算的时候写另一半，整张表算完才翻指针，
// 所以读口任何时刻看到的都是一整份同一拍的表。
module hap2_phase #(
    parameter integer ROWS     = 4,      // 实际阵列行数
    parameter integer COLS     = 4,      // 实际阵列列数（要求 PIPE ≤ COLS）
    parameter integer PITCH_UM = 10000,  // 阵元间距（微米）
    parameter integer PIPE     = 4,      // 并行几条开方流水线
    parameter integer PT_BITS  = 21,     // 坐标位宽（0.5 微米单位）
    parameter integer FRAC     = 40      // 常数 A 的小数位数
) (
    input  wire                      clk,
    input  wire                      rst_n,
    // ---- 生效配置 ----
    /* verilator lint_off UNUSEDSIGNAL */
    // 载波与高度这两个字段的取值范围由字段解析层把关（≤80000 / ≤300000），
    // 所以高位用不上，取低位就够
    input  wire [31:0]               cfg_carrier_hz,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [31:0]               cfg_phase_steps,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [31:0]               cfg_z_um,       // 焦点平面高度（微米）
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire                      cfg_change,     // 单拍：配置换了，常数要重算
    // ---- 焦点（0.5 微米单位，来自走步器）----
    input  wire signed [PT_BITS-1:0] focus_x,
    input  wire signed [PT_BITS-1:0] focus_y,
    input  wire                      start,          // 单拍：这一拍更新一次相位
    // ---- 状态 ----
    output reg                       busy,
    output reg                       done,           // 单拍：新表算完并已切过去
    output reg  [8:0]                sweep_cnt,      // 这次算到第几路（看波形用）
    // ---- 相位表读口（给状态快照用）----
    output reg                       pub_sel,        // 对外的是哪一半
    // 快照在读的时候可以把「读哪一半」钉住：算到一半翻指针也不会读串
    input  wire                      rd_sel,
    input  wire [7:0]                rd_addr,
    output reg  [7:0]                rd_data,
    // ---- 已经发布出去的那份相位表是对着哪个焦点算的 ----
    // 状态帧里的焦点和相位表要来自同一次计算，这里把那次用的坐标暴露出来
    output reg  signed [PT_BITS-1:0] pub_fx,
    output reg  signed [PT_BITS-1:0] pub_fy,
    output reg  [31:0]               pub_fz_um
);

    localparam integer CNT    = ROWS * COLS;    // 总路数
    localparam integer STEP_W = 9;              // 路数计数（最多 256 路）
    // 相邻两列的坐标差：一个间距 = 2·PITCH_UM 个 0.5 µm 单位
    localparam integer DX_STEP = 2 * PITCH_UM;

    // ---------------- 配置阶段：把 A 算成定点常数 ----------------
    // A_fx = round(f × 2^39 ÷ (10^6 × 343))。乘 2^39 就是左移 39 位。
    localparam [55:0] A_ROUND = 56'd171_500_000;    // = 343_000_000 ÷ 2，用来四舍五入
    localparam [31:0] A_DENOM = 32'd343_000_000;

    reg  [55:0] a_numer;
    reg         a_start;
    wire        a_done, a_div0;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [55:0] a_quot;     // A 只用到低 32 位，高位恒为 0
    /* verilator lint_on UNUSEDSIGNAL */

    fix_div #(.NUM_BITS(56), .DEN_BITS(32)) u_aconst (
        .clk (clk), .rst_n (rst_n), .start (a_start),
        .numer (a_numer), .denom (A_DENOM),
        .done (a_done), .div0 (a_div0), .quot (a_quot)
    );

    reg [31:0] a_fx;        // 常数 A：26 位整数 + FRAC 位小数

    // ---------------- 每路要用的量 ----------------
    function [3:0] log2s;           // S 只可能是 8..256，查表最省事
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

    reg  [8:0]                steps_q;              // 这次用的 S（≤ 256）
    reg  [5:0]                shift_q;              // FRAC − log2(S)
    reg  [41:0]               fz2;                  // 焦点高度差平方，全阵列共用
    reg  signed [PT_BITS-1:0] fx_q, fy_q;
    reg  [31:0]               fz_um_q;              // 这次算的焦点高度（微米）
    reg  signed [PT_BITS-1:0] last_fx, last_fy;     // 上一次算过的焦点

    // ---------------- 并行流水线 ----------------
    reg  [STEP_W-1:0]         base_ch;              // 这一组的第一路
    reg  signed [PT_BITS-1:0] x0_q, y0_q;           // 组首阵元的坐标
    reg  [7:0]                c0, r0;               // 组首在第几列、第几行
    reg                       lane_start;
    reg  [7:0]                lane_wr;
    reg  [PIPE-1:0]           sq_done_v;
    reg  [7:0]                lane_code [0:PIPE-1];

    genvar gi;
    generate
        for (gi = 0; gi < PIPE; gi = gi + 1) begin : g_lane
            // 组内第 gi 路在第几列、第几行（PIPE ≤ COLS，所以最多跨一行）
            wire [7:0] c_raw  = c0 + gi;
            wire       wrap_l = (c_raw >= COLS);
            // 阵元坐标：从组首坐标加减常数推出来，不做乘法
            wire signed [PT_BITS:0] x_step_l = gi * DX_STEP;
            wire signed [PT_BITS:0] x_wrap_l = wrap_l ? (COLS * DX_STEP) : 0;
            wire signed [PT_BITS:0] y_step_l = wrap_l ? DX_STEP : 0;
            wire signed [PT_BITS:0] x_l      = x0_q + x_step_l - x_wrap_l;
            wire signed [PT_BITS:0] y_l      = y0_q + y_step_l;

            wire signed [PT_BITS:0]     dx_l  = x_l - fx_q;
            wire signed [PT_BITS:0]     dy_l  = y_l - fy_q;
            wire [2*PT_BITS+1:0]        dx2_l = dx_l * dx_l;
            wire [2*PT_BITS+1:0]        dy2_l = dy_l * dy_l;
            wire [41:0]                 d2_l  = dx2_l + dy2_l + fz2;

            reg         sq_start_l;
            wire        sq_done_l;
            wire [20:0] sq_res_l;

            fix_sqrt #(.IN_BITS(42), .OUT_BITS(21)) u_sqrt (
                .clk (clk), .rst_n (rst_n), .start (sq_start_l),
                .value (d2_l), .done (sq_done_l), .result (sq_res_l)
            );

            // r（21 位）× A_fx（32 位）：只要低 FRAC 位，也就是小数部分
            /* verilator lint_off UNUSEDSIGNAL */
            // 乘积只看小数部分（低 FRAC 位），高位算出来就丢掉
            wire [47:0] prod_l    = sq_res_l * a_fx;
            /* verilator lint_on UNUSEDSIGNAL */
            wire [47:0] frac_l    = prod_l[FRAC-1:0];
            /* verilator lint_off UNUSEDSIGNAL */
            // 小数部分乘 S 之后按四舍五入取整：等于「加半个刻度再右移」
            wire [47:0] rounded_l = frac_l + (48'd1 << (shift_q - 1));
            /* verilator lint_on UNUSEDSIGNAL */
            wire [8:0]  units_l   = rounded_l[FRAC-1:0] >> shift_q;
            // 取负号（声波晚到要提前补）再对 S 取模
            wire [8:0]  smask_l   = steps_q[8:0] - 9'd1;
            // 取模之后一定小于 S ≤ 256，而掩码的第 8 位恒为 0，所以只用低 8 位
            /* verilator lint_off UNUSEDSIGNAL */
            wire [8:0]  code_full_l = (steps_q[8:0] - units_l) & smask_l;
            /* verilator lint_on UNUSEDSIGNAL */
            wire [7:0]  code_l      = code_full_l[7:0];

            always @(posedge clk) begin
                if (!rst_n) begin
                    sq_start_l    <= 1'b0;
                    sq_done_v[gi] <= 1'b0;
                    lane_code[gi] <= 8'd0;
                end else begin
                    sq_start_l    <= 1'b0;
                    sq_done_v[gi] <= 1'b0;
                    if (lane_start) begin
                        sq_start_l <= 1'b1;      // 这一拍把 d2_l 交给开方单元
                    end else if (sq_done_l) begin
                        // 开方结果这一拍就绪，相位码顺带也算出来了
                        sq_done_v[gi] <= 1'b1;
                        lane_code[gi] <= code_l;
                    end
                end
            end
        end
    endgenerate

    wire all_sq_done = &sq_done_v;

    // ---------------- 相位表（双缓冲，按最大 256 路分配）----------------
    reg [7:0] ram [0:511];
    reg       wr_en;
    /* verilator lint_off UNUSEDSIGNAL */
    reg [8:0] wr_addr;      // 最高位用不到（路数 ≤ 256），拼下标时只取低 8 位
    /* verilator lint_on UNUSEDSIGNAL */
    reg [7:0] wr_data;

    always @(posedge clk) begin
        // 写的是**没在对外的**那一半，整张表算完才翻指针（见 P_END）
        // 下标宽度必须刚好 9 位（1 位选择 + 8 位偏移）。wr_addr 是 9 位，
        // 直接拼会多出一位、把选择位挤掉——那样就永远只写同一半了。
        if (wr_en) ram[{~pub_sel, wr_addr[7:0]}] <= wr_data;
        // 读哪一半由调用方钉住（rd_sel），这样快照复制到一半、这边翻指针也不会读串
        rd_data <= ram[{rd_sel, rd_addr}];
    end

    // ---------------- 状态机 ----------------
    localparam [2:0] P_IDLE  = 3'd0;
    localparam [2:0] P_ACALC = 3'd1;   // 配置换了：先重算常数 A
    localparam [2:0] P_GO    = 3'd2;   // 发一组开方
    localparam [2:0] P_SQ    = 3'd3;   // 等这一组的开方
    localparam [2:0] P_WR    = 3'd4;   // 把这一组的相位码写进表
    localparam [2:0] P_END   = 3'd5;

    reg [2:0] p_state;
    reg       force_sweep;      // 常数重算完之后要强制算一遍，不能因为"焦点没动"跳过
    /* verilator lint_off UNUSEDSIGNAL */
    /* verilator lint_on UNUSEDSIGNAL */

    // 焦点没动、配置也没换就不用重算。单点图形和暂停时一直是这种情况，不浪费电。
    wire need_sweep = force_sweep || (focus_x != last_fx) || (focus_y != last_fy);

    // 一次扫描的准备工作（从待机开始算、或者配置换了强制算，都走这里）
    task begin_sweep;
        begin
            busy      <= 1'b1;
            fx_q      <= focus_x;
            fy_q      <= focus_y;
            last_fx   <= focus_x;
            last_fy   <= focus_y;
            steps_q   <= cfg_phase_steps[8:0];
            shift_q   <= FRAC[5:0] - {2'b0, log2s(cfg_phase_steps)};
            // 阵元在 z=0，焦点在 +z：高度差就是焦点高度（微米 × 2 → 0.5 µm 单位）
            fz2       <= (cfg_z_um[19:0] * 2) * (cfg_z_um[19:0] * 2);
            fz_um_q   <= cfg_z_um;
            base_ch   <= {STEP_W{1'b0}};
            sweep_cnt <= 9'd0;
            c0        <= 8'd0;
            r0        <= 8'd0;
            x0_q      <= (0 - (COLS-1)) * PITCH_UM;      // 第一路 = 第一列、第一行
            y0_q      <= (0 - (ROWS-1)) * PITCH_UM;
            force_sweep <= 1'b0;
        end
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin
            p_state     <= P_IDLE;
            busy        <= 1'b0;
            done        <= 1'b0;
            pub_sel     <= 1'b0;
            base_ch     <= {STEP_W{1'b0}};
            sweep_cnt   <= 9'd0;
            lane_wr     <= 8'd0;
            a_start     <= 1'b0;
            a_fx        <= 32'd0;
            a_numer     <= 56'd0;
            steps_q     <= 9'd64;
            shift_q     <= FRAC[5:0] - 6'd6;
            fz2         <= 42'd0;
            fz_um_q     <= 32'd150000;
            pub_fx      <= {PT_BITS{1'b0}};
            pub_fy      <= {PT_BITS{1'b0}};
            pub_fz_um   <= 32'd150000;
            fx_q        <= {PT_BITS{1'b0}};
            fy_q        <= {PT_BITS{1'b0}};
            last_fx     <= {PT_BITS{1'b0}};
            last_fy     <= {PT_BITS{1'b0}};
            lane_start  <= 1'b0;
            wr_en       <= 1'b0;
            wr_addr     <= 9'd0;
            wr_data     <= 8'd0;
            x0_q        <= {PT_BITS{1'b0}};
            y0_q        <= {PT_BITS{1'b0}};
            c0          <= 8'd0;
            r0          <= 8'd0;
            force_sweep <= 1'b0;
        end else begin
            done       <= 1'b0;
            lane_start <= 1'b0;
            a_start    <= 1'b0;
            wr_en      <= 1'b0;

            case (p_state)
                P_IDLE: begin
                    busy <= 1'b0;
                    if (cfg_change) begin
                        // 载波换了：先把常数 A 重算出来（56 拍，配置阶段不着急）
                        busy    <= 1'b1;
                        a_numer <= ({{38{1'b0}}, cfg_carrier_hz[17:0]} << 39) + A_ROUND;
                        a_start <= 1'b1;
                        p_state <= P_ACALC;
                    end else if (start && need_sweep) begin
                        begin_sweep;
                        p_state <= P_GO;
                    end
                end

                P_ACALC: begin
                    if (a_done) begin
                        a_fx        <= a_div0 ? 32'd0 : a_quot[31:0];
                        // 常数换了，相位表必须重算一遍（否则回传的还是旧载波的码）
                        force_sweep <= 1'b1;
                        busy        <= 1'b0;
                        p_state     <= P_IDLE;
                    end
                end

                // 发一组：lane_start 拉高一拍，各路开方单元锁存 d2
                P_GO: begin
                    lane_start <= 1'b1;
                    p_state    <= P_SQ;
                end

                P_SQ: begin
                    if (all_sq_done) begin
                        lane_wr <= 8'd0;
                        p_state <= P_WR;
                    end
                end

                // 一路一拍写进表里（PIPE 拍）
                P_WR: begin
                    if (base_ch + lane_wr < CNT) begin
                        wr_en   <= 1'b1;
                        wr_addr <= base_ch + lane_wr;
                        wr_data <= lane_code[lane_wr];
                    end
                    sweep_cnt <= base_ch + lane_wr + 1'b1;
                    if (lane_wr + 1'b1 >= PIPE[7:0]) begin
                        // 这一组写完了：算下一组的列号／行号／组首坐标
                        base_ch <= base_ch + PIPE[STEP_W-1:0];
                        if (base_ch + PIPE[STEP_W-1:0] >= CNT) begin
                            p_state <= P_END;
                        end else begin
                            // 组首往后挪 PIPE 列；越过行尾就换行（列号回绕、y 下移一行），
                            // x 也要跟着减掉一整行的宽度，保证 x0 永远对应当前的列号
                            if (c0 + PIPE[7:0] >= COLS) begin
                                c0   <= c0 + PIPE[7:0] - COLS;
                                r0   <= r0 + 8'd1;
                                y0_q <= y0_q + DX_STEP;
                            end else begin
                                c0   <= c0 + PIPE[7:0];
                            end
                            if (c0 + PIPE[7:0] >= COLS)
                                x0_q <= x0_q + PIPE[PT_BITS-1:0] * DX_STEP
                                              - COLS * DX_STEP;
                            else
                                x0_q <= x0_q + PIPE[PT_BITS-1:0] * DX_STEP;
                            p_state <= P_GO;
                        end
                    end else begin
                        lane_wr <= lane_wr + 1'b1;
                    end
                end

                P_END: begin
                    pub_sel <= ~pub_sel;      // 整张表算完了，切过去
                    // 顺手记住这张表是对着哪个焦点算的（状态帧要用同一份数据）
                    pub_fx    <= fx_q;
                    pub_fy    <= fy_q;
                    pub_fz_um <= fz_um_q;
                    done    <= 1'b1;
                    busy    <= 1'b0;
                    p_state <= P_IDLE;
                end

                default: p_state <= P_IDLE;
            endcase
        end
    end

endmodule
