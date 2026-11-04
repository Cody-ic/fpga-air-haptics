`timescale 1ns/1ps
module tb_cal_link;
    reg clk=0,rst_n=0;
    always #5 clk=~clk;
    wire [1:0] pos,neg;
    wire output_on,released;
    reg valid=0;
    reg [255:0] body=0;
    hap2_cal_startup #(.ROWS(1),.COLS(2),.CLK_HZ(1000000),.BAUD(100000),
        .SOURCE(0),.SETTLE_MS(3),.REPEAT_MS(3),.LINK_MS(10)) dut (
        .clk(clk),.rst_n(rst_n),.rx_line(1'b1),.boot_id(32'd99),.tx_line(),
        .out_pos(pos),.out_neg(neg),.output_on(output_on),.released(released),.saved_offsets());
    task send;
        input [7:0] kind;
        input [31:0] a0,a1,a2,a3,a4;
        begin
            @(negedge clk);
            body=0;body[31:24]=kind;body[63:32]=99;
            body[127:96]=a0;body[159:128]=a1;body[191:160]=a2;body[223:192]=a3;body[255:224]=a4;
            valid=1;
            @(negedge clk);valid=0;
            repeat(3) @(negedge clk);
        end
    endtask
    initial begin
        // The complete byte/CRC path is covered by tb_cal_startup. Here inject
        // decoded packets to isolate watchdog and explicit restart behavior.
        force dut.packet_valid=valid;
        force dut.packet=body;
        repeat(3) @(negedge clk);rst_n=1;
        send(7,0,0,100000,30,0);
        wait(dut.phase_ready);
        send(1,0,0,0,0,0);
        wait(dut.state==2);
        repeat(12000) @(negedge clk);
        if(dut.state!=5 || dut.error!=1 || dut.busy || output_on || pos!=0 || neg!=0 || released)
            $fatal(1,"disconnect watchdog must fail and mute");
        send(1,0,0,0,0,0);
        if(!dut.busy) $fatal(1,"explicit restart after failure");
        send(4,0,0,0,0,0);
        if(dut.busy || pos!=0 || output_on || dut.error!=1) $fatal(1,"explicit cancel");
        $display("PASS link watchdog, closed outputs and explicit restart/cancel");
        $finish;
    end
    initial begin #10000000;$fatal(1,"deadline");end
endmodule
