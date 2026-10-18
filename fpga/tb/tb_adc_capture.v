`timescale 1ns/1ps
module tb_adc_capture;
    reg clk=0, rst_n=0, start=0, consume=0, source_data=0;
    wire sdata;
    // Worst-case 40 ns converter access time plus 3 ns return-path delay.
    assign #3 sdata=source_data;
    wire sclk, cs_n, busy, valid, done;
    reg [7:0] addr=0;
    wire [11:0] data;
    integer conv=0, bitn=0, checks=0, errors=0, i;
    integer cycles=0, previous_start=0, rises=0, falls=0;
    time last_fall;
    reg [11:0] word;

    always #10 clk=~clk;
    always @(posedge clk) cycles=cycles+1;
    hap3_adc_capture #(.SAMPLES(200),.SAMPLE_CYCLES(125),.SCLK_HALF(2)) dut(
        .clk(clk),.rst_n(rst_n),.start(start),.consume(consume),.adc_sdata(sdata),
        .adc_sclk(sclk),.adc_cs_n(cs_n),.busy(busy),.result_valid(valid),.done(done),
        .rd_addr(addr),.rd_data(data));

    always @(negedge cs_n) begin
        conv=conv+1; bitn=0; word=(conv*37)^12'ha53; source_data=0;
        rises=0;falls=0;
        if(conv>1)check(cycles-previous_start==125,"exact 400 kS/s spacing");
        previous_start=cycles;
    end
    always @(posedge sclk) if(!cs_n)rises=rises+1;
    always @(negedge sclk) if(!cs_n) begin
        bitn=bitn+1;falls=falls+1;last_fall=$time;
        if(bitn<4)source_data<=#40 0;
        else if(bitn<16)source_data<=#40 word[15-bitn];
        else source_data<=#25 0;
    end
    always @(posedge cs_n) if(rst_n && conv>0)begin
        check(rises==16 && falls==16,"sixteen complete clocks");
        check($time-last_fall>=20,"CS holds after final clock");
    end
    task check(input ok,input [255:0] msg); begin checks=checks+1; if(!ok) begin errors=errors+1;$display("FAIL %s",msg);end end endtask
    initial begin
        repeat(4) @(negedge clk); rst_n=1; @(negedge clk); start=1; @(negedge clk); start=0;
        wait(done); check(valid,"result valid"); check(conv==201,"warm-up plus 200 conversions");
        for(i=0;i<200;i=i+1) begin
            @(negedge clk);addr=i;repeat(2)@(negedge clk);
            check(data==((((i+2)*37)^12'ha53)&12'hfff),"sample content with real delays");
        end
        start=1; @(negedge clk); start=0; repeat(20) @(negedge clk); check(conv==201,"pending result blocks overwrite");
        consume=1; @(negedge clk); consume=0; check(!valid,"consume clears pending result");
        $display("ADC capture checks: %0d, failures=%0d",checks,errors);
        if(errors) $fatal(1); $finish;
    end
    initial begin
        repeat(200000) @(posedge clk);
        $display("TIMEOUT busy=%0d active=%0d conv=%0d edge=%0d valid=%0d",busy,dut.active,conv,dut.edge_count,valid);
        $fatal(1);
    end
endmodule
