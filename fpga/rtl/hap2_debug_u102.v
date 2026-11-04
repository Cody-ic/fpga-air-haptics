`timescale 1ns/1ps

// Standalone R2 diagnostic, 50 MHz clock ONLY. U102 = logical channel 3
// = DATA7 / U94 QD / U96 driver. All other 63 latched outputs stay low.
// Send 8 bits MSB first at 25 MHz, latch once per 625 clocks (12.5 us).
// Thus the actual QD output has 40 kHz / 50% duty without frame jitter.
// First frame is all-zero; enable only after latch has settled for 40 ns.
module hap2_debug_u102 (
    input wire clk, rst_n,
    output reg [7:0] sr_data,
    output reg sr_clk, sr_latch,
    output wire sr_oe_n, sr_clear_n
);
    reg [9:0] cycle;
    reg phase, armed;
    assign sr_oe_n = !rst_n || !armed;
    assign sr_clear_n = rst_n;

    always @(posedge clk) begin
        if (!rst_n) begin
            cycle <= 0;
            phase <= 0;
            armed <= 0;
            sr_data <= 0;
            sr_clk <= 0;
            sr_latch <= 0;
        end else begin
            if (cycle == 624) begin
                cycle <= 0;
                phase <= ~phase;
            end else cycle <= cycle + 1'b1;

            if (cycle == 0) sr_data <= 0;
            else if (cycle <= 16) begin
                sr_clk <= cycle[0];
                // Bit 3 is presented at cycle 8, sampled at cycle 9.
                if (!cycle[0]) sr_data <= (cycle == 8 && phase) ? 8'h80 : 8'h00;
            end else if (cycle == 17) sr_latch <= 1;
            else if (cycle == 18) sr_latch <= 0;
            else if (cycle == 19) armed <= 1;
        end
    end
endmodule
