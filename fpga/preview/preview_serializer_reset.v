`timescale 1ns/1ps
// Per-lane reset drivers. Preserve four separate flops so the placer can locate
// each next to its OSER10 rather than stretch a shared reset across the I/O bank.
// Both related clocks and all reset recovery/removal paths remain fully timed.
module preview_serializer_reset(
    input wire pixel_clk, request,
    output wire [3:0] reset
);
    (* syn_preserve=1, syn_keep=1 *) reg [3:0] reset_q=4'b1111;
    always @(posedge pixel_clk) reset_q<={4{request}};
    assign reset=reset_q;
endmodule
