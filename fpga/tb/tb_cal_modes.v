`timescale 1ns/1ps
module tb_cal_modes #(parameter integer REF=63, N=64);
    reg clk=0,rst_n=0,start=0,abort_run=0,mv=0,importing=0;
    always #5 clk=~clk;
    reg [31:0] power_in=0;
    reg [8*N-1:0] imported=0;
    wire busy,valid;
    wire [2:0] state;
    wire [3:0] stage,error;
    wire [7:0] channel;
    wire [31:0] token;
    wire [8*N-1:0] saved,trial;
    wire [N-1:0] mask;
    hap2_calibrate #(.CHANNELS(N),.SOURCE(0),.CLK_HZ(1000),.SETTLE_MS(1),.REPEAT_MS(1)) dut (
        .clk(clk),.rst_n(rst_n),.begin_run(start),.abort_run(abort_run),.clear_saved(1'b0),
        .reference(REF[7:0]),.import_run(importing),.import_offsets(imported),.measure_valid(mv),
        .measure_good(1'b1),.measure_power(power_in),.busy(busy),.saved_valid(valid),
        .changed(),.committed(),.state(state),.stage(stage),.channel(channel),.error(error),
        .repeat_number(),.token(token),.tx_on(),.channel_mask(mask),.trial_offsets(trial),.saved_offsets(saved));
    integer reads,i,delta;
    real p,angle;
    task begin_round;
        begin
            @(negedge clk); start=1;
            @(negedge clk); start=0;
        end
    endtask
    task feed_round;
        begin
            reads=0;
            while(busy) begin
                if(state==2) begin
                    delta=(channel*5-REF*5+1024)%64;
                    angle=(delta-trial[8*channel +: 8])*6.283185307179586/64.0;
                    if(stage==0) p=9; // wideband scope background is a bound,
                    else if(stage==1) p=10000; // not subtracted from signal.
                    else if(stage==2) p=6400;
                    else p=16400+16000*$cos(angle);
                    @(negedge clk); power_in=$rtoi(p+0.5); mv=1;
                    @(negedge clk); mv=0; reads=reads+1;
                end else @(negedge clk);
            end
        end
    endtask
    reg [8*N-1:0] old_saved;
    initial begin
        repeat(3) @(negedge clk); rst_n=1;
        begin_round; feed_round;
        if(state!=4 || !valid || reads!=(2+6*(N-1))*3) $fatal(1,"manual full round");
        for(i=0;i<N;i=i+1)
            if(saved[8*i +: 8]!=(i*5-REF*5+1024)%64) $fatal(1,"reference mapping channel=%0d",i);
        old_saved=saved; imported=saved; importing=1;
        begin_round; feed_round;
        if(state!=4 || saved!==old_saved || reads!=N*6) $fatal(1,"import must reverify every channel");
        imported[7:0]=(imported[7:0]+16)%64;
        begin_round; feed_round;
        if(state!=5 || error!=6 || saved!==old_saved) $fatal(1,"reject bad imported candidate");
        importing=0;
        begin_round;
        wait(state==2);
        repeat(6100) @(negedge clk);
        if(!busy || state!=2) $fatal(1,"manual must wait longer than ADC timeout");
        repeat(114000) @(negedge clk);
        if(busy || state!=5 || error!=2 || saved!==old_saved) $fatal(1,"manual 120-second timeout");
        $display("PASS scope source: 64 channels/ref=63, no background subtraction, import reverify, atomic rollback, 120-second timeout");
        $finish;
    end
    initial begin #100000000; $fatal(1,"deadline"); end
endmodule
