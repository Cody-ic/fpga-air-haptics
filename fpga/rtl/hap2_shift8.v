`timescale 1ns/1ps

// R2: eight independent SN74LV595A lanes, common shift/latch clocks.
// At 50 MHz: 8 bits at 25 MHz, then latch; one snapshot every 19 clocks
// (380 ns < 25 us/64). All lanes use ONE snapshot, bit 7 first.
// ROW_MAJOR_XY grows from negative Y to positive Y; PCB DATA0 is top (+Y).
// Sampling adds a common 340 ns pipeline delay and <380 ns edge quantization.
module hap2_shift8 (
    input wire clk, rst_n,
    input wire enable,
    input wire [63:0] waves,
    output reg [7:0] sr_data,
    output reg sr_clk, sr_latch,
    output wire sr_oe_n, sr_clear_n
);
    reg [4:0] cycle;
    reg [63:0] snapshot;
    reg frame_valid, armed;
    integer lane;
    // OE also mutes between frames. Re-enable only after a fresh full frame.
    assign sr_oe_n = !rst_n || !enable || !armed;
    assign sr_clear_n = rst_n;

    always @(posedge clk) begin
        if (!rst_n) begin
            cycle <= 0;
            snapshot <= 0;
            sr_data <= 0;
            sr_clk <= 0;
            sr_latch <= 0;
            frame_valid <= 0;
            armed <= 0;
        end else begin
            cycle <= (cycle == 18) ? 0 : cycle + 1'b1;
            if (!enable) begin
                armed <= 0;
                frame_valid <= 0;
            end
            if (cycle == 0) begin
                // Previous latch has settled for 40 ns before first enable.
                armed <= frame_valid && enable;
                snapshot <= enable ? waves : 64'd0;
                frame_valid <= enable;
                for (lane=0; lane<8; lane=lane+1)
                    sr_data[lane] <= enable && waves[(7-lane)*8+7];
            end else if (cycle <= 16) begin
                sr_clk <= cycle[0];
                if (!cycle[0] && cycle < 16)
                    for (lane=0; lane<8; lane=lane+1)
                        sr_data[lane] <= snapshot[(7-lane)*8+7-(cycle>>1)];
            end else if (cycle == 17) begin
                // Last SRCLK rising edge was cycle 15: 40 ns setup to RCLK.
                sr_latch <= 1;
            end else begin
                sr_latch <= 0;
            end
        end
    end
endmodule
