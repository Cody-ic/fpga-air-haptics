`timescale 1ns/1ps

// 板级顶层的仿真测试台：专测「只会在真板子上才出问题」的那几件事。
//
//   1. 上电复位：复位没放开之前，所有驱动输出必须是低电平（换能器不能被驱动）
//   2. 复位放开之后，板子能正常握手（走一遍真实的 HELLO 报文）
//   3. 复位键再按一次 → 复位标识 boot 必须**变**（协议要求），而且变化后还能重新握手
//   4. 复位期间按键/串口都别乱动：输出一直是低的
//
// 说明：这里把 POR_MS 缩成 1 ms，不然仿真要跑几十毫秒才放开复位。
// 逻辑一模一样，只是常数不同。真实板子上是 10 ms。
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_board;

    localparam integer CLK_HZ       = 50_000_000;
    localparam integer BAUD         = 115200;
    localparam integer CLK_NS       = 20;            // 50 MHz
    localparam integer CLKS_PER_BIT = CLK_HZ / BAUD; // 434
    localparam integer POR_MS       = 1;             // 仿真里缩到 1 ms
    localparam integer ARRAY_N      = 16;            // 4×4

    reg clk;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;

    reg  key_rst_n, key0_n, key1_n, key2_n;
    reg  uart_line;
    wire uart_tx_pin;
    wire [ARRAY_N-1:0] array_pos;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [4:0] led;
    /* verilator lint_on UNUSEDSIGNAL */

    board_top #(
        .CLK_HZ      (CLK_HZ),
        .BAUD        (BAUD),
        .HW_ROWS     (4),
        .HW_COLS     (4),
        .HW_PITCH_UM (10000),
        .POR_MS      (POR_MS),
        .KEY_DEB_MS  (2),          // 仿真里按键消抖也缩短（逻辑相同）
        .STATE_MS    (0)           // 关掉主动上报，好一条一条数报文
    ) dut (
        .clk         (clk),
        .key_rst_n   (key_rst_n),
        .key0_n      (key0_n),
        .key1_n      (key1_n),
        .key2_n      (key2_n),
        .uart_rx_pin (uart_line),
        .uart_tx_pin (uart_tx_pin),
        .array_pos   (array_pos),
        .led         (led)
    );

    // ---------------- 板子发出来的字节 ----------------
    wire [7:0] board_byte;
    wire       board_byte_valid;
    /* verilator lint_off UNUSEDSIGNAL */
    wire       rx_err_dummy;
    /* verilator lint_on UNUSEDSIGNAL */
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_rx_board (
        .clk(clk), .rst_n(1'b1), .rx_line(uart_tx_pin),
        .rx_data(board_byte), .rx_valid(board_byte_valid),
        .rx_error(rx_err_dummy)
    );

    reg [7:0]  txbuf [0:8191];
    integer    txlen;
    integer    lf_count;
    always @(posedge clk) begin
        if (board_byte_valid) begin
            if (txlen < 8192) begin
                txbuf[txlen] <= board_byte;
                txlen        <= txlen + 1;
            end
            if (board_byte == 8'h0A) lf_count <= lf_count + 1;
        end
    end

    // ---------------- 向量：直接用端到端那套报文里的 HELLO ----------------
    reg [7:0]  pkt   [0:8191];
    reg [31:0] tplan [0:63];

    integer errors = 0;
    integer checks = 0;
    integer k, guard;

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

    // 发第 idx 条向量报文（top_packets.mem 就是端到端那套，第 0 条是 HELLO）
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
                $display("[板级] 等应答超时：只收到 %0d 帧（期望 %0d）  **失败**", lf_count, n);
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
                $display("[板级] 应答里找不到「%0s」  **失败**", needle);
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

    // 把 "boot=XXXXXXXX" 里的 8 位十六进制读出来，放进 boot_val
    integer boot_val;
    localparam [8*5-1:0] K_BOOT = "boot=";
    task read_boot;
        integer a, b, hit;
        reg [7:0] ch;
        begin
            boot_val = -1;
            for (a = 0; a + 5 <= txlen; a = a + 1) begin
                hit = 1;
                for (b = 0; b < 5; b = b + 1)
                    if (txbuf[a + b] !== K_BOOT[8*5-1 - 8*b -: 8]) hit = 0;
                if (hit && boot_val < 0) begin
                    boot_val = 0;
                    for (b = 0; b < 8; b = b + 1) begin
                        ch = txbuf[a + 5 + b];
                        boot_val = boot_val * 16;
                        if (ch >= "0" && ch <= "9") boot_val = boot_val + (ch - "0");
                        else                         boot_val = boot_val + 10 + (ch - "A");
                    end
                end
            end
            checks = checks + 1;
            if (boot_val <= 0) begin
                $display("[板级] 报文里没有合法的 boot 标识（实测 %0d）  **失败**", boot_val);
                errors = errors + 1;
            end
        end
    endtask

    // 按住复位键 ms 毫秒（高有效的是按键本身，这里直接驱动低有效的引脚）
    task press_reset;
        input integer ms;
        integer n;
        begin
            key_rst_n = 1'b0;
            for (n = 0; n < ms * (CLK_HZ/1000); n = n + 1) @(negedge clk);
        end
    endtask

    integer boot1, boot2;

    initial begin
        $readmemh("tb/vectors/top_packets.mem", pkt);
        $readmemh("tb/vectors/top_plan.mem",    tplan);

        // 上电：按键都是松开的（低有效 → 高电平）
        key_rst_n = 1'b1;
        key0_n    = 1'b1;
        key1_n    = 1'b1;
        key2_n    = 1'b1;
        uart_line = 1'b1;
        txlen     = 0;
        lf_count  = 0;
        boot1     = 0;
        boot2     = 0;

        $display("=========================================");
        $display("板级仿真：上电复位 + 复位标识 + 握手");
        $display("=========================================");

        // ---- 1. 上电复位期间：输出必须一直是低的 ----
        // POR_MS=1 ms，前 0.5 ms 复位一定还没放开
        checks = checks + 1;
        guard  = 0;
        for (k = 0; k < 25000; k = k + 1) begin
            @(negedge clk);
            if (array_pos !== {ARRAY_N{1'b0}}) guard = 1;
        end
        if (guard) begin
            $display("[板级] 上电复位期间驱动输出不是低电平  **失败**");
            errors = errors + 1;
        end else begin
            $display("  上电复位：前 0.5 ms 里 16 路输出全部保持低电平");
        end

        // ---- 2. 等复位放开，握手一次 ----
        repeat (60000) @(negedge clk);        // 1 ms 复位 + 余量
        clear_rx;
        send_case(0);                         // HELLO
        wait_frames(2);                       // ACK + STATE
        found("HAP3 ACK 1 HELLO proto=3");
        found("device=FPGA ");
        found("simulated=0");
        read_boot;
        boot1 = boot_val;
        if (boot1 > 0)
            $display("  第一次上电：握手成功，boot=%08X（非 0）", boot1);

        // ---- 3. 按一下复位键：输出重新变低，放开后 boot 必须变 ----
        clear_rx;
        press_reset(2);                       // 按住 2 ms（比 POR 的 1 ms 长）
        // 复位期间：输出必须低
        checks = checks + 1;
        guard  = 0;
        for (k = 0; k < 20000; k = k + 1) begin
            @(negedge clk);
            if (array_pos !== {ARRAY_N{1'b0}}) guard = 1;
        end
        if (guard) begin
            $display("[板级] 按复位键之后输出没关掉  **失败**");
            errors = errors + 1;
        end else begin
            $display("  复位键：按下之后 16 路输出立刻回到低电平");
        end
        key_rst_n = 1'b1;                     // 松开 → POR 再等 1 ms 才放开
        repeat (60000) @(negedge clk);

        clear_rx;
        send_case(0);                         // 再握手一次
        wait_frames(2);
        found("HAP3 ACK 1 HELLO proto=3");
        read_boot;
        boot2 = boot_val;
        checks = checks + 1;
        if (boot2 == boot1 || boot2 <= 0) begin
            $display("[板级] 复位之后 boot 应当改变：第一次 %08X，第二次 %08X  **失败**",
                     boot1, boot2);
            errors = errors + 1;
        end else begin
            $display("  复位标识：第一次 %08X → 第二次 %08X（每次复位都变）", boot1, boot2);
        end

        // ---- 4. 复位放开之后输出仍然是关的（没人下令就不驱动换能器）----
        checks = checks + 1;
        if (array_pos !== {ARRAY_N{1'b0}}) begin
            $display("[板级] 复位放开之后没人下命令，输出却已经在动  **失败**");
            errors = errors + 1;
        end else begin
            $display("  复位释放：没有配置/启动命令时，输出保持关闭");
        end

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
