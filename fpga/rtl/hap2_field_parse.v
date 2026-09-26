`timescale 1ns/1ps

// HAP2 字段解析：把已经通过 CRC 的一整行拆成「帧头 + 一组 key=value」。
//
// 这一层管的是**结构**，不管**语义**：
//   做：校验魔数、帧类型、序号、命令动词；认出每个字段是哪一个；
//       拒绝重复字段、非法字符、格式错误的数字；
//       数值换成整数并按协议第 3 节的范围检查；图形名/映射名换成编号；
//       path_xy_um 只记录它在行缓冲里的位置和长度，坐标解析交给后面的模块。
//   不做：不判断这条命令此刻是否被允许、不修改任何已生效配置、不产生应答。
//
// 读行缓冲和 hap2_frame_check 一样：rd_data 比 rd_addr 晚一拍，
// 所以用 idx 记录「当前 rd_data 上是第几个字节」，rd_addr 始终是 idx+1。
module hap2_field_parse #(
    parameter integer ADDR_W = 13      // 行缓冲 8192 字节，地址 13 位
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              start,      // 单拍脉冲：开始解析
    input  wire [ADDR_W:0]   len,        // 行长（不含 LF 与 CR）
    output reg  [ADDR_W-1:0] rd_addr,    // 读行缓冲的地址
    input  wire [7:0]        rd_data,    // 一拍之后有效的数据
    output reg               busy,
    output reg               parse_ok,   // 单拍脉冲：结构层通过
    output reg               parse_bad,  // 单拍脉冲：结构层不通过
    output reg  [3:0]        err_code,   // 错误分类，见 E_*
    output reg  [4:0]        bad_field,  // 出错字段编号，与字段无关时是 5'h1F
    output reg               header_ok,  // 帧头（魔数～动词）是否已完整认出
    output reg  [1:0]        kind,       // 0=CMD 1=ACK 2=ERR 3=TEL
    output reg  [15:0]       seq,
    output reg  [3:0]        verb,       // 命令编号，未识别为 V_NONE
    output reg               verb_unknown,
    output reg               field_valid, // 单拍脉冲：一个字段解析完成
    output reg  [4:0]        field_id,
    output reg  [31:0]       field_value, // 数值类字段的值（二进制补码）
    output reg  [2:0]        field_type,  // 0 数值 1 图形名 2 映射名 3 路径串 4 控制模式
    output reg  [2:0]        field_enum,  // 图形名/映射名/控制模式的编号
    output reg  [ADDR_W:0]   field_off,   // 字符串类字段在行缓冲里的起点
    output reg  [ADDR_W:0]   field_size,  // 字符串类字段的长度
    output reg  [18:0]       seen_mask    // 已经出现过的字段，按编号置位（共 19 个）
);

    // ---------------- 错误分类 ----------------
    localparam [3:0] E_NONE    = 4'd0;
    localparam [3:0] E_MAGIC   = 4'd1;   // 开头不是 HAP2
    localparam [3:0] E_HEADER  = 4'd2;   // 帧类型、序号、动词格式不对
    localparam [3:0] E_CHAR    = 4'd3;   // 出现了协议不允许的字符
    localparam [3:0] E_DUP     = 4'd4;   // 同一个字段出现了两次
    localparam [3:0] E_NUMBER  = 4'd5;   // 数值格式不对
    localparam [3:0] E_RANGE   = 4'd6;   // 数值超出协议范围
    localparam [3:0] E_ENUM    = 4'd7;   // 图形名或映射名不认识
    localparam [3:0] E_MISSING = 4'd8;   // 缺少必填字段
    localparam [3:0] E_TRUNC   = 4'd9;   // 行提前结束，没见到星号

    // ---------------- 字段编号（协议第 3 节的表）----------------
    localparam [3:0] F_CARRIER_HZ  = 4'd0;
    localparam [3:0] F_PHASE_STEPS = 4'd1;
    localparam [3:0] F_CX_UM       = 4'd2;
    localparam [3:0] F_CY_UM       = 4'd3;
    localparam [3:0] F_Z_UM        = 4'd4;
    localparam [3:0] F_RADIUS_UM   = 4'd5;
    localparam [3:0] F_REPEAT      = 4'd6;
    localparam [3:0] F_MOD_HZ      = 4'd7;
    localparam [3:0] F_LEVEL       = 4'd8;
    localparam [3:0] F_SHAPE       = 4'd9;
    localparam [3:0] F_PATH_XY     = 4'd10;
    localparam [3:0] F_PATH_CLOSED = 4'd11;
    localparam [3:0] F_HW_ROWS     = 4'd12;
    localparam [3:0] F_HW_COLS     = 4'd13;
    localparam [3:0] F_HW_PITCH    = 4'd14;
    localparam [3:0] F_MAPPING     = 4'd15;
    localparam [4:0] F_VALUE       = 5'd16;   // MODE 命令的参数 value=LOCAL/REMOTE
    localparam [4:0] F_SCAN_PATHS  = 5'd17;   // HAP3：多段草图，竖线分隔
    localparam [4:0] F_BLANK_US    = 5'd18;   // HAP3：段间关输出的跳转时长

    // CONFIG 必须带齐的 18 个字段；第 16 位是 MODE 的 value，不属于 CONFIG
    localparam [18:0] CONFIG_MASK = {3'b110, 16'hFFFF};

    // ---------------- 命令编号 ----------------
    localparam [3:0] V_NONE   = 4'hF;
    localparam [3:0] V_HELLO  = 4'd0;
    localparam [3:0] V_PING   = 4'd1;
    localparam [3:0] V_CONFIG = 4'd2;
    localparam [3:0] V_MODE   = 4'd3;
    localparam [3:0] V_START  = 4'd4;
    localparam [3:0] V_PAUSE  = 4'd5;
    localparam [3:0] V_STOP   = 4'd6;
    localparam [3:0] V_SNAP   = 4'd7;

    // ---------------- 帧类型 ----------------
    localparam [1:0] K_CMD = 2'd0;
    localparam [1:0] K_ACK = 2'd1;
    localparam [1:0] K_ERR = 2'd2;
    localparam [1:0] K_TEL = 2'd3;

    // ---------------- 值类型 ----------------
    localparam [2:0] T_NUMBER  = 3'd0;
    localparam [2:0] T_SHAPE   = 3'd1;
    localparam [2:0] T_MAPPING = 3'd2;
    localparam [2:0] T_PATH    = 3'd3;
    localparam [2:0] T_MODE    = 3'd4;   // LOCAL / REMOTE

    // ---------------- 常用字符 ----------------
    localparam [7:0] CH_SPACE = 8'h20;   // 空格
    localparam [7:0] CH_STAR  = 8'h2A;   // '*'
    localparam [7:0] CH_EQUAL = 8'h3D;   // '='
    localparam [7:0] CH_MINUS = 8'h2D;   // '-'
    localparam [7:0] CH_PLUS  = 8'h2B;   // '+'
    localparam [7:0] CH_M0 = "H";
    localparam [7:0] CH_M1 = "A";
    localparam [7:0] CH_M2 = "P";
    localparam [7:0] CH_M3 = "3";   // HAP3 的魔数末位

    // ---------------- 键名表 ----------------
    // 每个常量 128 位：键名靠**低位**放，左边补零。解析时每来一个字符就整体
    // 左移 8 位、把新字符放到最右边，所以最先到的字符最后会落在低位，
    // 与这里的写法一致。整体比较就一次问出「这是哪个字段」，而且顺带保证
    // 长度必须完全相等（补的零不可能被键名字符匹配上）。
    localparam [127:0] K_CARRIER_HZ  = {48'h0, "carrier_hz"};
    localparam [127:0] K_PHASE_STEPS = {40'h0, "phase_steps"};
    localparam [127:0] K_CX_UM       = {88'h0, "cx_um"};
    localparam [127:0] K_CY_UM       = {88'h0, "cy_um"};
    localparam [127:0] K_Z_UM        = {96'h0, "z_um"};
    localparam [127:0] K_RADIUS_UM   = {56'h0, "radius_um"};
    localparam [127:0] K_REPEAT      = {16'h0, "repeat_millihz"};
    localparam [127:0] K_MOD_HZ      = {80'h0, "mod_hz"};
    localparam [127:0] K_LEVEL       = {88'h0, "level"};
    localparam [127:0] K_SHAPE       = {88'h0, "shape"};
    localparam [127:0] K_PATH_XY     = {48'h0, "path_xy_um"};
    localparam [127:0] K_PATH_CLOSED = {40'h0, "path_closed"};
    localparam [127:0] K_HW_ROWS     = {72'h0, "hw_rows"};
    localparam [127:0] K_HW_COLS     = {72'h0, "hw_cols"};
    localparam [127:0] K_HW_PITCH    = {40'h0, "hw_pitch_um"};
    localparam [127:0] K_MAPPING     = {72'h0, "mapping"};
    localparam [127:0] K_VALUE       = {88'h0, "value"};      // MODE 命令的参数
    // 补零位数 = (16 - 字符数) 字节："scan_paths" 10 字符补 6 字节，"blank_us" 8 字符补 8 字节
    localparam [127:0] K_SCAN_PATHS  = {48'h0, "scan_paths"}; // HAP3 多段草图
    localparam [127:0] K_BLANK_US    = {64'h0, "blank_us"};   // HAP3 段间跳转时长

    // ---------------- 命令动词表（64 位）----------------
    localparam [63:0] W_HELLO  = {24'h0, "HELLO"};
    localparam [63:0] W_PING   = {32'h0, "PING"};
    localparam [63:0] W_CONFIG = {16'h0, "CONFIG"};
    localparam [63:0] W_MODE   = {32'h0, "MODE"};
    localparam [63:0] W_START  = {24'h0, "START"};
    localparam [63:0] W_PAUSE  = {24'h0, "PAUSE"};
    localparam [63:0] W_STOP   = {32'h0, "STOP"};
    localparam [63:0] W_SNAP   = {32'h0, "SNAP"};

    // ---------------- 帧类型与图形名表 ----------------
    localparam [23:0] KIND_CMD = "CMD";   // 3 个字符正好 24 位
    localparam [23:0] KIND_ACK = "ACK";
    localparam [23:0] KIND_ERR = "ERR";
    localparam [23:0] KIND_TEL = "TEL";

    localparam [127:0] S_POINT     = {88'h0, "POINT"};
    localparam [127:0] S_LINE_X    = {80'h0, "LINE_X"};
    localparam [127:0] S_LINE_Y    = {80'h0, "LINE_Y"};
    localparam [127:0] S_CIRCLE    = {80'h0, "CIRCLE"};
    localparam [127:0] S_SQUARE    = {80'h0, "SQUARE"};
    localparam [127:0] S_TRIANGLE  = {64'h0, "TRIANGLE"};
    localparam [127:0] S_ARROW     = {88'h0, "ARROW"};
    localparam [127:0] S_CUSTOM    = {80'h0, "CUSTOM"};
    localparam [127:0] M_ROW_MAJOR = {32'h0, "ROW_MAJOR_XY"};
    localparam [127:0] D_LOCAL     = {88'h0, "LOCAL"};
    localparam [127:0] D_REMOTE    = {80'h0, "REMOTE"};

    // ---------------- 字符判断 ----------------
    function is_digit;
        input [7:0] ch;
        begin
            is_digit = (ch >= "0") && (ch <= "9");
        end
    endfunction

    function is_lower;
        input [7:0] ch;
        begin
            is_lower = (ch >= "a") && (ch <= "z");
        end
    endfunction

    function is_upper;
        input [7:0] ch;
        begin
            is_upper = (ch >= "A") && (ch <= "Z");
        end
    endfunction

    function is_key_char;      // 键名允许：小写字母、数字、下划线
        input [7:0] ch;
        begin
            is_key_char = is_lower(ch) || is_digit(ch) || (ch == "_");
        end
    endfunction

    function is_value_char;    // 协议允许出现在值里的字符
        input [7:0] ch;
        begin
            is_value_char = is_lower(ch) || is_upper(ch) || is_digit(ch)
                         || (ch == "_") || (ch == ",") || (ch == ".")
                         || (ch == "?") || (ch == ":") || (ch == "+") || (ch == "-")
                         || (ch == "|");   // HAP3 用竖线分隔多段草图
        end
    endfunction

    function [2:0] type_of;    // 字段编号 -> 值类型
        input [4:0] id;
        begin
            case (id)
                F_SHAPE:   type_of = T_SHAPE;
                F_MAPPING: type_of = T_MAPPING;
                F_PATH_XY: type_of = T_PATH;
                F_SCAN_PATHS: type_of = T_PATH;   // 多段草图也是字符串，同样只记位置和长度
                F_VALUE:   type_of = T_MODE;
                default:   type_of = T_NUMBER;
            endcase
        end
    endfunction

    function in_range;         // 字段编号 -> 数值范围检查（协议第 3 节）
        input [3:0]  id;
        input [31:0] mag;      // 绝对值
        input        neg;      // 是否带负号
        begin
            case (id)
                F_CARRIER_HZ:  in_range = !neg && (mag >= 32'd20000) && (mag <= 32'd80000);
                F_PHASE_STEPS: in_range = !neg && ((mag == 32'd8)  || (mag == 32'd16)
                                       || (mag == 32'd32)  || (mag == 32'd64)
                                       || (mag == 32'd128) || (mag == 32'd256));
                F_CX_UM:       in_range = (mag <= 32'd100000);
                F_CY_UM:       in_range = (mag <= 32'd100000);
                F_Z_UM:        in_range = !neg && (mag >= 32'd20000) && (mag <= 32'd300000);
                F_RADIUS_UM:   in_range = !neg && (mag <= 32'd80000);
                F_REPEAT:      in_range = !neg && (mag >= 32'd10) && (mag <= 32'd200000);
                F_MOD_HZ:      in_range = !neg && (mag <= 32'd1000);
                F_LEVEL:       in_range = !neg && (mag <= 32'd100);
                F_PATH_CLOSED: in_range = !neg && (mag <= 32'd1);
                F_HW_ROWS:     in_range = !neg && (mag >= 32'd1) && (mag <= 32'd16);
                F_HW_COLS:     in_range = !neg && (mag >= 32'd1) && (mag <= 32'd16);
                F_HW_PITCH:    in_range = !neg && (mag >= 32'd1000) && (mag <= 32'd30000);
                F_BLANK_US:    in_range = !neg && (mag >= 32'd100) && (mag <= 32'd100000);
                default:       in_range = 1'b1;
            endcase
        end
    endfunction

    // ---------------- 状态 ----------------
    localparam [3:0] S_IDLE  = 4'd0;
    localparam [3:0] S_PRIME = 4'd1;   // 等一拍，让第一次读的数据到位
    localparam [3:0] S_MAGIC = 4'd2;
    localparam [3:0] S_SP1   = 4'd3;
    localparam [3:0] S_KIND  = 4'd4;
    localparam [3:0] S_SEQ   = 4'd5;
    localparam [3:0] S_VERB  = 4'd6;
    localparam [3:0] S_KEY   = 4'd7;
    localparam [3:0] S_VAL   = 4'd8;
    localparam [3:0] S_END   = 4'd9;

    reg [3:0]      state;
    reg [ADDR_W:0] idx;         // rd_data 当前对应第几个字节
    reg            fail_flag;

    reg [1:0]      magic_cnt;
    reg [23:0]     kind_buf;
    reg [1:0]      kind_cnt;
    reg [23:0]     seq_acc;
    reg [2:0]      seq_digits;
    reg [63:0]     verb_buf;
    reg [3:0]      verb_len;

    reg [127:0]    key_buf;
    reg [3:0]      key_len;
    reg            key_first;
    reg [4:0]      cur_key;
    reg            cur_known;
    reg [127:0]    enum_buf;
    reg [31:0]     acc;
    reg            acc_neg;
    reg [3:0]      acc_digits;
    reg [ADDR_W:0] val_start;

    wire [18:0] key_hit;
    wire        hit_any;
    wire [4:0]  hit_id;

    task fail;
        input [3:0] code;
        input [4:0] field;
        begin
            err_code  <= code;
            bad_field <= field;
            fail_flag <= 1'b1;
            state     <= S_END;
        end
    endtask

    task step;      // 前进一个字节；已经是行尾说明提前结束了
        begin
            if (idx >= len - 1'b1) begin
                err_code  <= E_TRUNC;
                bad_field <= 5'h1F;
                fail_flag <= 1'b1;
                state     <= S_END;
            end else begin
                idx     <= idx + 1'b1;
                rd_addr <= rd_addr + 1'b1;
            end
        end
    endtask

    // 键名查表
    assign key_hit = {
        (key_buf == K_BLANK_US),
        (key_buf == K_SCAN_PATHS),
        (key_buf == K_VALUE),
        (key_buf == K_MAPPING),
        (key_buf == K_HW_PITCH),
        (key_buf == K_HW_COLS),
        (key_buf == K_HW_ROWS),
        (key_buf == K_PATH_CLOSED),
        (key_buf == K_PATH_XY),
        (key_buf == K_SHAPE),
        (key_buf == K_LEVEL),
        (key_buf == K_MOD_HZ),
        (key_buf == K_REPEAT),
        (key_buf == K_RADIUS_UM),
        (key_buf == K_Z_UM),
        (key_buf == K_CY_UM),
        (key_buf == K_CX_UM),
        (key_buf == K_PHASE_STEPS),
        (key_buf == K_CARRIER_HZ)
    };
    assign hit_any = |key_hit;
    assign hit_id  = key_hit[0]  ? F_CARRIER_HZ
                   : key_hit[1]  ? F_PHASE_STEPS
                   : key_hit[2]  ? F_CX_UM
                   : key_hit[3]  ? F_CY_UM
                   : key_hit[4]  ? F_Z_UM
                   : key_hit[5]  ? F_RADIUS_UM
                   : key_hit[6]  ? F_REPEAT
                   : key_hit[7]  ? F_MOD_HZ
                   : key_hit[8]  ? F_LEVEL
                   : key_hit[9]  ? F_SHAPE
                   : key_hit[10] ? F_PATH_XY
                   : key_hit[11] ? F_PATH_CLOSED
                   : key_hit[12] ? F_HW_ROWS
                   : key_hit[13] ? F_HW_COLS
                   : key_hit[14] ? F_HW_PITCH
                   : key_hit[15] ? F_MAPPING
                   : key_hit[16] ? F_VALUE
                   : key_hit[17] ? F_SCAN_PATHS
                   : key_hit[18] ? F_BLANK_US
                   : 5'h1F;

    always @(posedge clk) begin
        if (!rst_n) begin
            state         <= S_IDLE;
            idx           <= 0;
            rd_addr       <= 0;
            busy          <= 1'b0;
            parse_ok      <= 1'b0;
            parse_bad     <= 1'b0;
            err_code      <= E_NONE;
            bad_field     <= 5'h1F;
            header_ok     <= 1'b0;
            kind          <= K_CMD;
            seq           <= 16'd0;
            verb          <= V_NONE;
            verb_unknown  <= 1'b0;
            field_valid   <= 1'b0;
            field_id      <= 5'h1F;
            field_value   <= 32'd0;
            field_type    <= T_NUMBER;
            field_enum    <= 3'd0;
            field_off     <= 0;
            field_size    <= 0;
            seen_mask     <= 19'h00000;
            fail_flag     <= 1'b0;
            magic_cnt     <= 2'd0;
            kind_buf      <= 24'h0;
            kind_cnt      <= 2'd0;
            seq_acc       <= 24'd0;
            seq_digits    <= 3'd0;
            verb_buf      <= 64'h0;
            verb_len      <= 4'd0;
            key_buf       <= 128'h0;
            key_len       <= 4'd0;
            key_first     <= 1'b0;
            cur_key       <= 5'h1F;
            cur_known     <= 1'b0;
            enum_buf      <= 128'h0;
            acc           <= 32'd0;
            acc_neg       <= 1'b0;
            acc_digits    <= 4'd0;
            val_start     <= 0;
        end else begin
            parse_ok    <= 1'b0;
            parse_bad   <= 1'b0;
            field_valid <= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        busy         <= 1'b1;
                        idx          <= 0;
                        rd_addr      <= 0;
                        fail_flag    <= 1'b0;
                        err_code     <= E_NONE;
                        bad_field    <= 5'h1F;
                        header_ok    <= 1'b0;
                        seen_mask    <= 19'h00000;
                        magic_cnt    <= 2'd0;
                        kind_buf     <= 24'h0;
                        kind_cnt     <= 2'd0;
                        seq_acc      <= 24'd0;
                        seq_digits   <= 3'd0;
                        verb_buf     <= 64'h0;
                        verb_len     <= 4'd0;
                        verb_unknown <= 1'b0;
                        state        <= S_PRIME;
                    end
                end

                S_PRIME: begin
                    rd_addr <= 1;
                    state   <= S_MAGIC;
                end

                S_MAGIC: begin
                    if (rd_data == CH_M0 && magic_cnt == 2'd0) begin
                        magic_cnt <= 2'd1;
                        step;
                    end else if (rd_data == CH_M1 && magic_cnt == 2'd1) begin
                        magic_cnt <= 2'd2;
                        step;
                    end else if (rd_data == CH_M2 && magic_cnt == 2'd2) begin
                        magic_cnt <= 2'd3;
                        step;
                    end else if (rd_data == CH_M3 && magic_cnt == 2'd3) begin
                        state <= S_SP1;
                        step;
                    end else begin
                        fail(E_MAGIC, 5'h1F);
                    end
                end

                S_SP1: begin
                    if (rd_data == CH_SPACE) begin
                        state <= S_KIND;
                        step;
                    end else begin
                        fail(E_HEADER, 5'h1F);
                    end
                end

                S_KIND: begin
                    if (rd_data == CH_SPACE) begin
                        case (kind_buf)
                            KIND_CMD: begin kind <= K_CMD; state <= S_SEQ; step; end
                            KIND_ACK: begin kind <= K_ACK; state <= S_SEQ; step; end
                            KIND_ERR: begin kind <= K_ERR; state <= S_SEQ; step; end
                            KIND_TEL: begin kind <= K_TEL; state <= S_SEQ; step; end
                            default:  fail(E_HEADER, 5'h1F);
                        endcase
                    end else if (kind_cnt < 2'd3 && is_upper(rd_data)) begin
                        kind_buf <= {kind_buf[15:0], rd_data};
                        kind_cnt <= kind_cnt + 2'd1;
                        step;
                    end else if (!is_upper(rd_data)) begin
                        fail(E_CHAR, 5'h1F);
                    end else begin
                        fail(E_HEADER, 5'h1F);
                    end
                end

                S_SEQ: begin
                    if (rd_data == CH_SPACE) begin
                        if (seq_digits == 0 || seq_acc == 0 || seq_acc > 24'd65535) begin
                            fail(E_HEADER, 5'h1F);
                        end else begin
                            seq   <= seq_acc[15:0];
                            state <= S_VERB;   // 序号后面那个空格就是动词的分隔符
                            step;
                        end
                    end else if (is_digit(rd_data) && seq_digits < 3'd5) begin
                        seq_acc    <= seq_acc * 24'd10 + {20'h0, rd_data[3:0]};
                        seq_digits <= seq_digits + 3'd1;
                        step;
                    end else begin
                        fail(E_HEADER, 5'h1F);
                    end
                end

                S_VERB: begin
                    if (rd_data == CH_SPACE || rd_data == CH_STAR) begin
                        if (verb_len == 0) begin
                            fail(E_HEADER, 5'h1F);
                        end else begin
                            case (verb_buf)
                                W_HELLO:  verb <= V_HELLO;
                                W_PING:   verb <= V_PING;
                                W_CONFIG: verb <= V_CONFIG;
                                W_MODE:   verb <= V_MODE;
                                W_START:  verb <= V_START;
                                W_PAUSE:  verb <= V_PAUSE;
                                W_STOP:   verb <= V_STOP;
                                W_SNAP:   verb <= V_SNAP;
                                default: begin
                                    verb         <= V_NONE;
                                    verb_unknown <= 1'b1;   // 不认识的命令不算结构错误
                                end
                            endcase
                            header_ok <= 1'b1;          // 帧头至此完整认出
                            if (rd_data == CH_STAR) begin
                                state <= S_END;
                            end else begin
                                key_buf   <= 128'h0;
                                key_len   <= 4'd0;
                                key_first <= 1'b1;
                                state     <= S_KEY;
                                step;
                            end
                        end
                    end else if (verb_len < 4'd8 && is_upper(rd_data)) begin
                        verb_buf <= {verb_buf[55:0], rd_data};
                        verb_len <= verb_len + 4'd1;
                        step;
                    end else begin
                        fail(E_CHAR, 5'h1F);
                    end
                end

                S_KEY: begin
                    if (rd_data == CH_EQUAL) begin
                        if (key_len == 0) begin
                            fail(E_CHAR, 5'h1F);
                        end else if (!hit_any) begin
                            // 不认识的键：忽略这个字段，但仍要检查它的字符是否合法
                            cur_known <= 1'b0;
                            cur_key   <= 5'h1F;
                            state     <= S_VAL;
                            step;
                        end else if (seen_mask[hit_id]) begin
                            fail(E_DUP, hit_id);
                        end else begin
                            cur_known   <= 1'b1;
                            cur_key     <= hit_id;
                            seen_mask[hit_id] <= 1'b1;
                            enum_buf    <= 128'h0;
                            acc         <= 32'd0;
                            acc_neg     <= 1'b0;
                            acc_digits  <= 4'd0;
                            val_start   <= idx + 1'b1;
                            state       <= S_VAL;
                            step;
                        end
                    end else if (is_key_char(rd_data)) begin
                        if (key_first && !is_lower(rd_data)) begin
                            fail(E_CHAR, 5'h1F);          // 键名必须以小写字母开头
                        end else if (key_len >= 4'd15) begin
                            fail(E_CHAR, 5'h1F);          // 键名过长
                        end else begin
                            key_buf   <= {key_buf[119:0], rd_data};   // 整体左移，新字符入低位
                            key_len   <= key_len + 4'd1;
                            key_first <= 1'b0;
                            step;
                        end
                    end else begin
                        fail(E_CHAR, 5'h1F);
                    end
                end

                S_VAL: begin
                    if (rd_data == CH_SPACE || rd_data == CH_STAR) begin
                        // 先把下一拍的去向安排好，再做本字段的收尾。
                        // 顺序很重要：如果收尾时判定失败，fail() 会把 state 改成 S_END，
                        // 覆盖掉这里的转移；反过来写的话失败就被悄悄吞掉了。
                        if (rd_data == CH_STAR) begin
                            state <= S_END;
                        end else begin
                            key_buf   <= 128'h0;
                            key_len   <= 4'd0;
                            key_first <= 1'b1;
                            state     <= S_KEY;
                            step;
                        end

                        if (cur_known) begin
                            case (type_of(cur_key))
                                T_NUMBER: begin
                                    if (acc_digits == 0) begin
                                        fail(E_NUMBER, cur_key);
                                    end else if (!in_range(cur_key, acc, acc_neg)) begin
                                        fail(E_RANGE, cur_key);
                                    end else begin
                                        field_valid <= 1'b1;
                                        field_type  <= T_NUMBER;
                                        field_id    <= cur_key;
                                        field_value <= acc_neg ? (32'd0 - acc) : acc;
                                    end
                                end

                                T_SHAPE: begin
                                    field_type <= T_SHAPE;
                                    field_id   <= cur_key;
                                    if      (enum_buf == S_POINT)    begin field_enum <= 3'd0; field_valid <= 1'b1; end
                                    else if (enum_buf == S_LINE_X)   begin field_enum <= 3'd1; field_valid <= 1'b1; end
                                    else if (enum_buf == S_LINE_Y)   begin field_enum <= 3'd2; field_valid <= 1'b1; end
                                    else if (enum_buf == S_CIRCLE)   begin field_enum <= 3'd3; field_valid <= 1'b1; end
                                    else if (enum_buf == S_SQUARE)   begin field_enum <= 3'd4; field_valid <= 1'b1; end
                                    else if (enum_buf == S_TRIANGLE) begin field_enum <= 3'd5; field_valid <= 1'b1; end
                                    else if (enum_buf == S_ARROW)    begin field_enum <= 3'd6; field_valid <= 1'b1; end
                                    else if (enum_buf == S_CUSTOM)   begin field_enum <= 3'd7; field_valid <= 1'b1; end
                                    else begin fail(E_ENUM, cur_key); end
                                end

                                T_MAPPING: begin
                                    field_type <= T_MAPPING;
                                    field_id   <= cur_key;
                                    if (enum_buf == M_ROW_MAJOR) begin
                                        field_enum  <= 3'd0;
                                        field_valid <= 1'b1;
                                    end else begin
                                        fail(E_ENUM, cur_key);
                                    end
                                end

                                T_MODE: begin
                                    field_type <= T_MODE;
                                    field_id   <= cur_key;
                                    if (enum_buf == D_LOCAL) begin
                                        field_enum  <= 3'd0;
                                        field_valid <= 1'b1;
                                    end else if (enum_buf == D_REMOTE) begin
                                        field_enum  <= 3'd1;
                                        field_valid <= 1'b1;
                                    end else begin
                                        fail(E_ENUM, cur_key);
                                    end
                                end

                                default: begin           // T_PATH
                                    field_valid <= 1'b1;
                                    field_type  <= T_PATH;
                                    field_id    <= cur_key;
                                    field_off   <= val_start;
                                    field_size  <= idx - val_start;
                                end
                            endcase
                        end
                    end else if (!is_value_char(rd_data)) begin
                        fail(E_CHAR, cur_known ? cur_key : 5'h1F);
                    end else if (!cur_known) begin
                        step;                                        // 未知键的值，跳过
                    end else begin
                        case (type_of(cur_key))
                            T_NUMBER: begin
                                if ((rd_data == CH_MINUS || rd_data == CH_PLUS)
                                        && acc_digits == 0 && val_start == idx) begin
                                    acc_neg <= (rd_data == CH_MINUS);
                                    step;
                                end else if (is_digit(rd_data) && acc_digits < 4'd9) begin
                                    acc        <= acc * 32'd10 + {28'h0, rd_data[3:0]};
                                    acc_digits <= acc_digits + 4'd1;
                                    step;
                                end else begin
                                    fail(E_NUMBER, cur_key);
                                end
                            end

                            T_SHAPE, T_MAPPING, T_MODE: begin
                                enum_buf <= {enum_buf[119:0], rd_data};
                                step;
                            end

                            default: begin                               // T_PATH
                                step;
                            end
                        endcase
                    end
                end

                S_END: begin
                    busy <= 1'b0;
                    if (fail_flag) begin
                        parse_bad <= 1'b1;
                    end else if (verb == V_CONFIG
                                 && (seen_mask & CONFIG_MASK) != CONFIG_MASK) begin
                        err_code  <= E_MISSING;      // CONFIG 必须带齐 18 个字段
                        bad_field <= 5'h1F;
                        parse_bad <= 1'b1;
                    end else begin
                        parse_ok <= 1'b1;
                    end
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
