`timescale 1ns/1ps

// Startup-only calibration link. Fixed 34-byte packets: A5 5A, version=2,
// type, seven little-endian u32 words, CRC16-CCITT of bytes 0..31 (LE).
// This endpoint is disconnected before the original HAP3 endpoint is released.
module hap2_cal_packet #(
    parameter integer CLK_HZ=50000000, BAUD=115200
) (
    input wire clk, rst_n, rx_line,
    output wire tx_line,
    output reg rx_valid,
    output reg [255:0] rx_body,
    input wire tx_start,
    input wire [255:0] tx_body,
    output reg tx_busy, tx_done
);
    function [15:0] crc_byte;
        input [15:0] crc;
        input [7:0] data;
        integer j;
        reg [15:0] c;
        begin
            c=crc^{data,8'd0};
            for(j=0;j<8;j=j+1) c=c[15] ? (c<<1)^16'h1021 : c<<1;
            crc_byte=c;
        end
    endfunction
    wire [7:0] rb;
    wire rv, re;
    uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_rx (
        .clk(clk),.rst_n(rst_n),.rx_line(rx_line),
        .rx_data(rb),.rx_valid(rv),.rx_error(re));
    reg [5:0] ri;
    reg [31:0] idle_ticks;
    reg [255:0] received;
    reg [15:0] rc;
    reg [7:0] crc_low;
    always @(posedge clk) begin
        if(!rst_n) begin
            ri<=0; idle_ticks<=0; received<=0; rc<=16'hffff;
            crc_low<=0; rx_body<=0; rx_valid<=0;
        end else begin
            rx_valid<=0;
            if(re || (ri!=0 && idle_ticks>=CLK_HZ/20)) begin ri<=0; idle_ticks<=0; end
            else if(rv) begin
                idle_ticks<=0;
                if(ri==0) begin
                    if(rb==8'ha5) begin received[7:0]<=rb; rc<=crc_byte(16'hffff,rb); ri<=1; end
                end else if(ri==1 && rb!=8'h5a) begin
                    if(rb==8'ha5) rc<=crc_byte(16'hffff,rb);
                    else ri<=0;
                end else if(ri<32) begin
                    received[8*ri +: 8]<=rb; rc<=crc_byte(rc,rb); ri<=ri+1;
                end else if(ri==32) begin crc_low<=rb; ri<=33; end
                else begin
                    ri<=0;
                    if({rb,crc_low}==rc && received[23:16]==2) begin
                        rx_body<=received; rx_valid<=1;
                    end
                end
            end else if(ri!=0) idle_ticks<=idle_ticks+1;
        end
    end
    reg [255:0] sending;
    reg [15:0] tc;
    reg [5:0] ti;
    reg [1:0] ts;
    reg send_byte;
    reg [7:0] tb;
    wire byte_done;
    uart_tx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_tx (
        .clk(clk),.rst_n(rst_n),.tx_start(send_byte),.tx_data(tb),
        .tx_line(tx_line),.tx_busy(),.tx_done(byte_done));
    always @(posedge clk) begin
        if(!rst_n) begin
            sending<=0; tc<=16'hffff; ti<=0; ts<=0;
            send_byte<=0; tb<=0; tx_busy<=0; tx_done<=0;
        end else begin
            send_byte<=0; tx_done<=0;
            case(ts)
                0: if(tx_start) begin
                    sending<=tx_body; tc<=16'hffff; ti<=0; tx_busy<=1; ts<=1;
                end
                1: begin
                    if(ti<32) begin tb<=sending[8*ti +: 8]; tc<=crc_byte(tc,sending[8*ti +: 8]); end
                    else tb<=ti==32 ? tc[7:0] : tc[15:8];
                    send_byte<=1; ts<=2;
                end
                2: if(byte_done) begin
                    if(ti==33) begin ts<=0; tx_busy<=0; tx_done<=1; end
                    else begin ti<=ti+1; ts<=1; end
                end
                default: ts<=0;
            endcase
        end
    end
endmodule
