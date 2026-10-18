`timescale 1ns/1ps
module tb_calibration;
    reg clk=0, rst_n=0, start=0, store=0, restore=0;
    reg [31:0] mask=16'hffff, steps=64;
    reg [13:0] goff=0, glen=24, ooff=32, olen=37;
    wire [12:0] rd_addr;
    reg [7:0] rd_data;
    wire busy, done, valid, changed, trial;
    wire [15:0] channel_mask;
    wire [127:0] offsets;
    reg [7:0] mem[0:127];
    reg [8*24-1:0] geom = "d3e4d118b6b2ca2469907382";
    reg [8*37-1:0] vals = "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,0";
    integer i, checks=0;

    always #5 clk=~clk;
    always @(posedge clk) rd_data <= mem[rd_addr];
    hap2_calibration u (
        .clk(clk),.rst_n(rst_n),.start(start),.action_store(store),
        .requested_mask(mask),.phase_steps(steps),.geometry_off(goff),
        .geometry_len(glen),.offsets_off(ooff),.offsets_len(olen),
        .rd_addr(rd_addr),.rd_data(rd_data),.busy(busy),.done(done),.valid(valid),
        .changed(changed),.restore(restore),.trial_active(trial),
        .channel_mask(channel_mask),.phase_offsets(offsets));

    task launch;
        begin
            @(negedge clk); start=1; @(negedge clk); start=0;
            while(!done) @(negedge clk);
        end
    endtask
    task check_ok;
        input ok;
        input [8*40-1:0] name;
        begin checks=checks+1; if(!ok) begin $display("FAIL %0s",name); $fatal; end end
    endtask

    initial begin
        for(i=0;i<128;i=i+1) mem[i]=0;
        for(i=0;i<24;i=i+1) mem[i]=geom[8*24-1-8*i -: 8];
        for(i=0;i<37;i=i+1) mem[32+i]=vals[8*37-1-8*i -: 8];
        repeat(4) @(negedge clk); rst_n=1;

        mask=16'hfffe; store=0; launch;
        check_ok(valid,"trial accepted"); check_ok(trial,"trial flag");
        check_ok(channel_mask==16'hfffe,"mask applied");
        check_ok(offsets[15*8 +: 8]==0,"single digit final offset parsed");

        mem[32]="9"; mem[33]="9"; olen=37; launch;
        check_ok(!valid,"bad offset rejected");
        check_ok(channel_mask==16'hfffe,"bad command atomic");
        check_ok(offsets[14*8 +: 8]==15,"old offsets retained");
        mem[32]=vals[8*37-1 -: 8]; mem[33]=vals[8*37-9 -: 8];

        @(negedge clk); restore=1; @(negedge clk); restore=0;
        check_ok(channel_mask==16'hffff,"restore full mask");
        check_ok(offsets==0,"restore saved defaults"); check_ok(!trial,"restore clears trial");

        store=1; mask=16'hffff; launch;
        check_ok(valid && !trial,"store accepted");
        @(negedge clk); restore=1; @(negedge clk); restore=0;
        check_ok(offsets[14*8 +: 8]==15,"stored offsets survive restore");

        $display("PASS calibration %0d checks",checks); $finish;
    end
endmodule
