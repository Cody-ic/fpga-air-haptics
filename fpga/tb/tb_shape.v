`timescale 1ns/1ps

// 预设图形生成器的测试台：CIRCLE / SQUARE / TRIANGLE / LINE_X / LINE_Y / POINT / ARROW
// 生成出来的点表，逐点与参考实现（desktop_app/model.py 的 trajectory_point）比对。
//
// 期望顶点由 gen_vectors.py 按参考实现的折线顶点算出来（圆取 i/128 处的精确 cos/sin），
// 单位 0.5 µm。容差 ±3 个坐标单位（≈1.5 µm）：折线顶点用 Q16 定点乘半径，
// 圆是 128 边形，都会带一点点量化误差——比一个相位档（40 kHz、64 档时约 134 µm）小得多。
//
// 运行方式见 fpga/README.md：cd fpga && bash tb/run_verilator.sh
module tb_shape;

    localparam integer CLK_NS = 20;
    localparam integer NCASES = 7;
    localparam integer PT_BITS = 21;

    reg clk, rst_n;
    initial clk = 1'b0;
    always #(CLK_NS / 2.0) clk = ~clk;

    reg [31:0] plan [0:6*NCASES-1];   // 形状 半径 圆心x 圆心y 顶点数 偏移
    reg [31:0] pts  [0:1023];         // 期望顶点：x y x y …

    reg        shape_start;
    reg [2:0]  shape;
    reg [31:0] radius_um, cx_um, cy_um;

    wire                      sh_pt_wr_en, sh_st_wr_en, sh_counts_en;
    wire [7:0]                sh_pt_wr_addr;
    wire signed [PT_BITS-1:0] sh_pt_wr_x, sh_pt_wr_y;
    wire [4:0]                sh_st_wr_addr;
    wire [8:0]                sh_st_wr_start, sh_st_wr_len, sh_npt_in;
    wire [5:0]                sh_nst_in;
    /* verilator lint_off UNUSEDSIGNAL */
    wire                      sh_busy;    // 只在波形上看
    /* verilator lint_on UNUSEDSIGNAL */
    wire                      sh_done;

    reg  [7:0]                rd_addr;
    wire signed [PT_BITS-1:0] pt_x, pt_y;
    reg  [4:0]                rd_st_addr;
    wire [8:0]                st_start, st_len, tab_npt;
    wire [5:0]                tab_nst;

    hap2_shape #(.PT_BITS(PT_BITS)) u_shape (
        .clk(clk), .rst_n(rst_n),
        .start(shape_start), .shape(shape),
        .radius_um(radius_um), .cx_um(cx_um), .cy_um(cy_um),
        .pt_wr_en(sh_pt_wr_en), .pt_wr_addr(sh_pt_wr_addr),
        .pt_wr_x(sh_pt_wr_x), .pt_wr_y(sh_pt_wr_y),
        .st_wr_en(sh_st_wr_en), .st_wr_addr(sh_st_wr_addr),
        .st_wr_start(sh_st_wr_start), .st_wr_len(sh_st_wr_len),
        .counts_en(sh_counts_en), .npt_out(sh_npt_in), .nst_out(sh_nst_in),
        .busy(sh_busy), .done(sh_done)
    );

    hap2_pt_table #(.PT_BITS(PT_BITS)) u_tab (
        .clk(clk), .rst_n(rst_n),
        .wr_en(sh_pt_wr_en), .wr_addr(sh_pt_wr_addr),
        .wr_x(sh_pt_wr_x), .wr_y(sh_pt_wr_y),
        .st_wr_en(sh_st_wr_en), .st_wr_addr(sh_st_wr_addr),
        .st_wr_start(sh_st_wr_start), .st_wr_len(sh_st_wr_len),
        .counts_en(sh_counts_en), .npt_in(sh_npt_in), .nst_in(sh_nst_in),
        .rd_pt_addr(rd_addr), .rd_pt_x(pt_x), .rd_pt_y(pt_y),
        .rd_st_addr(rd_st_addr), .rd_st_start(st_start), .rd_st_len(st_len),
        .point_count(tab_npt), .stroke_count(tab_nst)
    );

    integer errors = 0;
    integer checks = 0;
    integer case_i, i, timeout, off, npt, shp;
    integer want_x, want_y, dx, dy;
    reg [31:0] wv;

    task check_case;
        input integer idx;
        begin
            shp       = plan[6*idx + 0];
            radius_um = plan[6*idx + 1];
            cx_um     = plan[6*idx + 2];
            cy_um     = plan[6*idx + 3];
            npt       = plan[6*idx + 4];
            off       = plan[6*idx + 5];

            shape = shp[2:0];
            @(negedge clk);
            shape_start = 1'b1;
            @(negedge clk);
            shape_start = 1'b0;
            timeout = 0;
            while (!sh_done && timeout < 20000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            checks = checks + 1;
            if (timeout >= 20000) begin
                $display("[形状 %0d] 生成超时  **失败**", shp);
                errors = errors + 1;
            end

            // 点数、段数、段表
            checks = checks + 1;
            if (tab_npt !== npt[8:0] || tab_nst !== 6'd1) begin
                $display("[形状 %0d] 计数不对：点数 %0d（期望 %0d）、段数 %0d（期望 1）  **失败**",
                         shp, tab_npt, npt, tab_nst);
                errors = errors + 1;
            end
            checks = checks + 1;
            if (st_start !== 9'd0 || st_len !== npt[8:0]) begin
                $display("[形状 %0d] 段表不对：(起点 %0d, 长度 %0d)，期望 (0,%0d)  **失败**",
                         shp, st_start, st_len, npt);
                errors = errors + 1;
            end

            // 逐点比对
            for (i = 0; i < npt; i = i + 1) begin
                // 向量里存的是 21 位补码，读出来要按 21 位做符号扩展
                wv     = pts[2*(off + i) + 0];
                want_x = $signed(wv[20:0]);
                wv     = pts[2*(off + i) + 1];
                want_y = $signed(wv[20:0]);
                rd_addr = i[7:0];
                @(negedge clk);                 // 同步读：给地址后一拍出数据
                dx = pt_x - want_x;
                dy = pt_y - want_y;
                if (dx < 0) dx = -dx;
                if (dy < 0) dy = -dy;
                checks = checks + 1;
                if (dx > 3 || dy > 3) begin
                    $display("[形状 %0d] 第 %0d 个顶点：实测 (%0d,%0d)，期望 (%0d,%0d)  **失败**",
                             shp, i, pt_x, pt_y, want_x, want_y);
                    errors = errors + 1;
                end
            end
            $display("  形状 %0d：%0d 个顶点，逐点与参考实现一致（±3 个 0.5 µm 单位）", shp, npt);
        end
    endtask

    initial begin
        $readmemh("tb/vectors/shape_plan.mem", plan);
        $readmemh("tb/vectors/shape_pts.mem",  pts);

        rst_n       = 1'b0;
        shape_start = 1'b0;
        shape       = 3'd0;
        radius_um   = 32'd20000;
        cx_um       = 32'd0;
        cy_um       = 32'd0;
        rd_addr     = 8'd0;
        rd_st_addr  = 5'd0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("预设图形仿真：形状 → 点表 → 与参考实现逐点对拍");
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
