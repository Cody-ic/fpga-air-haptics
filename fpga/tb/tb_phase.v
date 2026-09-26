`timescale 1ns/1ps

// 相位计算的仿真测试台：把焦点交给 hap2_phase，逐通道和参考实现对拍。
//
// 期望值由 gen_vectors.py 调 desktop_app/model.py 的 focus_phases() 生成
// （golden.py 用的也是同一个函数），所以这一关过了，就说明板子算出来的相位码
// 和上位机认为的一样。
//
// 三份用例对应三种几何，各起一个实例：
//   4×4 间距 10 mm        —— 真板子的配置
//   8×8 间距 10 mm        —— 将来要扩的阵列
//   3×4 间距 12 mm、并行 3 —— 组首靠右，一组会跨到下一行，专门测那支补减
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_phase;

    localparam integer CLK_NS = 20;        // 50 MHz
    localparam integer NCASES = 3;

    reg clk, rst_n;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;

    // ---------------- 向量 ----------------
    // 每份 10 个字段：行 列 间距 并行 档数 载波 高度 帧数 焦点偏移 相位偏移
    reg [31:0] plan  [0:10*NCASES-1];
    reg [31:0] focus [0:63];            // 每帧三个数：fx_um fy_um z_um
    reg [7:0]  codes [0:511];           // 每帧 count 个相位码

    // ---------------- 三个实例 ----------------
    reg  [31:0] cfg_carrier, cfg_steps, cfg_z;
    reg         cfg_change;
    reg  signed [20:0] fx_21, fy_21;
    reg         ph_start;
    wire        ph_busy;
    wire        done0, done1, done2;
    wire        sel0, sel1, sel2;
    /* verilator lint_off UNUSEDSIGNAL */
    wire        bs1, bs2;
    wire [8:0]  cn0, cn1, cn2;
    /* verilator lint_on UNUSEDSIGNAL */
    /* verilator lint_off UNUSEDSIGNAL */
    wire signed [20:0] pf_x0, pf_y0, pf_x1, pf_y1, pf_x2, pf_y2;
    wire [31:0] pf_z0, pf_z1, pf_z2;
    /* verilator lint_on UNUSEDSIGNAL */
    reg  [7:0]  rd_addr;
    wire [7:0]  rd_data0, rd_data1, rd_data2;

    hap2_phase #(.ROWS(4), .COLS(4), .PITCH_UM(10000), .PIPE(4)) u_4x4 (
        .clk(clk), .rst_n(rst_n),
        .cfg_carrier_hz(cfg_carrier), .cfg_phase_steps(cfg_steps),
        .cfg_z_um(cfg_z), .cfg_change(cfg_change),
        .focus_x(fx_21), .focus_y(fy_21), .start(ph_start),
        .busy(ph_busy), .done(done0), .sweep_cnt(cn0), .pub_sel(sel0),
        .rd_sel(sel0), .rd_addr(rd_addr), .rd_data(rd_data0),
        .pub_fx(pf_x0), .pub_fy(pf_y0), .pub_fz_um(pf_z0)
    );

    hap2_phase #(.ROWS(8), .COLS(8), .PITCH_UM(10000), .PIPE(4)) u_8x8 (
        .clk(clk), .rst_n(rst_n),
        .cfg_carrier_hz(cfg_carrier), .cfg_phase_steps(cfg_steps),
        .cfg_z_um(cfg_z), .cfg_change(cfg_change),
        .focus_x(fx_21), .focus_y(fy_21), .start(ph_start),
        .busy(bs1), .done(done1), .sweep_cnt(cn1), .pub_sel(sel1),
        .rd_sel(sel1), .rd_addr(rd_addr), .rd_data(rd_data1),
        .pub_fx(pf_x1), .pub_fy(pf_y1), .pub_fz_um(pf_z1)
    );

    hap2_phase #(.ROWS(3), .COLS(4), .PITCH_UM(12000), .PIPE(3)) u_3x4 (
        .clk(clk), .rst_n(rst_n),
        .cfg_carrier_hz(cfg_carrier), .cfg_phase_steps(cfg_steps),
        .cfg_z_um(cfg_z), .cfg_change(cfg_change),
        .focus_x(fx_21), .focus_y(fy_21), .start(ph_start),
        .busy(bs2), .done(done2), .sweep_cnt(cn2), .pub_sel(sel2),
        .rd_sel(sel2), .rd_addr(rd_addr), .rd_data(rd_data2),
        .pub_fx(pf_x2), .pub_fy(pf_y2), .pub_fz_um(pf_z2)
    );

    // 三个实例各自什么时候算完，都记下来（不然后面的用例可能在读到一半时被偷跑）
    reg got_d0, got_d1, got_d2;
    always @(posedge clk) begin
        if (!rst_n) begin
            got_d0 <= 1'b0;
            got_d1 <= 1'b0;
            got_d2 <= 1'b0;
        end else begin
            if (done0) got_d0 <= 1'b1;
            if (done1) got_d1 <= 1'b1;
            if (done2) got_d2 <= 1'b1;
        end
    end

    integer errors = 0;
    integer checks = 0;
    integer case_i, frame_i, ch, timeout, stuck;
    integer rows, cols, pipe, steps, carrier, z_um, frames, f_off, c_off, count;
    reg [31:0] hx, hy;
    reg [7:0]  got, want;

    task read_ch;
        input integer which;
        begin
            rd_addr = ch[7:0];
            @(negedge clk);              // 同步读：给地址后一拍出数据
            case (which)
                0: got = rd_data0;
                1: got = rd_data1;
                default: got = rd_data2;
            endcase
        end
    endtask

    // 一份用例：配好常数 → 逐帧设焦点、等三个实例都算完、逐通道比对
    task check_case;
        input integer which;
        begin
            rows    = plan[10*which + 0];
            cols    = plan[10*which + 1];
            pipe    = plan[10*which + 3];
            steps   = plan[10*which + 4];
            carrier = plan[10*which + 5];
            z_um    = plan[10*which + 6];
            frames  = plan[10*which + 7];
            f_off   = plan[10*which + 8];
            c_off   = plan[10*which + 9];
            count   = rows * cols;

            // ---- 配置：载波/档数/高度，然后让常数重算 ----
            cfg_carrier = carrier;
            cfg_steps   = steps;
            cfg_z       = z_um;
            @(negedge clk);
            cfg_change = 1'b1;
            @(negedge clk);
            cfg_change = 1'b0;
            repeat (4) @(negedge clk);       // 等 busy 真的拉起来
            timeout = 0;
            while (ph_busy && timeout < 20000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            repeat (5) @(negedge clk);

            // ---- 逐帧 ----
            for (frame_i = 0; frame_i < frames; frame_i = frame_i + 1) begin
                hx = focus[f_off + 3*frame_i + 0];
                hy = focus[f_off + 3*frame_i + 1];
                fx_21 = $signed(hx) * 2;     // 微米 → 0.5 微米单位
                fy_21 = $signed(hy) * 2;
                got_d0 = 1'b0;
                got_d1 = 1'b0;
                got_d2 = 1'b0;
                @(negedge clk);
                ph_start = 1'b1;
                @(negedge clk);
                ph_start = 1'b0;
                timeout = 0;
                while (!(got_d0 && got_d1 && got_d2) && timeout < 20000) begin
                    @(negedge clk);
                    timeout = timeout + 1;
                end
                checks = checks + 1;
                if (!(got_d0 && got_d1 && got_d2)) begin
                    $display("[相位 %0d 第 %0d 帧] 三个实例没都算完（%b%b%b）  **失败**",
                             which, frame_i, got_d0, got_d1, got_d2);
                    errors = errors + 1;
                end
                stuck = 0;
                for (ch = 0; ch < count; ch = ch + 1) begin
                    want = codes[c_off + frame_i*count + ch];
                    read_ch(which);
                    checks = checks + 1;
                    if (got !== want) begin
                        if (stuck < 4) begin
                            $display("[相位 %0d 第 %0d 帧] 第 %0d 路：实测 %0d，期望 %0d  **失败**",
                                     which, frame_i, ch, got, want);
                        end
                        stuck = stuck + 1;
                        errors = errors + 1;
                    end
                end
                checks = checks + 1;
                if (stuck > 4) begin
                    $display("[相位 %0d 第 %0d 帧] 一共 %0d 路对不上", which, frame_i, stuck);
                end
            end
            $display("  用例 %0d：%0d×%0d（%0d 路，并行 %0d）、%0d 档、载波 %0d Hz、z=%0d mm、%0d 帧完成",
                     which, rows, cols, count, pipe, steps, carrier, z_um / 1000, frames);
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
        cfg_change  = 1'b0;
        fx_21       = 21'sd0;
        fy_21       = 21'sd0;
        ph_start    = 1'b0;
        rd_addr     = 8'd0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("相位计算仿真：焦点 → 每一路的相位码");
        $display("=========================================");

        for (case_i = 0; case_i < NCASES; case_i = case_i + 1) check_case(case_i);

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
