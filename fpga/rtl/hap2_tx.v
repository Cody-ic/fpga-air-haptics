`timescale 1ns/1ps

// HAP2 应答组装与发送：把命令裁决给出的「结论」变成电脑看得懂的 ASCII 报文。
//
// 四种报文：
//   HELLO 的应答：要额外报出自己的能力（帧类型、容量、实际阵列、坐标范围）
//   成功应答：HAP2 ACK <序号> <命令> applied=1 rev=N*CRC
//   错误应答：HAP2 ERR <序号> <命令> code=<错误码名>*CRC
//   状态快照：HAP2 TEL 0 STATE ...*CRC（含已生效配置、焦点、全阵列相位）
//
// 组装方式：每种报文写成一条「模板字符串」，里面用特殊符号当占位符——
//   @ 十进制数   # 4 位十六进制校验码   + 8 位十六进制启动标识
//   ~ 命令名     ^ 错误码名   % 控制模式   & 运行状态   $ 图形名   ! 原因
//   | 整张相位表
// 遇到普通字符就直接发；遇到占位符就去生成对应的内容。这样协议要加字段时，
// 改模板就行，不用改状态机。
//
// 校验码边走边算：每发一个正文字节就喂给 CRC 模块，遇到星号停止累加，
// 星号后面四位就是把算出来的值写成十六进制。
module hap2_tx #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD   = 115200,
    parameter integer DIGITS = 10            // 十进制最多几位
) (
    input  wire        clk,
    input  wire        rst_n,
    output wire        tx_line,
    output wire        busy,          // 1 = 手上还有报文没发完

    // ---- 来自命令裁决 ----
    input  wire        reply_valid,   // 单拍脉冲：有应答要发
    input  wire [1:0]  reply_kind,    // 0=ACK 1=ERR
    input  wire [3:0]  reply_verb,
    input  wire [15:0] reply_seq,
    input  wire [3:0]  reply_code,
    input  wire        reply_want_state,

    // ---- 设备状态（状态帧用）----
    input  wire [31:0] boot_id,
    input  wire [15:0] revision,
    input  wire [1:0]  mode,
    input  wire [1:0]  run_state,
    input  wire [2:0]  reason,
    input  wire        output_on,
    input  wire [7:0]  hw_rows,
    input  wire [7:0]  hw_cols,
    input  wire [31:0] hw_pitch_um,
    input  wire [31:0] cfg_carrier_hz,
    input  wire [31:0] cfg_phase_steps,
    input  wire [31:0] cfg_cx_um,
    input  wire [31:0] cfg_cy_um,
    input  wire [31:0] cfg_z_um,
    input  wire [31:0] cfg_radius_um,
    input  wire [31:0] cfg_repeat_millihz,
    input  wire [31:0] cfg_mod_hz,
    input  wire [31:0] cfg_level,
    input  wire [2:0]  cfg_shape,
    input  wire [31:0] cfg_path_closed,
    input  wire [31:0] cfg_blank_us,
    input  wire [31:0] fx_um,
    input  wire [31:0] fy_um,
    input  wire [31:0] fz_um,

    // ---- 相位表读口（同步读，一拍出数据）----
    output reg  [7:0]  phase_addr,
    input  wire [7:0]  phase_data
);

    // ---------------- 报文明细 ----------------
    // 字符串宽度必须和字符数完全相等：Verilog 里字符串是右对齐的，
    // 宽度写错会整体错位。这里每个模板都以换行结尾，发到换行就算一帧结束。
    localparam [8*314-1:0] T_HELLO =
        "HAP3 ACK @ HELLO proto=3 device=FPGA boot=+ simulated=0 hb_ms=@ caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE max_rows=@ max_cols=@ max_channels=@ max_nodes=@ max_scan_points=@ max_strokes=@ hw_rows=@ hw_cols=@ hw_pitch_um=@ mapping=ROW_MAJOR_XY x_min_um=@ x_max_um=@ y_min_um=@ y_max_um=@ z_min_um=@ z_max_um=@*#\n";
    localparam [8*31-1:0]  T_ACK   = "HAP3 ACK @ ~ applied=1 rev=@*#\n";
    localparam [8*22-1:0]  T_ERR   = "HAP3 ERR @ ~ code=^*#\n";
    localparam [8*372-1:0] T_STATE =
        "HAP3 TEL 0 STATE boot=+ sample=@ uptime_ms=@ rev=@ mode=% state=& output=@ scan_on=1 stroke_index=0 simulated=0 reason=! carrier_hz=@ phase_steps=@ cx_um=@ cy_um=@ z_um=@ radius_um=@ repeat_millihz=@ mod_hz=@ level=@ shape=$ path_xy_um=NONE path_closed=@ scan_paths=NONE blank_us=@ hw_rows=@ hw_cols=@ hw_pitch_um=@ mapping=ROW_MAJOR_XY fx_um=@ fy_um=@ fz_um=@ phases=|*#\n";

    // 设备声明的固定能力（这些目前是定值，将来由板级配置决定）
    localparam [31:0] HB_MS         = 32'd3000;
    localparam [31:0] MAX_ROWS      = 32'd16;
    localparam [31:0] MAX_COLS      = 32'd16;
    localparam [31:0] MAX_CHANNELS  = 32'd256;
    localparam [31:0] MAX_NODES     = 32'd64;
    localparam [31:0] MAX_SCAN_PTS  = 32'd256;
    localparam [31:0] MAX_STROKES   = 32'd32;
    localparam [31:0] WS_X_MIN      = -32'sd100000;
    localparam [31:0] WS_X_MAX      =  32'sd100000;
    localparam [31:0] WS_Y_MIN      = -32'sd100000;
    localparam [31:0] WS_Y_MAX      =  32'sd100000;
    localparam [31:0] WS_Z_MIN      =  32'sd20000;
    localparam [31:0] WS_Z_MAX      =  32'sd300000;

    // ---------------- 名字表 ----------------
    localparam [8*5-1:0]  N_HELLO   = "HELLO";
    localparam [8*4-1:0]  N_PING    = "PING";
    localparam [8*6-1:0]  N_CONFIG  = "CONFIG";
    localparam [8*4-1:0]  N_MODE    = "MODE";
    localparam [8*5-1:0]  N_START   = "START";
    localparam [8*5-1:0]  N_PAUSE   = "PAUSE";
    localparam [8*4-1:0]  N_STOP    = "STOP";
    localparam [8*4-1:0]  N_SNAP    = "SNAP";

    localparam [8*4-1:0]  C_NONE    = "NONE";
    localparam [8*4-1:0]  C_BUSY    = "BUSY";
    localparam [8*13-1:0] C_LOCAL   = "LOCAL_CONTROL";
    localparam [8*10-1:0] C_BADCFG  = "BAD_CONFIG";
    localparam [8*18-1:0] C_HANDSH  = "HANDSHAKE_REQUIRED";
    localparam [8*17-1:0] C_HWMIS   = "HARDWARE_MISMATCH";
    localparam [8*11-1:0] C_NOTRUN  = "NOT_RUNNING";
    localparam [8*8-1:0]  C_BADMODE = "BAD_MODE";
    localparam [8*15-1:0] C_UNKNOWN = "UNKNOWN_COMMAND";

    localparam [8*5-1:0]  M_LOCAL   = "LOCAL";
    localparam [8*6-1:0]  M_REMOTE  = "REMOTE";

    localparam [8*4-1:0]  R_IDLE    = "IDLE";
    localparam [8*7-1:0]  R_RUNNING = "RUNNING";
    localparam [8*6-1:0]  R_PAUSED  = "PAUSED";
    localparam [8*5-1:0]  R_FAULT   = "FAULT";

    localparam [8*5-1:0]  S_POINT   = "POINT";
    localparam [8*6-1:0]  S_LINEX   = "LINE_X";
    localparam [8*6-1:0]  S_LINEY   = "LINE_Y";
    localparam [8*6-1:0]  S_CIRCLE  = "CIRCLE";
    localparam [8*6-1:0]  S_SQUARE  = "SQUARE";
    localparam [8*8-1:0]  S_TRIANG  = "TRIANGLE";
    localparam [8*5-1:0]  S_ARROW   = "ARROW";
    localparam [8*6-1:0]  S_CUSTOM  = "CUSTOM";

    localparam [8*4-1:0]  Z_NONE    = "NONE";
    localparam [8*17-1:0] Z_HB      = "HEARTBEAT_TIMEOUT";

    // 帧编号
    localparam [1:0] F_HELLO = 2'd0;
    localparam [1:0] F_ACK   = 2'd1;
    localparam [1:0] F_ERR   = 2'd2;
    localparam [1:0] F_STATE = 2'd3;

    // 状态机
    localparam [3:0] S_IDLE     = 4'd0;
    localparam [3:0] S_NEXT     = 4'd1;   // 取模板下一个字符
    localparam [3:0] S_LIT      = 4'd2;   // 发一个已知字节
    localparam [3:0] S_WAIT_TX  = 4'd3;   // 等这个字节发完
    localparam [3:0] S_FRAME_END= 4'd4;
    localparam [3:0] S_NUM_WAIT = 4'd5;   // 等十进制转换完成
    localparam [3:0] S_NUM_DIG  = 4'd6;   // 一位一位发数字
    localparam [3:0] S_HEX      = 4'd7;   // 发十六进制
    localparam [3:0] S_NAME     = 4'd8;   // 发名字
    localparam [3:0] S_PH_LOAD  = 4'd9;
    localparam [3:0] S_PH_READ  = 4'd10;
    localparam [3:0] S_PH_WAIT  = 4'd11;
    localparam [3:0] S_PH_AFTER = 4'd12;
    localparam [3:0] S_PH_CONV  = 4'd13;

    // 占位符字符
    localparam [7:0] P_DEC   = "@";
    localparam [7:0] P_CRC   = "#";
    localparam [7:0] P_BOOT  = "+";
    localparam [7:0] P_VERB  = "~";
    localparam [7:0] P_CODE  = "^";
    localparam [7:0] P_MODE  = "%";
    localparam [7:0] P_RUN   = "&";
    localparam [7:0] P_SHAPE = "$";
    localparam [7:0] P_REASON= "!";
    localparam [7:0] P_PHASE = "|";

    // ---------------- 模板与取值表 ----------------
    function [7:0] tmpl_byte;
        input [1:0]  sel;
        input [9:0]  pos;
        begin
            // 字符串在向量里靠高位存放：第 0 个字符在最高字节，
            // 所以要从最高位往下数，用「-:」而不是「+:」。
            case (sel)
                F_HELLO: tmpl_byte = T_HELLO[8*314-1 - 8*pos -: 8];
                F_ACK:   tmpl_byte = T_ACK  [8*31 -1 - 8*pos -: 8];
                F_ERR:   tmpl_byte = T_ERR  [8*22 -1 - 8*pos -: 8];
                default: tmpl_byte = T_STATE[8*372-1 - 8*pos -: 8];
            endcase
        end
    endfunction

    function [31:0] value_at;
        input [1:0] sel;
        input [4:0] idx;
        begin
            case (sel)
                F_HELLO: begin
                    case (idx)
                        5'd0:  value_at = {16'h0, seq_lat};
                        5'd1:  value_at = HB_MS;
                        5'd2:  value_at = MAX_ROWS;
                        5'd3:  value_at = MAX_COLS;
                        5'd4:  value_at = MAX_CHANNELS;
                        5'd5:  value_at = MAX_NODES;
                        5'd6:  value_at = MAX_SCAN_PTS;     // HAP3 新增：草图点数上限
                        5'd7:  value_at = MAX_STROKES;      // HAP3 新增：段数上限
                        5'd8:  value_at = {24'h0, hw_rows};
                        5'd9:  value_at = {24'h0, hw_cols};
                        5'd10: value_at = hw_pitch_um;
                        5'd11: value_at = WS_X_MIN;
                        5'd12: value_at = WS_X_MAX;
                        5'd13: value_at = WS_Y_MIN;
                        5'd14: value_at = WS_Y_MAX;
                        5'd15: value_at = WS_Z_MIN;
                        default: value_at = WS_Z_MAX;
                    endcase
                end
                F_STATE: begin
                    case (idx)
                        5'd0:  value_at = {16'h0, sample};
                        5'd1:  value_at = uptime_ms;
                        5'd2:  value_at = {16'h0, revision};
                        5'd3:  value_at = output_on ? 32'd1 : 32'd0;
                        5'd4:  value_at = cfg_carrier_hz;
                        5'd5:  value_at = cfg_phase_steps;
                        5'd6:  value_at = cfg_cx_um;
                        5'd7:  value_at = cfg_cy_um;
                        5'd8:  value_at = cfg_z_um;
                        5'd9:  value_at = cfg_radius_um;
                        5'd10: value_at = cfg_repeat_millihz;
                        5'd11: value_at = cfg_mod_hz;
                        5'd12: value_at = cfg_level;
                        5'd13: value_at = cfg_path_closed;
                        5'd14: value_at = cfg_blank_us;     // HAP3 新增：段间跳转时长
                        5'd15: value_at = {24'h0, hw_rows};
                        5'd16: value_at = {24'h0, hw_cols};
                        5'd17: value_at = hw_pitch_um;
                        5'd18: value_at = fx_um;
                        5'd19: value_at = fy_um;
                        default: value_at = fz_um;
                    endcase
                end
                F_ACK: begin
                    // 通用应答：第一个数是序号，第二个是配置版本号
                    value_at = (idx == 0) ? {16'h0, seq_lat} : {16'h0, revision};
                end
                default: value_at = {16'h0, seq_lat};   // ERR 只要序号
            endcase
        end
    endfunction

    // 名字表：返回 {结束标志, 字节}，结束标志为 1 表示这一串发完了
    // 索引方式同上：字符串靠高位存放，所以从最高位往下数。
    function [8:0] name_char;
        input [2:0] grp;   // 0 命令 1 错误码 2 模式 3 运行 4 图形 5 原因
        input [4:0] id;
        input [4:0] pos;
        begin
            case (grp)
                3'd0: case (id)
                    4'd0: name_char = (pos <  5) ? {1'b0, N_HELLO [8*5 -1 - 8*pos -: 8]} : 9'h100;
                    4'd1: name_char = (pos <  4) ? {1'b0, N_PING  [8*4 -1 - 8*pos -: 8]} : 9'h100;
                    4'd2: name_char = (pos <  6) ? {1'b0, N_CONFIG[8*6 -1 - 8*pos -: 8]} : 9'h100;
                    4'd3: name_char = (pos <  4) ? {1'b0, N_MODE  [8*4 -1 - 8*pos -: 8]} : 9'h100;
                    4'd4: name_char = (pos <  5) ? {1'b0, N_START [8*5 -1 - 8*pos -: 8]} : 9'h100;
                    4'd5: name_char = (pos <  5) ? {1'b0, N_PAUSE [8*5 -1 - 8*pos -: 8]} : 9'h100;
                    4'd6: name_char = (pos <  4) ? {1'b0, N_STOP  [8*4 -1 - 8*pos -: 8]} : 9'h100;
                    default: name_char = (pos < 4) ? {1'b0, N_SNAP [8*4 -1 - 8*pos -: 8]} : 9'h100;
                endcase
                3'd1: case (id)
                    4'd0: name_char = (pos <  4) ? {1'b0, C_NONE   [8*4 -1 - 8*pos -: 8]} : 9'h100;
                    4'd1: name_char = (pos <  4) ? {1'b0, C_BUSY   [8*4 -1 - 8*pos -: 8]} : 9'h100;
                    4'd2: name_char = (pos < 13) ? {1'b0, C_LOCAL  [8*13-1 - 8*pos -: 8]} : 9'h100;
                    4'd3: name_char = (pos < 10) ? {1'b0, C_BADCFG [8*10-1 - 8*pos -: 8]} : 9'h100;
                    4'd5: name_char = (pos < 17) ? {1'b0, C_HWMIS  [8*17-1 - 8*pos -: 8]} : 9'h100;
                    4'd6: name_char = (pos < 11) ? {1'b0, C_NOTRUN [8*11-1 - 8*pos -: 8]} : 9'h100;
                    4'd7: name_char = (pos <  8) ? {1'b0, C_BADMODE[8*8 -1 - 8*pos -: 8]} : 9'h100;
                    4'd8: name_char = (pos < 15) ? {1'b0, C_UNKNOWN[8*15-1 - 8*pos -: 8]} : 9'h100;
                    default: name_char = (pos < 18) ? {1'b0, C_HANDSH[8*18-1 - 8*pos -: 8]} : 9'h100;
                endcase
                3'd2: case (id)
                    4'd1: name_char = (pos < 6) ? {1'b0, M_REMOTE[8*6 -1 - 8*pos -: 8]} : 9'h100;
                    default: name_char = (pos < 5) ? {1'b0, M_LOCAL [8*5 -1 - 8*pos -: 8]} : 9'h100;
                endcase
                3'd3: case (id)
                    4'd1: name_char = (pos < 7) ? {1'b0, R_RUNNING[8*7 -1 - 8*pos -: 8]} : 9'h100;
                    4'd2: name_char = (pos < 6) ? {1'b0, R_PAUSED [8*6 -1 - 8*pos -: 8]} : 9'h100;
                    4'd3: name_char = (pos < 5) ? {1'b0, R_FAULT  [8*5 -1 - 8*pos -: 8]} : 9'h100;
                    default: name_char = (pos < 4) ? {1'b0, R_IDLE[8*4 -1 - 8*pos -: 8]} : 9'h100;
                endcase
                3'd4: case (id)
                    4'd0: name_char = (pos < 5) ? {1'b0, S_POINT [8*5 -1 - 8*pos -: 8]} : 9'h100;
                    4'd1: name_char = (pos < 6) ? {1'b0, S_LINEX [8*6 -1 - 8*pos -: 8]} : 9'h100;
                    4'd2: name_char = (pos < 6) ? {1'b0, S_LINEY [8*6 -1 - 8*pos -: 8]} : 9'h100;
                    4'd3: name_char = (pos < 6) ? {1'b0, S_CIRCLE[8*6 -1 - 8*pos -: 8]} : 9'h100;
                    4'd4: name_char = (pos < 6) ? {1'b0, S_SQUARE[8*6 -1 - 8*pos -: 8]} : 9'h100;
                    4'd5: name_char = (pos < 8) ? {1'b0, S_TRIANG[8*8 -1 - 8*pos -: 8]} : 9'h100;
                    4'd6: name_char = (pos < 5) ? {1'b0, S_ARROW [8*5 -1 - 8*pos -: 8]} : 9'h100;
                    default: name_char = (pos < 6) ? {1'b0, S_CUSTOM[8*6 -1 - 8*pos -: 8]} : 9'h100;
                endcase
                default: case (id)
                    4'd1: name_char = (pos < 17) ? {1'b0, Z_HB  [8*17-1 - 8*pos -: 8]} : 9'h100;
                    default: name_char = (pos <  4) ? {1'b0, Z_NONE[8*4 -1 - 8*pos -: 8]} : 9'h100;
                endcase
            endcase
        end
    endfunction

    function [7:0] hex_char;    // 4 位数值 -> 一个大写十六进制字符
        input [3:0] n;
        begin
            hex_char = (n < 4'd10) ? (8'h30 + {4'h0, n}) : (8'h37 + {4'h0, n});
        end
    endfunction

    // ---------------- 内部信号 ----------------
    reg [3:0]  state;
    reg [1:0]  frame_sel;
    // ---------------- 待发队列 ----------------
    // 一条应答加一份状态帧要发 67 毫秒，而上位机每 0.8 秒发一次探活命令，
    // 撞上的概率约 8%。撞上时如果直接把新应答丢掉，上位机等 2 秒就会断开。
    // 所以这里放一个深度 4 的环形队列：发不完的先排队，绝不丢。
    localparam integer QDEPTH = 4;
    reg [26:0] qmem [0:QDEPTH-1];   // {want_state, code, seq, verb, kind}
    reg [1:0]  wr_ptr;
    reg [1:0]  rd_ptr;
    reg [2:0]  qcount;
    reg [15:0] drop_cnt;            // 队列满而丢掉的条数（正常情况下应当是 0）

    wire        q_full  = (qcount == QDEPTH);
    wire        q_empty = (qcount == 3'd0);
    wire [26:0] new_reply = {reply_want_state, reply_code, reply_seq, reply_verb, reply_kind};
    wire [26:0] cur_reply = qmem[rd_ptr];

    wire [1:0]  cur_kind = cur_reply[1:0];
    wire [3:0]  cur_verb = cur_reply[5:2];
    wire [15:0] cur_seq  = cur_reply[21:6];
    wire [3:0]  cur_code = cur_reply[25:22];
    wire        cur_want = cur_reply[26];

    wire        q_push = reply_valid && !q_full;
    wire        q_pop  = (state == S_IDLE) && !q_empty;

    reg [9:0]  pos;            // 模板读到第几个字符
    reg [4:0]  num_idx;        // 模板里第几个十进制占位符
    reg        want_state;     // 发完这条应答后要不要再发状态帧

    reg [15:0] seq_lat;
    reg [3:0]  verb_lat;
    reg [3:0]  code_lat;

    reg [7:0]  lit;            // 当前要发的字节
    reg [3:0]  ret_state;      // 发完这个字节回到哪个状态
    reg        crc_feed_en;    // 这个字节要不要喂给校验码

    reg [31:0] hex_shift;
    reg [3:0]  hex_idx;
    reg [3:0]  hex_len;
    reg        hex_nocrc;      // 1 = 这几位十六进制不参与校验码（只有校验码自己）

    reg [2:0]  name_grp;
    reg [4:0]  name_id;
    reg [4:0]  name_pos;
    reg [3:0]  name_ret;

    reg [4:0]  digit_idx;
    reg        sign_done;
    reg        dig_started;
    reg [3:0]  num_after;      // 数字发完之后去哪
    reg        num_neg;

    reg [8:0]  ph_idx;         // 相位表当前发到第几个通道

    // 组合取值：模板当前字符、名字表当前字符
    wire [7:0] c  = tmpl_byte(frame_sel, pos);
    wire [8:0] nc = name_char(name_grp, name_id, name_pos);

    reg [31:0] uptime_ms;
    reg [15:0] sample;
    reg [31:0] ms_cnt;

    // 子模块接口
    reg         tx_start;
    reg  [7:0]  tx_data;
    wire        tx_done;

    reg         dec_start;
    reg  [31:0] dec_value;
    wire        dec_done;
    wire        dec_neg;
    wire [4*DIGITS-1:0] dec_bcd;

    reg         crc_init;
    reg         crc_valid;
    reg  [7:0]  crc_data;
    wire [15:0] crc_now;

    wire [15:0] phase_count = hw_rows * hw_cols;

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst_n(rst_n), .tx_start(tx_start), .tx_data(tx_data),
        .tx_line(tx_line), .tx_done(tx_done)
    );

    dec_ascii #(.BITS(32), .DIGITS(DIGITS)) u_dec (
        .clk(clk), .rst_n(rst_n), .start(dec_start), .value(dec_value),
        .done(dec_done), .negative(dec_neg), .bcd(dec_bcd)
    );

    crc16_ccitt #(.INIT(16'hFFFF)) u_crc (
        .clk(clk), .rst_n(rst_n), .init(crc_init), .valid(crc_valid),
        .data(crc_data), .crc(crc_now)
    );

    assign busy = (state != S_IDLE) || !q_empty;

    // 毫秒计数器（本模块自己维护，复位后从 0 开始）
    always @(posedge clk) begin
        if (!rst_n) begin
            ms_cnt    <= 32'd0;
            uptime_ms <= 32'd0;
        end else if (ms_cnt >= (CLK_HZ / 1000) - 1) begin
            ms_cnt    <= 32'd0;
            uptime_ms <= uptime_ms + 1'b1;
        end else begin
            ms_cnt <= ms_cnt + 1'b1;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            state       <= S_IDLE;
            frame_sel   <= F_ACK;
            pos         <= 10'd0;
            num_idx     <= 5'd0;
            want_state  <= 1'b0;
            wr_ptr      <= 2'd0;
            rd_ptr      <= 2'd0;
            qcount      <= 3'd0;
            drop_cnt    <= 16'd0;
            seq_lat     <= 16'd0;
            verb_lat    <= 4'd0;
            code_lat    <= 4'd0;
            lit         <= 8'h00;
            ret_state   <= S_NEXT;
            crc_feed_en <= 1'b1;
            hex_shift   <= 32'd0;
            hex_idx     <= 4'd0;
            hex_len     <= 4'd4;
            hex_nocrc   <= 1'b1;
            name_grp    <= 3'd0;
            name_id     <= 5'd0;
            name_pos    <= 5'd0;
            name_ret    <= S_NEXT;
            digit_idx   <= 5'd0;
            sign_done   <= 1'b0;
            dig_started <= 1'b0;
            num_after   <= S_NEXT;
            num_neg     <= 1'b0;
            ph_idx      <= 9'd0;
            sample      <= 16'd0;
            tx_start    <= 1'b0;
            tx_data     <= 8'h00;
            dec_start   <= 1'b0;
            dec_value   <= 32'd0;
            crc_init    <= 1'b0;
            crc_valid   <= 1'b0;
            crc_data    <= 8'h00;
            phase_addr  <= 8'd0;
        end else begin
            tx_start  <= 1'b0;
            dec_start <= 1'b0;
            crc_init  <= 1'b0;
            crc_valid <= 1'b0;
            crc_feed_en <= 1'b1;

            // 待发队列：入队与出队可以同一拍发生，所以队列长度只算一次
            if (q_push) begin
                qmem[wr_ptr] <= new_reply;
                wr_ptr       <= wr_ptr + 1'b1;
            end else if (reply_valid && q_full) begin
                drop_cnt <= drop_cnt + 1'b1;    // 队列满才丢，正常情况下不会发生
            end
            if (q_pop) begin
                rd_ptr <= rd_ptr + 1'b1;
            end
            if (q_push && q_pop) begin
                qcount <= qcount;
            end else if (q_push) begin
                qcount <= qcount + 1'b1;
            end else if (q_pop) begin
                qcount <= qcount - 1'b1;
            end

            case (state)
                // -------- 等一条应答 --------
                S_IDLE: begin
                    if (!q_empty) begin
                        // 取出队首，开始组装这一条
                        seq_lat    <= cur_seq;
                        verb_lat   <= cur_verb;
                        code_lat   <= cur_code;
                        want_state <= cur_want;
                        if (cur_kind == 2'd0) begin
                            frame_sel <= (cur_verb == 4'd0) ? F_HELLO : F_ACK;
                        end else begin
                            frame_sel <= F_ERR;
                        end
                        pos       <= 10'd0;
                        num_idx   <= 5'd0;
                        crc_init  <= 1'b1;
                        state     <= S_NEXT;
                    end
                end

                // -------- 读模板 --------
                S_NEXT: begin
                    pos <= pos + 1'b1;
                    case (c)
                        P_DEC: begin
                            dec_value <= value_at(frame_sel, num_idx);
                            dec_start <= 1'b1;
                            num_idx   <= num_idx + 1'b1;
                            num_after <= S_NEXT;
                            state     <= S_NUM_WAIT;
                        end
                        P_CRC: begin
                            hex_shift <= {crc_now, 16'h0};   // 校验码靠高位放，先发高位
                            hex_idx   <= 4'd0;
                            hex_len   <= 4'd4;
                            hex_nocrc <= 1'b1;
                            state     <= S_HEX;
                        end
                        P_BOOT: begin
                            hex_shift <= boot_id;
                            hex_idx   <= 4'd0;
                            hex_len   <= 4'd8;
                            hex_nocrc <= 1'b0;               // 启动标识是正文，要算进校验码
                            state     <= S_HEX;
                        end
                        P_VERB: begin
                            name_grp <= 3'd0; name_id <= {1'b0, verb_lat};
                            name_pos <= 5'd0; name_ret <= S_NEXT; state <= S_NAME;
                        end
                        P_CODE: begin
                            name_grp <= 3'd1; name_id <= {1'b0, code_lat};
                            name_pos <= 5'd0; name_ret <= S_NEXT; state <= S_NAME;
                        end
                        P_MODE: begin
                            name_grp <= 3'd2; name_id <= {3'h0, mode};
                            name_pos <= 5'd0; name_ret <= S_NEXT; state <= S_NAME;
                        end
                        P_RUN: begin
                            name_grp <= 3'd3; name_id <= {3'h0, run_state};
                            name_pos <= 5'd0; name_ret <= S_NEXT; state <= S_NAME;
                        end
                        P_SHAPE: begin
                            name_grp <= 3'd4; name_id <= {2'h0, cfg_shape};
                            name_pos <= 5'd0; name_ret <= S_NEXT; state <= S_NAME;
                        end
                        P_REASON: begin
                            name_grp <= 3'd5; name_id <= {2'h0, reason};
                            name_pos <= 5'd0; name_ret <= S_NEXT; state <= S_NAME;
                        end
                        P_PHASE: begin
                            ph_idx <= 9'd0;
                            state  <= S_PH_LOAD;
                        end
                        default: begin
                            lit       <= c;
                            ret_state <= S_NEXT;
                            state     <= S_LIT;
                        end
                    endcase
                end

                // -------- 发一个字节 --------
                S_LIT: begin
                    tx_data  <= lit;
                    tx_start <= 1'b1;
                    // 星号和换行不算正文，不参与校验码
                    crc_valid <= crc_feed_en && (lit != "*") && (lit != 8'h0A);
                    crc_data  <= lit;
                    state     <= S_WAIT_TX;
                end

                S_WAIT_TX: begin
                    if (tx_done) begin
                        // 换行代表这一帧结束
                        state <= (lit == 8'h0A) ? S_FRAME_END : ret_state;
                    end
                end

                // -------- 一帧结束 --------
                S_FRAME_END: begin
                    if (frame_sel != F_STATE && want_state) begin
                        frame_sel <= F_STATE;
                        pos       <= 10'd0;
                        num_idx   <= 5'd0;
                        crc_init  <= 1'b1;
                        sample    <= sample + 1'b1;
                        state     <= S_NEXT;
                    end else begin
                        state <= S_IDLE;
                    end
                end

                // -------- 十进制数 --------
                S_NUM_WAIT: begin
                    if (dec_done) begin
                        num_neg     <= dec_neg;
                        digit_idx   <= DIGITS - 1;
                        sign_done   <= 1'b0;
                        dig_started <= 1'b0;
                        state       <= S_NUM_DIG;
                    end
                end

                S_NUM_DIG: begin
                    if (num_neg && !sign_done) begin
                        lit       <= "-";
                        sign_done <= 1'b1;
                        ret_state <= S_NUM_DIG;
                        state     <= S_LIT;
                    end else if (!dig_started && digit_idx != 0
                                 && dec_bcd[4*digit_idx +: 4] == 4'd0) begin
                        digit_idx <= digit_idx - 1'b1;      // 跳过前导零
                    end else begin
                        lit         <= 8'h30 + {4'h0, dec_bcd[4*digit_idx +: 4]};
                        dig_started <= 1'b1;
                        if (digit_idx == 0) begin
                            ret_state <= num_after;
                        end else begin
                            digit_idx <= digit_idx - 1'b1;
                            ret_state <= S_NUM_DIG;
                        end
                        state <= S_LIT;
                    end
                end

                // -------- 十六进制 --------
                S_HEX: begin
                    lit         <= hex_char(hex_shift[31:28]);
                    hex_shift   <= {hex_shift[27:0], 4'h0};
                    crc_feed_en <= ~hex_nocrc;
                    hex_idx     <= hex_idx + 1'b1;
                    ret_state   <= (hex_idx + 1'b1 >= hex_len) ? S_NEXT : S_HEX;
                    state       <= S_LIT;
                end

                // -------- 名字 --------
                S_NAME: begin
                    if (nc[8]) begin
                        state <= name_ret;
                    end else begin
                        lit       <= nc[7:0];
                        name_pos  <= name_pos + 1'b1;
                        ret_state <= S_NAME;
                        state     <= S_LIT;
                    end
                end

                // -------- 相位表 --------
                S_PH_LOAD: begin
                    phase_addr <= ph_idx[7:0];
                    state      <= S_PH_READ;
                end

                S_PH_READ: begin
                    // 相位表是同步读：这一拍只是等数据到位，下一拍 phase_data 才有效
                    state <= S_PH_CONV;
                end

                S_PH_CONV: begin
                    dec_value <= {24'h0, phase_data};
                    dec_start <= 1'b1;
                    state     <= S_PH_WAIT;
                end

                S_PH_WAIT: begin
                    if (dec_done) begin
                        num_neg     <= 1'b0;      // 相位一定是非负数
                        digit_idx   <= DIGITS - 1;
                        sign_done   <= 1'b1;
                        dig_started <= 1'b0;
                        num_after   <= S_PH_AFTER;
                        state       <= S_NUM_DIG;
                    end
                end

                S_PH_AFTER: begin
                    if (ph_idx + 1'b1 >= phase_count) begin
                        state <= S_NEXT;          // 相位表发完，继续模板
                    end else begin
                        ph_idx    <= ph_idx + 1'b1;
                        lit       <= ",";
                        ret_state <= S_PH_LOAD;
                        state     <= S_LIT;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
