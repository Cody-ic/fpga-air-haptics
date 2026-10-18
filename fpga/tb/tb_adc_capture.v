`timescale 1ns/1ps
module tb_adc_capture;
    reg clk=0, rst_n=0, start=0, consume=0, sdata=0;
    wire sclk, cs_n, busy, valid, done;
    reg [7:0] addr=0;
    wire [11:0] data;
    integer conv=0, bitn=0, checks=0, errors=0, i;
    reg [11:0] word;

    always #10 clk=~clk;
    hap3_adc_capture #(.SAMPLES(200),.SAMPLE_CYCLES(125),.SCLK_HALF(2)) dut(
        .clk(clk),.rst_n(rst_n),.start(start),.consume(consume),.adc_sdata(sdata),
        .adc_sclk(sclk),.adc_cs_n(cs_n),.busy(busy),.result_valid(valid),.done(done),
        .rd_addr(addr),.rd_data(data));

    always @(negedge cs_n) begin conv=conv+1; bitn=0; word=(conv==1)?12'habc:(conv-1); sdata=1'b0; end
    always @(negedge sclk) if(!cs_n) begin
        bitn=bitn+1;
        if(bitn<4) sdata=0; else sdata=word[15-bitn];
    end
    task check(input ok,input [255:0] msg); begin checks=checks+1; if(!ok) begin errors=errors+1;$display("FAIL %s",msg);end end endtask
    initial begin
        repeat(4) @(posedge clk); rst_n=1; @(posedge clk); start=1; @(posedge clk); start=0;
        wait(done); check(valid,"result valid"); check(conv==201,"warm-up plus 200 conversions");
        for(i=0;i<200;i=i+1) begin addr=i; repeat(2) @(posedge clk); check(data==i+1,"sample content"); end
        start=1; @(posedge clk); start=0; repeat(20) @(posedge clk); check(conv==201,"pending result blocks overwrite");
        consume=1; @(posedge clk); consume=0; check(!valid,"consume clears pending result");
        $display("ADC capture checks: %0d, failures=%0d",checks,errors);
        if(errors) $fatal(1); $finish;
    end
    initial begin
        repeat(200000) @(posedge clk);
        $display("TIMEOUT busy=%0d active=%0d conv=%0d edge=%0d valid=%0d",busy,dut.active,conv,dut.edge_count,valid);
        $fatal(1);
    end
endmodule
