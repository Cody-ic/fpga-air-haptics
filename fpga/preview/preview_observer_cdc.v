`timescale 1ns/1ps
// Request/acknowledge bundled-data transfer. Source holds the entire snapshot
// until a new request has crossed two source clocks. Destination copies only
// after the matching acknowledgement has crossed two destination clocks.
module preview_observer_cdc # (parameter WIDTH=189)(
    input wire source_clk,source_rst_n,dest_clk,dest_rst_n,
    input wire [WIDTH-1:0] source_data,
    output reg [WIDTH-1:0] dest_data
);
    reg request=0,acknowledge=0;
    (* syn_preserve=1 *) reg request_meta=0,request_sync=0;
    (* syn_preserve=1 *) reg ack_meta=0,ack_sync=0;
    (* syn_preserve=1 *) reg [WIDTH-1:0] held_data=0;
    reg waiting=0;
    always @(posedge source_clk) begin
        if(!source_rst_n) begin
            request_meta<=0;request_sync<=0;acknowledge<=0;held_data<=0;
        end else begin
            request_meta<=request;request_sync<=request_meta;
            if(request_sync!=acknowledge) begin
                held_data<=source_data;
                acknowledge<=request_sync;
            end
        end
    end
    always @(posedge dest_clk) begin
        if(!dest_rst_n) begin
            request<=0;ack_meta<=0;ack_sync<=0;waiting<=0;dest_data<=0;
        end else begin
            ack_meta<=acknowledge;ack_sync<=ack_meta;
            if(!waiting) begin request<=~request;waiting<=1;end
            else if(ack_sync==request) begin dest_data<=held_data;waiting<=0;end
        end
    end
endmodule
