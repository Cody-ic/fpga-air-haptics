`timescale 1ns/1ps

// 多段草图子系统：把「从串口收到的一行文本」变成「焦点每 10 µs 走到哪里」。
//
// 里面装了三块，按顺序串起来：
//   1. hap2_scan_parse   解析草图文案 → 点表 + 段表
//   2. hap2_traj_plan    点表 + 段表 + 配置 → 节拍表（开方、除法都在这边）
//   3. hap2_traj_walk    节拍表 → 每拍的焦点坐标、扫描开关、笔号
// 另外顺手把草图的**原文**抄一份存起来：因为 STATE 回传里要带上 `scan_paths`
// 原样字符串（上位机靠它认图形），而行缓冲下一帧就被覆盖了。
//
// 时序上是「解析完毕 → 编译完毕 → 才开始走」，由命令层按三个脉冲依次触发：
// parse_start → （等 parse_ok）→ plan_start → （等 plan_done）→ walk_start。
module hap2_traj_scan #(
    parameter integer ADDR_W   = 13,     // 行缓冲地址位宽（8192 字节）
    parameter integer PT_BITS  = 21,     // 坐标位宽（0.5 µm 单位）
    parameter integer MAX_PTS  = 256,
    parameter integer MAX_STK  = 32,
    parameter integer MAX_TXT  = 4096,   // 草图原文最长多少字节
    parameter integer FRAC     = 16,
    parameter integer TICK_CYC = 500,
    // 原文缓冲：上下两半各 4 KB。解析时写「没在对外的」那一半，配置被接受了
    // 才把对外的那一半切成它（换一个指针而已，不用搬运字节）。这样被拒绝的
    // 配置不会污染回传里那份 scan_paths。
    parameter integer TXT_AW   = 13
) (
    input  wire                      clk,
    input  wire                      rst_n,

    // ---- 来自命令层 ----
    input  wire                      parse_start,  // 单拍：解析当前行缓冲里的草图
    /* verilator lint_off UNUSEDSIGNAL */
    // 行缓冲最多 8 KB，而草图文案有 4 KB 上限，src_off 的最高位用不到
    input  wire [ADDR_W:0]           src_off,
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [ADDR_W:0]           src_len,
    input  wire                      plan_start,   // 单拍：把点表／段表编译成节拍表
    // ---- 预设图形（CIRCLE / SQUARE / ... ）----
    // preset=1 表示这次生效的是预设图形：点表由 hap2_shape 生成，而且没有抬笔时间
    input  wire                      preset,
    input  wire                      shape_start,  // 单拍：开始生成预设图形的点表
    input  wire [2:0]                shape,        // 和字段解析层一致
    input  wire [31:0]               cfg_radius_um,
    input  wire [31:0]               cfg_cx_um,
    input  wire [31:0]               cfg_cy_um,
    output wire                      shape_busy,
    output wire                      shape_done,   // 单拍：点表生成完了
    // ---- 图形包围盒（给命令层查「有没有越出工作空间」）----
    // 单位 0.5 µm，和点表一致。草图取解析器算出来的；预设图形直接由
    // 中心 ± 半径 推出来（每种形状都伸不出这个范围）。bb_vld=0 表示这份配置
    // 没有图形（scan_paths=NONE），那就没有可检查的包围盒。
    output wire                      bb_vld,
    output wire signed [PT_BITS-1:0] bb_xmin,
    output wire signed [PT_BITS-1:0] bb_xmax,
    output wire signed [PT_BITS-1:0] bb_ymin,
    output wire signed [PT_BITS-1:0] bb_ymax,
    // 单拍：这份配置被接受了，可以把抄下来的原文公布出去（STATE 里要回传它）。
    // 被拒绝的配置不能公布，否则回传的 scan_paths 和实际生效的图形对不上。
    input  wire                      txt_commit,
    // 和 txt_commit 同时给出：这次生效的配置里**没有草图**（预设图形，或 NONE），
    // 那回传的 scan_paths 就应当老老实实写 NONE
    input  wire                      txt_none,
    // 命令层说「这份已生效的配置有没有可用的节拍表」。注意它和 txt_none 不是一回事：
    // 预设图形没有草图文案（txt_none=1），但它**有**轨迹表。
    input  wire                      traj_ready_in,
    // 节拍表要用的是**影子配置**里的这两个值（编译在配置生效之前发生）
    input  wire [31:0]               cfg_repeat_millihz,
    input  wire [31:0]               cfg_blank_us,
    output wire                      parse_busy,
    output wire                      parse_ok,     // 单拍
    output wire                      parse_bad,    // 单拍
    output wire                      is_none,      // 原文就是 NONE（这份配置没有草图）
    output wire                      plan_busy,
    output wire                      plan_done,    // 单拍：节拍表算好了
    output wire                      plan_fault,   // 单拍：这份图形／配置不能用
    output wire [8:0]                point_count,
    output wire [5:0]                stroke_count,

    // ---- 行缓冲读口（解析时用，同步读）----
    output wire [ADDR_W-1:0]         rd_addr,
    input  wire [7:0]                rd_data,

    // ---- 走步器控制 ----
    input  wire                      walk_start,
    input  wire                      walk_hold,
    input  wire                      walk_stop,
    output wire signed [PT_BITS-1:0] focus_x,
    output wire signed [PT_BITS-1:0] focus_y,
    output wire                      scan_on,
    output wire [5:0]                stroke_index,
    output wire                      beat_pulse,
    output wire                      walk_running,

    // ---- 草图原文（给应答模块回传 scan_paths）----
    // 地址只到「一半」：对外那份永远从这一半的开头数起，用不着知道在哪一半
    input  wire [TXT_AW-2:0]         txt_addr,
    output reg  [7:0]                txt_data,
    output wire [ADDR_W:0]           txt_len,
    output wire                      txt_valid
);

    // ---- 解析器 ----
    wire [ADDR_W-1:0]         parse_rd_addr;
    wire [4:0]                st_addr;
    wire [8:0]                st_start, st_len;
    wire signed [PT_BITS-1:0] pt_x, pt_y;
    wire                      sp_bb_vld;
    wire signed [PT_BITS-1:0] sp_bb_xmin, sp_bb_xmax, sp_bb_ymin, sp_bb_ymax;
    reg  [7:0]                pt_addr;

    hap2_scan_parse #(
        .ADDR_W  (ADDR_W),
        .PT_BITS (PT_BITS),
        .MAX_PTS (MAX_PTS),
        .MAX_STK (MAX_STK),
        .MAX_TXT (MAX_TXT)
    ) u_parse (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (parse_start),
        .src_off      (src_off),
        .src_len      (src_len),
        .rd_addr      (parse_rd_addr),
        .rd_data      (rd_data),
        .busy         (parse_busy),
        .ok           (parse_ok),
        .bad          (parse_bad),
        .point_count  (point_count),
        .stroke_count (stroke_count),
        .is_none      (is_none),
        .bb_vld       (sp_bb_vld),
        .bb_xmin      (sp_bb_xmin),
        .bb_xmax      (sp_bb_xmax),
        .bb_ymin      (sp_bb_ymin),
        .bb_ymax      (sp_bb_ymax),
        .pt_addr      (pt_addr),
        .pt_x         (pt_x),
        .pt_y         (pt_y),
        .st_addr      (st_addr),
        .st_start     (st_start),
        .st_len       (st_len)
    );

    assign rd_addr = parse_rd_addr;

    // ---- 顺手把原文抄一份 ----
    // 解析器读哪个字节，我们就存哪个字节：rd_data 比 rd_addr 晚一拍有效，
    // 所以地址也要跟着打一拍，两边才对得上（同一个字节会被写两次，值一样，无害）。
    reg [TXT_AW-2:0] copy_addr_q;
    reg [7:0]        txt_ram [0:(1 << TXT_AW)-1];
    reg [ADDR_W:0]   txt_len_r;
    reg              txt_valid_r;
    reg              txt_sel;      // 0 = 对外的是低 4 KB，1 = 高 4 KB

    always @(posedge clk) begin
        if (!rst_n) begin
            copy_addr_q <= {(TXT_AW-1){1'b0}};
            txt_len_r   <= {ADDR_W+1{1'b0}};
            txt_valid_r <= 1'b0;
            txt_sel     <= 1'b0;
            txt_data    <= 8'h00;
        end else begin
            copy_addr_q <= (parse_rd_addr - src_off[ADDR_W-1:0]);
            if (parse_busy) txt_ram[{~txt_sel, copy_addr_q}] <= rd_data;
            txt_data <= txt_ram[{txt_sel, txt_addr}];
            if (txt_commit) begin
                if (txt_none) begin
                    // 没有草图：把 NONE 写进「没在对外的」那一半，再切过去
                    txt_ram[{~txt_sel, 12'd0}] <= "N";
                    txt_ram[{~txt_sel, 12'd1}] <= "O";
                    txt_ram[{~txt_sel, 12'd2}] <= "N";
                    txt_ram[{~txt_sel, 12'd3}] <= "E";
                    txt_len_r  <= 13'd4;
                end else begin
                    txt_len_r <= src_len;
                end
                txt_valid_r <= 1'b1;
                txt_sel     <= ~txt_sel;
            end
        end
    end

    assign txt_len   = txt_len_r;
    assign txt_valid = txt_valid_r;

    // ---- 节拍表 + 走步器（hap2_traj 里已经接好的一对）----
    wire signed [PT_BITS-1:0] plan_x0, plan_y0;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [31:0] laps_beats, blank_beats;        // 同上：节拍表算出来的全局量
    /* verilator lint_on UNUSEDSIGNAL */
    wire [8:0] move_count;                      // 编译刚算出来的行数（半成品，不能直接用）
    reg signed [PT_BITS-1:0] pub_x0, pub_y0;    // 已经把这份节拍表「发布」出去的起点
    reg [8:0]  pub_moves;                       // 已发布那份表的行数

    // mv_sel = 走步器现在读哪一半节拍表。编译写的是另一半（双缓冲）：
    // 编译到一半失败也不会破坏正在用的那张表，所以「被拒绝的配置不改变已生效的东西」
    // 这条承诺才真的成立——被拒绝之后，上一份草图仍然能启动。
    reg mv_sel;
    reg traj_ok;      // 有没有一张对得上当前配置的节拍表

    always @(posedge clk) begin
        if (!rst_n) begin
            mv_sel  <= 1'b0;
            traj_ok <= 1'b0;
            pub_x0  <= {PT_BITS{1'b0}};
            pub_y0  <= {PT_BITS{1'b0}};
            pub_moves <= 9'd0;
        end else begin
            if (plan_done) begin
                mv_sel  <= ~mv_sel;      // 新表算好了，切到新那一半
                traj_ok <= 1'b1;
                pub_x0  <= plan_x0;
                pub_y0  <= plan_y0;
                pub_moves <= move_count; // 行数也要跟着切，不然走步器会按错的行数循环
            end else if (!traj_ready_in) begin
                // 命令层说这份生效的配置没有轨迹表（比如 CUSTOM + scan_paths=NONE）。
                // 注意不能用 txt_none 判断：预设图形也没有草图文案，但它有轨迹表。
                traj_ok <= 1'b0;
            end
        end
    end

    // 没有可用节拍表时，走步器停在阵列中心（0,0）并且不开扫描，
    // 免得把上一份图形的表当成这一份继续走。
    wire signed [PT_BITS-1:0] walk_x0 = traj_ok ? pub_x0 : {PT_BITS{1'b0}};
    wire signed [PT_BITS-1:0] walk_y0 = traj_ok ? pub_y0 : {PT_BITS{1'b0}};

    // ---------------- 预设图形：单独一份点表 ----------------
    // 填这张表的是 hap2_shape（草图那条路填的是解析器自己的表）。两张表共用
    // 计划模块发出的地址，读出来的数据用 preset 选一份。
    wire                      sh_pt_wr_en, sh_st_wr_en, sh_counts_en;
    wire [7:0]                sh_pt_wr_addr;
    wire signed [PT_BITS-1:0] sh_pt_wr_x, sh_pt_wr_y, sh_pt_x, sh_pt_y;
    wire [4:0]                sh_st_wr_addr;
    wire [8:0]                sh_st_wr_start, sh_st_wr_len, sh_st_start, sh_st_len;
    wire [8:0]                sh_npt_in, sh_tab_npt;
    wire [5:0]                sh_nst_in, sh_tab_nst;

    hap2_shape #(
        .PT_BITS (PT_BITS)
    ) u_shape (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (shape_start),
        .shape        (shape),
        .radius_um    (cfg_radius_um),
        .cx_um        (cfg_cx_um),
        .cy_um        (cfg_cy_um),
        .pt_wr_en     (sh_pt_wr_en),
        .pt_wr_addr   (sh_pt_wr_addr),
        .pt_wr_x      (sh_pt_wr_x),
        .pt_wr_y      (sh_pt_wr_y),
        .st_wr_en     (sh_st_wr_en),
        .st_wr_addr   (sh_st_wr_addr),
        .st_wr_start  (sh_st_wr_start),
        .st_wr_len    (sh_st_wr_len),
        .counts_en    (sh_counts_en),
        .npt_out      (sh_npt_in),
        .nst_out      (sh_nst_in),
        .busy         (shape_busy),
        .done         (shape_done)
    );

    hap2_pt_table #(
        .PT_BITS (PT_BITS), .MAX_PTS (MAX_PTS), .MAX_STK (MAX_STK)
    ) u_shape_tab (
        .clk         (clk),
        .rst_n       (rst_n),
        .wr_en       (sh_pt_wr_en),
        .wr_addr     (sh_pt_wr_addr),
        .wr_x        (sh_pt_wr_x),
        .wr_y        (sh_pt_wr_y),
        .st_wr_en    (sh_st_wr_en),
        .st_wr_addr  (sh_st_wr_addr),
        .st_wr_start (sh_st_wr_start),
        .st_wr_len   (sh_st_wr_len),
        .counts_en   (sh_counts_en),
        .npt_in      (sh_npt_in),
        .nst_in      (sh_nst_in),
        .rd_pt_addr  (pt_addr),
        .rd_pt_x     (sh_pt_x),
        .rd_pt_y     (sh_pt_y),
        .rd_st_addr  (st_addr),
        .rd_st_start (sh_st_start),
        .rd_st_len   (sh_st_len),
        .point_count (sh_tab_npt),
        .stroke_count(sh_tab_nst)
    );

    // 计划模块读哪一份：草图 or 预设图形
    wire signed [PT_BITS-1:0] plan_pt_x = preset ? sh_pt_x : pt_x;
    wire signed [PT_BITS-1:0] plan_pt_y = preset ? sh_pt_y : pt_y;
    wire [8:0]                plan_st_start = preset ? sh_st_start : st_start;
    wire [8:0]                plan_st_len   = preset ? sh_st_len   : st_len;
    wire [8:0]                plan_npt      = preset ? sh_tab_npt  : point_count;
    wire [5:0]                plan_nst      = preset ? sh_tab_nst  : stroke_count;
    // 预设图形没有抬笔时间（参考实现里 blank_us 只对多段草图生效）
    wire [31:0]               plan_blank    = preset ? 32'd0 : cfg_blank_us;

    hap2_traj #(
        .PT_BITS (PT_BITS), .MAX_PTS (MAX_PTS), .MAX_STK (MAX_STK),
        .FRAC (FRAC), .TICK_CYC (TICK_CYC)
    ) u_traj (
        .clk                (clk),
        .rst_n              (rst_n),
        .tbl_sel            (mv_sel),
        .plan_start         (plan_start),
        .walk_start         (walk_start),
        .walk_hold          (walk_hold),
        .walk_stop          (walk_stop),
        .walk_x0            (walk_x0),
        .walk_y0            (walk_y0),
        .walk_moves         (pub_moves),
        .point_count        (plan_npt),
        .stroke_count       (plan_nst),
        .pt_addr            (pt_addr),
        .pt_x               (plan_pt_x),
        .pt_y               (plan_pt_y),
        .st_addr            (st_addr),
        .st_start           (plan_st_start),
        .st_len             (plan_st_len),
        .cfg_repeat_millihz (cfg_repeat_millihz),
        .cfg_blank_us       (plan_blank),
        .plan_busy          (plan_busy),
        .plan_done          (plan_done),
        .plan_fault         (plan_fault),
        .laps_beats         (laps_beats),
        .blank_beats        (blank_beats),
        .move_count         (move_count),
        .traj_x0            (plan_x0),
        .traj_y0            (plan_y0),
        .focus_x            (walk_fx),
        .focus_y            (walk_fy),
        .scan_on            (walk_scan),
        .stroke_index       (walk_stroke),
        .beat_pulse         (beat_pulse),
        .walk_running       (walk_running)
    );

    wire signed [PT_BITS-1:0] walk_fx, walk_fy;
    wire        walk_scan;
    wire [5:0]  walk_stroke;

    // 没有可用节拍表时（预设图形、或还没发过图形），对外一律报「停在阵列中心、
    // 没在扫描」——走步器内部状态是上一份图形留下的，不能露出去。
    assign focus_x      = traj_ok ? walk_fx     : {PT_BITS{1'b0}};
    assign focus_y      = traj_ok ? walk_fy     : {PT_BITS{1'b0}};
    assign scan_on      = traj_ok ? walk_scan   : 1'b0;
    assign stroke_index = traj_ok ? walk_stroke : 6'd0;

    // ---- 包围盒 ----
    // 预设图形：中心（有符号，可能为负）× 2 换算成 0.5 µm 单位，再 ± 半径。
    // 中间量用 32 位算，避免 21 位截断。半径上限 80000 µm、中心上限 ±100000 µm，
    // 所以结果一定落在 ±360000 个 0.5 µm 单位内，21 位装得下。
    /* verilator lint_off UNUSEDSIGNAL */   // 高 11 位用不到（换算完一定装得下 21 位）
    wire signed [31:0] preset_cx_q2 = $signed(cfg_cx_um) * 32'sd2;
    wire signed [31:0] preset_cy_q2 = $signed(cfg_cy_um) * 32'sd2;
    wire signed [31:0] preset_r_q2  = $signed(cfg_radius_um) * 32'sd2;

    assign bb_vld  = preset ? 1'b1 : sp_bb_vld;
    assign bb_xmin = preset ? preset_cx_q2[PT_BITS-1:0] - preset_r_q2[PT_BITS-1:0]
                            : sp_bb_xmin;
    assign bb_xmax = preset ? preset_cx_q2[PT_BITS-1:0] + preset_r_q2[PT_BITS-1:0]
                            : sp_bb_xmax;
    assign bb_ymin = preset ? preset_cy_q2[PT_BITS-1:0] - preset_r_q2[PT_BITS-1:0]
                            : sp_bb_ymin;
    assign bb_ymax = preset ? preset_cy_q2[PT_BITS-1:0] + preset_r_q2[PT_BITS-1:0]
                            : sp_bb_ymax;
    /* verilator lint_on UNUSEDSIGNAL */

endmodule
