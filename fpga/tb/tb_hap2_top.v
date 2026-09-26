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
    wire [15:0] array_pos, array_neg;

    hap2_top #(
        .CLK_HZ  (CLK_HZ),
        .BAUD    (BAUD),
        .HW_ROWS (4),
        .HW_COLS (4),
        .HW_PITCH_UM (10000),
        .HB_MS   (3000),
        // 测试台把「主动上报状态」关掉：这一轮要一条一条数「这条命令回了几个帧」
        .STATE_MS(0)
    ) dut (
        .clk         (clk),
        .rst_n       (rst_n),
        .uart_rx_pin (uart_line),
        .uart_tx_pin (uart_tx_pin),
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

    // 数 16 路输出上出现的上升沿（用来核对「跑起来方波在动、停下来不动」）
    integer drive_edges = 0;
    always @(posedge array_pos[0]) drive_edges = drive_edges + 1;

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
        end
    endtask

    initial begin
        $readmemh("tb/vectors/top_packets.mem", pkt);
        $readmemh("tb/vectors/top_plan.mem",    tplan);
        $readmemh("tb/vectors/top_dot_phase.mem", dot_exp);

        rst_n     = 1'b0;
        uart_line = 1'b1;
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

        // ---- 14. 预设图形：配置通过，但轨迹还没做 ----
        case_i = 13;
        clear_rx;
        send_case(13);
        wait_frames(2);
        found("HAP3 ACK 14 CONFIG applied=1 rev=3");
        // 预设图形没有草图，回传的 scan_paths 必须是 NONE
        found("scan_paths=NONE blank_us=");

        // ---- 15. 启动预设图形：焦点原地不动、扫描开关保持 0 ----
        case_i = 14;
        clear_rx;
        send_case(14);
        wait_frames(2);
        found("state=RUNNING");
        repeat (60000) @(negedge clk);
        checks = checks + 1;
        if (focus_x !== 0 || focus_y !== 0 || scan_on !== 1'b0 || output_on !== 1'b0) begin
            $display("[用例 %0d] 预设图形没有轨迹，应当原地不动：焦点 (%0d,%0d) 扫描 %b 输出 %b  **失败**",
                     case_i, focus_x, focus_y, scan_on, output_on);
            errors = errors + 1;
        end

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

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
