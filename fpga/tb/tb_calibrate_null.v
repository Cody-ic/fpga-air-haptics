`timescale 1ns/1ps
module tb_calibrate_null;
    reg clk=0;
    always #5 clk=~clk;
    reg rst_n=0, begin_run=0, abort_run=0, clear_saved=0, mv=0, good=1;
    reg [31:0] power_in=0;
    wire busy,valid,changed,committed,tx_on;
    wire [2:0] state;
    wire [3:0] stage,error;
    wire [7:0] channel;
    wire [31:0] token;
    wire [1:0] mask;
    wire [15:0] trial,saved;
    hap2_calibrate #(.NULL_SCALED_QC(1),.CHANNELS(2),.CLK_HZ(1000),.SETTLE_MS(1),.REPEAT_MS(1),.TIMEOUT_MS(20)) dut (
        .reference(8'd0),.import_run(1'b0),.import_offsets(16'd0),
        .clk(clk),.rst_n(rst_n),.begin_run(begin_run),.abort_run(abort_run),.clear_saved(clear_saved),
        .measure_valid(mv),.measure_good(good),.measure_power(power_in),.busy(busy),
        .saved_valid(valid),.changed(changed),.committed(committed),.state(state),.stage(stage),
        .channel(channel),.error(error),.token(token),.tx_on(tx_on),.channel_mask(mask),
        .trial_offsets(trial),.saved_offsets(saved));
    integer delta,mode=0,requests,cycles;
    real a,b,angle,p;
    reg [15:0] old_saved;
    task run_case;
        begin
            @(negedge clk); begin_run=1; good=1;
            @(negedge clk); begin_run=0; requests=0; cycles=0;
            while(busy && cycles<4000) begin
                if(state==2 && mode!=1) begin
                    // Physical output delays a positive phase code. Each
                    // channel carries an intrinsic phase error delta.
                    angle=(delta-trial[15:8])*6.283185307179586/64.0;
                    if(stage==0) p=2;
                    else if(stage==1) p=a+2;
                    else if(stage==2) p=b+2;
                    else p=a+b+2.0*$sqrt(a*b)*$cos(angle)+2;
                    if(mode==2 && stage==2) p=2; // weak target
                    if(mode==3 && stage==4) p=p+12000; // non-sinusoidal fit
                    if(mode==4 && stage==7) p=p*0.5; // failed verification
                    if(mode==8 && stage>=3 && stage<=6 && p<(a+b)/16) p=p+(requests%3)*30;
                    if(mode==9 && stage==3) p=2+(requests%3)*300;
                    if(mode==10 && stage==5) p=p+(requests%3)*5000;
                    if(mode==11 && stage==2) p=p+(requests%3)*1000;
                    if(mode==5 && requests==1) p=1000; // unstable noise
                    @(negedge clk); power_in=$rtoi(p+0.5); mv=1;
                    if(mode==6) good=0;
                    if(mode==7) power_in=32'h1000000;
                    requests=requests+1;
                    @(negedge clk); mv=0; power_in=32'hffffffff; // ignored without strobe
                end else @(negedge clk);
                cycles=cycles+1;
            end
            if(busy) $fatal(1,"engine hung");
        end
    endtask
    initial begin
        repeat(4) @(negedge clk); rst_n=1;
        a=10000; b=6400;
        for(delta=0;delta<64;delta=delta+1) begin
            // Vary the two single-channel amplitudes independently.
            a=10000+delta*83; b=6400+delta*51;
            run_case;
            if(state!=4 || !valid || saved[7:0]!=0 || saved[15:8]!=delta)
                $fatal(1,"phase %0d: state=%0d error=%0d saved=%0d",delta,state,error,saved[15:8]);
            if(requests!=24) $fatal(1,"expected 8 conditions x 3 repeats");
        end
        mode=8; a=10000; b=10000;
        for(delta=0;delta<64;delta=delta+1) begin
            run_case;
            if(state!=4 || !valid || saved[15:8]!=delta)
                $fatal(1,"small absolute null variation phase %0d: state=%0d error=%0d result=%0d",delta,state,error,saved[15:8]);
        end
        delta=32; run_case;
        old_saved=saved;
        for(mode=9;mode<=11;mode=mode+1) begin
            run_case;
            if(state!=5 || error!=3 || saved!==old_saved)
                $fatal(1,"large null/bright/single variation must fail: mode=%0d state=%0d error=%0d",mode,state,error);
        end
        for(mode=1;mode<=7;mode=mode+1) begin
            run_case;
            if(state!=5 || !valid || saved!==old_saved) $fatal(1,"failed run overwrote saved bank, mode=%0d",mode);
            case(mode)
                1: if(error!=2) $fatal(1,"timeout");
                2: if(error!=4) $fatal(1,"weak");
                3: if(error!=5 && error!=3) $fatal(1,"fit");
                4: if(error!=6) $fatal(1,"verify");
                default: if(error!=3) $fatal(1,"quality");
            endcase
        end
        @(negedge clk); begin_run=1;
        @(negedge clk); begin_run=0; abort_run=1;
        @(negedge clk); abort_run=0;
        if(state!=5 || error!=1 || saved!==old_saved) $fatal(1,"cancel");
        @(negedge clk); clear_saved=1;
        @(negedge clk); clear_saved=0;
        if(valid || saved!=0) $fatal(1,"clear");
        $display("PASS calibration: all 64 phases, median repeats, quality/weak/fit/verify/timeout/cancel/atomic commit");
        $finish;
    end
endmodule
