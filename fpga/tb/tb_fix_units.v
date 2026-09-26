// 算术单元的仿真测试台：整数开方 + 无符号除法。
//
// 这两个单元是轨迹发生器（算段长、算拍数）和相位计算（算距离、算相位码）
// 都要用的地基，所以单独测、测细一点。
//
// 运行方式见 fpga/README.md：
//   cd fpga && bash tb/run_verilator.sh
`timescale 1ns/1ps

module tb_fix_units;

    localparam integer CLK_NS   = 20;    // 50 MHz
    localparam integer IN_BITS  = 42;
    localparam integer OUT_BITS = 21;
    localparam integer NUM_BITS = 42;
    localparam integer DEN_BITS = 32;

    reg clk;
    reg rst_n;
    always #(CLK_NS / 2.0) clk = ~clk;

    // ---------------- 开方 ----------------
    reg                 sq_start;
    reg  [IN_BITS-1:0]  sq_value;
    wire                sq_done;
    wire [OUT_BITS-1:0] sq_result;

    fix_sqrt #(.IN_BITS(IN_BITS), .OUT_BITS(OUT_BITS)) u_sqrt (
        .clk (clk), .rst_n (rst_n), .start (sq_start), .value (sq_value),
        .done (sq_done), .result (sq_result)
    );

    // ---------------- 除法 ----------------
    reg                 dv_start;
    reg  [NUM_BITS-1:0] dv_numer;
    reg  [DEN_BITS-1:0] dv_denom;
    wire                dv_done, dv_div0;
    wire [NUM_BITS-1:0] dv_quot, dv_rem;

    fix_div #(.NUM_BITS(NUM_BITS), .DEN_BITS(DEN_BITS)) u_div (
        .clk (clk), .rst_n (rst_n), .start (dv_start),
        .numer (dv_numer), .denom (dv_denom),
        .done (dv_done), .div0 (dv_div0),
        .quot (dv_quot), .rem (dv_rem)
    );

    integer errors = 0;
    integer checks = 0;
    integer timeout;

    task check_sqrt;
        input [IN_BITS-1:0]  v;
        input [OUT_BITS-1:0] want;
        begin
            sq_value = v;
            sq_start = 1'b1;
            @(negedge clk);
            sq_start = 1'b0;
            timeout = 0;
            while (!sq_done && timeout < 1000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            checks = checks + 1;
            if (sq_result !== want) begin
                $display("[开方] 输入 %0d，实测 %0d，期望 %0d  **失败**", v, sq_result, want);
                errors = errors + 1;
            end else begin
                $display("[开方] 输入 %13d -> %7d  正确", v, sq_result);
            end
        end
    endtask

    task check_div;
        input [NUM_BITS-1:0] a;
        input [DEN_BITS-1:0] b;
        input [NUM_BITS-1:0] want_q;
        input [NUM_BITS-1:0] want_r;
        begin
            dv_numer = a;
            dv_denom = b;
            dv_start = 1'b1;
            @(negedge clk);
            dv_start = 1'b0;
            timeout = 0;
            while (!dv_done && timeout < 1000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            checks = checks + 1;
            if (dv_quot !== want_q || dv_rem !== want_r) begin
                $display("[除法] %0d / %0d 实测 %0d 余 %0d，期望 %0d 余 %0d  **失败**",
                         a, b, dv_quot, dv_rem, want_q, want_r);
                errors = errors + 1;
            end else begin
                $display("[除法] %13d / %10d = %10d 余 %6d  正确", a, b, dv_quot, dv_rem);
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        sq_start = 1'b0; sq_value = 0;
        dv_start = 1'b0; dv_numer = 0; dv_denom = 0;

        rst_n = 1'b0;
        repeat (5) @(negedge clk);
        rst_n = 1'b1;
        repeat (5) @(negedge clk);

        $display("=========================================");
        $display("算术单元仿真");
        $display("=========================================");

        // 开方的边界与典型值：第一个参数是输入，第二个是期望结果
        check_sqrt(42'd0,              21'd0);
        check_sqrt(42'd1,              21'd1);
        check_sqrt(42'd2,              21'd1);
        check_sqrt(42'd3,              21'd1);
        check_sqrt(42'd4,              21'd2);
        check_sqrt(42'd9,              21'd3);
        check_sqrt(42'd1000000,        21'd1000);
        // 典型的两轴平方和：dx²+dy²
        check_sqrt(42'd400000000000,   21'd632455);
        // 三轴平方和的上限：3 × 600000²
        check_sqrt(42'd1080000000000,  21'd1039230);
        // 42 位全 1（最大输入）；注意 42 位要写成 11 位十六进制：0x3FFFFFFFFFF
        check_sqrt(42'h3FFFFFFFFFF,    21'd2097151);

        $display("-----------------------------------------");
        check_div(42'd100,          32'd7,          42'd14,         42'd2);
        check_div(42'd100000000,    32'd500,        42'd200000,     42'd0);
        check_div(42'd10000000,     32'd3,          42'd3333333,    42'd1);
        check_div(42'd2097151,      32'd1,          42'd2097151,    42'd0);
        check_div(42'd5,            32'd10,         42'd0,          42'd5);
        check_div(42'd123456789,    32'd1000,       42'd123456,     42'd789);
        check_div(42'h3FFFFFFFFFF,  32'hFFFFFFFF,   42'd1024,       42'd1023);

        // 除数为 0：不算，只把 div0 拉高
        dv_numer = 42'd123;
        dv_denom = 32'd0;
        dv_start = 1'b1;
        @(negedge clk);
        dv_start = 1'b0;
        timeout = 0;
        while (!dv_done && timeout < 1000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        checks = checks + 1;
        if (dv_div0 !== 1'b1) begin
            $display("[除法] 除数为 0 时没有给出标记  **失败**");
            errors = errors + 1;
        end else begin
            $display("[除法] 除数为 0 时正确给出标记，不做除法");
        end

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
