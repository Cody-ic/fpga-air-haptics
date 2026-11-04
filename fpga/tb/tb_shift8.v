`timescale 1ns/1ps
module tb_shift8;
    reg clk=0, rst_n=0, enable=0;
    always #10 clk=~clk;
    reg [63:0] waves=0;
    wire [7:0] data;
    wire srclk,latch,oe_n,clear_n;
    hap2_shift8 dut(.clk(clk),.rst_n(rst_n),.enable(enable),.waves(waves),
        .sr_data(data),.sr_clk(srclk),.sr_latch(latch),.sr_oe_n(oe_n),.sr_clear_n(clear_n));
    // External eight-chip model, independent of internal serialization state.
    reg [7:0] shifts[0:7];
    reg [63:0] outputs=0;
    integer edges=0,frames=0,j,k;
    time last_data=0,last_clock=0,last_latch=0;
    always @(data) last_data=$time;
    always @(posedge srclk) begin
        if($time-last_data<20) $fatal(1,"SER setup time");
        last_clock=$time;
        for(integer i=0;i<8;i=i+1) shifts[i]={shifts[i][6:0],data[i]};
        edges=edges+1;
    end
    always @(posedge latch) begin
        if(edges!=8 || $time-last_clock!=40) $fatal(1,"shift count/latch setup");
        if(frames>0 && $time-last_latch!=380) $fatal(1,"frame period");
        last_latch=$time; edges=0; frames=frames+1;
        for(integer i=0;i<8;i=i+1) outputs[i*8 +: 8]=shifts[i];
    end
    task settled;
        begin repeat(60) @(negedge clk); end
    endtask
    initial begin
        repeat(4) @(negedge clk); rst_n=1;
        settled;
        if(!oe_n || outputs!==0) $fatal(1,"startup must latch zero while disabled");
        enable=1;
        for(k=0;k<64;k=k+1) begin
            waves=64'd1<<k; settled;
            // Logical bottom-left channel 0 -> DATA7/QA; top-right 63 -> DATA0/QH.
            if(oe_n || outputs!==(64'd1<<((7-k/8)*8+k%8)))
                $fatal(1,"physical lane/bit mapping channel %0d",k);
        end
        // Change live input halfway through a frame: output must stay atomic.
        waves=64'h123456789abcdef0; settled;
        @(posedge latch); @(negedge clk);
        waves=64'h0123456789abcdef;
        @(posedge srclk); @(negedge clk); waves=~waves;
        @(posedge latch); #1;
        if(outputs!==64'hefcdab8967452301) $fatal(1,"torn snapshot");
        // Stop and resume at every frame position. No old active frame may leak.
        for(j=0;j<19;j=j+1) begin
            waves=~64'd0; enable=1; settled;
            repeat(j) @(negedge clk);
            enable=0; #1;
            if(!oe_n) $fatal(1,"stop must disable immediately");
            @(negedge clk); waves=0; enable=1; #1;
            if(!oe_n) $fatal(1,"resume enabled stale latch");
            wait(!oe_n); #1;
            if(outputs!==0) $fatal(1,"resume leaked old frame");
        end
        @(negedge clk); rst_n=0; #1;
        if(!oe_n || clear_n) $fatal(1,"reset gating");
        $display("PASS shift8: 64 physical channels, atomic frames, 25 MHz shift/380 ns update, stop/resume/reset");
        $finish;
    end
    initial begin #1000000; $fatal(1,"deadline"); end
endmodule
