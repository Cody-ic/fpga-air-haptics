`timescale 1ns/1ps
module tb_debug_u102;
    reg clk = 0, rst_n = 0;
    always #10 clk = ~clk;
    wire [7:0] data;
    wire sclk, latch, oe_n, clear_n;
    generate if (`FPGA_DEBUG_U102 == 1) begin : board_test
        wire tx;
        wire [2:0] led;
        board_top #(.POR_MS(0)) dut (
            .clk(clk), .key_rst_n(rst_n), .key0_n(1'b1), .key1_n(1'b1),
            .uart_rx_pin(1'b1), .uart_tx_pin(tx), .led(led),
            .sr_data(data), .sr_clk(sclk), .sr_latch(latch),
            .sr_oe_n(oe_n), .sr_clear_n(clear_n)
        );
    end else begin : module_test
        hap2_debug_u102 dut (.clk(clk), .rst_n(rst_n), .sr_data(data),
            .sr_clk(sclk), .sr_latch(latch), .sr_oe_n(oe_n), .sr_clear_n(clear_n));
    end endgenerate

    // External eight-lane 595 model, independent of the DUT's counters.
    reg [63:0] shifted = 0, latched = 0;
    localparam [63:0] U102_MASK = 64'h0800000000000000; // DATA7 QD
    integer lane, pulses = 0, rises = 0, falls = 0;
    time last_latch = 0, last_rise = 0, last_fall = 0;
    reg have_frame = 0;
    always @(negedge clear_n) begin
        shifted = 0;
        pulses = 0;
        have_frame = 0;
        last_latch = 0;
        last_rise = 0;
        last_fall = 0;
    end
    always @(posedge sclk) begin
        if (!clear_n) $fatal(1, "Shift clock during reset");
        for (lane=0; lane<8; lane=lane+1)
            shifted[lane*8 +: 8] = {shifted[lane*8 +: 7], data[lane]};
        pulses = pulses + 1;
    end
    always @(posedge latch) begin
        if (pulses != 8) $fatal(1, "Expected eight shift clocks, got %0d", pulses);
        if (last_latch && $time-last_latch != 12500) $fatal(1, "Latch spacing is not 12.5 us");
        if ((shifted & ~U102_MASK) !== 64'b0) $fatal(1, "Another channel driven");
        if (!have_frame && shifted !== 0) $fatal(1, "First frame must clear ALL outputs");
        latched = shifted;
        have_frame = 1;
        pulses = 0;
        last_latch = $time;
    end
    always @(negedge oe_n) begin
        if (!have_frame || $time-last_latch < 40) $fatal(1, "Enabled before known zero frame settled");
    end
    always @(latched[59]) begin
        if (clear_n && have_frame) begin
            if (latched[59]) begin
                if (last_rise && $time-last_rise != 25000) $fatal(1, "Not 40 kHz");
                if (last_fall && $time-last_fall != 12500) $fatal(1, "Wrong low width");
                last_rise = $time;
                rises = rises + 1;
            end else if (last_rise) begin
                if ($time-last_rise != 12500) $fatal(1, "Wrong high width");
                last_fall = $time;
                falls = falls + 1;
            end
        end
    end
    initial begin
        #100;
        if (oe_n !== 1) $fatal(1, "Reset must mute");
        rst_n = 1;
        #510123;
        rst_n = 0;
        #1;
        if (oe_n !== 1) $fatal(1, "Reset did not mute immediately");
        #100;
        rst_n = 1;
        #510123;
        if (rises < 40 || falls < 40) $fatal(1, "Insufficient complete periods");
        $display("PASS U102 only; external QD period=25us, high=low=12.5us; reset/restart safe");
        $finish;
    end
    initial begin #2000000; $fatal(1, "Timeout"); end
endmodule
