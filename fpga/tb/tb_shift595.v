`timescale 1ns/1ps

// Pin-level 74HC595 model. Serial bits enter Q0, so sending bit 7 first
// leaves the last eight bits in their original Q7..Q0 order after RCLK.
module hc595_model (
    input wire ser, srclk, rclk, srclr_n, oe_n,
    output reg [7:0] latched = 0,
    output wire [7:0] q
);
    reg [7:0] shift = 0;
    always @(posedge srclk or negedge srclr_n) begin
        if (!srclr_n) shift <= 8'd0;
        else shift <= {shift[6:0], ser};
    end
    always @(posedge rclk) latched <= shift;
    assign q = oe_n ? 8'bzzzzzzzz : latched;
    // OE does not modify either internal register. The test checks the shared
    // output-enable pin separately, including the old nonzero latch on STOP.
endmodule

module tb_shift595;
    reg clk = 0;
    always #10 clk = ~clk;
    reg rst_n = 0, enable = 1, sample_tick = 0;
    reg [63:0] parallel_data = 0;
    wire [7:0] data;
    wire srclk, rclk, oe_n, srclr_n, fault;
    wire [63:0] physical_latches;
    wire [63:0] physical_outputs;
    integer checks = 0, shift_edges = 0, latch_edges = 0;
    time last_data_change [0:7];
    time last_sr_rise = 0, last_sr_fall = 0, last_rise_rclk = 0;

    hap2_shift595 dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .sample_tick(sample_tick),
        .parallel_data(parallel_data), .data(data), .srclk(srclk), .rclk(rclk),
        .oe_n(oe_n), .srclr_n(srclr_n), .fault(fault)
    );
    genvar row;
    generate
        for (row=0;row<8;row=row+1) begin: g_595
            hc595_model chip (.ser(data[row]), .srclk(srclk), .rclk(rclk),
                .srclr_n(srclr_n), .oe_n(oe_n), .latched(physical_latches[row*8 +: 8]),
                .q(physical_outputs[row*8 +: 8]));
            initial last_data_change[row] = 0;
            always @(data[row]) begin
                if (rst_n && last_sr_rise != 0 && ($time-last_sr_rise) < 5)
                    $fatal(1,"DATA hold violation on physical row %0d",row);
                last_data_change[row] = $time;
            end
        end
    endgenerate

    always @(posedge srclk) begin
        if (rst_n) begin
            check(!rclk,"SRCLK does not rise while RCLK is high");
            if (last_sr_fall != 0)
                check($time-last_sr_fall >= 20,"SRCLK low width is at least one 50 MHz cycle");
            for (integer r=0;r<8;r=r+1)
                check($time-last_data_change[r] >= 20,"DATA setup time is one full 50 MHz cycle");
            last_sr_rise = $time;
            shift_edges = shift_edges+1;
        end
    end
    always @(negedge srclk) begin
        if (rst_n && last_sr_rise != 0) begin
            check($time-last_sr_rise >= 20,"SRCLK high width is at least one 50 MHz cycle");
            last_sr_fall = $time;
        end
    end
    always @(posedge rclk) begin
        if (rst_n) begin
            check(!srclk,"RCLK latches only while SRCLK is low");
            check($time-last_sr_rise >= 40,"last SRCLK rise precedes RCLK by at least 40 ns");
            last_rise_rclk = $time;
            latch_edges = latch_edges+1;
        end
    end
    always @(negedge rclk) begin
        if (rst_n && last_rise_rclk != 0)
            check($time-last_rise_rclk >= 20,"RCLK high width is at least one 50 MHz cycle");
    end
    always @(negedge oe_n) begin
        if (rst_n)
            check($time-last_rise_rclk >= 20,"first output enable follows a full RCLK settling cycle");
    end

    task check;
        input ok;
        input [8*100-1:0] label;
        begin
            checks=checks+1;
            if (ok !== 1'b1) $fatal(1,"%0s",label);
        end
    endtask
    function [63:0] expected_physical;
        input [63:0] logical_bits;
        integer r;
        begin
            for(r=0;r<8;r=r+1)
                expected_physical[r*8 +: 8] = logical_bits[(7-r)*8 +: 8];
        end
    endfunction

    task sample_begin;
        input [63:0] bits;
        begin
            @(negedge clk); parallel_data=bits; sample_tick=1;
            @(posedge clk); #1;
            check(!srclk && !rclk,"sample locks before either shift/latch edge");
        end
    endtask

    task send_frame;
        input [63:0] bits;
        integer c, rise_before, latch_before;
        reg was_on;
        begin
            rise_before=shift_edges; latch_before=latch_edges;
            was_on=!oe_n;
            sample_begin(bits);
            for(c=1;c<=18;c=c+1) begin
                @(negedge clk); sample_tick=0;
                // Make a live-bus sampling bug visible: remaining columns
                // must continue to come from the 64-bit frame captured at c=0.
                parallel_data=~bits;
                @(posedge clk);#1;
                if(c<=16) begin
                    check(srclk === (c[0]),"SRCLK follows the exact 1..16 cycle schedule");
                    check(!rclk,"RCLK remains low throughout all eight serial bits");
                end else if(c==17) begin
                    check(rclk && !srclk,"RCLK rises at cycle 17");
                    check(physical_latches == expected_physical(bits),"eight real 595 latches preserve column order and reverse only rows");
                end else begin
                    check(!rclk && !srclk && !oe_n,"cycle 18 completes and enables the fresh frame");
                    check(physical_outputs == expected_physical(bits),"enabled physical output pins match the sampled frame");
                end
                if (!was_on && c<18) check(oe_n,"first frame stays disabled until its latch settles");
                check(!fault,"a legal frame does not fault");
            end
            check(shift_edges-rise_before==8 && latch_edges-latch_before==1,"one sample creates eight shift edges and one latch edge");
        end
    endtask

    task clear_and_wait;
        begin
            @(negedge clk); enable=0; sample_tick=0;#1;
            check(oe_n,"STOP disables output immediately without a system edge");
            repeat(21) @(negedge clk);
            check(oe_n && !fault && !srclk && !rclk,"OFF finishes its clear frame and clears fault");
            check(physical_latches==0,"OFF shifts and latches zeros rather than trusting SRCLR alone");
        end
    endtask

    integer i,c,n;
    reg [63:0] pattern;
    initial begin
        repeat(3) @(negedge clk);
        check(oe_n && !srclr_n,"reset holds OE disabled and SRCLR asserted");
        rst_n=1; sample_tick=1;
        // Startup ticks overlap only the mandatory zero frame and are ignored.
        repeat(10) @(negedge clk); sample_tick=0;
        repeat(12) @(negedge clk);
        check(!fault && oe_n && physical_latches==0,"cold startup completes zero latch before waiting for a fresh sample");
        send_frame(64'h0123456789abcdef);
        for(i=0;i<64;i=i+1) send_frame(64'd1 << i);
        send_frame(64'hffffffffffffffff);
        send_frame(64'h0000000000000000);
        // All 64 logical phase positions, checked through the physical latches.
        for(i=0;i<64;i=i+1) begin
            for(n=0;n<64;n=n+1) pattern[n]=(((i-n)&63)<32);
            send_frame(pattern);
            if (i[0]) @(negedge clk); // 19/20-cycle sample spacing, like the DDS.
        end

        // Interrupt every serialization position, including before the first
        // shift edge, after the last edge, and while RCLK is high.
        for(i=0;i<=18;i=i+1) begin
            sample_begin(64'hfedcba9876543210);
            for(c=1;c<=i;c=c+1) begin
                @(negedge clk);sample_tick=0;
                @(posedge clk);#1;
            end
            clear_and_wait;
            enable=1;
            repeat(3) @(negedge clk);
            check(oe_n,"restart cannot expose the old latch before a fresh sample");
            send_frame(64'h9249249249249249 ^ i);
        end

        // One-clock OFF/ON during an active frame. Ticks arriving while the
        // zero frame is still being sent must neither enable output nor fault.
        sample_begin(64'hffffffffffffffff);
        repeat(5) begin @(negedge clk);sample_tick=0; end
        enable=0;#1;check(oe_n,"quick restart first disables output");
        @(negedge clk);enable=1;sample_tick=1;
        repeat(16) begin @(negedge clk);check(oe_n && !fault,"zero initialization ignores early sample ticks");end
        sample_tick=0;
        repeat(5) @(negedge clk);
        check(oe_n && physical_latches==0,"quick restart clears old nonzero output before waiting");
        send_frame(64'h8040201008040201);

        // Deliberate valid-frame overrun must stick fail-closed until OFF.
        sample_begin(64'hffffffffffffffff);
        @(negedge clk);sample_tick=0;
        repeat(4) @(negedge clk);
        sample_tick=1;
        @(posedge clk);#1;
        check(fault && oe_n,"overrun fails closed");
        repeat(10) begin @(negedge clk);check(fault && oe_n,"overrun fault is sticky while enable remains high");end
        clear_and_wait;enable=1;
        send_frame(64'ha55aa55aa55aa55a);

        // Reset during active serialization also requires zero initialization.
        sample_begin(64'hffffffffffffffff);
        repeat(7) begin @(negedge clk);sample_tick=0;end
        rst_n=0;#1;check(oe_n && !srclr_n,"reset immediately disables output and clears shift registers");
        repeat(3) @(negedge clk);rst_n=1;
        repeat(21) @(negedge clk);
        check(oe_n && !fault && physical_latches==0,"reset clears the stored output latch before accepting another frame");
        send_frame(64'h8000000000000001);
        clear_and_wait;
        $display("595 pin-level checks passed: %0d; shift edges=%0d, latch edges=%0d",checks,shift_edges,latch_edges);
        $finish;
    end
    initial begin #2000000;$fatal(1,"595 test timed out");end
endmodule
