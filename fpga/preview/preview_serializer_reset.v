`timescale 1ns/1ps
// Explicit per-lane flops retain stable names for local placement next to each
// OSER10. The builders constrain lane[i].reset_ff near its serializer: a long
// core-to-I/O reset route cannot meet both edges of the fast clock.
// INIT keeps reset asserted during configuration. Both related clocks and all
// reset recovery/removal paths remain fully timed.
module preview_serializer_reset(
    input wire pixel_clk, request,
    output wire [3:0] reset
);
    genvar i;
    generate for(i=0;i<4;i=i+1) begin:lane
        (* syn_preserve=1, syn_keep=1 *)
        DFFSE #(.INIT(1'b1)) reset_ff(
            .D(request),.CLK(pixel_clk),.CE(1'b1),.SET(1'b0),.Q(reset[i]));
    end endgenerate
endmodule
