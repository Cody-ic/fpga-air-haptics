`timescale 1ns/1ps

// 输出级的仿真测试台：真的去数引脚上的电平，量出来对不对。
//
// 量两件事：
//   1. 载波频率对不对：数 200 个周期一共花了多少时钟，和 50 MHz ÷ 40 kHz = 1250 拍比。
//   2. 每一路的相位偏移对不对：以第 0 路的上升沿为基准，数每一路自己的上升沿离它多少拍，
//      再和「参考实现给出的相位码之差 × 一圈 1250 拍 ÷ 档数」比。
// 期望相位码由 gen_vectors.py 调 focus_phases() 生成（和 tb_phase 用的是同一份向量）。
//
// 顺带核对门控：enable=0 时所有输出必须是低电平，且一个跳变都不许有。
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_out;

    localparam integer CLK_NS = 20;        // 50 MHz
    localparam integer ROWS   = 4;
    localparam integer COLS   = 4;
    localparam integer CH     = ROWS * COLS;
    localparam integer ACC_W  = 32;       // 32 位累加器：40 kHz 的频率误差小到可以忽略

    reg clk, rst_n;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;

    // 向量
    reg [31:0] plan  [0:10*3-1];
    reg [31:0] focus [0:63];
    reg [7:0]  codes [0:511];

    reg  [31:0] cfg_carrier, cfg_steps, cfg_z;
    reg  [31:0] cfg_level, cfg_mod_hz;     // HAP3 的「驱动等级」和「调制频率」
    reg         cfg_change;
    reg  signed [20:0] fx_21, fy_21;
    reg         ph_start;
    reg         enable;
    /* verilator lint_off UNUSEDSIGNAL */
    wire        ph_pub_sel;
    /* verilator lint_on UNUSEDSIGNAL */
    wire        ph_busy;
    wire        ph_done;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [7:0]  ph_rd_data;               // 第一个读口本测试用不到（那是状态快照那条路）
    /* verilator lint_on UNUSEDSIGNAL */
    wire [7:0]  ph_rd2_addr, ph_rd2_data;
    /* verilator lint_off UNUSEDSIGNAL */
    wire signed [20:0] ph_pub_fx, ph_pub_fy;
    wire [31:0] ph_pub_fz_um;
    /* verilator lint_on UNUSEDSIGNAL */
    wire [CH-1:0] out_pos, out_neg;
    wire [CH-1:0] dt_pos, dt_neg;      // 第二路实例：带死区的互补输出
    /* verilator lint_off UNUSEDSIGNAL */
    wire [8:0] cnt_dummy;
    /* verilator lint_on UNUSEDSIGNAL */

    // 相位引擎 + 输出级，接线方式和顶层一致
    hap2_phase #(.ROWS(ROWS), .COLS(COLS), .PITCH_UM(10000), .PIPE(4)) u_phase (
        .clk(clk), .rst_n(rst_n),
        .cfg_carrier_hz(cfg_carrier), .cfg_phase_steps(cfg_steps),
        .cfg_z_um(cfg_z), .cfg_change(cfg_change),
        .focus_x(fx_21), .focus_y(fy_21), .start(ph_start),
        .busy(ph_busy), .done(ph_done), .sweep_cnt(cnt_dummy), .pub_sel(ph_pub_sel),
        .rd_sel(1'b0), .rd_addr(8'd0), .rd_data(ph_rd_data),
        .rd2_addr(ph_rd2_addr), .rd2_data(ph_rd2_data),
        .pub_fx(ph_pub_fx), .pub_fy(ph_pub_fy), .pub_fz_um(ph_pub_fz_um)
    );

    hap2_out #(
        .ROWS(ROWS), .COLS(COLS), .CLK_HZ(50_000_000), .ACC_BITS(ACC_W), .DEAD_CYC(0)
    ) u_out (
        .clk(clk), .rst_n(rst_n),
        .cfg_carrier_hz(cfg_carrier), .cfg_phase_steps(cfg_steps),
        .cfg_change(cfg_change),
        .cfg_level(cfg_level), .cfg_mod_hz(cfg_mod_hz),
        .enable(enable),
        .tbl_new(ph_done),
        .tbl_addr(ph_rd2_addr), .tbl_data(ph_rd2_data),
        .out_pos(out_pos), .out_neg(out_neg)
    );

    // 同一个相位表再喂一路「带死区」的输出级，专门验死区：
    // 上下管任何时刻都不许同时导通，但换向时必须留出空档。
    hap2_out #(
        .ROWS(ROWS), .COLS(COLS), .CLK_HZ(50_000_000), .ACC_BITS(ACC_W), .DEAD_CYC(4)
    ) u_out_dt (
        .clk(clk), .rst_n(rst_n),
        .cfg_carrier_hz(cfg_carrier), .cfg_phase_steps(cfg_steps),
        .cfg_change(cfg_change),
        .cfg_level(cfg_level), .cfg_mod_hz(cfg_mod_hz),
        .enable(enable),
        .tbl_new(ph_done),
        .tbl_addr(ph_rd2_addr), .tbl_data(ph_rd2_data),
        .out_pos(dt_pos), .out_neg(dt_neg)
    );

    /* verilator lint_off BLKSEQ */
    // 注意：输出寄存器比 enable 晚一拍，统计时要拿「当时那个 enable」
    reg     enable_q;           // 初值在 initial 里给
    integer dt_both_high;       // 上下管同时为高的拍数：必须恒为 0
    integer dt_gap_run;         // 当前连续「两个都为低」的拍数
    integer dt_gap_max;         // 观测到的最大空档
    integer dt0_bad;            // DEAD_CYC=0 那一路不互补的拍数：也必须恒为 0
    always @(posedge clk) begin
        enable_q <= enable;
        if (enable_q) begin
            if ((dt_pos & dt_neg) != 0) dt_both_high = dt_both_high + 1;
            if (dt_pos[0] == 1'b0 && dt_neg[0] == 1'b0) begin
                dt_gap_run = dt_gap_run + 1;
                if (dt_gap_run > dt_gap_max) dt_gap_max = dt_gap_run;
            end else begin
                dt_gap_run = 0;
            end
            if (out_pos[0] == out_neg[0]) dt0_bad = dt0_bad + 1;
        end else begin
            dt_gap_run = 0;
        end
    end
    /* verilator lint_on BLKSEQ */

    integer errors = 0;
    integer checks = 0;
    /* verilator lint_off UNUSEDSIGNAL */
    integer case_i;                 // 只在失败信息里露个脸
    /* verilator lint_on UNUSEDSIGNAL */
    integer ch, timeout, k;
    integer rows, cols, steps, f_off, c_off, count;
    integer edge_at [0:CH-1];
    integer seen_edge;
    reg [31:0] hx, hy;

    // 等第 0 路出现一次上升沿；等不到就把 found_edge 置 0（不挂住仿真）
    integer guard;
    reg     found_edge;
    /* verilator lint_off BLKSEQ */
    integer edge_cnt0;              // 由事件块维护，初值在 initial 里给
    always @(posedge out_pos[0]) edge_cnt0 = edge_cnt0 + 1;

    // 每一路的上升沿时刻（由事件驱动记录，比逐拍采样可靠）
    integer edge_time [0:CH-1];
    genvar gc;
    generate
        for (gc = 0; gc < CH; gc = gc + 1) begin : g_edge
            always @(posedge out_pos[gc]) edge_time[gc] = $time;
        end
    endgenerate
    /* verilator lint_on BLKSEQ */
    /* verilator lint_on UNUSEDSIGNAL */
    task wait_rising0;
        integer c0;
        begin
            c0    = edge_cnt0;
            guard = 0;
            while (edge_cnt0 == c0 && guard < 200000) begin
                @(negedge clk);
                guard    = guard + 1;
            end
            found_edge = (edge_cnt0 != c0);
        end
    endtask

    // 量第 0 路在**一个载波周期里**有多少拍是高电平（= 占空比 × 1250）
    integer high_ticks, duty_total;
    task measure_duty;
        integer n;
        begin
            wait_rising0;
            high_ticks = 0; duty_total = 0;
            if (found_edge) begin
                for (n = 0; n < 1250; n = n + 1) begin
                    @(negedge clk);
                    duty_total = duty_total + 1;
                    if (out_pos[0]) high_ticks = high_ticks + 1;
                end
            end
        end
    endtask

    // 在 win_cycles 拍里数两件事：输出跳变次数、以及「静音」出现了几次
    // （连续 2000 拍不动 = 40 µs 没跳变，正常 50% 方波最长只会有半个周期约 625 拍，
    //   所以这一定是调制包络把输出关掉了）
    integer env_edges, env_silence, idle_run;
    reg     last_pos;
    task measure_envelope;
        input integer win_cycles;
        integer n;
        begin
            env_edges = 0; env_silence = 0; idle_run = 0;
            last_pos = out_pos[0];
            for (n = 0; n < win_cycles; n = n + 1) begin
                @(negedge clk);
                if (out_pos[0] !== last_pos) begin
                    env_edges = env_edges + 1;
                    idle_run  = 0;
                end else begin
                    idle_run = idle_run + 1;
                    if (idle_run == 2000) env_silence = env_silence + 1;
                end
                last_pos = out_pos[0];
            end
        end
    endtask

    // 量第 0 路的周期：从一次上升沿开始，等 200 个周期，看花了多少时间
    integer period_ticks;
    task measure_period;
        integer c0, n;
        time    t_start, t_stop;
        begin
            wait_rising0;
            period_ticks = 0;
            if (found_edge) begin
                t_start = $time;
                c0      = edge_cnt0;
                n = 0;
                while (edge_cnt0 - c0 < 200 && n < 20_000_000) begin
                    @(negedge clk);
                    n = n + 1;
                end
                t_stop       = $time;
                // 时间单位是 ns，时钟 20 ns 一拍
                period_ticks = (t_stop - t_start) / (CLK_NS * 200);
            end
        end
    endtask

    // 以第 0 路的上升沿为准，量每一路下一个上升沿离它多少拍
    task measure_offsets;
        integer t, i;
        time    t0;
        reg     mark [0:CH-1];
        begin
            wait_rising0;
            for (i = 0; i < CH; i = i + 1) edge_at[i] = -1;
            if (found_edge) begin
                t0 = $time;
                for (i = 0; i < CH; i = i + 1) mark[i] = 1'b0;
                for (t = 0; t <= 3*1250; t = t + 1) begin
                    @(negedge clk);
                    for (i = 0; i < CH; i = i + 1)
                        if (!mark[i] && edge_time[i] > t0) begin
                            mark[i]    = 1'b1;
                            edge_at[i] = (edge_time[i] - t0) / CLK_NS;   // 换算成拍数
                        end
                end
            end
        end
    endtask

    initial begin
        $readmemh("tb/vectors/phase_plan.mem",  plan);
        $readmemh("tb/vectors/phase_focus.mem", focus);
        $readmemh("tb/vectors/phase_codes.mem", codes);

        rst_n       = 1'b0;
        cfg_carrier = 32'd40000;
        cfg_steps   = 32'd64;
        cfg_z       = 32'd150000;
        cfg_level   = 32'd100;     // 满驱动（= 50% 占空比，和以前的波形一模一样）
        cfg_mod_hz  = 32'd0;       // 不调制
        cfg_change  = 1'b0;
        fx_21       = 21'sd0;
        fy_21       = 21'sd0;
        ph_start    = 1'b0;
        enable      = 1'b0;
        edge_cnt0   = 0;
        dt_both_high = 0; dt_gap_run = 0; dt_gap_max = 0; dt0_bad = 0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("输出级仿真：相位码 → 引脚上的方波");
        $display("=========================================");

        // ---- 先用第一个用例（真板子的 4×4）----
        case_i  = 0;
        rows    = plan[0];  cols = plan[1];  steps = plan[4];
        cfg_carrier = plan[5];
        f_off   = plan[8];
        c_off   = plan[9];
        count   = rows * cols;
        cfg_steps = steps;
        cfg_z     = plan[6];

        // ---- 门控：enable=0 时输出必须一直是低电平 ----
        enable = 1'b0;
        checks = checks + 1;
        timeout = 0;
        seen_edge = 0;
        for (k = 0; k < 3000; k = k + 1) begin
            @(negedge clk);
            if (out_pos !== {CH{1'b0}} || out_neg !== {CH{1'b0}}) seen_edge = 1;
        end
        if (seen_edge) begin
            $display("[输出-门控] enable=0 时输出居然还在动  **失败**");
            errors = errors + 1;
        end else begin
            $display("  门控：enable=0 期间 16 路都保持低电平");
        end

        // 配置（载波换了会重算增量）+ 第一帧焦点
        cfg_change = 1'b1;
        @(negedge clk);
        cfg_change = 1'b0;
        // 载波换了要先等常数重算完（56 拍），不然接下来的 start 会被漏掉
        repeat (4) @(negedge clk);
        timeout = 0;
        while (ph_busy && timeout < 20000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        repeat (5) @(negedge clk);
        hx = focus[f_off + 0];
        hy = focus[f_off + 1];
        fx_21 = $signed(hx) * 2;
        fy_21 = $signed(hy) * 2;
        @(negedge clk);
        ph_start = 1'b1;
        @(negedge clk);
        ph_start = 1'b0;
        timeout = 0;
        while (!ph_done && timeout < 20000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        repeat (100) @(negedge clk);     // 等输出级把相位码读进来
        enable = 1'b1;

        // ---- 量载波周期 ----
        measure_period;
        checks = checks + 1;
        // 50 MHz ÷ 40 kHz = 1250 拍一个周期，允许 ±1 拍
        if (period_ticks < 1249 || period_ticks > 1251) begin
            $display("[输出-频率] 每个周期 %0d 拍，期望 1250±1  **失败**", period_ticks);
            errors = errors + 1;
        end else begin
            $display("  载波：每周期 %0d 拍（50 MHz ÷ 40 kHz = 1250）", period_ticks);
        end

        // ---- 量每一路的相位偏移 ----
        measure_offsets;
        checks = checks + 1;
        for (ch = 0; ch < count; ch = ch + 1) begin
            // 期望：以第 0 路为基准，码之差（按档数取模）× 每档拍数
            k = ((codes[c_off + 0*count + ch] - codes[c_off + 0*count + 0]) % steps + steps) % steps;
            // 码大的意思是「提前」，所以它的下一个上升沿落在基准沿之后
            // 「一个周期减去提前量」的位置上
            timeout = period_ticks - (k * period_ticks) / steps;
            checks = checks + 1;
            if (edge_at[ch] < 0) begin
                $display("[输出-相位] 第 %0d 路没有出现上升沿  **失败**", ch);
                errors = errors + 1;
            end else if (edge_at[ch] < timeout - 3 || edge_at[ch] > timeout + 3) begin
                $display("[输出-相位] 第 %0d 路上升沿在 %0d 拍，期望 %0d 拍（±3）  **失败**",
                         ch, edge_at[ch], timeout);
                errors = errors + 1;
            end
        end
        $display("  相位：以第 0 路的上升沿为基准，逐路量了上升沿位置（共 %0d 路）", count);

        // ---- 死区：上下管不许同时导通，但换向必须留空档 ----
        checks = checks + 1;
        if (dt_both_high != 0) begin
            $display("[输出-死区] 上下两个输出同时为高 %0d 拍（半桥这样会烧管子）  **失败**",
                     dt_both_high);
            errors = errors + 1;
        end else begin
            $display("  死区：DEAD_CYC=4 那一路，上下管同时为高 0 拍");
        end
        checks = checks + 1;
        if (dt_gap_max < 3 || dt_gap_max > 6) begin
            $display("[输出-死区] 换向时的空档是 %0d 拍，期望 4 拍左右  **失败**", dt_gap_max);
            errors = errors + 1;
        end else begin
            $display("  死区：每次换向都有 %0d 拍「两个都不导通」", dt_gap_max);
        end
        checks = checks + 1;
        if (dt0_bad != 0) begin
            $display("[输出-死区] DEAD_CYC=0 那一路不是严格互补：%0d 拍  **失败**", dt0_bad);
            errors = errors + 1;
        end

        // ---- 暂停（enable=0）：输出必须立刻关掉 ----
        @(negedge clk);
        enable = 1'b0;
        repeat (5) @(negedge clk);
        checks = checks + 1;
        if (out_pos !== {CH{1'b0}} || out_neg !== {CH{1'b0}}) begin
            $display("[输出-关断] enable 拉低之后输出没关  **失败**");
            errors = errors + 1;
        end else begin
            $display("  关断：enable 拉低后 5 拍内所有输出都归零");
        end

        // ---- 驱动等级 level：用占空比调幅度 ----
        // level=100 时应当还是原来那个 50% 方波（1250 拍里高 625 拍）；
        // level=50 时占空比减半（高约 312 拍）。这条把「等级→占空比」这条路钉住。
        enable    = 1'b1;
        cfg_level = 32'd100;
        repeat (20) @(negedge clk);
        measure_duty;
        checks = checks + 1;
        if (high_ticks < 618 || high_ticks > 632) begin
            $display("[输出-等级] level=100 时一个周期里高 %0d 拍，期望 625±7（50%% 占空比）  **失败**",
                     high_ticks);
            errors = errors + 1;
        end else begin
            $display("  等级：level=100 → 一个周期（%0d 拍）里高 %0d 拍，占空比约 50%%",
                     duty_total, high_ticks);
        end

        cfg_level = 32'd50;
        repeat (20) @(negedge clk);
        measure_duty;
        checks = checks + 1;
        if (high_ticks < 305 || high_ticks > 320) begin
            $display("[输出-等级] level=50 时一个周期里高 %0d 拍，期望 312±8  **失败**", high_ticks);
            errors = errors + 1;
        end else begin
            $display("  等级：level=50 → 高 %0d 拍，占空比约 25%%（幅度随等级单调变化）", high_ticks);
        end

        // level=0：即使 enable 还是 1，也不许有任何输出（“等级 0 = 不驱动”）
        cfg_level = 32'd0;
        repeat (20) @(negedge clk);
        checks = checks + 1;
        seen_edge = 0;
        for (k = 0; k < 3000; k = k + 1) begin
            @(negedge clk);
            if (out_pos !== {CH{1'b0}}) seen_edge = 1;
        end
        if (seen_edge) begin
            $display("[输出-等级] level=0 时输出还在动  **失败**");
            errors = errors + 1;
        end else begin
            $display("  等级：level=0 → 即使 enable=1，输出也全部拉低");
        end
        cfg_level = 32'd100;
        repeat (20) @(negedge clk);

        // ---- 调制频率 mod_hz：把输出整段开/关 ----
        // mod_hz=100 → 包络周期 10 ms（50 MHz ÷ 100 = 500000 拍），占空比 50%，
        // 所以在 50 ms 的窗口里：静音段约 5 次、跳变次数约为不调制时的一半。
        cfg_mod_hz = 32'd100;
        cfg_change = 1'b1;
        @(negedge clk);
        cfg_change = 1'b0;
        repeat (400) @(negedge clk);      // 等两趟除法（载波 + 调制）算完
        measure_envelope(2_500_000);      // 50 ms
        checks = checks + 1;
        if (env_silence < 4 || env_silence > 6) begin
            $display("[输出-调制] mod_hz=100 时 50 ms 内静音 %0d 次，期望 5±1  **失败**", env_silence);
            errors = errors + 1;
        end else begin
            $display("  调制：mod_hz=100 → 50 ms 里输出被关掉 %0d 次（包络 100 Hz）", env_silence);
        end
        checks = checks + 1;
        if (env_edges < 1900 || env_edges > 2100) begin
            $display("[输出-调制] mod_hz=100 时 50 ms 内跳变 %0d 次，期望 2000±100  **失败**", env_edges);
            errors = errors + 1;
        end else begin
            $display("  调制：mod_hz=100 → 50 ms 内跳变 %0d 次（约为不调制 4000 次的一半，包络占空比 50%%）",
                     env_edges);
        end

        // 关掉调制：应当回到「一直在动」，静音段一次都没有
        cfg_mod_hz = 32'd0;
        cfg_change = 1'b1;
        @(negedge clk);
        cfg_change = 1'b0;
        repeat (400) @(negedge clk);
        measure_envelope(500_000);        // 10 ms
        checks = checks + 1;
        if (env_silence != 0 || env_edges < 700) begin
            $display("[输出-调制] mod_hz=0 时 10 ms 内静音 %0d 次、跳变 %0d 次，期望 0 次 / 约 800 次  **失败**",
                     env_silence, env_edges);
            errors = errors + 1;
        end else begin
            $display("  调制：mod_hz=0 → 10 ms 内跳变 %0d 次、一次静音都没有（回到连续输出）",
                     env_edges);
        end

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
