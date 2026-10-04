// HAP2 接收通路仿真测试台。
//
// 运行方式见 fpga/README.md，核心是：
//   cd fpga && bash tb/run_verilator.sh
//
// 测试内容：
//   1. CRC16 自检（参考向量 123456789 -> 29B1）
//   2. 七个真实报文用例，逐个检查行长与通过/判错
//   3. 行缓冲超长丢弃（用小上限实例快速验证同一段逻辑）
//
// 注意：向量文件路径是相对“运行时的工作目录”解析的，所以必须在 fpga/ 下运行。
`timescale 1ns/1ps

module tb_hap2_rx;

    localparam integer CLK_HZ       = 50_000_000;
    localparam integer BAUD         = 115200;
    localparam integer CLKS_PER_BIT = (CLK_HZ + (BAUD / 2)) / BAUD;      // 434
    localparam real    CLK_NS       = 1000.0 / (CLK_HZ / 1000000.0);     // 20.0 ns
    localparam integer ADDR_W       = 13;   // 行缓冲 8192 字节
    localparam integer NCASE        = 7;
    localparam integer PKT_MAX      = 2048;
    localparam integer FNCASE       = 10;     // 字段级用例个数
    localparam integer FPKT_MAX     = 4096;
    localparam integer CN           = 18;     // 命令级用例个数
    localparam integer CPKT_MAX     = 4096;

    // 统计
    integer errors;      // 失败项数
    integer checks;      // 检查项数
    integer rx_bytes;    // dut 解出来的字节数
    integer rx_errs;     // dut 报告的帧错次数
    integer sent_bytes;  // 测试台实际发出去的字节数

    // ---------------- 时钟与复位 ----------------
    reg clk;
    reg rst_n;

    always #(CLK_NS / 2.0) clk = ~clk;

    // ---------------- 被测电路 ----------------
    reg  uart_line;
    wire            rx_valid;
    wire            rx_error;
    wire            line_ready;
    wire [ADDR_W:0] line_len;
    wire            line_dropped;
    wire            frame_ok;
    wire            frame_bad;
    wire [3:0]      err_code;
    wire [1:0]      kind;
    wire [15:0]     seq;
    wire [3:0]      verb;
    wire [18:0]     seen_mask;
    wire            verb_unknown;
    wire            header_ok;
    wire            field_valid;
    wire [4:0]      field_id;
    wire [2:0]      field_type;
    wire [31:0]     field_value;
    wire [2:0]      field_enum;
    wire [4:0]      bad_field;
    wire [ADDR_W:0] field_off;
    wire [ADDR_W:0] field_size;
    wire [15:0]     crc_value;
    wire [15:0]     crc_expected;

    initial begin
        clk        = 1'b0;
        rst_n      = 1'b0;
        uart_line  = 1'b1;
        errors     = 0;
        checks     = 0;
        rx_bytes   = 0;
        rx_errs    = 0;
        sent_bytes = 0;
    end

    hap2_rx #(
        .CLK_HZ   (CLK_HZ),
        .BAUD     (BAUD),
        .MAX_LINE (8192),
        .ADDR_W   (ADDR_W)
    ) dut (
        .clk          (clk),
        .rst_n        (rst_n),
        .uart_rx_pin  (uart_line),
        // 这个测试台里草图解析器读的是测试自己的字符串缓冲（下面的 dut_scan），
        // 不走行缓冲，所以把额外的读口挂空。
        .ext_rd_addr  ({ADDR_W{1'b0}}),
        .ext_rd_busy  (1'b0),
        .buf_data_out (dummy_buf_data),
        .line_ready   (line_ready),
        .line_len     (line_len),
        .line_dropped (line_dropped),
        .frame_ok     (frame_ok),
        .frame_bad    (frame_bad),
        .err_code     (err_code),
        .header_ok    (header_ok),
        .kind         (kind),
        .seq          (seq),
        .verb         (verb),
        .verb_unknown (verb_unknown),
        .seen_mask    (seen_mask),
        .field_valid  (field_valid),
        .field_id     (field_id),
        .field_type   (field_type),
        .field_value  (field_value),
        .field_enum   (field_enum),
        .field_off    (field_off),
        .field_size   (field_size),
        .bad_field    (bad_field),
        .crc_value    (crc_value),
        .crc_expected (crc_expected),
        .rx_valid     (rx_valid),
        .rx_error     (rx_error)
    );

    // ---------------- 命令状态机 ----------------
    wire            heartbeat_lost;
    wire            reply_valid;
    wire [1:0]      reply_kind;
    wire [15:0]     reply_seq;
    wire [3:0]      reply_code;
    wire            reply_want_state;
    wire [1:0]      cmd_mode;
    wire [1:0]      cmd_run;
    wire [2:0]      cmd_reason;
    wire [15:0]     cmd_rev;
    wire            config_changed;
    wire [31:0]     cfg_carrier_hz;
    wire [31:0]     cfg_phase_steps;
    wire [31:0]     cfg_cx_um;
    wire [31:0]     cfg_cy_um;
    wire [31:0]     cfg_z_um;
    wire [31:0]     cfg_radius_um;
    wire [31:0]     cfg_repeat_millihz;
    wire [31:0]     cfg_mod_hz;
    wire [31:0]     cfg_level;
    wire [2:0]      cfg_shape;
    wire [31:0]     cfg_path_closed;
    wire [31:0]     cfg_blank_us;
    /* verilator lint_off UNUSEDSIGNAL */
    // 轨迹子系统的接口在这个测试台里挂着不动（草图子系统由 tb_hap2_top 端到端测）
    wire            traj_parse_start, traj_plan_start, walk_hold;
    wire            walk_start, walk_stop, traj_ready;
    wire [ADDR_W:0] traj_src_off, traj_src_len;
    wire [31:0]     traj_repeat_millihz, traj_blank_us_w;
    wire            traj_shape_start, traj_preset;
    wire [2:0]      traj_shape_kind;
    wire [31:0]     traj_radius_um, traj_cx_um, traj_cy_um;
    wire [7:0]      dummy_buf_data;
    /* verilator lint_on UNUSEDSIGNAL */

    // 轨迹子系统的「替身」：这个测试台只测收报文和命令裁决，不接真的草图子系统。
    // 命令层现在是「预设图形先让形状生成器出点表（traj_shape_start），再编译节拍表
    // （traj_plan_start），两步的完成脉冲都回来了才回 ACK」。这里让两个完成脉冲
    // 一拍后就跟上，否则命令层会一直等在待办状态里，后面每条命令都只回 BUSY。
    reg traj_shape_done_r, traj_plan_done_r;
    always @(posedge clk) begin
        if (!rst_n) begin
            traj_shape_done_r <= 1'b0;
            traj_plan_done_r  <= 1'b0;
        end else begin
            traj_shape_done_r <= traj_shape_start;
            traj_plan_done_r  <= traj_plan_start;
        end
    end

    hap2_cmd #(
        .ADDR_W (ADDR_W)
    ) dut_cmd (
        .clk              (clk),
        .rst_n            (rst_n),
        .frame_ok         (frame_ok),
        .frame_bad        (frame_bad),
        .header_ok        (header_ok),
        .kind             (kind),
        .seq              (seq),
        .verb             (verb),
        .verb_unknown     (verb_unknown),
        .field_valid      (field_valid),
        .field_id         (field_id),
        .field_value      (field_value),
        .field_enum       (field_enum),
        .field_off        (field_off),
        .field_size       (field_size),
        .seen_mask        (seen_mask),
        .array_rows       (32'd4),
        .array_cols       (32'd4),
        .array_pitch_um   (32'd10000),
        .heartbeat_lost   (heartbeat_lost),
        // 这个测试台只测收报文和命令裁决，草图子系统不接（由 tb_hap2_top 端到端测）
        .traj_parse_start (traj_parse_start),
        .traj_plan_start  (traj_plan_start),
        .traj_shape_start (traj_shape_start),
        .traj_preset      (traj_preset),
        .traj_shape_kind  (traj_shape_kind),
        .traj_radius_um   (traj_radius_um),
        .traj_cx_um       (traj_cx_um),
        .traj_cy_um       (traj_cy_um),
        .traj_shape_done  (traj_shape_done_r),
        .traj_parse_ok    (1'b0),
        .traj_parse_bad   (1'b0),
        .traj_none        (1'b0),
        .traj_plan_done   (traj_plan_done_r),
        .traj_plan_fault  (1'b0),
        .traj_src_off     (traj_src_off),
        .traj_src_len     (traj_src_len),
        .traj_repeat_millihz (traj_repeat_millihz),
        .traj_blank_us    (traj_blank_us_w),
        .walk_start       (walk_start),
        .walk_hold        (walk_hold),
        .walk_stop        (walk_stop),
        .traj_ready       (traj_ready),
        .reply_valid      (reply_valid),
        .reply_kind       (reply_kind),
        .reply_seq        (reply_seq),
        .reply_code       (reply_code),
        .reply_want_state (reply_want_state),
        .mode             (cmd_mode),
        .run_state        (cmd_run),
        .reason           (cmd_reason),
        .revision         (cmd_rev),
        .config_changed   (config_changed),
        .cfg_carrier_hz   (cfg_carrier_hz),
        .cfg_phase_steps  (cfg_phase_steps),
        .cfg_cx_um        (cfg_cx_um),
        .cfg_cy_um        (cfg_cy_um),
        .cfg_z_um         (cfg_z_um),
        .cfg_radius_um    (cfg_radius_um),
        .cfg_repeat_millihz (cfg_repeat_millihz),
        .cfg_mod_hz       (cfg_mod_hz),
        .cfg_level        (cfg_level),
        .cfg_shape        (cfg_shape),
        .cfg_path_closed  (cfg_path_closed),
        .cfg_blank_us     (cfg_blank_us)
    );

    // ---------------- 应答发送通路 ----------------
    // ---------------- 多段草图解析器 ----------------
    localparam integer SN          = 9;      // 草图解析用例个数
    localparam integer BUF_BYTES   = 2048;

    reg  [7:0]  strbuf [0:BUF_BYTES-1];      // 测试用的字符串缓冲（同步读）
    reg  [7:0]  scan_pkt [0:4095];           // 各用例的字节
    reg  [31:0] scan_plan [0:9*SN-1];

    reg  [10:0] scan_off;                    // 字符串起点
    reg  [11:0] scan_len;                    // 字符串长度
    reg         scan_start;
    wire [10:0] scan_rd_addr;
    reg  [7:0]  scan_data;
    wire        scan_busy;
    wire        scan_ok;
    wire        scan_bad;
    wire [8:0]  scan_pts;
    wire [5:0]  scan_stk;
    /* verilator lint_off UNUSEDSIGNAL */
    wire        scan_is_none;      // 草图子系统由 tb_hap2_top 端到端测，这里只接出来看
    /* verilator lint_on UNUSEDSIGNAL */
    reg  [7:0]  scan_pt_addr;
    wire signed [20:0] scan_x;
    wire signed [20:0] scan_y;
    reg  [4:0]  scan_st_addr;
    wire [8:0]  scan_st_start;
    wire [8:0]  scan_st_len;

    always @(posedge clk) scan_data <= strbuf[scan_rd_addr];

    hap2_scan_parse #(
        .ADDR_W  (11),          // 测试缓冲 2048 字节
        .PT_BITS (21),
        .MAX_PTS (256),
        .MAX_STK (32)
    ) dut_scan (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (scan_start),
        .src_off      (scan_off),
        .src_len      (scan_len),
        .rd_addr      (scan_rd_addr),
        .rd_data      (scan_data),
        .busy         (scan_busy),
        .ok           (scan_ok),
        .bad          (scan_bad),
        .point_count  (scan_pts),
        .stroke_count (scan_stk),
        .is_none      (scan_is_none),
        .pt_addr      (scan_pt_addr),
        .pt_x         (scan_x),
        .pt_y         (scan_y),
        .st_addr      (scan_st_addr),
        .st_start     (scan_st_start),
        .st_len       (scan_st_len)
    );

    // 看门狗：仿真里把超时缩到 200 毫秒，免得等真实的 3 秒（3 秒 = 1.5 亿拍）。
    // 注意不能缩得太小：一条应答加一份状态帧要发 67 毫秒，超时必须比它长。
    hap2_watchdog #(
        .CLK_HZ (CLK_HZ),
        .HB_MS  (200)
    ) dut_wd (
        .clk        (clk),
        .rst_n      (rst_n),
        .host_frame (frame_ok),
        .mode       (cmd_mode),
        .run_state  (cmd_run),
        .timeout    (heartbeat_lost)
    );

    wire        board_tx;          // 板子发回上位机的串口线
    wire        tx_busy;
    wire [7:0]  phase_addr;
    reg  [7:0]  phase_data;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [11:0] tx_txt_addr;       // 同上：只接出来，不参与判断
    wire        state_capture_unused;   // 同上
    /* verilator lint_on UNUSEDSIGNAL */

    hap2_tx #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD),
        .DIGITS (10)
    ) dut_tx (
        .clk              (clk),
        .rst_n            (rst_n),
        .tx_line          (board_tx),
        .busy             (tx_busy),
        .reply_valid      (reply_valid),
        .reply_kind       (reply_kind),
        .reply_verb       (verb),
        .reply_seq        (reply_seq),
        .reply_code       (reply_code),
        .reply_want_state (reply_want_state),
        .state_push       (1'b0),
        .state_capture    (state_capture_unused),
        .boot_id          (32'hA1B2C3D4),
        .revision         (cmd_rev),
        .revision_report  (cmd_rev),
        .mode             (cmd_mode),
        .run_state        (cmd_run),
        .reason           (cmd_reason),
        .output_on        (cmd_run == 2'd1),
        .hw_rows          (8'd4),
        .hw_cols          (8'd4),
        .hw_pitch_um      (32'd10000),
        .cfg_carrier_hz   (cfg_carrier_hz),
        .cfg_phase_steps  (cfg_phase_steps),
        .cfg_cx_um        (cfg_cx_um),
        .cfg_cy_um        (cfg_cy_um),
        .cfg_z_um         (cfg_z_um),
        .cfg_radius_um    (cfg_radius_um),
        .cfg_repeat_millihz (cfg_repeat_millihz),
        .cfg_mod_hz       (cfg_mod_hz),
        .cfg_level        (cfg_level),
        .cfg_shape        (cfg_shape),
        .cfg_path_closed  (cfg_path_closed),
        .cfg_blank_us     (cfg_blank_us),
        // 走步器／相位表在这条测试里都是空的：扫描开关恒 0，相位回全 0，原文回 NONE
        .scan_on          (1'b0),
        .stroke_index     (6'd0),
        .txt_addr         (tx_txt_addr),
        .txt_data         (8'd0),
        .txt_len          (13'd4),
        .txt_valid        (1'b0),
        .fx_um            (32'd0),
        .fy_um            (32'd0),
        .fz_um            (32'd150000),
        .phase_addr       (phase_addr),
        .phase_data       (phase_data)
    );

    // 相位表：真实设计里由相位求解模块来写，这里先放一组已知值
    reg [7:0] phase_mem [0:255];
    always @(posedge clk) phase_data <= phase_mem[phase_addr];

    // 板子发出来的字节，用一个串口接收器解码
    wire [7:0] board_byte;
    wire       board_byte_valid;

    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_rx_board (
        .clk(clk), .rst_n(rst_n), .rx_line(board_tx),
        .rx_data(board_byte), .rx_valid(board_byte_valid)
    );

    reg [7:0]  txbuf [0:2047];     // 收到的板子应答
    integer    txlen;

    always @(posedge clk) begin
        if (board_byte_valid) begin
            if (txlen < 2048) begin
                txbuf[txlen] <= board_byte;
                txlen <= txlen + 1;
            end
        end
    end

    // 测试台自己也算一遍校验码，用来核对板子发出来的值
    reg         tbcrc_init;
    reg         tbcrc_valid;
    reg  [7:0]  tbcrc_data;
    wire [15:0] tbcrc_val;
    crc16_ccitt u_tbcrc (
        .clk(clk), .rst_n(rst_n), .init(tbcrc_init), .valid(tbcrc_valid),
        .data(tbcrc_data), .crc(tbcrc_val)
    );

    // 应答对照表
    reg [7:0]  txexp [0:511];
    reg [31:0] txplan [0:5];

    // ---------------- 向量表 ----------------
    // 整帧级：偏移 字节数 期望行长 期望通过 发送方式
    reg [7:0]  pkt  [0:PKT_MAX-1];
    reg [31:0] plan [0:5*NCASE-1];
    // 字段级：偏移 字节数 期望通过 期望命令 期望错误码 期望字段表
    reg [7:0]  fpkt  [0:FPKT_MAX-1];
    reg [31:0] fplan [0:6*FNCASE-1];
    // 命令级：偏移 字节数 应答类型 错误码 模式 运行状态 版本增量 要不要跟状态 序号
    reg [7:0]  cpkt  [0:CPKT_MAX-1];
    reg [31:0] cplan [0:9*CN-1];

    initial begin
        $readmemh("tb/vectors/packets.mem", pkt);
        $readmemh("tb/vectors/plan.mem", plan);
        $readmemh("tb/vectors/field_packets.mem", fpkt);
        $readmemh("tb/vectors/field_plan.mem", fplan);
        $readmemh("tb/vectors/cmd_packets.mem", cpkt);
        $readmemh("tb/vectors/cmd_plan.mem", cplan);
        $readmemh("tb/vectors/tx_expect.mem", txexp);
        $readmemh("tb/vectors/tx_expect_plan.mem", txplan);
        $readmemh("tb/vectors/scan_packets.mem", scan_pkt);
        $readmemh("tb/vectors/scan_plan.mem", scan_plan);
        for (k = 0; k < BUF_BYTES; k = k + 1) strbuf[k] = scan_pkt[k];
        scan_start = 1'b0;
        scan_off   = 11'd0;
        scan_len   = 12'd0;
        txlen = 0;
    end

    // ---------------- 结果捕获 ----------------
    reg            got_line;
    reg            got_drop;
    reg            got_ok;
    reg            got_bad;
    reg [ADDR_W:0] got_len;
    reg [3:0]      got_verb;   // 整帧结论出来时的命令编号
    reg [3:0]      got_err;    // 整帧结论出来时的错误分类
    reg [18:0]     got_seen;   // 整帧结论出来时的字段表（19 个字段）

    // 字段寄存器组：模拟下一个模块会怎么收字段流
    reg [31:0]     fval  [0:16];   // 数值类字段的值（编号 0~16）
    reg [2:0]      fenum [0:16];   // 图形名/映射名/控制模式的编号
    reg            DEBUG;          // 打开后打印每个字段流，排查用

    // 应答捕获
    reg            got_reply;
    reg [1:0]      got_kind;
    reg [3:0]      got_code;
    reg            got_want_state;
    reg [15:0]     got_reply_seq;
    reg            got_cfg_change;

    always @(posedge clk) begin
        if (line_ready)   begin got_line <= 1'b1; got_len <= line_len; end
        if (line_dropped) got_drop <= 1'b1;
        if (frame_ok)     got_ok   <= 1'b1;
        if (frame_bad)    got_bad  <= 1'b1;
        if (frame_ok || frame_bad) begin
            got_verb <= verb;
            got_seen <= seen_mask;
        end
        if (frame_bad)    got_err  <= err_code;
        if (field_valid) begin
            fval[field_id]  <= field_value;
            fenum[field_id] <= field_enum;
            if (DEBUG) $display("    [字段流] 编号=%0d 类型=%0d 数值=%0d 枚举=%0d",
                                field_id, field_type, field_value, field_enum);
        end
        if (frame_bad && DEBUG) $display("    [整帧] 判错：错误码=%0d 字段=%0d", err_code, bad_field);
        if (rx_valid)     rx_bytes <= rx_bytes + 1;
        if (rx_error)     rx_errs  <= rx_errs + 1;
        if (reply_valid) begin
            got_reply      <= 1'b1;
            got_kind       <= reply_kind;
            got_code       <= reply_code;
            got_want_state <= reply_want_state;
            got_reply_seq  <= reply_seq;
        end
        if (config_changed) got_cfg_change <= 1'b1;
    end

    task clear_flags;
        begin
            got_line = 1'b0;
            got_drop = 1'b0;
            got_ok   = 1'b0;
            got_bad  = 1'b0;
            got_len  = 0;
            got_verb = 4'hF;
            got_err  = 4'hF;
            got_seen = 19'h00000;
        end
    endtask

    task clear_fields;
        integer k;
        begin
            for (k = 0; k < 17; k = k + 1) begin
                fval[k]  = 32'd0;
                fenum[k] = 3'd0;
            end
        end
    endtask

    task clear_reply;
        begin
            got_reply      = 1'b0;
            got_kind       = 2'd0;
            got_code       = 4'd0;
            got_want_state = 1'b0;
            got_reply_seq  = 16'd0;
            got_cfg_change = 1'b0;
        end
    endtask

    // ---------------- 串口发送 ----------------
    // 每个电平都在时钟下降沿之后改变，避开被测试电路采样的上升沿。
    task send_byte;
        input [7:0] b;
        integer i;
        integer k;
        begin
            @(negedge clk);
            uart_line = 1'b0;                                   // 起始位
            for (k = 0; k < CLKS_PER_BIT; k = k + 1) @(negedge clk);
            for (i = 0; i < 8; i = i + 1) begin                  // 8 个数据位，低位先发
                uart_line = b[i];
                for (k = 0; k < CLKS_PER_BIT; k = k + 1) @(negedge clk);
            end
            uart_line = 1'b1;                                    // 停止位
            for (k = 0; k < CLKS_PER_BIT; k = k + 1) @(negedge clk);
        end
    endtask

    // ---------------- CRC 自检 ----------------
    reg         st_init  = 1'b0;
    reg         st_valid = 1'b0;
    reg  [7:0]  st_data  = 8'h00;
    wire [15:0] st_crc;
    reg  [7:0]  crc_bytes [0:8];

    crc16_ccitt u_crc_selftest (
        .clk   (clk),
        .rst_n (rst_n),
        .init  (st_init),
        .valid (st_valid),
        .data  (st_data),
        .crc   (st_crc)
    );

    task run_crc_selftest;
        integer i;
        begin
            st_init = 1'b1;
            @(negedge clk);
            st_init = 1'b0;
            for (i = 0; i < 9; i = i + 1) begin
                st_data  = crc_bytes[i];
                st_valid = 1'b1;
                @(negedge clk);
            end
            st_valid = 1'b0;
            @(negedge clk);
            if (st_crc === 16'h29B1) begin
                $display("[CRC 自检] \"123456789\" -> %04X  期望 29B1  通过", st_crc);
            end else begin
                $display("[CRC 自检] \"123456789\" -> %04X  期望 29B1  **失败**", st_crc);
                errors = errors + 1;
            end
            checks = checks + 1;
        end
    endtask

    // ---------------- 用例检查 ----------------
    task check_case;
        input integer idx;
        input [ADDR_W:0] expect_len;
        input integer expect_ok;
        begin
            checks = checks + 1;
            if (got_line !== 1'b1) begin
                $display("[用例 %0d] 没有产生行长通知  **失败**", idx);
                errors = errors + 1;
            end else if (got_drop !== 1'b0) begin
                $display("[用例 %0d] 被误判为超长丢弃  **失败**", idx);
                errors = errors + 1;
            end else if (got_len !== expect_len) begin
                $display("[用例 %0d] 行长不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_len, expect_len);
                errors = errors + 1;
            end else if (expect_ok == 1 && got_ok !== 1'b1) begin
                $display("[用例 %0d] 应通过却判错（frame_ok=%b frame_bad=%b）**失败**",
                         idx, got_ok, got_bad);
                errors = errors + 1;
            end else if (expect_ok == 0 && got_bad !== 1'b1) begin
                $display("[用例 %0d] 应判错却通过（frame_ok=%b frame_bad=%b）**失败**",
                         idx, got_ok, got_bad);
                errors = errors + 1;
            end else if (expect_ok == 1 && crc_value !== crc_expected) begin
                $display("[用例 %0d] 判通过但 CRC 与报文不一致：算得 %04X，报文 %04X  **失败**",
                         idx, crc_value, crc_expected);
                errors = errors + 1;
            end else begin
                $display("[用例 %0d] 行长 %0d，通过=%0d  符合预期",
                         idx, got_len, got_ok);
            end
        end
    endtask

    // 字段级用例：核对整帧结论、命令编号、错误分类、字段表
    task check_field_case;
        input integer idx;
        input integer expect_ok;
        input integer expect_verb;
        input integer expect_err;
        input integer expect_seen;
        begin
            checks = checks + 1;
            if (expect_ok == 1 && got_ok !== 1'b1) begin
                $display("[字段 %0d] 应通过却没通过（错误码 %0d）**失败**", idx, got_err);
                errors = errors + 1;
            end else if (expect_ok == 0 && got_bad !== 1'b1) begin
                $display("[字段 %0d] 应判错却通过了  **失败**", idx);
                errors = errors + 1;
            end else if (got_verb !== expect_verb[3:0]) begin
                $display("[字段 %0d] 命令编号不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_verb, expect_verb);
                errors = errors + 1;
            end else if (expect_ok == 0 && got_err !== expect_err[3:0]) begin
                $display("[字段 %0d] 错误分类不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_err, expect_err);
                errors = errors + 1;
            end else if (got_seen !== expect_seen[18:0]) begin
                $display("[字段 %0d] 字段表不符：实测 %04X，期望 %04X  **失败**",
                         idx, got_seen, expect_seen);
                errors = errors + 1;
            end else begin
                $display("[字段 %0d] 通过=%0d 命令=%0d 错误=%0d 字段表=%04X  符合预期",
                         idx, got_ok, got_verb, got_err, got_seen);
            end
        end
    endtask

    // 抽查几个数值，确认数字真的被解析出来了
    task check_config_values;
        input integer idx;
        begin
            checks = checks + 1;
            // 字段编号：0 载波、1 相位档、4 高度、8 等级、14 间距
            if (fval[0] !== 32'd40000 || fval[1] !== 32'd64 || fval[4] !== 32'd150000
                    || fval[8] !== 32'd30 || fval[14] !== 32'd10000) begin
                $display("[字段 %0d] 数值不对：载波=%0d 相位档=%0d 高度=%0d 等级=%0d 间距=%0d  **失败**",
                         idx, fval[0], fval[1], fval[4], fval[8], fval[14]);
                errors = errors + 1;
            end else begin
                $display("[字段 %0d] 数值抽查：载波 %0d Hz，相位档 %0d，高度 %0d µm，等级 %0d，间距 %0d µm",
                         idx, fval[0], fval[1], fval[4], fval[8], fval[14]);
            end
        end
    endtask

    // 图形名应被换成编号：TRIANGLE = 5
    task check_shape_enum;
        begin
            checks = checks + 1;
            if (fenum[9] !== 3'd5) begin
                $display("[字段-图形名] 三角形编号应为 5，实测 %0d  **失败**", fenum[9]);
                errors = errors + 1;
            end else begin
                $display("[字段-图形名] TRIANGLE 被认成编号 5  符合预期");
            end
        end
    endtask

    // ---------------- 应答发送的检查 ----------------
    // ---------------- 多段草图解析的检查 ----------------
    always @(posedge clk) begin
        if (scan_ok) begin
            scan_result <= 1;
            scan_npt    <= scan_pts;
            scan_nst    <= scan_stk;
        end
        if (scan_bad) scan_result <= 0;
    end

    function integer sign16;      // 16 位补码 -> 有符号整数
        input [31:0] v;
        begin
            sign16 = v[15] ? (v - 32'd65536) : v;
        end
    endfunction

    task check_scan_case;
        input integer idx;
        input integer exp_ok;
        input integer exp_pts;
        input integer exp_stk;
        input integer exp_x0;
        input integer exp_y0;
        input integer exp_x1;
        input integer exp_y1;
        begin
            checks = checks + 1;
            if (scan_result !== exp_ok) begin
                $display("[草图 %0d] 结果不符：实测 %0d，期望 %0d  **失败**",
                         idx, scan_result, exp_ok);
                errors = errors + 1;
            end else if (exp_ok == 0) begin
                $display("[草图 %0d] 按预期拒绝  符合预期", idx);
            end else if (scan_npt !== exp_pts || scan_nst !== exp_stk) begin
                $display("[草图 %0d] 点数/段数不符：实测 %0d/%0d，期望 %0d/%0d  **失败**",
                         idx, scan_npt, scan_nst, exp_pts, exp_stk);
                errors = errors + 1;
            end else if (scan_x0 !== exp_x0 || scan_y0 !== exp_y0
                         || scan_x1 !== exp_x1 || scan_y1 !== exp_y1) begin
                $display("[草图 %0d] 首末点不符：实测 (%0d,%0d)-(%0d,%0d)，期望 (%0d,%0d)-(%0d,%0d)  **失败**",
                         idx, scan_x0, scan_y0, scan_x1, scan_y1,
                         exp_x0, exp_y0, exp_x1, exp_y1);
                errors = errors + 1;
            end else begin
                $display("[草图 %0d] 点数 %0d，段数 %0d，首点 (%0d,%0d)，末点 (%0d,%0d)  符合预期",
                         idx, scan_npt, scan_nst, scan_x0, scan_y0, scan_x1, scan_y1);
            end
        end
    endtask

    // 十六进制字符
    function [7:0] hex_of;
        input [15:0]  v;
        input integer nib;      // 0 = 最低 4 位
        reg [3:0] n;
        begin
            n = v[4*nib +: 4];
            hex_of = (n < 4'd10) ? (8'h30 + {4'h0, n}) : (8'h37 + {4'h0, n});
        end
    endfunction

    // 在收到的字节流里找一段 ASCII。pat 左对齐存放，plen 是字符数。
    function found;
        input [8*24-1:0] pat;
        input integer    plen;
        integer i, j;
        reg hit;
        begin
            found = 1'b0;
            for (i = 0; i + plen <= txlen; i = i + 1) begin
                hit = 1'b1;
                for (j = 0; j < plen; j = j + 1) begin
                    if (txbuf[i+j] !== pat[8*(24-1-j) +: 8]) hit = 1'b0;
                end
                if (hit) found = 1'b1;
            end
        end
    endfunction

    // 找出第 n 帧（从 0 开始）的起止下标
    task frame_range;
        input  integer n;
        output integer start_i;
        output integer stop_i;
        integer i, seen;
        begin
            start_i = -1;
            stop_i  = -1;
            seen    = 0;
            for (i = 0; i < txlen; i = i + 1) begin
                if (stop_i >= 0) begin
                    i = txlen;
                end else if (txbuf[i] == 8'h0A) begin
                    seen = seen + 1;
                    if (seen == n + 1) stop_i = i + 1;
                end else if (seen == n && start_i < 0) begin
                    start_i = i;
                end
            end
        end
    endtask

    // 逐字节对照板子发出来的应答
    task check_expected;
        input integer idx;
        integer off, len, i;
        reg ok;
        begin
            off = txplan[2*idx + 0];
            len = txplan[2*idx + 1];
            checks = checks + 1;
            if (txlen < len) begin
                $display("[应答 %0d] 收到的字节太少：%0d，期望 %0d  **失败**", idx, txlen, len);
                errors = errors + 1;
            end else begin
                ok = 1'b1;
                for (i = 0; i < len; i = i + 1)
                    if (txbuf[i] !== txexp[off + i]) ok = 1'b0;
                if (!ok) begin
                    $display("[应答 %0d] 与期望不一致  **失败**", idx);
                    for (i = 0; i < len; i = i + 1) begin
                        if (txbuf[i] !== txexp[off + i]) begin
                            $display("    第 %0d 个字节：实测 %02X，期望 %02X（%c / %c）",
                                     i, txbuf[i], txexp[off+i], txbuf[i], txexp[off+i]);
                            i = len;      // 只报第一个不同的位置
                        end
                    end
                    errors = errors + 1;
                end else begin
                    $display("[应答 %0d] 前 %0d 字节与期望完全一致（含校验码）", idx, len);
                end
            end
        end
    endtask

    // 核对某一帧里的校验码
    task check_frame_crc;
        input integer start_i;
        input integer stop_i;
        integer i, star;
        reg ok;
        begin
            star = -1;
            for (i = start_i; i < stop_i; i = i + 1)
                if (txbuf[i] == 8'h2A && star < 0) star = i;
            checks = checks + 1;
            if (star < 0 || star + 5 > stop_i) begin
                $display("[校验码] 这一帧里没找到星号，或校验码不齐  **失败**");
                errors = errors + 1;
            end else begin
                tbcrc_init = 1'b1;
                @(negedge clk);
                tbcrc_init = 1'b0;
                for (i = start_i; i < star; i = i + 1) begin
                    tbcrc_data  = txbuf[i];
                    tbcrc_valid = 1'b1;
                    @(negedge clk);
                end
                tbcrc_valid = 1'b0;
                @(negedge clk);
                ok = 1'b1;
                for (i = 0; i < 4; i = i + 1)
                    if (txbuf[star+1+i] !== hex_of(tbcrc_val, 3-i)) ok = 1'b0;
                if (!ok) begin
                    $display("[校验码] 算得 %04X，但帧里写的是 %c%c%c%c  **失败**",
                             tbcrc_val, txbuf[star+1], txbuf[star+2], txbuf[star+3], txbuf[star+4]);
                    errors = errors + 1;
                end else begin
                    $display("[校验码] 帧内校验码正确：%04X", tbcrc_val);
                end
            end
        end
    endtask

    // 命令级用例：核对应答类型、错误码、模式、运行状态、版本号、要不要跟状态
    task check_cmd_case;
        input integer idx;
        input integer exp_kind;
        input integer exp_code;
        input integer exp_mode;
        input integer exp_run;
        input integer exp_rev;
        input integer exp_want;
        input integer exp_seq;
        begin
            checks = checks + 1;
            if (got_reply !== 1'b1) begin
                $display("[命令 %0d] 没有产生应答  **失败**", idx);
                errors = errors + 1;
            end else if (got_kind !== exp_kind[1:0]) begin
                $display("[命令 %0d] 应答类型不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_kind, exp_kind);
                errors = errors + 1;
            end else if (got_code !== exp_code[3:0]) begin
                $display("[命令 %0d] 错误码不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_code, exp_code);
                errors = errors + 1;
            end else if (cmd_mode !== exp_mode[1:0]) begin
                $display("[命令 %0d] 控制模式不符：实测 %0d，期望 %0d  **失败**",
                         idx, cmd_mode, exp_mode);
                errors = errors + 1;
            end else if (cmd_run !== exp_run[1:0]) begin
                $display("[命令 %0d] 运行状态不符：实测 %0d，期望 %0d  **失败**",
                         idx, cmd_run, exp_run);
                errors = errors + 1;
            end else if (cmd_rev !== exp_rev[15:0]) begin
                $display("[命令 %0d] 版本号不符：实测 %0d，期望 %0d  **失败**",
                         idx, cmd_rev, exp_rev);
                errors = errors + 1;
            end else if (got_want_state !== exp_want[0]) begin
                $display("[命令 %0d] “要不要跟状态”不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_want_state, exp_want);
                errors = errors + 1;
            end else if (got_reply_seq !== exp_seq[15:0]) begin
                $display("[命令 %0d] 应答里的序号没原样抄回：实测 %0d，期望 %0d  **失败**",
                         idx, got_reply_seq, exp_seq);
                errors = errors + 1;
            end else if (got_cfg_change !== exp_rev_delta[0]) begin
                $display("[命令 %0d] 配置替换次数不符：实测 %0d，期望 %0d  **失败**",
                         idx, got_cfg_change, exp_rev_delta);
                errors = errors + 1;
            end else begin
                $display("[命令 %0d] %s 错误码=%0d 模式=%0d 运行=%0d 版本=%0d 跟状态=%0d  符合预期",
                         idx, (got_kind == 2'd0) ? "ACK" : "ERR",
                         got_code, cmd_mode, cmd_run, cmd_rev, got_want_state);
            end
        end
    endtask

    // ---------------- 行缓冲超长丢弃 ----------------
    // 用一个上限只有 64 字节的小实例，快速验证“超长整行丢弃”这段逻辑。
    reg        small_valid = 1'b0;
    reg  [7:0] small_data  = 8'h00;
    wire       small_ready;
    wire       small_dropped;
    wire [6:0] small_len;
    reg        small_got_ready;
    reg        small_got_drop;
    reg [6:0]  small_got_len;

    hap2_line_rx #(
        .MAX_LINE (64),
        .ADDR_W   (6)
    ) u_small (
        .clk          (clk),
        .rst_n        (rst_n),
        .rx_valid     (small_valid),
        .rx_data      (small_data),
        .line_ready   (small_ready),
        .line_len     (small_len),
        .line_dropped (small_dropped),
        .rd_addr      (6'd0)
    );

    always @(posedge clk) begin
        if (small_ready)   begin small_got_ready <= 1'b1; small_got_len <= small_len; end
        if (small_dropped) small_got_drop <= 1'b1;
    end

    task small_push;
        input integer count;
        input [7:0]   value;
        integer k;
        begin
            for (k = 0; k < count; k = k + 1) begin
                @(negedge clk);
                small_valid = 1'b1;
                small_data  = value;
            end
            @(negedge clk);
            small_valid = 1'b0;
        end
    endtask

    task check_overflow;
        begin
            // 63 字节正文 + LF：应当成行，长度 63
            small_got_ready = 1'b0;
            small_got_drop  = 1'b0;
            small_got_len   = 0;
            small_push(63, 8'h41);
            small_push(1, 8'h0A);
            repeat (4) @(negedge clk);
            checks = checks + 1;
            if (small_got_ready !== 1'b1 || small_got_len !== 7'd63 || small_got_drop !== 1'b0) begin
                $display("[超长-边界] 63 字节正文：就绪=%b 长度=%0d 丢弃=%b  **失败**",
                         small_got_ready, small_got_len, small_got_drop);
                errors = errors + 1;
            end else begin
                $display("[超长-边界] 63 字节正文被正常收下，长度 63  符合预期");
            end

            // 64 字节正文 + LF：超过上限，应当整行丢弃
            small_got_ready = 1'b0;
            small_got_drop  = 1'b0;
            small_push(64, 8'h41);
            small_push(1, 8'h0A);
            repeat (4) @(negedge clk);
            checks = checks + 1;
            if (small_got_drop !== 1'b1 || small_got_ready !== 1'b0) begin
                $display("[超长-丢弃] 64 字节正文：就绪=%b 丢弃=%b  **失败**",
                         small_got_ready, small_got_drop);
                errors = errors + 1;
            end else begin
                $display("[超长-丢弃] 64 字节正文被整行丢弃  符合预期");
            end
        end
    endtask

    // ---------------- 主流程 ----------------
    integer case_idx;
    integer offset;
    integer count;
    integer expect_len;
    integer expect_ok;
    integer mode;
    integer i;
    integer j;
    integer exp_ok;
    integer exp_verb;
    integer exp_err;
    integer exp_seen;
    integer exp_kind;
    integer exp_code;
    integer exp_mode;
    integer exp_run;
    integer exp_rev_delta;
    integer exp_want;
    integer exp_rev;
    integer timeout;
    integer f_start;
    integer f_stop;
    integer k;
    integer frame_count;
    integer scan_idx;
    integer scan_result;      // 1 = 通过，0 = 拒绝
    integer scan_x0, scan_y0, scan_x1, scan_y1;
    integer scan_npt, scan_nst;
    integer st0_start, st0_len, st1_start, st1_len;

    initial begin
        crc_bytes[0] = "1";
        crc_bytes[1] = "2";
        crc_bytes[2] = "3";
        crc_bytes[3] = "4";
        crc_bytes[4] = "5";
        crc_bytes[5] = "6";
        crc_bytes[6] = "7";
        crc_bytes[7] = "8";
        crc_bytes[8] = "9";

        // 相位表放一组已知值：第 i 路 = (i*3) mod 64，便于核对状态帧里的相位串
        for (k = 0; k < 256; k = k + 1) phase_mem[k] = (k * 3) % 64;

        rst_n = 1'b0;
        DEBUG = 1'b0;
        repeat (10) @(negedge clk);
        rst_n = 1'b1;
        repeat (10) @(negedge clk);

        $display("=========================================");
        $display("HAP2 接收通路仿真：%0d baud，时钟 %0.0f MHz，位时间 %0d 个时钟",
                 BAUD, CLK_HZ / 1000000.0, CLKS_PER_BIT);
        $display("=========================================");

        run_crc_selftest;

        for (case_idx = 0; case_idx < NCASE; case_idx = case_idx + 1) begin
            offset     = plan[5*case_idx + 0];
            count      = plan[5*case_idx + 1];
            expect_len = plan[5*case_idx + 2];
            expect_ok  = plan[5*case_idx + 3];
            mode       = plan[5*case_idx + 4];

            clear_flags;
            for (i = 0; i < count; i = i + 1) begin
                send_byte(pkt[offset + i]);
                sent_bytes = sent_bytes + 1;
                if (mode == 1 && i == (count / 2)) begin
                    repeat (500) @(negedge clk);   // 中间挖个大空档，验证不要求连续
                end
            end
            // 等 CRC 校验和字段解析都跑完（两趟扫描，各约行长个周期）
            repeat (expect_len + 600) @(negedge clk);
            check_case(case_idx, expect_len, expect_ok);
        end

        // ---- 字段级用例 ----
        $display("-----------------------------------------");
        for (case_idx = 0; case_idx < FNCASE; case_idx = case_idx + 1) begin
            offset   = fplan[6*case_idx + 0];
            count    = fplan[6*case_idx + 1];
            exp_ok   = fplan[6*case_idx + 2];
            exp_verb = fplan[6*case_idx + 3];
            exp_err  = fplan[6*case_idx + 4];
            exp_seen = fplan[6*case_idx + 5];

            clear_flags;
            clear_fields;
            for (j = 0; j < count; j = j + 1) begin
                send_byte(fpkt[offset + j]);
                sent_bytes = sent_bytes + 1;
            end
            repeat (900) @(negedge clk);
            check_field_case(case_idx, exp_ok, exp_verb, exp_err, exp_seen);
            if (case_idx == 0) begin
                check_config_values(case_idx);
            end
        end
        check_shape_enum;

        // ---- 多段草图解析 ----
        $display("-----------------------------------------");
        for (scan_idx = 0; scan_idx < SN; scan_idx = scan_idx + 1) begin
            scan_off    = scan_plan[9*scan_idx + 0];
            scan_len    = scan_plan[9*scan_idx + 1];
            scan_result = -1;
            scan_start  = 1'b1;
            @(negedge clk);
            scan_start  = 1'b0;
            timeout = 0;
            while (scan_busy && timeout < 200000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            repeat (6) @(negedge clk);
            if (scan_result == 1) begin
                scan_pt_addr = 8'd0;
                @(negedge clk); @(negedge clk);
                scan_x0 = scan_x; scan_y0 = scan_y;
                scan_pt_addr = scan_pts - 1'b1;
                @(negedge clk); @(negedge clk);
                scan_x1 = scan_x; scan_y1 = scan_y;
                scan_npt = scan_pts;
                scan_nst = scan_stk;
                // 段表抽查：只有两段那个用例，第一段 (起点 0, 长度 2)、第二段 (2, 2)
                if (scan_idx == 1) begin
                    scan_st_addr = 5'd0;
                    @(negedge clk);
                    st0_start = scan_st_start; st0_len = scan_st_len;
                    scan_st_addr = 5'd1;
                    @(negedge clk);
                    st1_start = scan_st_start; st1_len = scan_st_len;
                    checks = checks + 1;
                    if (st0_start !== 0 || st0_len !== 2 || st1_start !== 2 || st1_len !== 2) begin
                        $display("[草图-段表] 实测 (%0d,%0d)(%0d,%0d)，期望 (0,2)(2,2)  **失败**",
                                 st0_start, st0_len, st1_start, st1_len);
                        errors = errors + 1;
                    end else begin
                        $display("[草图-段表] 两段分别是 (起点 0 长度 2) 和 (起点 2 长度 2)  符合预期");
                    end
                end
            end
            check_scan_case(scan_idx,
                            scan_plan[9*scan_idx + 2],
                            scan_plan[9*scan_idx + 3],
                            scan_plan[9*scan_idx + 4],
                            sign16(scan_plan[9*scan_idx + 5]), sign16(scan_plan[9*scan_idx + 6]),
                            sign16(scan_plan[9*scan_idx + 7]), sign16(scan_plan[9*scan_idx + 8]));
        end

        // ---- 命令级用例：权限矩阵 ----
        $display("-----------------------------------------");
        // 前面的用例已经让命令引擎握过手、改过配置了。这一组要测「上电刚起来」
        // 的状态，所以先把整条通路复位一次，各组用例之间互不影响。
        rst_n = 1'b0;
        repeat (5) @(negedge clk);
        rst_n = 1'b1;
        repeat (5) @(negedge clk);

        exp_rev = 0;
        for (case_idx = 0; case_idx < CN; case_idx = case_idx + 1) begin
            offset        = cplan[9*case_idx + 0];
            count         = cplan[9*case_idx + 1];
            exp_kind      = cplan[9*case_idx + 2];
            exp_code      = cplan[9*case_idx + 3];
            exp_mode      = cplan[9*case_idx + 4];
            exp_run       = cplan[9*case_idx + 5];
            exp_rev_delta = cplan[9*case_idx + 6];
            exp_want      = cplan[9*case_idx + 7];
            exp_seen      = cplan[9*case_idx + 8];   // 复用变量存期望序号

            clear_reply;
            txlen = 0;                      // 清空"板子发出来的字节"缓冲
            for (j = 0; j < count; j = j + 1) begin
                send_byte(cpkt[offset + j]);
                sent_bytes = sent_bytes + 1;
            end
            repeat (900) @(negedge clk);
            // 等应答真的发完：一条应答加一份状态帧，串口要发几百字节
            timeout = 0;
            while (tx_busy && timeout < 4000000) begin
                @(negedge clk);
                timeout = timeout + 1;
            end
            // 期望「配置被接受」的用例，版本号在这一帧里就加过一了，
            // 所以先把期望值推进，再拿去比对
            if (exp_rev_delta == 1 && exp_kind == 0) exp_rev = exp_rev + 1;
            check_cmd_case(case_idx, exp_kind, exp_code, exp_mode, exp_run,
                           exp_rev, exp_want, exp_seen);

            // ---- 逐字节核对比子发回来的报文 ----
            if (case_idx == 1) check_expected(0);      // 握手应答
            if (case_idx == 2) check_expected(1);      // 配置应答
            if (case_idx == 7) check_expected(2);      // 拒绝应答

            // 只有「应答后面还要跟状态帧」的用例才有第二条报文；拒绝应答只有一条
            if (case_idx == 1 || case_idx == 2) begin
                frame_range(1, f_start, f_stop);       // 第二条就是状态帧
                check_frame_crc(f_start, f_stop);
                checks = checks + 1;
                if (!found({"HAP3 TEL 0 STATE", 64'h0}, 16)) begin
                    $display("[状态帧] 开头不是 HAP3 TEL 0 STATE  **失败**");
                    errors = errors + 1;
                end else begin
                    $display("[状态帧] 开头正确，共 %0d 字节", f_stop - f_start);
                end
            end

            // 状态帧里该有的内容（只在握手那一拍打印一次，便于人工核对）
            if (case_idx == 1) begin
                checks = checks + 1;
                if (!found({"state=IDLE", 112'h0}, 10)) begin
                    $display("[状态帧] 找不到 state=IDLE  **失败**");
                    errors = errors + 1;
                end else if (!found({"carrier_hz=40000", 64'h0}, 16)) begin
                    $display("[状态帧] 找不到 carrier_hz=40000  **失败**");
                    errors = errors + 1;
                end else if (!found({"shape=CIRCLE", 96'h0}, 12)) begin
                    $display("[状态帧] 找不到 shape=CIRCLE  **失败**");
                    errors = errors + 1;
                end else if (!found({"phases=0,3,6,9,12,", 48'h0}, 18)) begin
                    $display("[状态帧] 相位串开头不对  **失败**");
                    errors = errors + 1;
                end else if (!found({",42,45*", 136'h0}, 7)) begin
                    $display("[状态帧] 相位串结尾不对  **失败**");
                    errors = errors + 1;
                end else begin
                    $display("[状态帧] 关键字段与相位串都正确");
                end
                $write("[状态帧内容] ");
                for (k = f_start; k < f_stop; k = k + 1)
                    $write("%c", txbuf[k]);
                $write("\n");
            end
        end

        // 配置真的换进去了吗？抽查三个值
        checks = checks + 1;
        if (cfg_carrier_hz !== 32'd40000 || cfg_level !== 32'd30 || cfg_shape !== 3'd3) begin
            $display("[命令-配置落地] 生效配置不对：载波=%0d 等级=%0d 形状=%0d  **失败**",
                     cfg_carrier_hz, cfg_level, cfg_shape);
            errors = errors + 1;
        end else begin
            $display("[命令-配置落地] 载波 %0d Hz，等级 %0d，形状 %0d  符合预期",
                     cfg_carrier_hz, cfg_level, cfg_shape);
        end

        // 看门狗：先启动（第 9 号用例就是 START），然后什么都不发，看板子会不会自己停
        offset = cplan[9*9 + 0];
        count  = cplan[9*9 + 1];
        clear_reply;
        for (j = 0; j < count; j = j + 1) begin
            send_byte(cpkt[offset + j]);
            sent_bytes = sent_bytes + 1;
        end
        repeat (900) @(negedge clk);
        checks = checks + 1;
        if (cmd_run !== 2'd1) begin
            $display("[看门狗] 启动没成功，运行状态=%0d  **失败**", cmd_run);
            errors = errors + 1;
        end
        // 从这里开始不再发任何报文，等看门狗自己数到超时
        timeout = 0;
        while (cmd_run != 2'd0 && timeout < 12000000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        checks = checks + 1;
        if (cmd_run !== 2'd0 || cmd_reason !== 3'd1) begin
            $display("[看门狗] 主机失联后应回到待机并记原因，实测运行=%0d 原因=%0d  **失败**",
                     cmd_run, cmd_reason);
            errors = errors + 1;
        end else begin
            $display("[看门狗] 主机停止发报文 %0d 拍后自动回到待机，原因码 %0d  符合预期",
                     timeout, cmd_reason);
        end

        // 反面用例：只要主机按时发探活报文，就不该停
        offset = cplan[9*9 + 0];      // START
        count  = cplan[9*9 + 1];
        clear_reply;
        for (j = 0; j < count; j = j + 1) begin
            send_byte(cpkt[offset + j]);
            sent_bytes = sent_bytes + 1;
        end
        repeat (900) @(negedge clk);
        for (k = 0; k < 4; k = k + 1) begin
            // 探活报文（第 4 号用例），间隔约 7 毫秒，短于 20 毫秒的超时
            offset = cplan[9*4 + 0];
            count  = cplan[9*4 + 1];
            for (j = 0; j < count; j = j + 1) begin
                send_byte(cpkt[offset + j]);
                sent_bytes = sent_bytes + 1;
            end
            repeat (250000) @(negedge clk);
        end
        checks = checks + 1;
        if (cmd_run !== 2'd1) begin
            $display("[看门狗-反面] 主机一直在探活，板子却停了：运行状态=%0d  **失败**", cmd_run);
            errors = errors + 1;
        end else begin
            $display("[看门狗-反面] 主机按时探活，板子保持运行  符合预期");
        end

        // 让刚才排队的应答全部发完，后面的用例从一个干净的状态开始
        timeout = 0;
        while (tx_busy && timeout < 4000000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        repeat (20000) @(negedge clk);
        offset = cplan[9*14 + 0];     // 停止，回到待机，这样配置命令才会被接受
        count  = cplan[9*14 + 1];
        clear_reply;
        for (j = 0; j < count; j = j + 1) begin
            send_byte(cpkt[offset + j]);
            sent_bytes = sent_bytes + 1;
        end
        timeout = 0;
        while (tx_busy && timeout < 4000000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        repeat (20000) @(negedge clk);

        // 关键用例：板子正在发长应答时，主机又发来一条命令。
        // 上位机每 0.8 秒发一次探活，而一条应答要发 67 毫秒，撞上的概率约 8%。
        // 撞上时板子必须也能回话，不能把这条命令丢掉。
        clear_reply;
        txlen = 0;
        offset = cplan[9*2 + 0];      // 第 2 号用例是配置命令，会产生 417 字节的长应答
        count  = cplan[9*2 + 1];
        for (j = 0; j < count; j = j + 1) begin
            send_byte(cpkt[offset + j]);
            sent_bytes = sent_bytes + 1;
        end
        // 配置命令本身要收 104 毫秒，等板子开始回话（tx_busy 拉高）
        timeout = 0;
        while (!tx_busy && timeout < 3000000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        checks = checks + 1;
        if (!tx_busy) begin
            $display("[撞车用例] 前提不成立：此刻板子并没有在发报文  **失败**");
            errors = errors + 1;
        end
        offset = cplan[9*4 + 0];      // 探活
        count  = cplan[9*4 + 1];
        for (j = 0; j < count; j = j + 1) begin
            send_byte(cpkt[offset + j]);
            sent_bytes = sent_bytes + 1;
        end
        timeout = 0;
        while (tx_busy && timeout < 4000000) begin
            @(negedge clk);
            timeout = timeout + 1;
        end
        repeat (20000) @(negedge clk);       // 再留点时间给第二条应答
        // 数一下收到几帧：配置应答 + 状态帧 + 探活应答 = 3 帧
        frame_count = 0;
        for (k = 0; k < txlen; k = k + 1)
            if (txbuf[k] == 8'h0A) frame_count = frame_count + 1;
        // 把每一帧的开头打出来，便于人工核对
        f_start = 0;
        for (k = 0; k < txlen; k = k + 1) begin
            if (txbuf[k] == 8'h0A) begin
                $write("[撞车用例] 帧起于 %0d 长度 %0d 开头：", f_start, k + 1 - f_start);
                for (j = f_start; j < f_start + 34 && j <= k; j = j + 1) $write("%c", txbuf[j]);
                $write("\n");
                f_start = k + 1;
            end
        end
        checks = checks + 1;
        if (frame_count !== 3) begin
            $display("[撞车用例] 长应答期间插进来的命令被丢了：只收到 %0d 帧，应该有 3 帧  **失败**",
                     frame_count);
            errors = errors + 1;
        end else begin
            $display("[撞车用例] 长应答期间插进来的探活也回了话，共 3 帧  符合预期");
        end

        check_overflow;

        // 串口层自查：解出来的字节数要和发出去的一致，且不应出现帧错
        repeat (20) @(negedge clk);
        checks = checks + 1;
        if (rx_bytes !== sent_bytes) begin
            $display("[串口层] 收到 %0d 字节，发出 %0d 字节  **失败**", rx_bytes, sent_bytes);
            errors = errors + 1;
        end else begin
            $display("[串口层] 收到 %0d 字节，与发送一致", rx_bytes);
        end
        checks = checks + 1;
        if (rx_errs !== 0) begin
            $display("[串口层] 出现 %0d 次帧错  **失败**", rx_errs);
            errors = errors + 1;
        end else begin
            $display("[串口层] 未出现帧错");
        end

        $display("=========================================");
        $display("共检查 %0d 项，失败 %0d 项", checks, errors);
        if (errors == 0) begin
            $display("结果：全部通过");
        end else begin
            $display("结果：有失败");
        end
        $display("=========================================");
        $finish;
    end

endmodule
