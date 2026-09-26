`timescale 1ns/1ps

// 多段草图解析：把 config 里的 scan_paths 文本读成两张表。
//
// 输入是一段字符串，形如  "-10000:0,0:5000,10000:0|0:10000,5000:12000"
//   :  分开横坐标和纵坐标
//   ,  分开同一段里的两个点
//   |  分开两段（两笔）
//
// 输出两张表：
//   point table  每个点的 (x, y)，**0.5 微米为单位**的有符号整数
//                （阵元坐标可能出现半微米，乘 2 之后全是整数）
//   stroke table 每一笔从第几个点开始、有几个点
//
// 解析时顺带做完所有检查，任何一项不过就整份拒绝——绝不做"截断后继续"：
//   - 每点坐标绝对值 ≤ 300000 µm（即 ±600000 个 0.5 µm 单位）
//   - 总点数 ≤ 256，段数 ≤ 32
//   - 同一段里相邻的两个点不能重合
//   - 每段至少一个点（出现空段就拒绝）
//   - 原文长度 ≤ MAX_TXT（4 KB，上位机的草图字符串上限）
//
// 协议里「这一段没有多段草图」写作 `scan_paths=NONE`，所以这里专门认一下这四个
// 字符：通过、点数和段数都是 0，并把 is_none 拉高告诉上层「没有草图」。
//
// 点表是简单双口 RAM：写口在本模块，读口给轨迹发生器（同步读，一拍出数据）。
//
// 流程是严格顺序的：S_CH 处理一个字节，遇到点结束就去 S_STORE_PT 落表，
// 落完再回来读下一个字节。多花一两个节拍，但每一步发生什么一眼能看清。
module hap2_scan_parse #(
    parameter integer ADDR_W  = 13,    // 行缓冲地址位宽（8192 字节）
    parameter integer PT_BITS = 21,    // 单个坐标的位宽
    parameter integer MAX_PTS = 256,
    parameter integer MAX_STK = 32,
    parameter integer MAX_TXT = 4096   // 草图原文最长多少字节
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire                      start,     // 单拍脉冲：开始解析
    input  wire [ADDR_W-1:0]         src_off,   // 字符串在行缓冲里的起点（总是 < 8192）
    input  wire [ADDR_W:0]           src_len,   // 字符串长度
    output reg  [ADDR_W-1:0]         rd_addr,   // 行缓冲读口
    input  wire [7:0]                rd_data,   // 一拍之后有效
    output reg                       busy,
    output reg                       ok,        // 单拍脉冲：解析通过
    output reg                       bad,       // 单拍脉冲：解析失败
    output reg  [8:0]                point_count,
    output reg  [5:0]                stroke_count,
    output reg                       is_none,   // 原文就是 NONE（没有草图）
    // ---- 给轨迹发生器读的表 ----
    input  wire [7:0]                pt_addr,
    output reg  signed [PT_BITS-1:0] pt_x,
    output reg  signed [PT_BITS-1:0] pt_y,
    input  wire [4:0]                st_addr,
    output wire [8:0]                st_start,
    output wire [8:0]                st_len
);

    // 字符串里是微米，所以范围检查也用微米；存进点表时才换算成 0.5 微米单位
    localparam signed [PT_BITS-1:0] LIMIT = 21'sd300000;   // ±300000 µm

    localparam [2:0] S_IDLE     = 3'd0;
    localparam [2:0] S_PRIME    = 3'd1;   // 等一拍，让第一次读的数据到位
    localparam [2:0] S_CH       = 3'd2;   // 处理当前字节
    localparam [2:0] S_STORE_PT = 3'd3;   // 把攒好的点写进点表
    localparam [2:0] S_STORE_ST = 3'd4;   // 把刚刚结束的一段写进段表
    localparam [2:0] S_DONE     = 3'd5;
    localparam [2:0] S_NONE     = 3'd6;   // 认一下 NONE 这四个字符

    reg signed [PT_BITS-1:0] pt_xram [0:MAX_PTS-1];
    reg signed [PT_BITS-1:0] pt_yram [0:MAX_PTS-1];
    reg [8:0]                st_startram [0:MAX_STK-1];
    reg [8:0]                st_lenram   [0:MAX_STK-1];

    reg [2:0]      state;
    reg [ADDR_W:0] idx;          // rd_data 当前对应字符串里的第几个字节
    reg            fail_flag;

    reg signed [PT_BITS-1:0] acc;      // 正在攒的那个数（绝对值）
    reg [2:0]      digits;
    reg            neg;
    reg            in_num;
    reg            have_colon;         // 这个点已经见过冒号（横坐标攒完了）
    reg signed [PT_BITS-1:0] cur_x;    // 当前点已攒好的横坐标
    reg signed [PT_BITS-1:0] new_x;    // 待写入的点（绝对值已带符号）
    reg signed [PT_BITS-1:0] new_y;
    reg            st_ends;            // 这个点结束时是否也要收一段
    reg            pt_is_last;         // 这个点是整串的最后一个点
    reg signed [PT_BITS-1:0] prev_x;
    reg signed [PT_BITS-1:0] prev_y;
    reg            has_prev;
    reg [8:0]      pt_idx;             // 下一个点写到哪
    reg [5:0]      st_idx;             // 下一段写到哪
    reg [8:0]      st_pt_start;
    reg [1:0]      none_i;             // NONE 认到第几个字符
    reg            none_ph;            // 0 = 等数据；1 = 数据有效可以看

    assign st_start = st_startram[st_addr];
    assign st_len   = st_lenram[st_addr];

    // 点表用同步读：这一拍给地址，下一拍 pt_x / pt_y 才有效
    always @(posedge clk) begin
        pt_x <= pt_xram[pt_addr];
        pt_y <= pt_yram[pt_addr];
    end

    function is_digit;
        input [7:0] ch;
        begin
            is_digit = (ch >= "0") && (ch <= "9");
        end
    endfunction

    // NONE 的第 i 个字符
    function [7:0] none_char;
        input [1:0] i;
        begin
            case (i)
                2'd0:    none_char = "N";
                2'd1:    none_char = "O";
                2'd2:    none_char = "N";
                default: none_char = "E";
            endcase
        end
    endfunction

    wire at_last = (idx >= src_len - 1'b1);    // 当前是字符串最后一个字节

    // 累加器与其变体。注意 acc_next 是本拍加上这一位之后的值——
    // 非阻塞赋值要下一拍才生效，收尾时直接用 acc 会丢掉最后一位数字。
    wire signed [PT_BITS-1:0] acc_next  = acc * 32'sd10 + {17'h0, rd_data[3:0]};
    wire signed [PT_BITS-1:0] acc_val   = neg ? -acc      : acc;
    wire signed [PT_BITS-1:0] acc_val_n = neg ? -acc_next : acc_next;
    wire signed [PT_BITS-1:0] half_val  = acc_val   * 2;   // 微米 -> 0.5 微米
    wire signed [PT_BITS-1:0] half_val_n= acc_val_n * 2;

    always @(posedge clk) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            idx          <= 0;
            rd_addr      <= 0;
            busy         <= 1'b0;
            ok           <= 1'b0;
            bad          <= 1'b0;
            point_count  <= 9'd0;
            stroke_count <= 6'd0;
            fail_flag    <= 1'b0;
            acc          <= 0;
            digits       <= 3'd0;
            neg          <= 1'b0;
            in_num       <= 1'b0;
            have_colon   <= 1'b0;
            cur_x        <= 0;
            new_x        <= 0;
            new_y        <= 0;
            st_ends      <= 1'b0;
            pt_is_last   <= 1'b0;
            prev_x       <= 0;
            prev_y       <= 0;
            has_prev     <= 1'b0;
            pt_idx       <= 9'd0;
            st_idx       <= 6'd0;
            st_pt_start  <= 9'd0;
            is_none      <= 1'b0;
            none_i       <= 2'd0;
            none_ph      <= 1'b0;
        end else begin
            ok  <= 1'b0;
            bad <= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        busy        <= 1'b1;
                        idx         <= 0;
                        rd_addr     <= src_off;
                        fail_flag   <= 1'b0;
                        is_none     <= 1'b0;
                        acc         <= 0;
                        digits      <= 3'd0;
                        neg         <= 1'b0;
                        in_num      <= 1'b0;
                        have_colon  <= 1'b0;
                        cur_x       <= 0;
                        has_prev    <= 1'b0;
                        pt_idx      <= 9'd0;
                        st_idx      <= 6'd0;
                        st_pt_start <= 9'd0;
                        none_i      <= 2'd0;
                        none_ph     <= 1'b0;
                        if (src_len == 0 || src_len > MAX_TXT) begin
                            fail_flag <= 1'b1;      // 空串或超出原文上限
                            state     <= S_DONE;
                        end else if (src_len == 4) begin
                            state <= S_NONE;        // 可能是 NONE，也可能是别的 4 字符
                        end else begin
                            state <= S_PRIME;
                        end
                    end
                end

                // -------- 辨认 NONE --------
                // 点表是同步读：地址在上一拍发出，数据要到「再下一拍」才有效，
                // 所以每认一个字符要两拍（一拍等数据，一拍比对）。
                S_NONE: begin
                    if (!none_ph) begin
                        none_ph <= 1'b1;
                    end else if (rd_data !== none_char(none_i)) begin
                        // 不是 NONE：退回正常的草图解析重来一遍。
                        // （4 个字符的合法草图是存在的，比如单点 "10:2"）
                        // 不是 NONE：退回正常的草图解析重来一遍。
                        // （4 个字符的合法草图是存在的，比如单点 "10:2"）
                        rd_addr <= src_off;
                        none_ph <= 1'b0;
                        state   <= S_PRIME;
                    end else if (none_i == 2'd3) begin
                        point_count  <= 9'd0;
                        stroke_count <= 6'd0;
                        is_none      <= 1'b1;
                        state        <= S_DONE;
                    end else begin
                        none_i  <= none_i + 2'd1;
                        rd_addr <= rd_addr + 1'b1;
                        none_ph <= 1'b0;
                    end
                end

                S_PRIME: begin
                    // 这一拍地址已经喂给 RAM，下一拍数据才到
                    rd_addr <= rd_addr + 1'b1;
                    state   <= S_CH;
                end

                // -------- 处理一个字节 --------
                S_CH: begin
                    if (is_digit(rd_data)) begin
                        if (digits >= 3'd6) begin
                            fail_flag <= 1'b1;
                            state     <= S_DONE;
                        end else begin
                            acc    <= acc_next;
                            digits <= digits + 3'd1;
                            in_num <= 1'b1;
                            if (at_last) begin
                                if (!have_colon || acc_next > LIMIT) begin
                                    fail_flag <= 1'b1;
                                    state     <= S_DONE;
                                end else begin
                                    // 最后一个字节是数字：这个点落表，并且收掉最后一段
                                    new_x      <= cur_x;
                                    new_y      <= half_val_n;   // 含刚才这一位，且已换算
                                    st_ends    <= 1'b1;
                                    pt_is_last <= 1'b1;
                                    state      <= S_STORE_PT;
                                end
                            end else begin
                                idx     <= idx + 1'b1;
                                rd_addr <= rd_addr + 1'b1;
                            end
                        end
                    end else if (rd_data == "-" && !in_num && digits == 0) begin
                        neg <= 1'b1;
                        if (at_last) begin
                            fail_flag <= 1'b1;      // 以负号结尾，非法
                            state     <= S_DONE;
                        end else begin
                            idx     <= idx + 1'b1;
                            rd_addr <= rd_addr + 1'b1;
                        end
                    end else if (rd_data == ":" && in_num) begin
                        if (acc > LIMIT) begin
                            fail_flag <= 1'b1;
                            state     <= S_DONE;
                        end else begin
                            cur_x  <= half_val;
                            acc    <= 0; digits <= 3'd0; neg <= 1'b0; in_num <= 1'b0;
                            have_colon <= 1'b1;
                            if (at_last) begin
                                fail_flag <= 1'b1;  // 以冒号结尾，缺纵坐标
                                state     <= S_DONE;
                            end else begin
                                idx     <= idx + 1'b1;
                                rd_addr <= rd_addr + 1'b1;
                            end
                        end
                    end else if ((rd_data == "," || rd_data == "|") && in_num) begin
                        if (!have_colon || acc > LIMIT) begin
                            fail_flag <= 1'b1;
                            state     <= S_DONE;
                        end else begin
                            new_x      <= cur_x;
                            new_y      <= half_val;
                            st_ends    <= (rd_data == "|");
                            pt_is_last <= at_last;
                            state      <= S_STORE_PT;
                        end
                    end else begin
                        fail_flag <= 1'b1;          // 出现的字符不符合格式
                        state     <= S_DONE;
                    end
                end

                // -------- 把一个点写进点表 --------
                S_STORE_PT: begin
                    if (pt_idx >= MAX_PTS) begin
                        fail_flag <= 1'b1;          // 点数超容量：拒绝，不截断
                        state     <= S_DONE;
                    end else if (has_prev && new_x == prev_x && new_y == prev_y) begin
                        fail_flag <= 1'b1;          // 同一段里相邻点重合
                        state     <= S_DONE;
                    end else begin
                        pt_xram[pt_idx[7:0]] <= new_x;
                        pt_yram[pt_idx[7:0]] <= new_y;
                        pt_idx   <= pt_idx + 1'b1;
                        prev_x   <= new_x;
                        prev_y   <= new_y;
                        has_prev <= 1'b1;
                        if (st_ends) begin
                            state <= S_STORE_ST;
                        end else if (pt_is_last) begin
                            state <= S_STORE_ST;    // 最后一个点：顺带收掉最后一段
                        end else begin
                            acc     <= 0; digits <= 3'd0; neg <= 1'b0; in_num <= 1'b0;
                            have_colon <= 1'b0;
                            idx     <= idx + 1'b1;
                            rd_addr <= rd_addr + 1'b1;
                            state   <= S_CH;
                        end
                    end
                end

                // -------- 把刚结束的一段写进段表 --------
                S_STORE_ST: begin
                    if (pt_idx == st_pt_start) begin
                        fail_flag <= 1'b1;          // 空段
                        state     <= S_DONE;
                    end else if (st_idx >= MAX_STK) begin
                        fail_flag <= 1'b1;          // 段数超容量
                        state     <= S_DONE;
                    end else if (pt_is_last) begin
                        st_startram[st_idx[4:0]] <= st_pt_start;
                        st_lenram[st_idx[4:0]]   <= pt_idx - st_pt_start;
                        point_count  <= pt_idx;
                        stroke_count <= st_idx + 1'b1;
                        state        <= S_DONE;
                    end else begin
                        st_startram[st_idx[4:0]] <= st_pt_start;
                        st_lenram[st_idx[4:0]]   <= pt_idx - st_pt_start;
                        st_idx      <= st_idx + 1'b1;
                        st_pt_start <= pt_idx;
                        has_prev    <= 1'b0;        // 新的一段，查重从头开始
                        acc     <= 0; digits <= 3'd0; neg <= 1'b0; in_num <= 1'b0;
                        have_colon <= 1'b0;
                        idx     <= idx + 1'b1;
                        rd_addr <= rd_addr + 1'b1;
                        state   <= S_CH;
                    end
                end

                S_DONE: begin
                    busy <= 1'b0;
                    if (fail_flag) begin
                        bad <= 1'b1;
                    end else begin
                        ok <= 1'b1;                 // 唯一的成功出口
                    end
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
