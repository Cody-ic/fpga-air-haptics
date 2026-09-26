`timescale 1ns/1ps

// 轨迹节拍表：把「点表 + 段表 + 配置」编译成一张「焦点什么时候走到哪里」的表。
//
// 用词提醒：这里说的「节拍表」说的是焦点的**运动时间安排**（每 10 µs 一拍），
// 跟 FPGA 工程里说的「时序」（信号能不能在一个时钟周期内稳定下来）不是一回事。
//
// 输出的每一行 = 一段「匀速直线移动」，按时间顺序排好：
//   mv_beats    这一段走多少拍（一拍 10 µs）
//   mv_step_x/y 每一拍 x / y 各走多少（定点小数，低 FRAC 位是小数部分）
//   mv_scan     这一段是扫描期还是抬笔跳转期（跳转期必须关输出）
//   mv_stroke   这一段属于第几笔
//
// 时间语义照抄参考实现 desktop_app/model.py 的 trajectory_sample()：
//   一圈周期   laps_beats  = 10^8 / repeat_millihz          拍
//   抬笔跳转   blank_beats = blank_us / 10                  拍
//   可扫描时长 active      = laps_beats − 笔数 × blank_beats （必须 > 0）
//   每笔权重   weight      = max(该笔路径总长, 0.1 mm)       ← 协议明文规定
//   每笔拍数   = active × weight / Σweight
//   笔内每段   = 该笔拍数 × 段长 / 该笔总长
//   每拍步进   = 该段 |dx|（或 |dy|）× 2^FRAC / 该段拍数
//
// 一笔扫描完，紧接着是「从这一笔末点直线走到下一笔首点」的抬笔跳转段
// （同样占一行，scan=0）。最后一笔的跳转段回到第一笔首点，于是整圈闭环。
//
// 分配拍数用「带余数累计」而不是各自取整：每次除法的余数带到下一次分配里，
// 于是 Σ每段拍数 = active 一拍不差，一圈正好 laps_beats 拍，长期不会跑偏。
//
// 分两趟走：第一趟只算各笔、各段的长度（要开方）；第二趟分配拍数、算步进
// （要除法）。全部算完才整体生效——中途发现任何问题就整份拒绝、保留旧配置。
module hap2_traj_plan #(
    parameter integer PT_BITS = 21,     // 坐标位宽（0.5 微米单位）
    parameter integer MAX_PTS = 256,    // 点表容量
    parameter integer MAX_STK = 32,     // 段（笔）表容量
    parameter integer FRAC    = 16      // 步进的小数位
) (
    input  wire                      clk,
    input  wire                      rst_n,
    // ---- 点表（来自 hap2_scan_parse；同步读，给地址后一拍出数据）----
    input  wire [8:0]                point_count,
    input  wire [5:0]                stroke_count,
    output reg  [7:0]                pt_addr,
    input  wire signed [PT_BITS-1:0] pt_x,
    input  wire signed [PT_BITS-1:0] pt_y,
    // ---- 段表（来自 hap2_scan_parse；组合读，同拍出数据）----
    output reg  [4:0]                st_addr,
    input  wire [8:0]                st_start,
    input  wire [8:0]                st_len,
    // ---- 配置 ----
    input  wire [31:0]               cfg_repeat_millihz,
    input  wire [31:0]               cfg_blank_us,
    // ---- 控制 ----
    input  wire                      start,     // 单拍脉冲：开始编译
    output reg                       busy,
    output reg                       done,      // 单拍：编译通过
    output reg                       fault,     // 单拍：这份配置不能用
    // ---- 全局量 ----
    output reg  [31:0]               laps_beats,
    output reg  [23:0]               blank_beats,
    output reg  [8:0]                move_count,
    output reg  signed [PT_BITS-1:0] start_x,   // 轨迹起点 = 第一笔的第一个点
    output reg  signed [PT_BITS-1:0] start_y,
    // ---- 移动表读口（给轨迹走步器）----
    input  wire [8:0]                rd_mv,
    output reg  [23:0]               mv_beats,
    output reg  signed [31:0]        mv_step_x,
    output reg  signed [31:0]        mv_step_y,
    output reg                       mv_scan,
    output reg  [5:0]                mv_stroke
);

    // 一拍 10 µs：一圈拍数 = (10^9 µs/秒 ÷ repeat_millihz) ÷ 10 = 10^8 / repeat_millihz
    localparam [31:0] TICKS_PER_X = 32'd100_000_000;
    localparam [31:0] TICK_US     = 32'd10;
    // 协议规定：单点或长度小于 0.1 mm 的一笔按 0.1 mm 权重分配（0.1 mm = 200 个 0.5 µm 单位）
    localparam [31:0] MIN_WEIGHT  = 32'd200;

    // 行数上限：每个点后跟一段，每笔末尾再加一条跳转段，单点笔还要多一条「原地停留」
    localparam integer MAX_MV = MAX_PTS + MAX_STK;

    // 注意点表是同步读：「给地址那一拍的下下拍」数据才有效（给地址的下一拍，
    // 数据寄存器的输出还是**上一个**地址的内容）。所以每次取点前都要空等一拍：
    //   S_P1_STK 发地址 -> S_P1_WAIT 空等 -> S_P1_TAK 收数据
    localparam [4:0] S_IDLE   = 5'd0;
    localparam [4:0] S_LOADST = 5'd1;    // 把段表抄进本地
    localparam [4:0] S_FIXED  = 5'd2;    // 一圈拍数 + 两条拒绝条件
    localparam [4:0] S_P1_STK = 5'd3;    // 第一趟：进入第 s 笔
    localparam [4:0] S_P1_WAIT= 5'd4;    // 第一趟：等点表数据
    localparam [4:0] S_P1_TAK = 5'd5;    // 第一趟：收一个点
    localparam [4:0] S_P1_LEN = 5'd6;    // 第一趟：等开方、记段长
    localparam [4:0] S_P2_STK = 5'd7;    // 第二趟：进入第 s 笔，先分配这一笔的拍数
    localparam [4:0] S_P2_STW = 5'd8;
    localparam [4:0] S_P2_SEG = 5'd9;    // 段内拍数
    localparam [4:0] S_P2_SGW = 5'd10;
    localparam [4:0] S_P2_SX  = 5'd11;   // 每拍 x 走多少
    localparam [4:0] S_P2_SXW = 5'd12;
    localparam [4:0] S_P2_SY  = 5'd13;   // 每拍 y 走多少
    localparam [4:0] S_P2_SYW = 5'd14;
    localparam [4:0] S_P2_WR  = 5'd15;   // 落一行
    localparam [4:0] S_P2_BLKA= 5'd16;   // 抬笔跳转段：发本笔末点地址
    localparam [4:0] S_P2_BLKB= 5'd17;   // 空等
    localparam [4:0] S_P2_BLKC= 5'd18;   // 收末点、发下一笔首点地址
    localparam [4:0] S_P2_BLKD= 5'd19;   // 空等
    localparam [4:0] S_P2_BX  = 5'd20;
    localparam [4:0] S_P2_BXW = 5'd21;
    localparam [4:0] S_P2_BY  = 5'd22;
    localparam [4:0] S_P2_BYW = 5'd23;
    localparam [4:0] S_DONE   = 5'd24;

    // ---- 段表本地副本（避开跨模块读的时序讲究，后面反复要用）----
    reg [8:0]  s_start [0:MAX_STK-1];
    reg [8:0]  s_npt   [0:MAX_STK-1];    // 这一笔有几个点
    reg [31:0] raw_len [0:MAX_STK-1];    // 这一笔的路径总长（0.5 µm 单位）

    // ---- 第一趟的中间结果（按「扫描小段」顺序编号）----
    reg [31:0]        len_tab [0:MAX_PTS-1];
    reg signed [31:0] dx_tab  [0:MAX_PTS-1];
    reg signed [31:0] dy_tab  [0:MAX_PTS-1];

    // ---- 输出：移动表 ----
    reg [23:0]        beats_tab [0:MAX_MV-1];
    reg signed [31:0] stx_tab   [0:MAX_MV-1];
    reg signed [31:0] sty_tab   [0:MAX_MV-1];
    reg               scan_tab  [0:MAX_MV-1];
    reg [5:0]         stroke_tab[0:MAX_MV-1];

    reg [4:0]  state;
    reg        fail;
    reg [5:0]  s;            // 当前第几笔
    reg [8:0]  j;            // 第一趟：笔内第几个点；第二趟：笔内第几小段
    reg [8:0]  gs;           // 第一趟：全局小段编号
    reg [8:0]  g2;           // 第二趟：全局小段编号
    reg [8:0]  mv_wr;        // 移动表写到第几行
    reg [31:0] total_w;      // 所有笔的权重之和
    reg [31:0] active_beats; // 一圈里用来扫描的拍数
    reg [31:0] rem_stroke;   // 笔与笔之间分配拍数留下的余数
    reg [31:0] rem_seg;      // 笔内段与段之间分配拍数留下的余数
    reg [23:0] bstroke;      // 当前这一笔分到多少拍
    reg [23:0] seg_beats;    // 当前这一小段分到多少拍
    reg signed [PT_BITS-1:0] prev_x, prev_y, cur_x, cur_y;
    reg signed [PT_BITS:0]   hold_dx, hold_dy;
    reg signed [PT_BITS-1:0] blk_fx, blk_fy;
    reg signed [PT_BITS:0]   blk_dx, blk_dy;
    reg [23:0] wr_beats;
    reg signed [31:0] wr_sx, wr_sy;
    reg        wr_scan;
    reg [1:0]  wr_next;      // 0 = 还有下一小段；1 = 接着写跳转段；2 = 换下一笔

    reg         sq_start;
    reg  [41:0] sq_value;
    wire        sq_done;
    wire [20:0] sq_result;

    // 除法单元给 56 位：最大的被除数是「可扫描拍数 × 一笔的权重」，约 4.3×10^15
    reg  [55:0] dv_numer;
    reg  [31:0] dv_denom;
    reg         dv_start;
    wire        dv_done, dv_div0;
    wire [55:0] dv_quot;
    /* verilator lint_off UNUSEDSIGNAL */
    // 余数只需要低 32 位（除数最多 32 位，余数一定小于除数）
    wire [55:0] dv_rem;
    /* verilator lint_on UNUSEDSIGNAL */

    fix_sqrt #(.IN_BITS(42), .OUT_BITS(21)) u_sqrt (
        .clk (clk), .rst_n (rst_n), .start (sq_start), .value (sq_value),
        .done (sq_done), .result (sq_result)
    );

    fix_div #(.NUM_BITS(56), .DEN_BITS(32)) u_div (
        .clk (clk), .rst_n (rst_n), .start (dv_start),
        .numer (dv_numer), .denom (dv_denom),
        .done (dv_done), .div0 (dv_div0), .quot (dv_quot), .rem (dv_rem)
    );

    // 移动表读口：同步读，给地址后一拍出数据
    always @(posedge clk) begin
        mv_beats  <= beats_tab [rd_mv];
        mv_step_x <= stx_tab   [rd_mv];
        mv_step_y <= sty_tab   [rd_mv];
        mv_scan   <= scan_tab  [rd_mv];
        mv_stroke <= stroke_tab[rd_mv];
    end

    // 当前这一小段的两轴差值、平方和（第一趟用）
    wire signed [PT_BITS:0]     dx_now = pt_x - prev_x;
    wire signed [PT_BITS:0]     dy_now = pt_y - prev_y;
    wire signed [2*PT_BITS+1:0] dx2    = dx_now * dx_now;
    wire signed [2*PT_BITS+1:0] dy2    = dy_now * dy_now;
    wire [41:0]                 sqsum  = dx2 + dy2;

    // 这一笔读完后的总长，以及它的权重（不足 0.1 mm 的按 0.1 mm 算）
    wire [31:0] raw_final    = raw_len[s[4:0]] + {{11{1'b0}}, sq_result};
    wire [31:0] weight_final = (raw_final < MIN_WEIGHT) ? MIN_WEIGHT : raw_final;
    // 分配阶段用的权重（此时 raw_len 已经累计完）
    wire [31:0] weight_s     = (raw_len[s[4:0]] < MIN_WEIGHT) ? MIN_WEIGHT
                                                              : raw_len[s[4:0]];

    // 除法单元是无符号的，所以先取绝对值，最后再把符号加回去
    wire signed [31:0] seg_dx     = dx_tab[g2];
    wire signed [31:0] seg_dy     = dy_tab[g2];
    wire [31:0]        abs_seg_dx = seg_dx[31] ? (~seg_dx + 32'd1) : seg_dx;
    wire [31:0]        abs_seg_dy = seg_dy[31] ? (~seg_dy + 32'd1) : seg_dy;
    wire [31:0]        blk_dy32   = {{11{blk_dy[PT_BITS]}}, blk_dy};
    wire [31:0]        abs_blk_dy = blk_dy[PT_BITS] ? (~blk_dy32 + 32'd1) : blk_dy32;
    // 本拍刚从点表取回的两个端点之差（还没打拍进寄存器）。
    // 跳转段的 x 除法要在**取回端点的同一拍**起算，用寄存器里那份会用到上一段的旧值。
    wire signed [PT_BITS:0] blk_dx_now = pt_x - blk_fx;
    wire signed [PT_BITS:0] blk_dy_now = pt_y - blk_fy;
    wire [31:0] blk_dx_now32 = {{11{blk_dx_now[PT_BITS]}}, blk_dx_now};
    wire [31:0] abs_blk_dx_now = blk_dx_now[PT_BITS] ? (~blk_dx_now32 + 32'd1)
                                                     : blk_dx_now32;

    // 协议显式要求的拒绝条件：blank_us × 笔数 × repeat_millihz ≥ 10^9
    // （意思是「一秒里所有抬笔时间加起来已经超过 1 秒」）。这条只在配置阶段算一次，
    // 所以用一个 64 位乘法换「和协议公式一字不差」，不占运行阶段的时序预算。
    wire [63:0] blank_budget = {32'h0, cfg_blank_us} * {58'h0, stroke_count}
                             * {32'h0, cfg_repeat_millihz};
    // 抬笔总时长（拍）
    wire [31:0] blank_total_beats = (cfg_blank_us / TICK_US) * {26'h0, stroke_count};

    always @(posedge clk) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            busy         <= 1'b0;
            done         <= 1'b0;
            fault        <= 1'b0;
            fail         <= 1'b0;
            s            <= 6'd0;
            j            <= 9'd0;
            gs           <= 9'd0;
            g2           <= 9'd0;
            mv_wr        <= 9'd0;
            st_addr      <= 5'd0;
            pt_addr      <= 8'd0;
            total_w      <= 32'd0;
            active_beats <= 32'd0;
            rem_stroke   <= 32'd0;
            rem_seg      <= 32'd0;
            bstroke      <= 24'd0;
            seg_beats    <= 24'd0;
            laps_beats   <= 32'd0;
            blank_beats  <= 24'd0;
            move_count   <= 9'd0;
            start_x      <= {PT_BITS{1'b0}};
            start_y      <= {PT_BITS{1'b0}};
            prev_x       <= {PT_BITS{1'b0}};
            prev_y       <= {PT_BITS{1'b0}};
            cur_x        <= {PT_BITS{1'b0}};
            cur_y        <= {PT_BITS{1'b0}};
            hold_dx      <= {(PT_BITS+1){1'b0}};
            hold_dy      <= {(PT_BITS+1){1'b0}};
            blk_fx       <= {PT_BITS{1'b0}};
            blk_fy       <= {PT_BITS{1'b0}};
            blk_dx       <= {(PT_BITS+1){1'b0}};
            blk_dy       <= {(PT_BITS+1){1'b0}};
            wr_beats     <= 24'd0;
            wr_sx        <= 32'sd0;
            wr_sy        <= 32'sd0;
            wr_scan      <= 1'b0;
            wr_next      <= 2'd0;
            sq_start     <= 1'b0;
            sq_value     <= 42'd0;
            dv_start     <= 1'b0;
            dv_numer     <= 56'd0;
            dv_denom     <= 32'd1;
        end else begin
            done     <= 1'b0;
            fault    <= 1'b0;
            sq_start <= 1'b0;
            dv_start <= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        busy       <= 1'b1;
                        fail       <= 1'b0;
                        s          <= 6'd0;
                        gs         <= 9'd0;
                        g2         <= 9'd0;
                        mv_wr      <= 9'd0;
                        total_w    <= 32'd0;
                        rem_stroke <= 32'd0;
                        // 先算一圈多少拍
                        dv_numer   <= {24'h0, TICKS_PER_X};
                        dv_denom   <= cfg_repeat_millihz;
                        dv_start   <= 1'b1;
                        state      <= S_LOADST;
                    end
                end

                // ---- 把段表抄进本地 ----
                S_LOADST: begin
                    // 一致性检查：这一笔的点必须落在点表范围内（段表坏了就别往下算）
                    if (s != 6'd0 && ({1'b0, st_start} + {1'b0, st_len}) > {1'b0, point_count}) begin
                        fail  <= 1'b1;
                        state <= S_DONE;
                    end else begin
                        if (s != 6'd0) begin
                            s_start[s[4:0]-5'd1] <= st_start;
                            s_npt  [s[4:0]-5'd1] <= st_len;
                        end
                        if (s >= {1'b0, stroke_count}) begin
                            state <= S_FIXED;
                        end else begin
                            st_addr <= s[4:0];
                            s       <= s + 6'd1;
                        end
                    end
                end

                // ---- 一圈拍数 + 拒绝条件 ----
                S_FIXED: begin
                    if (dv_done) begin
                        blank_beats <= cfg_blank_us / TICK_US;
                        if (stroke_count == 6'd0) begin
                            fail  <= 1'b1;                       // 没有任何一笔：兜底
                            state <= S_DONE;
                        end else if (dv_div0 || dv_quot[31:0] == 32'd0) begin
                            fail  <= 1'b1;                       // 一圈拍数为 0：兜底
                            state <= S_DONE;
                        end else if (blank_budget >= 64'd1_000_000_000) begin
                            fail  <= 1'b1;                       // 协议明文：一秒里装不下这么多抬笔
                            state <= S_DONE;
                        end else if (dv_quot[31:0] <= blank_total_beats) begin
                            fail  <= 1'b1;                       // 抬笔把一圈占满，没有扫描时间
                            state <= S_DONE;
                        end else begin
                            laps_beats   <= dv_quot[31:0];
                            active_beats <= dv_quot[31:0] - blank_total_beats;
                            s            <= 6'd0;
                            state        <= S_P1_STK;
                        end
                    end
                end

                // ================= 第一趟：算长度 =================
                S_P1_STK: begin
                    if (s >= {1'b0, stroke_count}) begin
                        s     <= 6'd0;
                        state <= S_P2_STK;
                    end else begin
                        raw_len[s[4:0]] <= 32'd0;
                        j       <= 9'd0;
                        pt_addr <= s_start[s[4:0]][7:0];         // 这一笔的首点
                        state   <= S_P1_WAIT;
                    end
                end

                S_P1_WAIT: begin
                    state <= S_P1_TAK;                           // 空等一拍，等点表数据到位
                end

                S_P1_TAK: begin
                    if (j == 9'd0) begin
                        prev_x <= pt_x;                          // 首点：先记下来
                        prev_y <= pt_y;
                        if (s == 6'd0) begin
                            start_x <= pt_x;                     // 轨迹起点
                            start_y <= pt_y;
                        end
                        if (s_npt[s[4:0]] < 9'd2) begin
                            // 单点笔：没有线段，但它照样按 0.1 mm 的权重参与分配
                            total_w <= total_w + MIN_WEIGHT;
                            s       <= s + 6'd1;
                            state   <= S_P1_STK;
                        end else begin
                            j       <= 9'd1;
                            pt_addr <= pt_addr + 8'd1;
                            state   <= S_P1_WAIT;
                        end
                    end else begin
                        cur_x    <= pt_x;                        // 这一小段的后一个端点
                        cur_y    <= pt_y;
                        hold_dx  <= dx_now;
                        hold_dy  <= dy_now;
                        sq_value <= sqsum;
                        sq_start <= 1'b1;
                        state    <= S_P1_LEN;
                    end
                end

                S_P1_LEN: begin
                    if (sq_done) begin
                        len_tab[gs]     <= {{11{1'b0}}, sq_result};
                        dx_tab [gs]     <= {{10{hold_dx[PT_BITS]}}, hold_dx};
                        dy_tab [gs]     <= {{10{hold_dy[PT_BITS]}}, hold_dy};
                        raw_len[s[4:0]] <= raw_final;
                        gs              <= gs + 9'd1;
                        prev_x          <= cur_x;
                        prev_y          <= cur_y;
                        if (j + 9'd1 >= s_npt[s[4:0]]) begin
                            total_w <= total_w + weight_final;   // 一笔读完，按笔加权重
                            s       <= s + 6'd1;
                            state   <= S_P1_STK;
                        end else begin
                            j       <= j + 9'd1;
                            pt_addr <= pt_addr + 8'd1;
                            state   <= S_P1_WAIT;
                        end
                    end
                end

                // ================= 第二趟：分配拍数、算步进 =================
                S_P2_STK: begin
                    if (s >= {1'b0, stroke_count}) begin
                        state <= S_DONE;
                    end else begin
                        j        <= 9'd0;
                        rem_seg  <= 32'd0;
                        dv_numer <= active_beats * weight_s + rem_stroke;
                        dv_denom <= total_w;
                        dv_start <= 1'b1;
                        state    <= S_P2_STW;
                    end
                end

                S_P2_STW: begin
                    if (dv_done) begin
                        if (dv_div0) begin
                            fail  <= 1'b1;                       // 权重和为 0：不该发生
                            state <= S_DONE;
                        end else begin
                            bstroke    <= dv_quot[23:0];
                            rem_stroke <= dv_rem[31:0];
                            if (s_npt[s[4:0]] < 9'd2) begin
                                // 单点笔：参考实现会让焦点停在这个点上一小段时间，
                                // 所以写一条「原地停留」的行（scan=1、步进为 0）。
                                // 停留时间短到不足一拍就不用写这一行了（位置本来就停在这儿）。
                                if (dv_quot[23:0] == 24'd0) begin
                                    state <= S_P2_BLKA;
                                end else begin
                                    wr_beats <= dv_quot[23:0];
                                    wr_sx    <= 32'sd0;
                                    wr_sy    <= 32'sd0;
                                    wr_scan  <= 1'b1;
                                    wr_next  <= 2'd1;
                                    state    <= S_P2_WR;
                                end
                            end else begin
                                state <= S_P2_SEG;
                            end
                        end
                    end
                end

                // 段内拍数 = 这一笔的拍数 × 段长 ÷ 这一笔总长（余数累计，加总不缺不多）
                S_P2_SEG: begin
                    dv_numer <= bstroke * len_tab[g2] + rem_seg;
                    dv_denom <= raw_len[s[4:0]];
                    dv_start <= 1'b1;
                    state    <= S_P2_SGW;
                end

                S_P2_SGW: begin
                    if (dv_done) begin
                        if (dv_div0) begin
                            fail  <= 1'b1;
                            state <= S_DONE;
                        end else if (dv_quot[23:0] == 24'd0) begin
                            // 这一小段连一拍都分不到：10 µs 的节拍跟不上这么碎的图形。
                            // 与其偷偷把它拉长（会让一圈的拍数对不上、长期跑偏），
                            // 不如整份拒绝，让上位机降低重复频率或简化图形。
                            fail  <= 1'b1;
                            state <= S_DONE;
                        end else begin
                            seg_beats <= dv_quot[23:0];
                            rem_seg   <= dv_rem[31:0];
                            state     <= S_P2_SX;
                        end
                    end
                end

                // 每拍 x 走多少 = |dx| × 2^FRAC ÷ 段拍数（加半个除数 = 四舍五入）
                S_P2_SX: begin
                    dv_numer <= ({24'h0, abs_seg_dx} << FRAC) + {33'h0, seg_beats[23:1]};
                    dv_denom <= {8'h0, seg_beats};
                    dv_start <= 1'b1;
                    state    <= S_P2_SXW;
                end

                S_P2_SXW: begin
                    if (dv_done) begin
                        if (|dv_quot[55:31]) begin
                            fail  <= 1'b1;                       // 一拍要走 16 mm 以上：这份图形没法用
                            state <= S_DONE;
                        end else begin
                            wr_sx <= seg_dx[31] ? -$signed(dv_quot[31:0])
                                                :  $signed(dv_quot[31:0]);
                            state <= S_P2_SY;
                        end
                    end
                end

                S_P2_SY: begin
                    dv_numer <= ({24'h0, abs_seg_dy} << FRAC) + {33'h0, seg_beats[23:1]};
                    dv_denom <= {8'h0, seg_beats};
                    dv_start <= 1'b1;
                    state    <= S_P2_SYW;
                end

                S_P2_SYW: begin
                    if (dv_done) begin
                        if (|dv_quot[55:31]) begin
                            fail  <= 1'b1;
                            state <= S_DONE;
                        end else begin
                            wr_sy    <= seg_dy[31] ? -$signed(dv_quot[31:0])
                                                   :  $signed(dv_quot[31:0]);
                            wr_beats <= seg_beats;
                            wr_scan  <= 1'b1;
                            g2       <= g2 + 9'd1;
                            if (j + 9'd1 >= s_npt[s[4:0]] - 9'd1) begin
                                wr_next <= 2'd1;                 // 这一笔的扫描段写完了
                            end else begin
                                j       <= j + 9'd1;
                                wr_next <= 2'd0;
                            end
                            state    <= S_P2_WR;
                        end
                    end
                end

                // ---- 抬笔跳转段：从本笔末点直线走到下一笔首点 ----
                S_P2_BLKA: begin
                    pt_addr <= s_start[s[4:0]] + s_npt[s[4:0]] - 9'd1;
                    state   <= S_P2_BLKB;
                end

                S_P2_BLKB: begin
                    state <= S_P2_BLKC;                              // 空等一拍
                end

                S_P2_BLKC: begin
                    blk_fx  <= pt_x;                                 // 本笔末点
                    blk_fy  <= pt_y;
                    pt_addr <= (s + 6'd1 >= {1'b0, stroke_count}) ? s_start[0][7:0]
                                                                  : s_start[s[4:0]+5'd1][7:0];
                    state   <= S_P2_BLKD;
                end

                S_P2_BLKD: begin
                    state <= S_P2_BX;                                // 空等一拍
                end

                S_P2_BX: begin
                    blk_dx   <= blk_dx_now;                          // 本拍 pt 已是下一笔首点
                    blk_dy   <= blk_dy_now;
                    dv_numer <= ({24'h0, abs_blk_dx_now} << FRAC) + {33'h0, blank_beats[23:1]};
                    dv_denom <= {8'h0, blank_beats};
                    dv_start <= 1'b1;
                    state    <= S_P2_BXW;
                end

                S_P2_BXW: begin
                    if (dv_done) begin
                        if (dv_div0 || |dv_quot[55:31]) begin
                            fail  <= 1'b1;
                            state <= S_DONE;
                        end else begin
                            wr_sx <= blk_dx[PT_BITS] ? -$signed(dv_quot[31:0])
                                                     :  $signed(dv_quot[31:0]);
                            state <= S_P2_BY;
                        end
                    end
                end

                S_P2_BY: begin
                    dv_numer <= ({24'h0, abs_blk_dy} << FRAC) + {33'h0, blank_beats[23:1]};
                    dv_denom <= {8'h0, blank_beats};
                    dv_start <= 1'b1;
                    state    <= S_P2_BYW;
                end

                S_P2_BYW: begin
                    if (dv_done) begin
                        if (dv_div0 || |dv_quot[55:31]) begin
                            fail  <= 1'b1;
                            state <= S_DONE;
                        end else begin
                            wr_sy    <= blk_dy[PT_BITS] ? -$signed(dv_quot[31:0])
                                                        :  $signed(dv_quot[31:0]);
                            wr_beats <= blank_beats;
                            wr_scan  <= 1'b0;                        // 跳转期间必须关输出
                            wr_next  <= 2'd2;
                            state    <= S_P2_WR;
                        end
                    end
                end

                // ---- 落一行 ----
                S_P2_WR: begin
                    beats_tab [mv_wr] <= wr_beats;
                    stx_tab   [mv_wr] <= wr_sx;
                    sty_tab   [mv_wr] <= wr_sy;
                    scan_tab  [mv_wr] <= wr_scan;
                    stroke_tab[mv_wr] <= s;
                    mv_wr <= mv_wr + 9'd1;
                    case (wr_next)
                        2'd0:    state <= S_P2_SEG;
                        2'd1:    state <= S_P2_BLKA;
                        default: begin
                                     s     <= s + 6'd1;
                                     state <= S_P2_STK;
                                 end
                    endcase
                end

                S_DONE: begin
                    busy       <= 1'b0;
                    move_count <= mv_wr;
                    if (fail) fault <= 1'b1;
                    else      done  <= 1'b1;
                    state      <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
