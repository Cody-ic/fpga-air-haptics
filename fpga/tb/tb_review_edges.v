`timescale 1ns/1ps

// 审查回归：四态冷启动、字段边界、24/32 位输出、动态相位提交、快照一致性。
module tb_review_edges;
    reg clk = 1'b0;
    always #10 clk = ~clk;
    reg rst_n = 1'b0;
    integer checks = 0;

    task check;
        input ok;
        input [8*96-1:0] label;
        begin
            checks = checks + 1;
            if (ok !== 1'b1) $fatal(1, "%0s", label);
        end
    endtask

    reg ext_rst_n = 1'b1;
    wire por_n;
    por_reset #(.CLK_HZ(1000), .HOLD_MS(3)) u_por (
        .clk(clk), .ext_rst_n(ext_rst_n), .rst_n(por_n)
    );
    reg board_rst_n = 1'b1;
    // 使用当前板级默认值：4×4、11 mm 间距。
    wire [15:0] board_pos;   // board_top 默认 4x4 = 16 路
    board_top #(.CLK_HZ(1_000_000), .POR_MS(1), .STATE_MS(0)) u_board (
        .clk(clk), .key_rst_n(board_rst_n), .key0_n(1'b1),
        // 本地“停止”键这一版没有分配引脚（干净脚不够），所以 board_top 里没有
        // key2_n 端口；这里只把三个真实存在的键接上。
        .key1_n(1'b1), .uart_rx_pin(1'b1), .array_pos(board_pos)
    );

    reg parse_start = 1'b0;
    reg [13:0] parse_len;
    wire [12:0] parse_addr;
    reg [7:0] parse_data;
    reg [7:0] text_mem [0:127];
    wire parse_ok, parse_bad;
    wire [3:0] parse_error;
    always @(posedge clk) parse_data <= text_mem[parse_addr];
    hap2_field_parse u_parse (
        .clk(clk), .rst_n(rst_n), .start(parse_start), .len(parse_len),
        .rd_addr(parse_addr), .rd_data(parse_data),
        .parse_ok(parse_ok), .parse_bad(parse_bad), .err_code(parse_error)
    );

    // 字段层的输入已经过 CRC 校验；这里单独测试其数值语义。
    task field_case;
        input [8*64-1:0] text;
        input want_ok;
        integer len, i, guard;
        begin
            len = 0;
            for (i = 0; i < 64; i = i + 1)
                if (text[8*i +: 8] != 0) len = i + 1;
            for (i = 0; i < len; i = i + 1)
                text_mem[i] = text[8*(len-1-i) +: 8];
            @(negedge clk);
            parse_len = len;
            parse_start = 1'b1;
            @(negedge clk);
            parse_start = 1'b0;
            guard = 0;
            while (!parse_ok && !parse_bad && guard < 500) begin
                @(negedge clk);
                guard = guard + 1;
            end
            check(guard < 500, "field parser completed");
            check(parse_ok === want_ok && parse_bad === !want_ok, text);
            if (!want_ok) check(parse_error === 4'd6, "invalid blank_us reports E_RANGE");
            @(negedge clk);
        end
    endtask

    reg cfg_change = 1'b0, tbl_new = 1'b0, enable = 1'b0;
    reg [31:0] level = 32'd100;
    reg [7:0] phase_mem [0:3];
    wire [7:0] addr24, addr32;
    reg [7:0] data24, data32;
    wire [3:0] pos24, neg24, pos32, neg32;
    always @(posedge clk) begin
        data24 <= phase_mem[addr24];
        data32 <= phase_mem[addr32];
    end
    hap2_out #(.ROWS(1), .COLS(4)) u_out24 (
        .clk(clk), .rst_n(rst_n), .cfg_carrier_hz(32'd40000),
        .cfg_phase_steps(32'd64), .cfg_change(cfg_change),
        .cfg_level(level), .cfg_mod_hz(32'd0), .enable(enable),
        .tbl_new(tbl_new), .tbl_addr(addr24), .tbl_data(data24),
        .out_pos(pos24), .out_neg(neg24)
    );
    hap2_out #(.ROWS(1), .COLS(4), .ACC_BITS(32)) u_out32 (
        .clk(clk), .rst_n(rst_n), .cfg_carrier_hz(32'd40000),
        .cfg_phase_steps(32'd64), .cfg_change(cfg_change),
        .cfg_level(level), .cfg_mod_hz(32'd0), .enable(enable),
        .tbl_new(tbl_new), .tbl_addr(addr32), .tbl_data(data32),
        .out_pos(pos32), .out_neg(neg32)
    );
    hap2_top #(.STATE_MS(0)) u_top (
        .clk(clk), .rst_n(rst_n), .uart_rx_pin(1'b1), .boot_id(32'h1234),
        .key_next(1'b0), .key_play(1'b0), .key_stop(1'b0)
    );

    task count_duty;
        input integer expected;
        integer n, hi24, hi32;
        begin
            hi24 = 0;
            hi32 = 0;
            for (n = 0; n < 1250; n = n + 1) begin
                @(negedge clk);
                if (pos24[0] === 1'b1) hi24 = hi24 + 1;
                if (pos32[0] === 1'b1) hi32 = hi32 + 1;
                check(^{pos24, neg24, pos32, neg32} !== 1'bx, "known output levels");
            end
            check(hi24 >= expected-2 && hi24 <= expected+2, "24-bit output duty");
            check(hi32 >= expected-2 && hi32 <= expected+2, "32-bit output duty");
        end
    endtask

    integer i, j, guard;
    reg all_old, all_new;
    reg [31:0] boot_before;
    reg [31:0] old_offsets [0:3];
    initial begin
        check(u_board.HW_PITCH_UM == 11000, "board default matches the 11 mm R5 array");
        check(u_board.u_core.u_top.u_phase.PITCH_UM == 11000, "board pitch reaches the phase solver");
        phase_mem[0] = 0; phase_mem[1] = 3;
        phase_mem[2] = 7; phase_mem[3] = 11;
        repeat (4) begin
            @(negedge clk);
            check(por_n === 1'b0, "cold POR remains asserted without a key press");
            check(board_pos === 16'd0, "cold board outputs are off");
        end
        repeat (4) @(negedge clk);
        check(por_n === 1'b1, "cold POR releases after its hold interval");
        ext_rst_n = 1'b0;
        #1;
        check(por_n === 1'b0, "external POR asserts asynchronously");
        @(negedge clk); ext_rst_n = 1'b1;
        repeat (4) begin @(negedge clk); check(!por_n, "reset release is delayed"); end
        repeat (4) @(negedge clk);
        check(por_n === 1'b1, "POR can release again");
        rst_n = 1'b1;

        field_case("HAP3 CMD 1 PING blank_us=0*0000", 1'b0);
        field_case("HAP3 CMD 1 PING blank_us=99*0000", 1'b0);
        field_case("HAP3 CMD 1 PING blank_us=-100*0000", 1'b0);
        field_case("HAP3 CMD 1 PING blank_us=100*0000", 1'b1);
        field_case("HAP3 CMD 1 PING blank_us=100000*0000", 1'b1);
        field_case("HAP3 CMD 1 PING blank_us=100001*0000", 1'b0);
        field_case("HAP3 CMD 1 PING carrier_hz=20000*0000", 1'b1);

        cfg_change = 1'b1; tbl_new = 1'b1;
        @(negedge clk); cfg_change = 1'b0; tbl_new = 1'b0;
        repeat (100) @(negedge clk);
        enable = 1'b1;
        count_duty(625);
        level = 32'd50; count_duty(313);
        level = 32'd0;
        repeat (10) @(negedge clk);
        check({pos24, neg24, pos32, neg32} === 16'd0, "level zero disables both bridge outputs");
        level = 32'd100;

        for (i = 0; i < 4; i = i + 1) begin
            old_offsets[i] = u_out32.off[i];
            phase_mem[i] = 13 + i*4;
        end
        tbl_new = 1'b1; @(negedge clk); tbl_new = 1'b0;
        for (j = 0; j < 20; j = j + 1) begin
            @(negedge clk);
            all_old = 1'b1; all_new = 1'b1;
            for (i = 0; i < 4; i = i + 1) begin
                if (u_out32.off[i] !== old_offsets[i]) all_old = 1'b0;
                if (u_out32.off[i] !== ({24'd0, phase_mem[i]} << 26)) all_new = 1'b0;
            end
            check(all_old || all_new, "all channel phases change atomically");
        end
        check(all_new, "last channel is included in the phase commit");

        // 未计算过相位时，状态快照使用明确的零值，不读未初始化 RAM。
        force u_top.state_capture = 1'b1;
        @(negedge clk); force u_top.state_capture = 1'b0;
        repeat (40) @(negedge clk);
        for (i = 0; i < 16; i = i + 1)
            check(u_top.snap_ph[i] === 8'd0, "initial phase snapshot is known zero");
        force u_top.config_changed = 1'b1;
        @(negedge clk); release u_top.config_changed;
        guard = 0;
        while (!u_top.ph_done && guard < 1000) begin @(negedge clk); guard = guard+1; end
        check(guard < 1000, "phase engine publishes a complete table");
        force u_top.ph_pub_fx = -21'sd20;
        force u_top.ph_pub_fy = -21'sd40;
        force u_top.ph_pub_fz_um = 32'd150000;
        @(negedge clk);
        force u_top.state_capture = 1'b1;
        @(negedge clk); force u_top.state_capture = 1'b0;
        force u_top.ph_pub_fx = 21'sd6000;
        force u_top.ph_pub_fy = 21'sd8000;
        force u_top.ph_pub_fz_um = 32'd200000;
        repeat (40) @(negedge clk);
        check($signed(u_top.u_tx.fx_um) === -32'sd10, "snapshot keeps signed x coordinate");
        check($signed(u_top.u_tx.fy_um) === -32'sd20, "snapshot keeps signed y coordinate");
        check(u_top.u_tx.fz_um === 32'd150000, "snapshot height does not follow later publications");
        release u_top.ph_pub_fx; release u_top.ph_pub_fy; release u_top.ph_pub_fz_um;
        release u_top.state_capture;

        // 本地首次播放要先编译；配置接受时不能让 STOP 吞掉自动 START。
        check(u_top.u_cmd.mode == 2'd1, "cold boot remains remote and idle until a key is pressed");
        check(u_board.u_core.u_top.MAX_LINE == 8192, "board receive capacity includes full HAP3 frame");
        force u_top.loc_play = 1'b1;
        @(negedge clk); force u_top.loc_play = 1'b0;
        check(u_top.u_cmd.mode == 2'd0, "first key selects local mode without a host handshake");
        guard = 0;
        while (!u_top.walk_running && guard < 100000) begin
            @(negedge clk); guard = guard+1;
        end
        check(guard < 100000, "local first play starts after configuration commit");
        check(u_top.focus_x === 21'sd40000 && u_top.focus_y === 21'sd0,
              "local first play uses the committed circle start");
        force u_top.loc_shape = 3'd4;
        force u_top.loc_next = 1'b1;
        @(negedge clk); force u_top.loc_next = 1'b0;
        // 等旧走步器停下，再确认新图形自动开始。
        repeat (5) @(negedge clk);
        guard = 0;
        while (!u_top.walk_running && guard < 100000) begin
            @(negedge clk); guard = guard+1;
        end
        check(guard < 100000, "local shape change resumes playback");
        check(u_top.focus_x === -21'sd40000 && u_top.focus_y === -21'sd40000,
              "local resumed playback uses the newly committed square start");
        release u_top.loc_play; release u_top.loc_next; release u_top.loc_shape;

        repeat (1010) @(negedge clk);
        boot_before = u_board.u_core.boot_q;
        check((^boot_before !== 1'bx) && boot_before != 0, "board boot identifier is initialized");
        board_rst_n = 1'b0;
        repeat (5) @(negedge clk);
        board_rst_n = 1'b1;
        repeat (1010) @(negedge clk);
        check(u_board.u_core.boot_q !== boot_before, "board reset changes the boot identifier");
        boot_before = u_board.u_core.boot_q;
        repeat (20) @(negedge clk);
        check(u_board.u_core.boot_q === boot_before, "boot identifier is stable within a session");
        $display("Review edge checks passed: %0d", checks);
        $finish;
    end
endmodule
