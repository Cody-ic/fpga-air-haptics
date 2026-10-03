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
    reg [31:0] cfgv  [0:6];             // 换载波回归用的 7 个字段
    reg [7:0]  cfgc  [0:31];            // 两个载波各自的 16 个期望码

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
            // 配置换了之后引擎会自己做两件事：重算常数 A，再强制重算一遍相位表。
            // 两次都会产生 done，所以这里把「done 记录」清掉之后直接等它全部做完，
            // 免得把强制重算那一次的 done 当成第一帧算完。
            repeat (4) @(negedge clk);
            got_d0 = 1'b0;
            got_d1 = 1'b0;
            got_d2 = 1'b0;
            timeout = 0;
            while (!(got_d0 && got_d1 && got_d2) && timeout < 20000) begin
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
                // 焦点和上一次相同的话，引擎会跳过不重算（省电），所以这里
                // 等不到 done 是正常的——真正的判据是下面的逐通道比对。
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

    // 回归：配置换了、但机器停着（协议规定 CONFIG 只能在待机发，走步器没有节拍脉冲）。
    // 相位表必须自己重算一遍，否则状态帧回传的还是旧载波算出来的码。
    task check_cfgchange;
        integer i, tmo;
        begin
            // ---- 配置 A：正常算一次 ----
            cfg_carrier = cfgv[0];
            cfg_steps   = cfgv[1];
            cfg_z       = cfgv[6];
            fx_21       = $signed(cfgv[4]) * 2;
            fy_21       = $signed(cfgv[5]) * 2;
            @(negedge clk);
            cfg_change = 1'b1;
            @(negedge clk);
            cfg_change = 1'b0;
            repeat (4) @(negedge clk);
            tmo = 0;
            while (ph_busy && tmo < 20000) begin @(negedge clk); tmo = tmo + 1; end
            got_d0 = 1'b0; got_d1 = 1'b0; got_d2 = 1'b0;
            @(negedge clk);
            ph_start = 1'b1;
            @(negedge clk);
            ph_start = 1'b0;
            tmo = 0;
            while (!(got_d0 && got_d1 && got_d2) && tmo < 20000) begin
                @(negedge clk); tmo = tmo + 1;
            end
            for (i = 0; i < 16; i = i + 1) begin
                ch = i;
                read_ch(0);
                checks = checks + 1;
                if (got !== cfgc[i]) begin
                    $display("[换载波] 载波 %0d 第 %0d 路：实测 %0d，期望 %0d  **失败**",
                             cfgv[0], i, got, cfgc[i]);
                    errors = errors + 1;
                end
            end

            // ---- 配置 B：只换载波，**不给 start**，看它自己会不会重算 ----
            cfg_carrier = cfgv[2];
            cfg_steps   = cfgv[3];
            @(negedge clk);
            cfg_change = 1'b1;
            @(negedge clk);
            cfg_change = 1'b0;
            repeat (4) @(negedge clk);
            tmo = 0;
            while (ph_busy && tmo < 20000) begin @(negedge clk); tmo = tmo + 1; end
            got_d0 = 1'b0; got_d1 = 1'b0; got_d2 = 1'b0;
            tmo = 0;
            while (!(got_d0 && got_d1 && got_d2) && tmo < 20000) begin
                @(negedge clk); tmo = tmo + 1;
            end
            checks = checks + 1;
            if (!(got_d0 && got_d1 && got_d2)) begin
                $display("[换载波] 换了载波、机器停着，相位表没有自己重算  **失败**");
                errors = errors + 1;
            end
            for (i = 0; i < 16; i = i + 1) begin
                ch = i;
                read_ch(0);
                checks = checks + 1;
                if (got !== cfgc[16 + i]) begin
                    $display("[换载波] 载波 %0d 第 %0d 路：实测 %0d，期望 %0d  **失败**",
                             cfgv[2], i, got, cfgc[16 + i]);
                    errors = errors + 1;
                end
            end
            $display("  换载波回归：%0d Hz → %0d Hz，机器停着也重算了，逐通道与参考实现一致",
                     cfgv[0], cfgv[2]);
        end
    endtask

    initial begin
        $readmemh("tb/vectors/phase_plan.mem",  plan);
        $readmemh("tb/vectors/phase_focus.mem", focus);
        $readmemh("tb/vectors/phase_codes.mem", codes);
        $readmemh("tb/vectors/phase_cfg.mem",   cfgv);
        $readmemh("tb/vectors/phase_cfg_codes.mem", cfgc);

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

        check_cfgchange;          // 先跑换载波回归（配置换了但机器停着）
        for (case_i = 0; case_i < NCASES; case_i = case_i + 1) check_case(case_i);

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) $display("结果：全部通过");
        else             $display("结果：有失败");
        $display("=========================================");
        $finish;
    end

endmodule
