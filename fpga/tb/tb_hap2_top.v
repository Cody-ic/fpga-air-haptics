`timescale 1ns/1ps

// 端到端测试台：把 hap2_top 当成一块真的板子。
//
// 测试台只碰四根线：
//   串口输入线（往里发报文）、串口输出线（读板子回的报文），
//   以及顶层的焦点坐标／扫描开关／输出使能（直接量，不用等状态帧）。
//
// 报文向量由 gen_vectors.py 生成（真实 CRC，不是手抄的），
// 顺序是：握手 → 发图形 → 启动 → 暂停 → 恢复 → 停止 → 拒绝的图形 → 要状态
//        → 预设图形 → 启动 → 停止 → 探活。
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_hap2_top;

    localparam integer CLK_HZ       = 50_000_000;
    localparam integer BAUD         = 115200;
    localparam integer CLK_NS       = 20;
    localparam integer CLKS_PER_BIT = CLK_HZ / BAUD;   // 434
    localparam integer PT_BITS      = 21;

    reg clk;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;
    reg rst_n;

    reg  uart_line;                 // 测试台 -> 板子
    wire uart_tx_pin;               // 板子 -> 测试台
    wire signed [PT_BITS-1:0] focus_x, focus_y;
    wire scan_on, output_on, walk_running, beat_pulse;
    wire [5:0] stroke_index;
    /* verilator lint_off UNUSEDSIGNAL */
    wire mode_local;                    // 这一套用例不测本地模式，只看波形
    /* verilator lint_on UNUSEDSIGNAL */
    // 面板按键（高有效：1 = 按下）。这一套用例里由测试台直接驱动，模拟按键。
    reg key_next, key_play, key_stop;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [15:0] array_pos, array_neg;   // 测试只盯第 0 路，其余位在波形上看
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_top #(
        .CLK_HZ  (CLK_HZ),
        .BAUD    (BAUD),
        .HW_ROWS (4),
        .HW_COLS (4),
        .HW_PITCH_UM (10000),
        .HB_MS   (3000),
        // 面板按键的消抖时间：真实板子上用 20 ms，仿真里缩到 2 ms 免得跑太久
        // （逻辑一模一样，只是常数不同；真实的按键抖动在 tb_local.v 里单独测）
        .KEY_DEB_MS(2),
        // 测试台把「主动上报状态」关掉：这一轮要一条一条数「这条命令回了几个帧」
        .STATE_MS(0)
    ) dut (
        .clk         (clk),
        .rst_n       (rst_n),
        .uart_rx_pin (uart_line),
        .uart_tx_pin (uart_tx_pin),
        // 复位标识：真板子上由板级顶层给一个「每次复位都变」的值；这里给常数，
        // 好让状态帧里 boot=... 的期望值保持固定。
        .boot_id     (32'hA1B2C3D4),
        // 面板按键：这一套用例只走串口那条路，按键保持松开（高有效，0 = 没按）
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
        .array_neg   (array_neg)
    );

    // 数 16 路输出上出现的上升沿（用来核对「跑起来方波在动、停下来不动」），
    // 顺便记住每一路有没有出现过高低电平（有没有哪一路一直不动）
    /* verilator lint_off BLKSEQ */
    /* verilator lint_off UNUSEDSIGNAL */
    integer drive_edges;
    integer drive_edges_neg;
    reg [15:0] seen_hi, seen_lo;
    always @(posedge array_pos[0]) drive_edges     = drive_edges + 1;
    always @(posedge array_neg[0]) drive_edges_neg = drive_edges_neg + 1;
    always @(posedge clk) begin
        seen_hi <= seen_hi | array_pos;
        seen_lo <= seen_lo | ~array_pos;
    end
    /* verilator lint_on UNUSEDSIGNAL */
    /* verilator lint_on BLKSEQ */

    // ---------------- 板子发出来的字节 ----------------
    wire [7:0] board_byte;
    wire       board_byte_valid;

    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_rx_board (
        .clk(clk), .rst_n(rst_n), .rx_line(uart_tx_pin),
        .rx_data(board_byte), .rx_valid(board_byte_valid)
    );

    reg [7:0] txbuf [0:8191];
    integer   txlen;
    integer   lf_count;

    always @(posedge clk) begin
        if (board_byte_valid) begin
            if (txlen < 8192) begin
                txbuf[txlen] <= board_byte;
                txlen        <= txlen + 1;
            end
            if (board_byte == 8'h0A) lf_count <= lf_count + 1;
        end
    end

    // ---------------- 向量 ----------------
    reg [7:0]  pkt   [0:8191];
    reg [31:0] tplan [0:63];       // 每条报文两个字段（偏移、长度）
    reg [7:0]  dot_exp [0:255];    // 单点草图的相位串期望值（以 0 结尾）

    integer errors = 0;
    integer checks = 0;
    integer st, val, timeout;
    integer case_i;

    // ---------------- 工具 ----------------
    task clear_rx;
        begin
            txlen    = 0;
            lf_count = 0;
        end
    endtask

    task send_byte;
        input [7:0] b;
        integer i2, k2;
        begin
            @(negedge clk);
            uart_line = 1'b0;
            for (k2 = 0; k2 < CLKS_PER_BIT; k2 = k2 + 1) @(negedge clk);
            for (i2 = 0; i2 < 8; i2 = i2 + 1) begin
                uart_line = b[i2];
                for (k2 = 0; k2 < CLKS_PER_BIT; k2 = k2 + 1) @(negedge clk);
            end
            uart_line = 1'b1;
            for (k2 = 0; k2 < CLKS_PER_BIT; k2 = k2 + 1) @(negedge clk);
        end
    endtask

    // 发第 idx 条向量报文
    task send_case;
        input integer idx;
        integer off, len, j;
        begin
            off = tplan[2*idx];
            len = tplan[2*idx + 1];
            for (j = 0; j < len; j = j + 1) send_byte(pkt[off + j]);
        end
    endtask

    // 等板子把 n 帧发完（数换行），最多等 timeout 拍
    task wait_frames;
        input integer n;
        begin
            timeout = 0;
            while (lf_count < n && timeout < 12_000_000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            checks = checks + 1;
            if (lf_count < n) begin
                $display("[用例 %0d] 等应答超时：只收到 %0d 帧（期望 %0d）  **失败**",
                         case_i, lf_count, n);
                errors = errors + 1;
            end
        end
    endtask

    // 在收到的字节流里找一段文本（和 tb_hap2_rx 里同一个套路）。
    // Verilog 的字符串字面量是「右对齐、左边补零」的：在 64 字节的向量里，
    // 第 b 个字符落在字节 (nlen-1-b) 上。
    // 字符串字面量是「右对齐、左边补零」的，所以长度可以从补的零里推出来，
    // 不用手工数（数错一位就永远匹配不上）。
    task found;
        input [8*64-1:0] needle;
        integer a, b, hit, nlen;
        reg ok;
        begin
            nlen = 0;
            for (b = 0; b < 64; b = b + 1)
                if (needle[8*b +: 8] != 8'h00) nlen = b + 1;
            ok = 1'b0;
            for (a = 0; a + nlen <= txlen; a = a + 1) begin
                hit = 1;
                for (b = 0; b < nlen; b = b + 1) begin
                    if (txbuf[a + b] !== needle[8*(nlen-1-b) +: 8]) hit = 0;
                end
                if (hit) ok = 1'b1;
            end
            checks = checks + 1;
            if (!ok) begin
                $display("[用例 %0d] 应答里找不到「%0s」  **失败**", case_i, needle);
                // 找不到的时候把实际收到的字节原样打出来（否则只能靠猜）
                $write("    实际收到：");
                for (a = 0; a < txlen; a = a + 1) begin
                    if (txbuf[a] == 8'h0A) $write("\n              ");
                    else                   $write("%c", txbuf[a]);
                end
                $write("\n");
                errors = errors + 1;
            end
        end
    endtask

    // 找 "key=" 后面的十进制数，结果放进 val；找不到就报错
    task field_int;
        input [8*16-1:0] key;
        integer a, b, hit, p, klen;
        begin
            klen = 0;
            for (b = 0; b < 16; b = b + 1)
                if (key[8*b +: 8] != 8'h00) klen = b + 1;
            val = -1;
            for (a = 0; a + klen <= txlen; a = a + 1) begin
                hit = 1;
                for (b = 0; b < klen; b = b + 1) begin
                    if (txbuf[a + b] !== key[8*klen-1 - 8*b -: 8]) hit = 0;
                end
                if (hit && val < 0) begin
                    p   = a + klen;
                    val = 0;
                    while (p < txlen && txbuf[p] >= 8'h30 && txbuf[p] <= 8'h39) begin
                        val = val * 10 + (txbuf[p] - 8'h30);
                        p   = p + 1;
                    end
                end
            end
            checks = checks + 1;
            if (val < 0) begin
                $display("[用例 %0d] 应答里找不到字段「%0s」  **失败**", case_i, key);
                errors = errors + 1;
            end
        end
    endtask

    // ---- 面板按键：模拟真人按一下 ----
    // 按下 3 ms、松开 3 ms（都比消抖的 2 ms 长），所以一定会被认一次、且只认一次。
    localparam integer KEY_CYC = 150_000;      // 3 ms @ 50 MHz
    task press_key;                             // 带抖动地按一下：先抖几下再按住
        input [1:0] which;                      // 0 = next, 1 = play, 2 = stop
        integer b;
        begin
            for (b = 0; b < 8; b = b + 1) begin
                // 抖动：每 5k 拍翻一次，累计不到消抖时间，所以不该被认成「按下」
                if (which == 0) key_next = ~key_next;
                if (which == 1) key_play = ~key_play;
                if (which == 2) key_stop = ~key_stop;
                repeat (5000) @(negedge clk);
            end
            // 按住足够久 → 认一次
            if (which == 0) key_next = 1'b1;
            if (which == 1) key_play = 1'b1;
            if (which == 2) key_stop = 1'b1;
            repeat (KEY_CYC) @(negedge clk);
            // 松开，并且等消抖确认，免得两次按键黏在一起
            if (which == 0) key_next = 1'b0;
            if (which == 1) key_play = 1'b0;
            if (which == 2) key_stop = 1'b0;
            repeat (KEY_CYC) @(negedge clk);
        end
    endtask

    // 本地按键触发的配置要走「生成点表 + 编译节拍表」，等它一会儿
    task wait_local_build;
        begin
            repeat (300000) @(negedge clk);      // 6 ms，够几毫秒的编译
        end
    endtask

    // 预设图形跑起来的样子（不挑形状）：每一拍都在扫描，而且焦点真的在动。
    // 预设图形没有抬笔时间，所以 scan_on 必须恒为 1。
    integer pm_beats, pm_blank, pm_xmin, pm_xmax, pm_ymin, pm_ymax;
    task watch_preset_motion;
        input integer span_units;      // 期望至少动多远（0.5 µm 单位）
        input integer cycles;
        integer c;
        begin
            pm_beats = 0; pm_blank = 0;
            pm_xmin = 100000000; pm_xmax = -100000000;
            pm_ymin = 100000000; pm_ymax = -100000000;
            for (c = 0; c < cycles; c = c + 1) begin
                @(negedge clk);
                if (beat_pulse) begin
                    pm_beats = pm_beats + 1;
                    if (!scan_on) pm_blank = pm_blank + 1;
                    if (focus_x < pm_xmin) pm_xmin = focus_x;
                    if (focus_x > pm_xmax) pm_xmax = focus_x;
                    if (focus_y < pm_ymin) pm_ymin = focus_y;
                    if (focus_y > pm_ymax) pm_ymax = focus_y;
                end
            end
            checks = checks + 1;
            if (pm_beats == 0 || pm_blank != 0) begin
                $display("[用例 %0d] 预设图形应当全程扫描：%0d 拍里 %0d 拍关着输出  **失败**",
                         case_i, pm_beats, pm_blank);
                errors = errors + 1;
            end
            checks = checks + 1;
            if ((pm_xmax - pm_xmin) < span_units && (pm_ymax - pm_ymin) < span_units) begin
                $display("[用例 %0d] 焦点没怎么动：x %0d→%0d、y %0d→%0d  **失败**",
                         case_i, pm_xmin, pm_xmax, pm_ymin, pm_ymax);
                errors = errors + 1;
            end else begin
                $display("  本地播放：%0d 拍全程扫描，焦点 x %0d→%0d、y %0d→%0d（真的在动）",
                         pm_beats, pm_xmin, pm_xmax, pm_ymin, pm_ymax);
            end
        end
    endtask

    // 在收到的字节里找「以 0 结尾的那串期望字节」（单点草图的相位串）
    task found_expect;
        input [8*32-1:0] what;
        integer a, b, hit, n, ok;
        begin
            n = 0;
            while (n < 256 && dot_exp[n] != 8'h00) n = n + 1;
            ok = 0;
            for (a = 0; a + n <= txlen; a = a + 1) begin
                hit = 1;
                for (b = 0; b < n; b = b + 1)
                    if (txbuf[a + b] !== dot_exp[b]) hit = 0;
                if (hit) ok = 1;
            end
            checks = checks + 1;
            if (!ok) begin
                $display("[用例 %0d] %0s 与参考实现不一致  **失败**", case_i, what);
                errors = errors + 1;
            end else begin
                $display("  %0s 与参考实现逐位一致（%0d 个字符）", what, n);
            end
        end
    endtask

    // 断言某段文本**不**出现（比如「相位串全是 0」）
    task absent;
        input [8*64-1:0] needle;
        integer a, b, hit, nlen, found_it;
        begin
            nlen = 0;
            for (b = 0; b < 64; b = b + 1)
                if (needle[8*b +: 8] != 8'h00) nlen = b + 1;
            found_it = 0;
            for (a = 0; a + nlen <= txlen; a = a + 1) begin
                hit = 1;
                for (b = 0; b < nlen; b = b + 1)
                    if (txbuf[a + b] !== needle[8*(nlen-1-b) +: 8]) hit = 0;
                if (hit) found_it = 1;
            end
            checks = checks + 1;
            if (found_it) begin
                $display("[用例 %0d] 不应出现的「%0s」出现了  **失败**", case_i, needle);
                errors = errors + 1;
            end
        end
    endtask

    task expect_val;
        input integer got;
        input integer want;
        input [8*32-1:0] what;
        begin
            checks = checks + 1;
            if (got !== want) begin
                $display("[用例 %0d] %0s 不对：实测 %0d，期望 %0d  **失败**", case_i, what, got, want);
                errors = errors + 1;
            end
        end
    endtask

    // 盯住走步器看一段时间：统计扫描／抬笔拍数、笔号、焦点有没有动，
    // 并顺手核对输出使能的三个条件。
    integer seen_scan, seen_blank, stroke_max, fx_min, fx_max, gate_bad;
    task watch_trajectory;
        input integer cycles;
        integer c;
        begin
            seen_scan = 0; seen_blank = 0; stroke_max = 0;
            fx_min = 1000000; fx_max = -1000000; gate_bad = 0;
            for (c = 0; c < cycles; c = c + 1) begin
                @(negedge clk);
                if (beat_pulse) begin
                    if (scan_on) seen_scan = seen_scan + 1;
                    else         seen_blank = seen_blank + 1;
                    if (stroke_index > stroke_max) stroke_max = stroke_index;
                    if (focus_x < fx_min) fx_min = focus_x;
                    if (focus_x > fx_max) fx_max = focus_x;
                end
                // output 的三条件：运行中 且 等级>0 且 正在扫描。
                // 这一组用例的 level 都是 30，所以只要看「运行中 + 正在扫描」。
                if (output_on !== (scan_on && walk_running)) gate_bad = 1;
            end
            checks = checks + 1;
            if (seen_scan == 0 || seen_blank == 0) begin
                $display("[用例 %0d] 没看到「扫描一段、抬笔一段」：扫描 %0d 拍，抬笔 %0d 拍  **失败**",
                         case_i, seen_scan, seen_blank);
                errors = errors + 1;
            end else begin
                $display("  走步器：扫描 %0d 拍、抬笔 %0d 拍、最多到第 %0d 笔、焦点 x 从 %0d 到 %0d（0.5 µm）",
                         seen_scan, seen_blank, stroke_max, fx_min, fx_max);
            end
            checks = checks + 1;
            if (stroke_max < 1) begin
                $display("[用例 %0d] 没有走到第二笔（最多到第 %0d 笔）  **失败**", case_i, stroke_max);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (fx_max <= fx_min) begin
                $display("[用例 %0d] 焦点没有动  **失败**", case_i);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (gate_bad) begin
                $display("[用例 %0d] 输出使能条件不满足（必须运行中 + 等级>0 + 正在扫描）  **失败**", case_i);
                errors = errors + 1;
            end
        end
    endtask

    // 单点草图：焦点应当一直停在那一点上，扫描开关照常一开一关（原地停留 + 抬笔）
    integer dot_scan, dot_blank, dot_moved, dot_gate_bad;
    task watch_dot;
        input integer cycles;
        input integer wx;
        input integer wy;
        integer c, lx, ly;
        begin
            dot_scan = 0; dot_blank = 0; dot_moved = 0; dot_gate_bad = 0;
            lx = 0; ly = 0;
            for (c = 0; c < cycles; c = c + 1) begin
                @(negedge clk);
                if (beat_pulse) begin
                    if (scan_on) dot_scan = dot_scan + 1;
                    else         dot_blank = dot_blank + 1;
                    if ((lx != 0 || ly != 0) && (focus_x !== lx || focus_y !== ly))
                        dot_moved = 1;
                    lx = focus_x;
                    ly = focus_y;
                end
                if (output_on !== (scan_on && walk_running)) dot_gate_bad = 1;
            end
            checks = checks + 1;
            if (focus_x !== wx || focus_y !== wy) begin
                $display("[用例 %0d] 单点草图应当停在 (%0d,%0d)：实测 (%0d,%0d)  **失败**",
                         case_i, wx, wy, focus_x, focus_y);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (dot_moved) begin
                $display("[用例 %0d] 单点草图不应该移动焦点  **失败**", case_i);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (dot_scan == 0 || dot_blank == 0) begin
                $display("[用例 %0d] 单点草图也要「停留一段、抬笔一段」：停留 %0d 拍、抬笔 %0d 拍  **失败**",
                         case_i, dot_scan, dot_blank);
                errors = errors + 1;
            end else begin
                $display("  单点草图：停留 %0d 拍、抬笔 %0d 拍，焦点始终在 (%0d,%0d)",
                         dot_scan, dot_blank, focus_x, focus_y);
            end
            checks = checks + 1;
            if (dot_gate_bad) begin
                $display("[用例 %0d] 输出使能条件不满足  **失败**", case_i);
                errors = errors + 1;
            end
        end
    endtask

    // 盯住 16 路驱动输出：跑起来方波要在动，停下来必须彻底不动
    integer drv_before, drv_after;
    // 预设图形（圆）跑起来的样子：焦点在半径 r 的圆上转，而且全程都在扫描
    integer cc_beats, cc_blank, cc_rmin, cc_rmax, cc_fxmin, cc_fxmax, cc_fymin, cc_fymax;
    task watch_circle;
        input integer radius_units;     // 0.5 µm 单位
        input integer cycles;
        integer c, rr;
        begin
            cc_beats = 0; cc_blank = 0;
            cc_rmin = 100000000; cc_rmax = -100000000;
            cc_fxmin = 100000000; cc_fxmax = -100000000;
            cc_fymin = 100000000; cc_fymax = -100000000;
            for (c = 0; c < cycles; c = c + 1) begin
                @(negedge clk);
                if (beat_pulse) begin
                    cc_beats = cc_beats + 1;
                    if (!scan_on) cc_blank = cc_blank + 1;
                    // 到圆心的距离（整数近似就够判数量级）
                    rr = (focus_x > 0 ? focus_x : -focus_x)
                       + (focus_y > 0 ? focus_y : -focus_y);
                    if (focus_x < cc_fxmin) cc_fxmin = focus_x;
                    if (focus_x > cc_fxmax) cc_fxmax = focus_x;
                    if (focus_y < cc_fymin) cc_fymin = focus_y;
                    if (focus_y > cc_fymax) cc_fymax = focus_y;
                    if (rr < cc_rmin) cc_rmin = rr;
                    if (rr > cc_rmax) cc_rmax = rr;
                end
            end
            checks = checks + 1;
            if (cc_beats == 0) begin
                $display("[用例 %0d] 预设图形没有节拍  **失败**", case_i);
                errors = errors + 1;
            end
            // 预设图形没有抬笔：整圈每一拍都在扫描（参考实现里 blank_us 只对多段草图生效）
            checks = checks + 1;
            if (cc_blank != 0) begin
                $display("[用例 %0d] 预设图形不该有抬笔时间，却出现 %0d 拍 scan_on=0  **失败**",
                         case_i, cc_blank);
                errors = errors + 1;
            end
            // 圆上的点到圆心距离 ≈ 半径。这里用 |x|+|y| 近似（它落在 [r, 1.42r]），
            // 再留 1/16 的余量：节拍表把圆切成 128 条弦，弦中间的采样点比半径
            // 最多短 r×(1−cos(π/128)) ≈ 12 个 0.5 µm 单位，不留余量会误判。
            checks = checks + 1;
            if (cc_rmin < radius_units * 15 / 16 || cc_rmax > radius_units * 3 / 2) begin
                $display("[用例 %0d] 焦点不在半径 %0d 的圆上：|x|+|y| 落在 %0d～%0d  **失败**",
                         case_i, radius_units, cc_rmin, cc_rmax);
                errors = errors + 1;
            end
            checks = checks + 1;
            // 「动没动」要看两个轴里跨得更大的那个：8 ms 的窗口只覆盖一圈的
            // 115°，如果这一小段正好压着圆的起点（x 最大处），光看 x 方向的
            // 跨度就只有 0.48r，会误判；这时 y 方向的跨度是 1.7r。
            if ((cc_fxmax - cc_fxmin < radius_units / 2)
                && (cc_fymax - cc_fymin < radius_units / 2)) begin
                $display("[用例 %0d] 焦点没怎么动：x 从 %0d 到 %0d、y 从 %0d 到 %0d  **失败**",
                         case_i, cc_fxmin, cc_fxmax, cc_fymin, cc_fymax);
                errors = errors + 1;
            end
            $display("  预设图形（圆，半径 %0d 个 0.5 µm 单位）：%0d 拍全程扫描，|x|+|y| 在 %0d～%0d，x 从 %0d 到 %0d、y 从 %0d 到 %0d",
                     radius_units, cc_beats, cc_rmin, cc_rmax,
                     cc_fxmin, cc_fxmax, cc_fymin, cc_fymax);
        end
    endtask

    task check_drive;
        input integer want_edges;    // 1 = 这段时间里必须有上升沿，0 = 一个都不许有
        begin
            drv_before = drive_edges;
            // 窗口取 6 毫秒：抬笔最长 2 毫秒，所以这段时间里一定包含扫描段，
            // 40 kHz 的方波一定在动
            repeat (300000) @(negedge clk);
            drv_after  = drive_edges;
            checks = checks + 1;
            if (want_edges && drv_after == drv_before) begin
                $display("[用例 %0d] 16 路驱动输出没有方波  **失败**", case_i);
                errors = errors + 1;
            end else if (!want_edges && drv_after != drv_before) begin
                $display("[用例 %0d] 本该没有输出，却出现了 %0d 个上升沿  **失败**",
                         case_i, drv_after - drv_before);
                errors = errors + 1;
            end else begin
                $display("  驱动输出：这段时间上升沿 %0d 个（%0s）",
                         drv_after - drv_before, want_edges ? "应当有" : "应当没有");
            end
            if (want_edges) begin
                // 顺便核对：16 路都必须既出现过低电平也出现过高电平（没有哪一路是死的），
                // 互补输出那一路也应该在动
                checks = checks + 1;
                if ((seen_hi & seen_lo) !== 16'hFFFF) begin
                    $display("[用例 %0d] 有通道一直是同一个电平：高过=%b 低过=%b  **失败**",
                             case_i, seen_hi, seen_lo);
                    errors = errors + 1;
                end
                checks = checks + 1;
                if (drive_edges_neg == 0) begin
                    $display("[用例 %0d] 互补输出（array_neg）没有任何跳变  **失败**", case_i);
                    errors = errors + 1;
                end
            end
        end
    endtask

    initial begin
        $readmemh("tb/vectors/top_packets.mem", pkt);
        $readmemh("tb/vectors/top_plan.mem",    tplan);
        $readmemh("tb/vectors/top_dot_phase.mem", dot_exp);

        rst_n     = 1'b0;
        uart_line = 1'b1;
        key_next  = 1'b0;
        key_play  = 1'b0;
        key_stop  = 1'b0;
        txlen     = 0;
        lf_count  = 0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("端到端仿真：串口线进、串口线出");
        $display("=========================================");

        // ---- 1. 握手 ----
        case_i = 0;
        clear_rx;
        send_case(0);
        wait_frames(2);
        found("HAP3 ACK 1 HELLO proto=3");
        found("caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,SCAN_PATHS ");
        found("state=IDLE");

        // ---- 2. 发一份带两笔草图的配置 ----
        // 这一条要走「解析 → 编译节拍表 → 才回 ACK」，所以应答会晚一点来。
        case_i = 1;
        clear_rx;
        send_case(1);
        wait_frames(2);
        found("HAP3 ACK 2 CONFIG applied=1 rev=1");
        found("scan_paths=1000:2000,11000:2000|1000:7000,11000:7000 ");
        found("scan_on=0 stroke_index=0");
        field_int("rev=");
        expect_val(val, 1, "版本号");

        // ---- 3. 启动，看它真的按节拍走 ----
        case_i = 2;
        clear_rx;
        send_case(2);
        wait_frames(2);
        found("HAP3 ACK 3 START");
        found("state=RUNNING");
        // 走一圈是 25 毫秒（2500 拍）：这里看 1.2 百万拍 = 24 毫秒 = 2400 个节拍，
        // 足够看到「扫描 → 抬笔 → 第二笔」
        absent("phases=0,0,0,0,0,0");     // 跑起来相位不该全是 0
        watch_trajectory(1200000);
        check_drive(1);                   // 跑起来 16 路驱动输出必须在动

        // ---- 4. 暂停：位置冻住、输出关掉 ----
        case_i = 3;
        clear_rx;
        send_case(3);
        wait_frames(2);
        found("state=PAUSED");
        checks = checks + 1;
        if (output_on !== 1'b0) begin
            $display("[用例 %0d] 暂停之后输出没关  **失败**", case_i);
            errors = errors + 1;
        end
        st = focus_x;                     // 暂停时的位置（后面核对「接着走」）
        repeat (60000) @(negedge clk);     // 60 万拍 = 12 毫秒，早就该冻住了
        checks = checks + 1;
        if (focus_x !== st) begin
            $display("[用例 %0d] 暂停之后焦点还在动：%0d -> %0d  **失败**", case_i, st, focus_x);
            errors = errors + 1;
        end

        // ---- 5. 恢复：接着走，不是回到起点 ----
        case_i = 4;
        clear_rx;
        send_case(4);
        // 命令刚发完就来看位置：恢复的语义是「解开冻结」，位置寄存器一动不动，
        // 所以这一刻应当还在暂停的地方（顶多走了一两拍），而不是跳回起点。
        repeat (500) @(negedge clk);
        checks = checks + 1;
        if (focus_x - st > 200 || st - focus_x > 200) begin
            $display("[用例 %0d] 恢复瞬间位置跳了：暂停在 %0d，恢复后 %0d  **失败**",
                     case_i, st, focus_x);
            errors = errors + 1;
        end
        wait_frames(2);
        found("state=RUNNING");
        repeat (60000) @(negedge clk);

        // ---- 6. 停止：回到起点 ----
        case_i = 5;
        clear_rx;
        send_case(5);
        wait_frames(2);
        found("state=IDLE");
        repeat (1000) @(negedge clk);
        checks = checks + 1;
        if (focus_x !== 21'sd2000 || focus_y !== 21'sd4000) begin
            $display("[用例 %0d] 停止后没有回到起点：实测 (%0d,%0d)，期望 (2000,4000)  **失败**",
                     case_i, focus_x, focus_y);
            errors = errors + 1;
        end
        checks = checks + 1;
        if (scan_on !== 1'b0 || output_on !== 1'b0) begin
            $display("[用例 %0d] 停止后输出没关  **失败**", case_i);
            errors = errors + 1;
        end
        check_drive(0);                   // 停下来之后驱动输出必须彻底安静

        // ---- 7. 一份碎到做不出节拍表的配置：整份拒绝 ----
        case_i = 6;
        clear_rx;
        send_case(6);
        wait_frames(1);
        found("HAP3 ERR 7 CONFIG code=BAD_CONFIG");

        // ---- 8. 要一份状态：版本号应当还停在 1（上面那份没生效）----
        case_i = 7;
        clear_rx;
        send_case(7);
        wait_frames(2);
        found("HAP3 TEL 0 STATE");
        // 被拒绝的那份图形不能污染回传：scan_paths 仍是上一份**生效**的草图
        found("scan_paths=1000:2000,11000:2000|1000:7000,11000:7000 ");
        field_int("rev=");
        expect_val(val, 1, "被拒绝之后的版本号");

        // ---- 9. 被拒绝之后启动：上一份草图的节拍表还应当能跑 ----
        // （节拍表是双缓冲的，编译失败只写另一半，不动正在用的那张）
        case_i = 8;
        clear_rx;
        send_case(8);
        wait_frames(2);
        found("HAP3 ACK 9 START");
        found("state=RUNNING");
        watch_trajectory(1200000);

        // ---- 10. 停止 ----
        case_i = 9;
        clear_rx;
        send_case(9);
        wait_frames(2);
        found("state=IDLE");

        // ---- 11. 4 个字符的单点草图：不能被当成 NONE 拒掉 ----
        case_i = 10;
        clear_rx;
        send_case(10);
        wait_frames(2);
        found("HAP3 ACK 11 CONFIG applied=1 rev=2");
        found("scan_paths=10:2 blank_us=");

        // ---- 12. 启动单点草图：原地停留，扫描开关照常开关 ----
        case_i = 11;
        clear_rx;
        send_case(11);
        wait_frames(2);
        found("state=RUNNING");
        // 单点在 10 µm : 2 µm，换成 0.5 µm 单位就是 (20,4)
        watch_dot(1200000, 20, 4);
        // 相位串要和参考实现逐位一致：这一条把「相位引擎 + 同一瞬间快照 + 回传」整条链钉住
        found_expect("单点草图的相位串");

        // ---- 13. 停止 ----
        case_i = 12;
        clear_rx;
        send_case(12);
        wait_frames(2);
        found("state=IDLE");

        // ---- 14. 预设图形（圆）：点表由板子自己按形状生成 ----
        case_i = 13;
        clear_rx;
        send_case(13);
        wait_frames(2);
        found("HAP3 ACK 14 CONFIG applied=1 rev=3");
        // 预设图形没有草图，回传的 scan_paths 必须是 NONE
        found("scan_paths=NONE blank_us=");

        // ---- 15. 启动预设图形：焦点沿半径 20 mm 的圆转，全程都在扫描 ----
        case_i = 14;
        clear_rx;
        send_case(14);
        wait_frames(2);
        found("state=RUNNING");
        checks = checks + 1;
        if (output_on !== 1'b1) begin
            // 预设图形整圈都在扫描，等级 30 > 0，又处在 RUNNING，输出必须是开的
            $display("[用例 %0d] 预设图形跑起来之后输出没开（scan_on=%b）  **失败**",
                     case_i, scan_on);
            errors = errors + 1;
        end
        // 半径 20000 µm = 40000 个 0.5 µm 单位
        watch_circle(40000, 400000);

        // ---- 16. 停止 ----
        case_i = 15;
        clear_rx;
        send_case(15);
        wait_frames(2);
        found("state=IDLE");

        // ---- 17. 探活：只回一条 ACK，不跟状态 ----
        case_i = 16;
        clear_rx;
        send_case(16);
        wait_frames(1);
        found("HAP3 ACK 17 PING");
        repeat (20000) @(negedge clk);
        checks = checks + 1;
        if (lf_count !== 1) begin
            $display("[用例 %0d] 探活应当只回一条报文，实测 %0d 条  **失败**", case_i, lf_count);
            errors = errors + 1;
        end

        // ---- 18. 配置和探活连着发：探活不能顶掉配置应答的序号 ----
        // 探活会在节拍表编译期间到达。这一条是回归用例：以前命令层会在每一帧
        // 都记「回给谁」，探活一插进来就把序号顶掉了，配置的 ACK 会回错序号。
        case_i = 17;
        clear_rx;
        send_case(17);
        wait_frames(3);                    // 探活 ACK + 配置 ACK + 配置的 STATE
        found("HAP3 ACK 19 PING");
        found("HAP3 ACK 18 CONFIG applied=1 rev=4");

        // ---- 19. 工作空间越界：字段各自合法，但整张图越界（第 4 项）----
        // 草图点 x=150000 µm，参考实现允许（≤300 mm），但设备声明的工作空间是 ±100 mm
        case_i = 18;
        clear_rx;
        send_case(18);
        wait_frames(1);
        found("HAP3 ERR 20 CONFIG code=OUT_OF_WORKSPACE");

        // 预设图形：cx=90000 + radius=20000 = 110000 µm，同样是越界
        case_i = 19;
        clear_rx;
        send_case(19);
        wait_frames(1);
        found("HAP3 ERR 21 CONFIG code=OUT_OF_WORKSPACE");

        // 两次被拒之后要版本号没动（还是 4）：要一份状态来看看真实的 rev
        case_i = 20;
        clear_rx;
        send_case(23);                       // SNAP
        wait_frames(2);
        found("HAP3 ACK 25 SNAP");
        field_int("rev=");
        checks = checks + 1;
        if (val !== 4) begin
            $display("[用例 %0d] 越界配置被拒之后版本号应当还是 4，实测 %0d  **失败**", case_i, val);
            errors = errors + 1;
        end else begin
            $display("  越界拒绝：ERR OUT_OF_WORKSPACE，版本号保持 4（上一份配置没被动过）");
        end

        // 边界用例：cx=80000 + radius=20000 = 正好 100000 µm，压线应当接受
        case_i = 21;
        clear_rx;
        send_case(20);
        wait_frames(2);
        found("HAP3 ACK 22 CONFIG applied=1 rev=5");

        // ---- 20. 本地模式：按键选图形、播放、停止（第 6 项）----
        case_i = 22;
        clear_rx;
        send_case(21);                       // MODE value=LOCAL
        wait_frames(2);
        found("HAP3 ACK 23 MODE applied=1 rev=5");
        found("mode=LOCAL");

        // 按一次「换图形」：默认选中的是圆，按一下应当变成方（SQUARE）。
        // press_key 里先抖 8 下再按住——抖动不该被认，所以只能发生**一次**动作：
        // 版本号 5 → 6，图形 CIRCLE → SQUARE。
        clear_rx;                            // 先清空，方便核对「按键本身不回串口」
        press_key(0);
        wait_local_build;
        checks = checks + 1;
        if (lf_count !== 0) begin
            $display("[用例 %0d] 本地按键不该往串口回话，实测回了 %0d 条  **失败**",
                     case_i, lf_count);
            errors = errors + 1;
        end else begin
            $display("  本地按键：不经过串口，电脑那边一条报文都收不到（靠主动上报刷界面）");
        end

        case_i = 23;
        clear_rx;
        send_case(23);                       // SNAP：读回本地动作的结果
        wait_frames(2);
        found("shape=SQUARE");
        found("scan_paths=NONE");
        field_int("rev=");
        checks = checks + 1;
        if (val !== 6) begin
            $display("[用例 %0d] 本地换图形应当只动一次（rev 5→6），实测 rev=%0d  **失败**", case_i, val);
            errors = errors + 1;
        end else begin
            $display("  本地换图形：按键抖动 8 次 + 按住一次 → 只发生一次动作，rev 5→6，图形变 SQUARE");
        end

        // 按「播放」：本地应当自己跑起来（不需要电脑下 START）
        case_i = 24;
        press_key(1);
        checks = checks + 1;
        if (walk_running !== 1'b1) begin
            $display("[用例 %0d] 本地按键播放之后走步器应当跑起来，实测 walk_running=%b  **失败**",
                     case_i, walk_running);
            errors = errors + 1;
        end else begin
            $display("  本地播放：按键之后板子自己跑起来（走步器已启动）");
        end
        // 真的在走：焦点必须动起来、而且全程都在扫描（方形的半边长是 20 mm，
        // 所以取 5 mm 作为「动了」的门槛）
        watch_preset_motion(10000, 1200000);

        // 再按一次「播放」= 暂停：焦点应当冻住、输出关掉
        case_i = 25;
        press_key(1);
        checks = checks + 1;
        st = focus_x;
        repeat (200000) @(negedge clk);
        if (focus_x !== st || output_on !== 1'b0) begin
            $display("[用例 %0d] 本地暂停没生效：焦点 %0d→%0d、输出 %b  **失败**",
                     case_i, st, focus_x, output_on);
            errors = errors + 1;
        end else begin
            $display("  本地暂停：再按一次播放，焦点冻在 (%0d,%0d)、输出关闭", focus_x, focus_y);
        end

        // 按「停止」：回到待机，输出保持关闭
        case_i = 26;
        press_key(2);
        checks = checks + 1;
        if (walk_running !== 1'b0 || output_on !== 1'b0) begin
            $display("[用例 %0d] 本地停止没生效：走步器 %b、输出 %b  **失败**",
                     case_i, walk_running, output_on);
            errors = errors + 1;
        end else begin
            $display("  本地停止：回到 IDLE，输出关闭");
        end

        // 本地模式下，电脑的 CONFIG/START 必须被拒（协议规定 LOCAL 不接受远程命令）
        case_i = 27;
        clear_rx;
        send_case(20);                       // 一条本来合法的 CONFIG
        wait_frames(1);
        found("HAP3 ERR 22 CONFIG code=LOCAL_CONTROL");

        // ---- 21. 切回电脑控制 ----
        case_i = 28;
        clear_rx;
        send_case(22);                       // MODE value=REMOTE
        wait_frames(2);
        found("HAP3 ACK 24 MODE applied=1 rev=6");
        found("mode=REMOTE");

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
