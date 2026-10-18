`timescale 1ns/1ps

// CAPTURE end-to-end check: UART command -> ADCS7476 model -> delayed UART ACK.
module tb_capture_protocol;
    localparam integer CLK_HZ = 1_000_000;
    localparam integer BAUD = 100_000;
    localparam integer CLKS_PER_BIT = CLK_HZ / BAUD;
    localparam [8*24-1:0] REQUEST = "HAP3 CMD 8 CAPTURE*B779\n";

    reg clk=0, rst_n=0, uart_in=1, adc_sdata=0;
    wire uart_out, adc_sclk, adc_cs_n;
    wire [15:0] array_pos, array_neg;
    wire [7:0] rx_byte;
    wire rx_valid;
    integer errors=0, checks=0, i, conv=0, bitn=0, rx_len=0;
    integer raw_start=-1, raw_end=-1, value=0, values=0, expected=1;
    reg [11:0] adc_word;
    reg [7:0] rx_mem [0:2047];
    reg capture_done_seen=0;
    reg [15:0] crc_calc, crc_wire;

    always #500 clk=~clk;

    hap2_top #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STATE_MS(0)) dut (
        .clk(clk), .rst_n(rst_n), .uart_rx_pin(uart_in), .uart_tx_pin(uart_out),
        .boot_id(32'h1234abcd), .key_next(1'b0), .key_play(1'b0), .key_stop(1'b0),
        .output_fault(1'b0), .adc_sdata(adc_sdata), .adc_sclk(adc_sclk), .adc_cs_n(adc_cs_n),
        .mode_local(), .focus_x(), .focus_y(), .scan_on(), .stroke_index(),
        .output_on(), .beat_pulse(), .walk_running(), .array_pos(array_pos),
        .array_neg(array_neg), .sample_tick(), .drive_enable()
    );

    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) monitor (
        .clk(clk), .rst_n(rst_n), .rx_line(uart_out), .rx_data(rx_byte), .rx_valid(rx_valid)
    );

    always @(posedge clk) begin
        if (dut.adc_done) capture_done_seen <= 1'b1;
        if (rx_valid) begin
            if (!capture_done_seen) begin
                errors <= errors+1;
                $display("FAIL: CAPTURE replied before sampling finished");
            end
            rx_mem[rx_len] <= rx_byte;
            rx_len <= rx_len+1;
        end
    end

    // ADCS7476 emits four leading zeroes followed by 12 data bits.
    always @(negedge adc_cs_n) begin
        conv=conv+1; bitn=0; adc_word=(conv==1)?12'habd:(conv-1); adc_sdata=0;
    end
    always @(negedge adc_sclk) if (!adc_cs_n) begin
        bitn=bitn+1;
        if (bitn<4 || bitn>=16) adc_sdata=0; else adc_sdata=adc_word[15-bitn];
    end

    function [15:0] crc_step(input [15:0] old_crc, input [7:0] data);
        integer k;
        reg [15:0] c;
        begin
            c=old_crc ^ ({8'h00,data} << 8);
            for(k=0;k<8;k=k+1) c=c[15] ? ((c<<1)^16'h1021) : (c<<1);
            crc_step=c;
        end
    endfunction

    function [3:0] hex_value(input [7:0] c);
        begin hex_value=(c>="A") ? c-"A"+10 : c-"0"; end
    endfunction

    task check(input ok, input [8*80-1:0] msg);
        begin checks=checks+1; if(!ok) begin errors=errors+1; $display("FAIL: %s",msg); end end
    endtask

    task send_byte(input [7:0] b);
        integer j;
        begin
            uart_in=0; repeat(CLKS_PER_BIT) @(negedge clk);
            for(j=0;j<8;j=j+1) begin uart_in=b[j]; repeat(CLKS_PER_BIT) @(negedge clk); end
            uart_in=1; repeat(CLKS_PER_BIT) @(negedge clk);
        end
    endtask

    initial begin
        repeat(5) @(posedge clk); rst_n=1;
        // This bench bypasses HELLO only to isolate the CAPTURE transaction.
        force dut.u_cmd.connected=1'b1;
        for(i=0;i<24;i=i+1) send_byte(REQUEST[8*24-1-8*i -: 8]);
        release dut.u_cmd.connected;
        wait(rx_len>0 && rx_mem[rx_len-1]==8'h0a);
        repeat(3) @(posedge clk);

        check(conv==201,"one warm-up conversion plus exactly 200 samples");
        check(rx_mem[0]=="H" && rx_mem[5]=="A" && rx_mem[9]=="8",
              "reply is HAP3 ACK with original sequence 8");
        for(i=0;i<rx_len-4;i=i+1)
            if(rx_mem[i]=="r" && rx_mem[i+1]=="a" && rx_mem[i+2]=="w" && rx_mem[i+3]=="=")
                raw_start=i+4;
        for(i=raw_start;i<rx_len;i=i+1) if(rx_mem[i]=="*") raw_end=i;
        check(raw_start>0 && raw_end>raw_start,"reply contains a raw sample list");

        value=0; values=0; expected=1;
        for(i=raw_start;i<=raw_end;i=i+1) begin
            if(i==raw_end || rx_mem[i]==",") begin
                values=values+1;
                if(value!=expected) begin
                    errors=errors+1; $display("FAIL sample %0d got %0d addr=%0d data=%0d",
                                              expected,value,dut.adc_rd_addr,dut.adc_rd_data);
                end
                expected=expected+1; value=0;
            end else value=value*10+(rx_mem[i]-"0");
        end
        check(values==200 && expected==201,"reply contains exactly the 200 captured values");

        crc_calc=16'hffff;
        for(i=0;i<raw_end;i=i+1) crc_calc=crc_step(crc_calc,rx_mem[i]);
        crc_wire={hex_value(rx_mem[raw_end+1]),hex_value(rx_mem[raw_end+2]),
                  hex_value(rx_mem[raw_end+3]),hex_value(rx_mem[raw_end+4])};
        check(crc_calc==crc_wire,"CAPTURE reply CRC matches its full body");
        repeat(3) @(posedge clk);
        check(dut.u_adc.result_valid==0,"sample buffer is released only after reply completes");

        $display("CAPTURE protocol checks=%0d failures=%0d bytes=%0d",checks,errors,rx_len);
        if(errors) $fatal(1); $finish;
    end

    initial begin
        repeat(500000) @(posedge clk);
        $display("TIMEOUT conv=%0d rx_len=%0d",conv,rx_len);
        $fatal(1);
    end
endmodule
