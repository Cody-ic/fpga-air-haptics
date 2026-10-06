`timescale 1ns/1ps

// 本地按键模块的测试台：专测「机械按键」这件事。
//
// 机械触点在按下和松开的那几毫秒里电平会来回抖（示波器上是一串毛刺）。
// 如果直接拿它做「按一下换一个图形」，一次按下去会换七八个图形——这个测试台就是
// 盯着这件事：抖动多少次都只能算**一次**，按住不放也不能一直重复触发。
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_local;

    localparam integer CLK_HZ = 50_000_000;
    localparam integer CLK_NS = 20;
    localparam integer DEB_MS = 1;                        // 仿真里缩到 1 ms
    localparam integer DEB_CYC = (CLK_HZ/1000) * DEB_MS;  // 50000 拍

    reg clk, rst_n;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;

    reg key_next, key_play, key_stop;
    wire next_pulse, play_pulse, stop_pulse;
    wire [2:0] sel;

    hap2_local #(
        .CLK_HZ (CLK_HZ),
        .DEB_MS (DEB_MS)
    ) dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .key_next   (key_next),
        .key_play   (key_play),
        .key_stop   (key_stop),
        .next_pulse (next_pulse),
        .play_pulse (play_pulse),
        .stop_pulse (stop_pulse),
        .sel        (sel)
    );

    // 数脉冲（事件驱动，不会漏掉单拍脉冲）
    integer n_next, n_play, n_stop;
    /* verilator lint_off BLKSEQ */
    always @(posedge clk) begin
        if (next_pulse) n_next = n_next + 1;
        if (play_pulse) n_play = n_play + 1;
        if (stop_pulse) n_stop = n_stop + 1;
    end
    /* verilator lint_on BLKSEQ */

    integer errors = 0;
    integer checks = 0;
    integer k;

    task expect_counts;
        input integer wn, wp, ws;
        input [8*64-1:0] what;      // 中文一个字 3 字节，留够宽度免得截断成乱码
        begin
            checks = checks + 1;
            if (n_next !== wn || n_play !== wp || n_stop !== ws) begin
                $display("[按键] %0s：实测 next=%0d play=%0d stop=%0d，期望 %0d/%0d/%0d  **失败**",
                         what, n_next, n_play, n_stop, wn, wp, ws);
                errors = errors + 1;
            end else begin
                $display("  按键：%0s（next=%0d play=%0d stop=%0d）",
                         what, n_next, n_play, n_stop);
            end
        end
    endtask

    // 抖动着按一下：先来回抖 bounces 次（每次间隔远小于消抖时间），再连续按住的时长 hold_cyc
    task bouncy_press;
        input [1:0] which;
        input integer bounces;
        input integer hold_cyc;
        integer b;
        begin
            for (b = 0; b < bounces; b = b + 1) begin
                if (which == 0) key_next = ~key_next;
                if (which == 1) key_play = ~key_play;
                if (which == 2) key_stop = ~key_stop;
                repeat (1000) @(negedge clk);        // 20 µs，远小于 1 ms 的消抖
            end
            if (which == 0) key_next = 1'b1;
            if (which == 1) key_play = 1'b1;
            if (which == 2) key_stop = 1'b1;
            repeat (hold_cyc) @(negedge clk);
        end
    endtask

    task release_key;
        input [1:0] which;
        begin
            if (which == 0) key_next = 1'b0;
            if (which == 1) key_play = 1'b0;
            if (which == 2) key_stop = 1'b0;
            repeat (2 * DEB_CYC) @(negedge clk);     // 松开并且等消抖确认
        end
    endtask

    initial begin
        rst_n    = 1'b0;
        key_next = 1'b0;
        key_play = 1'b0;
        key_stop = 1'b0;
        n_next   = 0;
        n_play   = 0;
        n_stop   = 0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("本地按键仿真：抖动、按住、松开、循环选图形");
        $display("=========================================");

        // ---- 上电默认选中 CIRCLE(3) ----
        checks = checks + 1;
        if (sel !== 3'd3) begin
            $display("[按键] 上电默认图形应当是 CIRCLE(3)，实测 %0d  **失败**", sel);
            errors = errors + 1;
        end else begin
            $display("  上电：默认选中 CIRCLE（%0d），还没按过任何键（next=%0d play=%0d stop=%0d）",
                     sel, n_next, n_play, n_stop);
        end

        // ---- 1. 抖动 12 下再按住：只能算一次 ----
        bouncy_press(0, 12, DEB_CYC + 2000);
        expect_counts(1, 0, 0, "抖动 12 下再按住：只算一次");
        release_key(0);
        expect_counts(1, 0, 0, "松开不产生新动作");
        checks = checks + 1;
        if (sel !== 3'd4) begin
            $display("[按键] 按一次 NEXT 之后应当选中 SQUARE(4)，实测 %0d  **失败**", sel);
            errors = errors + 1;
        end else begin
            $display("  换图形：CIRCLE(3) → SQUARE(4)（只走了一格）");
        end

        // ---- 2. 按住不放很久：还是只有一次 ----
        bouncy_press(0, 0, 3 * DEB_CYC);
        expect_counts(2, 0, 0, "按住不放：不会一直重复触发");
        release_key(0);

        // ---- 3. 松开时间不够长就再按：第二下不算（防连击）----
        key_next = 1'b1;                         // 按住
        repeat (DEB_CYC + 2000) @(negedge clk);  // 确认按下
        key_next = 1'b0;
        repeat (DEB_CYC / 4) @(negedge clk);     // 只松开四分之一，消抖还没确认
        key_next = 1'b1;
        repeat (DEB_CYC + 2000) @(negedge clk);
        // 第一下算过一次（累计 3），第二下因为「松开没被确认」所以不该再算
        expect_counts(3, 0, 0, "松开时间不足再按：两下只算一次");
        release_key(0);

        // ---- 4. 三个键互不干扰 ----
        bouncy_press(1, 4, DEB_CYC + 2000);
        expect_counts(3, 1, 0, "按播放：只影响播放");
        release_key(1);
        bouncy_press(2, 6, DEB_CYC + 2000);
        expect_counts(3, 1, 1, "按停止：只影响停止");
        release_key(2);

        // ---- 5. NEXT 走满 7 个图形后回到开头 ----
        // 到这一步 sel 已经是 ARROW(6)，再按 4 次正好回到 CIRCLE(3)：6→0→1→2→3
        for (k = 0; k < 4; k = k + 1) begin
            bouncy_press(0, 2, DEB_CYC + 2000);
            release_key(0);
        end
        checks = checks + 1;
        if (sel !== 3'd3 || n_next !== 7) begin
            $display("[按键] 连按 4 次应当回到 CIRCLE(3) 且总次数 7，实测 sel=%0d 次数=%0d  **失败**",
                     sel, n_next);
            errors = errors + 1;
        end else begin
            $display("  循环：7 个预设图形循环选中，ARROW(6)→…→CIRCLE(3)（累计 7 次动作）");
        end

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
