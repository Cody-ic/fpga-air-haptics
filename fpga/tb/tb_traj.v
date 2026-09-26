`timescale 1ns/1ps

// 轨迹通路（节拍表 + 走步器）的仿真测试台。
//
// 向量由 gen_vectors.py 生成：
//   点表 / 段表                          —— 输入（真实硬件上由串口解析模块产出）
//   节拍表每一行的拍数、步进、扫描、笔号   —— 配置阶段该算出来的结果
//   每一拍的焦点 + 扫描开关 + 笔号        —— 运行阶段该播放出来的结果
//                                           （位置由参考实现 model.py 给出）
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_traj;

    localparam integer PT_BITS   = 21;
    localparam integer FRAC      = 16;
    localparam integer TICK_CYC  = 500;      // 50 MHz 下 10 µs = 500 个时钟
    localparam integer CASES     = 8;
    localparam integer PT_SLOTS  = 256;      // 每个用例在点表里占多少行
    localparam integer STK_SLOTS = 32;       // 每个用例在段表里占多少行
    localparam integer MV_MAX    = 1024;     // 所有用例的节拍表行数之和
    localparam integer GD_MAX    = 8192;     // 所有用例的样本数之和

    reg clk;
    initial clk = 1'b0;
    always #10 clk = ~clk;                   // 50 MHz
    reg rst_n;

    // ---------------- 向量 ----------------
    reg [127:0] hdr0 [0:CASES-1];            // 段数 点数 抬笔拍数 行数 样本数 表偏移 样本偏移 是否通过
    reg [95:0]  hdr1 [0:CASES-1];            // 重复频率 抬笔时长 一圈拍数
    reg signed [PT_BITS-1:0] mem_ptx [0:CASES*PT_SLOTS-1];
    reg signed [PT_BITS-1:0] mem_pty [0:CASES*PT_SLOTS-1];
    reg [8:0] mem_stks [0:CASES*STK_SLOTS-1];
    reg [8:0] mem_stkl [0:CASES*STK_SLOTS-1];
    reg [95:0] mv_mem  [0:MV_MAX-1];
    reg [63:0] gd_mem  [0:GD_MAX-1];
    reg [23:0] gd2_mem [0:GD_MAX-1];

    // ---------------- 点表 / 段表（模仿 hap2_scan_parse 的读写口）----------------
    reg  [10:0] pt_base;                     // 这一路的点表基址（用例序号 × 256）
    reg  [7:0]  st_base;                     // 这一路的段表基址（用例序号 × 32）
    wire [7:0]  pt_addr;
    wire [10:0] pt_full = pt_base + {3'b0, pt_addr};
    reg  signed [PT_BITS-1:0] pt_x, pt_y;

    wire [4:0]  st_addr;
    wire [7:0]  st_full = st_base + {3'b0, st_addr};
    wire [8:0]  st_start = mem_stks[st_full];   // 段表是组合读（同拍出数据）
    wire [8:0]  st_len   = mem_stkl[st_full];

    // 点表是同步读：给地址后一拍才出数据（和 hap2_scan_parse 一致）
    always @(posedge clk) begin
        pt_x <= mem_ptx[pt_full];
        pt_y <= mem_pty[pt_full];
    end

    // ---------------- 被测电路 ----------------
    reg  [8:0]  point_count;
    reg  [5:0]  stroke_count;
    reg  [31:0] cfg_repeat, cfg_blank;
    reg         plan_start;
    /* verilator lint_off UNUSEDSIGNAL */
    wire        plan_busy, plan_done, plan_fault;   // busy 只用来看仿真波形，不参与判断
    /* verilator lint_on UNUSEDSIGNAL */
    wire [31:0] laps_beats;
    wire [23:0] plan_blank_beats;
    wire [8:0]  move_count;
    wire signed [PT_BITS-1:0] traj_x0, traj_y0;

    reg  chk_mode;                           // 1 = 测试台直接读节拍表
    reg  [8:0] tb_rd;
    // 节拍表是双缓冲的：编译写另一半，算完才切过去（这里跟着做同样的事）
    reg  tbl_sel;
    always @(posedge clk) begin
        if (!rst_n)        tbl_sel <= 1'b0;
        else if (plan_done) tbl_sel <= ~tbl_sel;
    end
    wire [8:0] walk_rd;
    wire [8:0] rd_mv = chk_mode ? tb_rd : walk_rd;
    wire [23:0] mv_beats;
    wire signed [31:0] mv_step_x, mv_step_y;
    wire mv_scan;
    wire [5:0] mv_stroke;

    hap2_traj_plan #(
        .PT_BITS (PT_BITS), .FRAC (FRAC)
    ) u_plan (
        .clk (clk), .rst_n (rst_n),
        .tbl_sel (tbl_sel),
        .point_count (point_count), .stroke_count (stroke_count),
        .pt_addr (pt_addr), .pt_x (pt_x), .pt_y (pt_y),
        .st_addr (st_addr), .st_start (st_start), .st_len (st_len),
        .cfg_repeat_millihz (cfg_repeat), .cfg_blank_us (cfg_blank),
        .start (plan_start), .busy (plan_busy), .done (plan_done), .fault (plan_fault),
        .laps_beats (laps_beats), .blank_beats (plan_blank_beats),
        .move_count (move_count), .start_x (traj_x0), .start_y (traj_y0),
        .rd_mv (rd_mv), .mv_beats (mv_beats), .mv_step_x (mv_step_x),
        .mv_step_y (mv_step_y), .mv_scan (mv_scan), .mv_stroke (mv_stroke)
    );

    reg  walk_start, walk_hold, walk_stop;
    wire signed [PT_BITS-1:0] focus_x, focus_y;
    wire scan_on;
    wire [5:0] stroke_index;
    wire beat_pulse, walk_running;

    hap2_traj_walk #(
        .PT_BITS (PT_BITS), .FRAC (FRAC), .TICK_CYC (TICK_CYC)
    ) u_walk (
        .clk (clk), .rst_n (rst_n),
        .start (walk_start), .hold (walk_hold), .stop (walk_stop),
        .start_x (traj_x0), .start_y (traj_y0), .move_count (move_count),
        .rd_mv (walk_rd), .mv_beats (mv_beats), .mv_step_x (mv_step_x),
        .mv_step_y (mv_step_y), .mv_scan (mv_scan), .mv_stroke (mv_stroke),
        .focus_x (focus_x), .focus_y (focus_y), .scan_on (scan_on),
        .stroke_index (stroke_index), .beat_pulse (beat_pulse), .running (walk_running)
    );

    // ---------------- 观测：每来一个节拍脉冲就记一次当时的输出 ----------------
    integer beat_cnt;
    reg signed [PT_BITS-1:0] obs_x, obs_y;
    reg obs_scan;
    reg [5:0] obs_stroke;

    always @(posedge clk) begin
        if (beat_pulse) begin
            beat_cnt   <= beat_cnt + 1;
            obs_x      <= focus_x;
            obs_y      <= focus_y;
            obs_scan   <= scan_on;
            obs_stroke <= stroke_index;
        end
    end

    integer errors = 0;
    integer checks = 0;
    integer case_i, m, k;
    integer n_moves, n_samples, mv_off, gd_off;
    integer target, tol, adx, ady, max_ex, max_ey;
    integer want_nst, want_npt, want_blank, want_laps, want_ok;
    integer got_done, got_fault;
    reg [127:0] h0;
    reg [95:0]  h1;
    reg [95:0]  mv;
    reg [63:0]  gd;
    reg [23:0]  gd2;
    reg signed [31:0] wx, wy, diff_x, diff_y;

    task wait_beat(input integer goal);
        begin
            while (beat_cnt < goal) @(negedge clk);
        end
    endtask

    // 一路用例的节拍表逐行核对
    task check_table;
        begin
            checks = checks + 1;
            if (move_count !== n_moves) begin
                $display("  行数不符：实测 %0d，期望 %0d", move_count, n_moves);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (laps_beats !== want_laps) begin
                $display("  一圈拍数不符：实测 %0d，期望 %0d", laps_beats, want_laps);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (plan_blank_beats !== want_blank) begin
                $display("  抬笔拍数不符：实测 %0d，期望 %0d", plan_blank_beats, want_blank);
                errors = errors + 1;
            end
            chk_mode = 1'b1;
            for (m = 0; m < n_moves; m = m + 1) begin
                tb_rd = m[8:0];
                @(posedge clk); @(negedge clk);      // 同步读：给地址后一拍出数据
                mv = mv_mem[mv_off + m];
                checks = checks + 1;
                if (mv_beats !== mv[95:72] || mv_step_x !== $signed(mv[71:40])
                    || mv_step_y !== $signed(mv[39:8]) || mv_scan !== mv[6]
                    || mv_stroke !== mv[5:0] || mv[7] !== 1'b0) begin
                    $display("  第 %0d 行不符：拍数 %0d/%0d  步进x %0d/%0d  步进y %0d/%0d  扫描 %b/%b  笔号 %0d/%0d",
                             m, mv_beats, mv[95:72], mv_step_x, $signed(mv[71:40]),
                             mv_step_y, $signed(mv[39:8]), mv_scan, mv[6],
                             mv_stroke, mv[5:0]);
                    errors = errors + 1;
                end
            end
            chk_mode = 1'b0;
        end
    endtask

    // 一路用例：从头走一圈，和参考实现的每一拍对拍
    task walk_and_check;
        begin
            walk_stop = 1'b1; @(negedge clk); walk_stop = 1'b0;
            walk_start = 1'b1; @(negedge clk); walk_start = 1'b0;
            beat_cnt = 0;
            while (!walk_running) @(negedge clk);
            @(negedge clk);
            checks = checks + 1;
            if (focus_x !== traj_x0 || focus_y !== traj_y0) begin
                $display("  起点不符：实测 (%0d,%0d)，期望 (%0d,%0d)",
                         focus_x, focus_y, traj_x0, traj_y0);
                errors = errors + 1;
            end
            max_ex = 0;
            max_ey = 0;
            for (k = 0; k < n_samples; k = k + 1) begin
                gd  = gd_mem [gd_off + k];
                gd2 = gd2_mem[gd_off + k];
                target = gd[63:48];
                wait_beat(target);
                wx = $signed(gd[47:24]);
                wy = $signed(gd[23:0]);
                tol = gd2[23:8];
                diff_x = obs_x - wx;
                diff_y = obs_y - wy;
                adx = (diff_x < 0) ? -diff_x : diff_x;
                ady = (diff_y < 0) ? -diff_y : diff_y;
                if (adx > max_ex) max_ex = adx;
                if (ady > max_ey) max_ey = ady;
                checks = checks + 1;
                if (adx > tol || ady > tol) begin
                    $display("  第 %0d 拍位置偏太多：实测 (%0d,%0d)，期望 (%0d,%0d)，容差 %0d",
                             target, obs_x, obs_y, wx, wy, tol);
                    errors = errors + 1;
                end
                if (gd2[7]) begin                   // 严格样本：连开关和笔号一起比
                    checks = checks + 1;
                    if (obs_scan !== gd2[6] || obs_stroke !== gd2[5:0]) begin
                        $display("  第 %0d 拍开关/笔号不符：扫描 %b/%b  笔号 %0d/%0d",
                                 target, obs_scan, gd2[6], obs_stroke, gd2[5:0]);
                        errors = errors + 1;
                    end
                end
            end
            $display("  用例 %0d：对比 %0d 拍，位置最大偏差 x=%0d y=%0d（0.5 µm 单位）",
                     case_i, n_samples, max_ex, max_ey);
            walk_stop = 1'b1; @(negedge clk); walk_stop = 1'b0;
        end
    endtask

    initial begin
        $readmemh("tb/vectors/traj_hdr0.mem",  hdr0);
        $readmemh("tb/vectors/traj_hdr1.mem",  hdr1);
        $readmemh("tb/vectors/traj_ptx.mem",   mem_ptx);
        $readmemh("tb/vectors/traj_pty.mem",   mem_pty);
        $readmemh("tb/vectors/traj_stks.mem",  mem_stks);
        $readmemh("tb/vectors/traj_stkl.mem",  mem_stkl);
        $readmemh("tb/vectors/traj_moves.mem", mv_mem);
        $readmemh("tb/vectors/traj_gold.mem",  gd_mem);
        $readmemh("tb/vectors/traj_gold2.mem", gd2_mem);

        rst_n      = 1'b0;
        plan_start = 1'b0;
        walk_start = 1'b0;
        walk_hold  = 1'b0;
        walk_stop  = 1'b0;
        chk_mode   = 1'b0;
        tb_rd      = 9'd0;
        pt_base    = 11'd0;
        st_base    = 11'd0;
        point_count  = 9'd0;
        stroke_count = 6'd0;
        cfg_repeat = 32'd0;
        cfg_blank  = 32'd0;
        beat_cnt   = 0;
        repeat (5) @(negedge clk);
        rst_n = 1'b1;
        repeat (5) @(negedge clk);

        $display("=========================================");
        $display("轨迹通路仿真（节拍表 + 走步器）");
        $display("=========================================");

        for (case_i = 0; case_i < CASES; case_i = case_i + 1) begin
            h0 = hdr0[case_i];
            h1 = hdr1[case_i];
            want_nst   = h0[127:112];
            want_npt   = h0[111:96];
            want_blank = h0[95:80];
            n_moves    = h0[79:64];
            n_samples  = h0[63:48];
            mv_off     = h0[47:32];
            gd_off     = h0[31:16];
            want_ok    = h0[15:0];
            cfg_repeat = h1[95:64];
            cfg_blank  = h1[63:32];
            want_laps  = h1[31:0];

            point_count  = want_npt[8:0];
            stroke_count = want_nst[5:0];
            pt_base      = case_i * PT_SLOTS;
            st_base      = case_i * STK_SLOTS;
            repeat (2) @(negedge clk);

            plan_start = 1'b1; @(negedge clk); plan_start = 1'b0;
            // done / fault 只拉高一拍，所以要在循环里边走边记
            got_done = 0;
            got_fault = 0;
            while (!got_done && !got_fault) begin
                @(negedge clk);
                if (plan_done)  got_done  = 1;
                if (plan_fault) got_fault = 1;
            end
            @(negedge clk);

            checks = checks + 1;
            if (got_fault !== (want_ok === 0)) begin
                $display("用例 %0d：通过/拒绝判断错（fault=%b done=%b，期望 %s）",
                         case_i, got_fault, got_done, want_ok ? "通过" : "拒绝");
                errors = errors + 1;
            end
            if (!want_ok) begin
                $display("用例 %0d：按预期拒绝（保留上一份配置）", case_i);
            end else begin
                $display("用例 %0d：重复 %0d 毫赫兹、抬笔 %0d µs、%0d 笔 %0d 点",
                         case_i, cfg_repeat, cfg_blank, want_nst, want_npt);
                check_table;
                walk_and_check;
            end
        end

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
