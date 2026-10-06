`timescale 1ns/1ps

// 8×8（64 路）端到端预演：板子还没到，先把「换成 64 路之后这条链还走得通吗」跑一遍。
//
// 和 tb_hap2_top 的区别只有两点：
//   1. 顶层参数换成 HW_ROWS=8 / HW_COLS=8 / PH_PIPE=8
//   2. 报文里的阵列字段是 8×8（换阵列上位机会重新握手，协议就是这么规定的）
//
// 检查四件事：握手声明 8×8、64 路的圆能配上、启动后焦点真的在动、
// 状态帧里的相位串正好是 64 个数。
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_top8;

    localparam integer CLK_HZ       = 50_000_000;
    localparam integer BAUD         = 115200;
    localparam integer CLK_NS       = 20;
    localparam integer CLKS_PER_BIT = CLK_HZ / BAUD;
    localparam integer PT_BITS      = 21;
    localparam integer ROWS         = 8;
    localparam integer COLS         = 8;
    localparam integer CH           = ROWS * COLS;

    reg clk;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;
    reg rst_n;

    reg  uart_line;
    wire uart_tx_pin;
    wire signed [PT_BITS-1:0] focus_x;
    wire scan_on, output_on, beat_pulse;
    /* verilator lint_off UNUSEDSIGNAL */
    wire signed [PT_BITS-1:0] focus_y;
    wire walk_running, mode_local;
    wire rx_err_dummy;
    wire [5:0] stroke_index;
    wire [CH-1:0] array_pos, array_neg;
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_top #(
        .CLK_HZ      (CLK_HZ),
        .BAUD        (BAUD),
        .HW_ROWS     (ROWS),
        .HW_COLS     (COLS),
        .HW_PITCH_UM (10000),
        .PH_PIPE     (8),          // 64 路建议 8 条并行流水线（4 条也够，但只剩 4% 余量）
        .KEY_DEB_MS  (2),
        .STATE_MS    (0)
    ) dut (
        .clk         (clk),
        .rst_n       (rst_n),
        .uart_rx_pin (uart_line),
        .uart_tx_pin (uart_tx_pin),
        .boot_id     (32'hA1B2C3D4),
        .key_next    (1'b0),
        .key_play    (1'b0),
        .key_stop    (1'b0),
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

    // ---------------- 板子发出来的字节 ----------------
    wire [7:0] board_byte;
    wire       board_byte_valid;
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_rx_board (
        .clk(clk), .rst_n(rst_n), .rx_line(uart_tx_pin),
        .rx_data(board_byte), .rx_valid(board_byte_valid), .rx_error(rx_err_dummy)
    );

    reg [7:0] txbuf [0:8191];
    integer   txlen, lf_count;
    always @(posedge clk) begin
        if (board_byte_valid) begin
            if (txlen < 8192) begin
                txbuf[txlen] <= board_byte;
                txlen        <= txlen + 1;
            end
            if (board_byte == 8'h0A) lf_count <= lf_count + 1;
        end
    end

    reg [7:0]  pkt   [0:8191];
    reg [31:0] tplan [0:63];
    integer    errors = 0, checks = 0, guard = 0;
    integer    case_i;

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

    task send_case;
        input integer idx;
        integer o, l, j;
        begin
            o = tplan[2*idx];
            l = tplan[2*idx + 1];
            for (j = 0; j < l; j = j + 1) send_byte(pkt[o + j]);
        end
    endtask

    task wait_frames;
        input integer n;
        begin
            guard = 0;
            while (lf_count < n && guard < 12_000_000) begin
                @(negedge clk);
                guard = guard + 1;
            end
            checks = checks + 1;
            if (lf_count < n) begin
                $display("[8×8 用例 %0d] 等应答超时：只收到 %0d 帧（期望 %0d）  **失败**",
                         case_i, lf_count, n);
                errors = errors + 1;
            end
        end
    endtask

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
                for (b = 0; b < nlen; b = b + 1)
                    if (txbuf[a + b] !== needle[8*(nlen-1-b) +: 8]) hit = 0;
                if (hit) ok = 1'b1;
            end
            checks = checks + 1;
            if (!ok) begin
                $display("[8×8 用例 %0d] 应答里找不到「%0s」  **失败**", case_i, needle);
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

    // 数 "phases=" 后面逗号分隔的数字个数（用逗号数 + 1）
    integer ph_count;
    localparam [8*7-1:0] K_PH = "phases=";
    task count_phases;
        integer a, b, hit, in_ph;
        begin
            ph_count = 0;
            in_ph    = 0;
            for (a = 0; a + 7 <= txlen; a = a + 1) begin
                hit = 1;
                for (b = 0; b < 7; b = b + 1)
                    if (txbuf[a + b] !== K_PH[8*7-1 - 8*b -: 8]) hit = 0;
                if (hit) begin
                    in_ph = 1;
                    b     = a + 7;
                    while (in_ph && b < txlen) begin
                        if (txbuf[b] == ",")                    ph_count = ph_count + 1;
                        else if (txbuf[b] == "*")               in_ph = 0;
                        b = b + 1;
                    end
                    if (ph_count > 0) ph_count = ph_count + 1;   // n 个逗号 = n+1 个数
                end
            end
        end
    endtask

    // 8×8 的走步器在跑：数节拍、看焦点动没动、扫描开关是否全程为 1
    integer w_beats, w_blank, w_xmin, w_xmax;
    task watch_walk;
        input integer cycles;
        integer c;
        begin
            w_beats = 0; w_blank = 0;
            w_xmin = 100000000; w_xmax = -100000000;
            for (c = 0; c < cycles; c = c + 1) begin
                @(negedge clk);
                if (beat_pulse) begin
                    w_beats = w_beats + 1;
                    if (!scan_on) w_blank = w_blank + 1;
                    if (focus_x < w_xmin) w_xmin = focus_x;
                    if (focus_x > w_xmax) w_xmax = focus_x;
                end
            end
            checks = checks + 1;
            if (w_beats == 0 || w_blank != 0) begin
                $display("[8×8 用例 %0d] 预设图形应当全程扫描：%0d 拍里 %0d 拍关着输出  **失败**",
                         case_i, w_beats, w_blank);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (w_xmax - w_xmin < 10000) begin
                $display("[8×8 用例 %0d] 焦点没怎么动：x %0d→%0d  **失败**",
                         case_i, w_xmin, w_xmax);
                errors = errors + 1;
            end else begin
                $display("  8×8 走步器：%0d 拍全程扫描，焦点 x %0d→%0d", w_beats, w_xmin, w_xmax);
            end
        end
    endtask

    initial begin
        $readmemh("tb/vectors/top8_packets.mem", pkt);
        $readmemh("tb/vectors/top8_plan.mem",    tplan);

        rst_n     = 1'b0;
        uart_line = 1'b1;
        txlen     = 0;
        lf_count  = 0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("8×8 端到端仿真：64 路的完整链路");
        $display("=========================================");

        // ---- 1. 握手：能力里必须声明 8×8 ----
        case_i = 0;
        clear_rx;
        send_case(0);
        wait_frames(2);
        found("HAP3 ACK 1 HELLO proto=3");
        found("hw_rows=8 hw_cols=8 hw_pitch_um=10000");
        found("max_channels=256");

        // ---- 2. 配一个 20 mm 的圆（64 路点表 + 节拍表）----
        case_i = 1;
        clear_rx;
        send_case(1);
        wait_frames(2);
        found("HAP3 ACK 2 CONFIG applied=1 rev=1");
        found("scan_paths=NONE");

        // ---- 3. 启动：焦点要沿圆动起来 ----
        case_i = 2;
        clear_rx;
        send_case(2);
        wait_frames(2);
        found("state=RUNNING");
        checks = checks + 1;
        if (output_on !== 1'b1) begin
            $display("[8×8 用例 %0d] 运行中输出应当是开的（scan_on=%b）  **失败**", case_i, scan_on);
            errors = errors + 1;
        end
        watch_walk(1200000);

        // ---- 4. 要一份状态：相位串必须正好 64 个数 ----
        case_i = 3;
        clear_rx;
        send_case(3);
        wait_frames(2);
        found("HAP3 ACK 4 SNAP");
        count_phases;
        checks = checks + 1;
        if (ph_count !== CH) begin
            $display("[8×8 用例 %0d] 相位串应当有 %0d 个数，实测 %0d  **失败**",
                     case_i, CH, ph_count);
            errors = errors + 1;
        end else begin
            $display("  8×8 状态帧：phases= 里正好 %0d 个相位码", ph_count);
        end

        // ---- 5. 停止 ----
        case_i = 4;
        clear_rx;
        send_case(4);
        wait_frames(2);
        found("state=IDLE");

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
