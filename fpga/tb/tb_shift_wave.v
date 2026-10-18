`timescale 1ns/1ps

module hc595_wave_model (
    input wire ser, srclk, rclk, srclr_n, oe_n,
    output reg [7:0] latched = 0,
    output wire [7:0] driver_input
);
    reg [7:0] shift = 0;
    always @(posedge srclk or negedge srclr_n) begin
        if (!srclr_n) shift <= 0;
        else shift <= {shift[6:0],ser};
    end
    always @(posedge rclk) latched <= shift;
    // The PCB's input pull-downs make a disabled 595 read low at the driver.
    assign driver_input = oe_n ? 8'd0 : latched;
endmodule

// Integration regression for the actual DDS -> serializer -> 8 physical 595s.
// Each logical channel receives a different phase code (0..63). The test
// measures physical driver inputs, not hap2_out's internal parallel vector.
module tb_shift_wave;
    reg clk=0;
    always #10 clk=~clk;
    reg rst_n=0, cfg_change=0, tbl_new=0, enable=0;
    wire [7:0] tbl_addr;
    reg [7:0] tbl_data;
    reg [7:0] phases [0:63];
    wire [63:0] parallel_data;
    wire sample_tick, drive_enable;
    wire [7:0] data;
    wire srclk,rclk,oe_n,srclr_n,fault;
    wire [63:0] physical,latched;
    wire [63:0] logical_pins;
    always @(posedge clk) tbl_data <= phases[tbl_addr];
    hap2_out #(.ROWS(8),.COLS(8),.CLK_HZ(50000000),.ACC_BITS(32)) u_out (
        .clk(clk),.rst_n(rst_n),.cfg_carrier_hz(32'd40000),
        .cfg_phase_steps(32'd64),.cfg_change(cfg_change),
        .cfg_level(32'd100),.cfg_mod_hz(32'd0),.enable(enable),
        .tbl_new(tbl_new),.tbl_addr(tbl_addr),.tbl_data(tbl_data),
        .channel_mask(64'hffffffffffffffff), .phase_offsets(512'd0),
        .out_pos(parallel_data),.sample_tick(sample_tick),.drive_enable(drive_enable)
    );
    hap2_shift595 u_shift (
        .clk(clk),.rst_n(rst_n),.enable(drive_enable),.sample_tick(sample_tick),
        .parallel_data(parallel_data),.data(data),.srclk(srclk),.rclk(rclk),
        .oe_n(oe_n),.srclr_n(srclr_n),.fault(fault)
    );
    genvar row;
    generate
        for(row=0;row<8;row=row+1) begin:g_row
            hc595_wave_model chip (
                .ser(data[row]),.srclk(srclk),.rclk(rclk),.srclr_n(srclr_n),.oe_n(oe_n),
                .latched(latched[row*8 +: 8]),.driver_input(physical[row*8 +: 8])
            );
            assign logical_pins[(7-row)*8 +: 8]=physical[row*8 +: 8];
        end
    endgenerate

    integer checks=0,cycle_count=0,last_tick=0,ticks19=0,ticks20=0;
    integer sample_cycle=0,captured_frames=0;
    reg measure=0,pending=0;
    reg [63:0] expected_latch;
    reg [63:0] prev_pins=0;
    integer first_rise[0:63],last_rise[0:63],last_fall[0:63],periods[0:63],highs[0:63];
    integer min_period=999999,max_period=0,min_high=999999,max_high=0;

    task check;
        input ok;
        input [8*100-1:0] label;
        begin
            checks=checks+1;
            if(ok !== 1'b1) $fatal(1,"%0s",label);
        end
    endtask
    function [63:0] physical_order;
        input [63:0] logical_bits;
        integer r;
        begin
            for(r=0;r<8;r=r+1) physical_order[r*8 +: 8]=logical_bits[(7-r)*8 +: 8];
        end
    endfunction

    integer ch,dt;
    always @(posedge clk) begin
        cycle_count=cycle_count+1;
        if(!rst_n || !drive_enable) pending=0;
        else if(sample_tick) begin
            expected_latch=physical_order(parallel_data);
            sample_cycle=cycle_count;
            pending=1;
        end
        if(measure && sample_tick) begin
            if(last_tick!=0) begin
                check(cycle_count-last_tick==19 || cycle_count-last_tick==20,
                      "DDS slot samples are exactly 19 or 20 system cycles apart");
                if(cycle_count-last_tick==19) ticks19=ticks19+1;
                else ticks20=ticks20+1;
            end
            last_tick=cycle_count;
        end
        #1;
        if(measure) begin
            check(!fault && !oe_n,"steady 40 kHz playback remains enabled without serializer overrun");
            for(ch=0;ch<64;ch=ch+1) begin
                if(logical_pins[ch] && !prev_pins[ch]) begin
                    if(first_rise[ch]<0) first_rise[ch]=cycle_count;
                    if(last_rise[ch]>=0) begin
                        dt=cycle_count-last_rise[ch];
                        check(dt>=1249 && dt<=1251,"each physical channel retains the 40 kHz period");
                        periods[ch]=periods[ch]+1;
                        if(dt<min_period) min_period=dt;
                        if(dt>max_period) max_period=dt;
                    end
                    last_rise[ch]=cycle_count;
                end else if(!logical_pins[ch] && prev_pins[ch] && last_rise[ch]>=0) begin
                    dt=cycle_count-last_rise[ch];
                    check(dt>=605 && dt<=645,"each physical high pulse is 50 percent within one 64-slot sample");
                    highs[ch]=highs[ch]+1;
                    last_fall[ch]=cycle_count;
                    if(dt<min_high) min_high=dt;
                    if(dt>max_high) max_high=dt;
                end
            end
        end
        prev_pins=logical_pins;
    end
    always @(posedge rclk) begin
        #1;
        if(pending && drive_enable) begin
            check(cycle_count-sample_cycle==17,"physical RCLK follows its DDS sample by exactly 17 cycles");
            check(latched==expected_latch,"real 595 latches match the captured DDS output with PCB row order");
            pending=0;
            captured_frames=captured_frames+1;
        end
    end

    integer i,delta,wanted,err;
    integer max_phase_error=0;
    initial begin
        for(i=0;i<64;i=i+1) begin
            phases[i]=i;
            first_rise[i]=-1;last_rise[i]=-1;last_fall[i]=-1;periods[i]=0;highs[i]=0;
        end
        repeat(5) @(negedge clk);rst_n=1;
        cfg_change=1;
        @(negedge clk);cfg_change=0;
        repeat(100) @(negedge clk);
        tbl_new=1;
        @(negedge clk);tbl_new=0;
        repeat(150) @(negedge clk);
        check(!drive_enable && oe_n,"idle phase preparation does not activate the PCB");
        enable=1;
        // Ignore the first partial pulse and wait several whole carrier periods.
        repeat(4000) @(negedge clk);
        measure=1;
        repeat(100000) @(negedge clk);
        measure=0;
        check(ticks19>1000 && ticks20>1000,"both fractional-DDS slot lengths occur repeatedly");
        check(captured_frames>5000,"thousands of actual 595 frames have been checked");
        for(i=0;i<64;i=i+1) begin
            check(periods[i]>=78 && highs[i]>=78,"every channel produces many complete measured cycles");
            delta=(first_rise[i]-first_rise[0]+1250)%1250;
            wanted=(i*1250+32)/64;
            err=delta-wanted;
            if(err>625) err=err-1250;
            if(err<(-625)) err=err+1250;
            if(err<0) err=-err;
            if(err>max_phase_error) max_phase_error=err;
            check(err<=21,"physical channel phase delay follows code times 1/64 cycle");
        end
        $display("Physical wave: period %0d..%0d cycles; high %0d..%0d cycles; max phase error %0d cycles; slot counts 19=%0d,20=%0d",
                 min_period,max_period,min_high,max_high,max_phase_error,ticks19,ticks20);
        enable=0;#1;
        check(oe_n && physical==0,"STOP immediately disables all 64 physical driver inputs");
        repeat(25) @(negedge clk);
        check(!fault && oe_n && latched==0,"STOP flushes and latches the zero frame");
        check(captured_frames>0,"integration test observed real serialized frames");
        $display("DDS-to-595 waveform checks passed: %0d, frames=%0d",checks,captured_frames);
        $finish;
    end
    initial begin #3000000;$fatal(1,"DDS-to-595 waveform test timed out");end
endmodule
