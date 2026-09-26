`timescale 1ns/1ps

// HAP2 接收通路顶层：串口收字节 → 攒成一整行 → 校验 CRC → 解析字段。
//
// 三段是串行的，共用同一块行缓冲：CRC 校验先跑完，字段解析才开始。
// 因为两者不会同时在读，用一个多路选择器把读地址让给当前在跑的那一个。
//
// 输出分三层：
//   frame_ok / frame_bad + err_code   整帧的结论
//   kind / seq / verb / verb_unknown  帧头
//   field_* / seen_mask               逐个字段的解析结果
// 命令是否被允许、要不要改配置、怎么应答，都属于下一个模块。
module hap2_rx #(
    parameter integer CLK_HZ   = 50_000_000,
    parameter integer BAUD     = 115200,
    parameter integer MAX_LINE = 4096,
    parameter integer ADDR_W   = 13
) (
    input  wire            clk,
    input  wire            rst_n,
    input  wire            uart_rx_pin,
    // 行就绪
    output reg             line_ready,     // 单拍脉冲：收到一整行
    output reg  [ADDR_W:0] line_len,       // 行长（不含 LF 与 CR）
    output reg             line_dropped,   // 单拍脉冲：本行超长，已整行丢弃
    // 整帧结论
    output reg             frame_ok,       // 单拍脉冲：CRC 与字段解析都通过
    output reg             frame_bad,      // 单拍脉冲：其中一步没通过
    output reg  [3:0]      err_code,       // 错误分类，见 hap2_field_parse 的 E_*
    output reg  [4:0]      bad_field,      // 出错字段编号
    output wire            header_ok,      // 帧头是否已完整认出
    // 帧头
    output wire [1:0]      kind,
    output wire [15:0]     seq,
    output wire [3:0]      verb,
    output wire            verb_unknown,
    // 字段流
    output wire            field_valid,
    output wire [4:0]      field_id,
    output wire [31:0]     field_value,
    output wire [2:0]      field_type,
    output wire [2:0]      field_enum,
    output wire [ADDR_W:0] field_off,
    output wire [ADDR_W:0] field_size,
    output wire [18:0]     seen_mask,
    // 调试
    output wire [15:0]     crc_value,
    output wire [15:0]     crc_expected,
    output wire            rx_valid,
    output wire            rx_error
);

    // 顶层自己用的错误码：帧格式或 CRC 没过（对应字段解析里没有这一项）
    localparam [3:0] E_FRAME = 4'hE;

    localparam [1:0] C_IDLE  = 2'd0;
    localparam [1:0] C_CHECK = 2'd1;
    localparam [1:0] C_PARSE = 2'd2;

    reg [1:0] ctl;
    reg       check_start;
    reg       parse_start;

    wire              byte_valid;
    wire [7:0]        byte_data;
    wire              byte_error;
    wire              line_ready_now;
    wire [ADDR_W:0]   line_len_now;
    wire              line_dropped_now;

    wire [ADDR_W-1:0] check_addr;
    wire [ADDR_W-1:0] parse_addr;
    wire [7:0]        buf_data;
    wire              parse_busy;
    wire              check_ok;
    wire              check_bad;
    wire              parse_ok_now;
    wire              parse_bad_now;
    wire [3:0]        parse_err;
    wire [4:0]        parse_badfield;

    // 读口让给正在读的那一个
    wire [ADDR_W-1:0] buf_addr;
    assign buf_addr = parse_busy ? parse_addr : check_addr;

    assign rx_valid = byte_valid;
    assign rx_error = byte_error;

    uart_rx #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD)
    ) u_uart (
        .clk      (clk),
        .rst_n    (rst_n),
        .rx_line  (uart_rx_pin),
        .rx_data  (byte_data),
        .rx_valid (byte_valid),
        .rx_error (byte_error)
    );

    hap2_line_rx #(
        .MAX_LINE (MAX_LINE),
        .ADDR_W   (ADDR_W)
    ) u_line (
        .clk          (clk),
        .rst_n        (rst_n),
        .rx_valid     (byte_valid),
        .rx_data      (byte_data),
        .line_ready   (line_ready_now),
        .line_len     (line_len_now),
        .line_dropped (line_dropped_now),
        .rd_addr      (buf_addr),
        .rd_data      (buf_data)
    );

    hap2_frame_check #(
        .ADDR_W (ADDR_W)
    ) u_check (
        .clk          (clk),
        .rst_n        (rst_n),
        .start        (check_start),
        .len          (line_len_now),
        .rd_addr      (check_addr),
        .rd_data      (buf_data),
        .frame_ok     (check_ok),
        .frame_bad    (check_bad),
        .crc_value    (crc_value),
        .crc_expected (crc_expected)
    );

    hap2_field_parse #(
        .ADDR_W (ADDR_W)
    ) u_parse (
        .clk           (clk),
        .rst_n         (rst_n),
        .start         (parse_start),
        .len           (line_len_now),
        .rd_addr       (parse_addr),
        .rd_data       (buf_data),
        .busy          (parse_busy),
        .parse_ok      (parse_ok_now),
        .parse_bad     (parse_bad_now),
        .err_code      (parse_err),
        .bad_field     (parse_badfield),
        .header_ok     (header_ok),
        .kind          (kind),
        .seq           (seq),
        .verb          (verb),
        .verb_unknown  (verb_unknown),
        .field_valid   (field_valid),
        .field_id      (field_id),
        .field_value   (field_value),
        .field_type    (field_type),
        .field_enum    (field_enum),
        .field_off     (field_off),
        .field_size    (field_size),
        .seen_mask     (seen_mask)
    );

    // 三段流水线的控制器
    always @(posedge clk) begin
        if (!rst_n) begin
            ctl          <= C_IDLE;
            check_start  <= 1'b0;
            parse_start  <= 1'b0;
            line_ready   <= 1'b0;
            line_len     <= 0;
            line_dropped <= 1'b0;
            frame_ok     <= 1'b0;
            frame_bad    <= 1'b0;
            err_code     <= 4'd0;
            bad_field    <= 4'hF;
        end else begin
            check_start  <= 1'b0;
            parse_start  <= 1'b0;
            line_ready   <= 1'b0;
            line_dropped <= 1'b0;
            frame_ok     <= 1'b0;
            frame_bad    <= 1'b0;

            if (line_ready_now) begin
                line_ready <= 1'b1;
                line_len   <= line_len_now;
            end
            if (line_dropped_now) begin
                line_dropped <= 1'b1;
            end

            case (ctl)
                C_IDLE: begin
                    if (line_ready_now) begin
                        check_start <= 1'b1;      // 下一拍开始校验 CRC
                        ctl         <= C_CHECK;
                    end
                end

                C_CHECK: begin
                    if (check_ok) begin
                        parse_start <= 1'b1;      // 下一拍开始解析字段
                        ctl         <= C_PARSE;
                    end else if (check_bad) begin
                        err_code  <= E_FRAME;
                        bad_field <= 4'hF;
                        frame_bad <= 1'b1;
                        ctl       <= C_IDLE;
                    end
                end

                C_PARSE: begin
                    if (parse_ok_now) begin
                        frame_ok <= 1'b1;
                        ctl      <= C_IDLE;
                    end else if (parse_bad_now) begin
                        err_code  <= parse_err;
                        bad_field <= parse_badfield;
                        frame_bad <= 1'b1;
                        ctl       <= C_IDLE;
                    end
                end

                default: ctl <= C_IDLE;
            endcase
        end
    end

endmodule
