`timescale 1ns/1ps

// Eight parallel SN74LV595A registers: one 8-bit device for each PCB row.
// Logical Y increases opposite to physical DATA row order; X is unchanged.
// sample_tick must be at least 19 clk cycles apart (50 MHz -> 25 MHz SRCLK).
// From a sample at cycle 0: SRCLK rises at 1,3,...15; falls at 2,4,...16;
// RCLK rises at 17 and falls at 18. OE is enabled only after that full interval.
module hap2_shift595 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,
    input  wire        sample_tick,
    input  wire [63:0] parallel_data,
    output reg  [7:0]  data,
    output reg         srclk,
    output reg         rclk,
    output wire        oe_n,
    output wire        srclr_n,
    output reg         fault
);
    reg [63:0] frame;
    reg [4:0] cycle;
    reg [2:0] column;
    reg busy, zeroing, initialized, drive_ready, enable_q;

    // Disabling output never waits for serialization or a clock edge.
    // SRCLR clears the shift registers; it does not clear their output latches,
    // so reset/STOP additionally shift and latch a complete zero frame.
    assign oe_n = !(rst_n && enable && drive_ready && !fault);
    assign srclr_n = rst_n;

    function [7:0] column_data;
        input [63:0] bits;
        input [2:0] col;
        integer row;
        begin
            for (row = 0; row < 8; row = row + 1)
                column_data[row] = bits[(7-row)*8+{29'd0, col}];
        end
    endfunction

    always @(posedge clk) begin
        if (!rst_n) begin
            frame       <= 64'd0;
            cycle       <= 5'd0;
            column      <= 3'd7;
            busy        <= 1'b0;
            zeroing     <= 1'b0;
            initialized <= 1'b0;
            drive_ready <= 1'b0;
            enable_q    <= 1'b0;
            data        <= 8'd0;
            srclk       <= 1'b0;
            rclk        <= 1'b0;
            fault       <= 1'b0;
        end else begin
            enable_q <= enable;
            if (!enable) fault <= 1'b0;

            if (enable_q && !enable) begin
                // Cancel an interrupted frame before accepting a restart.
                // A quick enable pulse cannot expose the previous output latch.
                frame       <= 64'd0;
                data        <= 8'd0;
                srclk       <= 1'b0;
                rclk        <= 1'b0;
                column      <= 3'd7;
                cycle       <= 5'd1;
                busy        <= 1'b1;
                zeroing     <= 1'b1;
                initialized <= 1'b0;
                drive_ready <= 1'b0;
            end else if (fault) begin
                // Sticky fail-closed state. OFF clears the fault and requires
                // the same zero-latch initialization as a cold start.
                busy        <= 1'b0;
                initialized <= 1'b0;
                drive_ready <= 1'b0;
                srclk       <= 1'b0;
                rclk        <= 1'b0;
                data        <= 8'd0;
            end else if (busy && !zeroing && enable && sample_tick) begin
                // Never silently discard an active DDS sample. This also
                // protects the very first active frame before OE was enabled.
                fault       <= 1'b1;
                busy        <= 1'b0;
                initialized <= 1'b0;
                drive_ready <= 1'b0;
                srclk       <= 1'b0;
                rclk        <= 1'b0;
                data        <= 8'd0;
            end else if (busy) begin
                if (cycle < 5'd17) begin
                    if (cycle[0]) begin
                        srclk <= 1'b1;
                    end else begin
                        srclk <= 1'b0;
                        if (column != 3'd0) begin
                            column <= column - 3'd1;
                            data <= column_data(frame, column - 3'd1);
                        end
                    end
                    cycle <= cycle + 5'd1;
                end else if (cycle == 5'd17) begin
                    rclk <= 1'b1;
                    cycle <= 5'd18;
                end else begin
                    rclk <= 1'b0;
                    busy <= 1'b0;
                    if (zeroing) begin
                        initialized <= 1'b1;
                        zeroing <= 1'b0;
                    end else begin
                        drive_ready <= 1'b1;
                    end
                end
            end else if (!initialized) begin
                frame       <= 64'd0;
                data        <= 8'd0;
                srclk       <= 1'b0;
                rclk        <= 1'b0;
                column      <= 3'd7;
                cycle       <= 5'd1;
                busy        <= 1'b1;
                zeroing     <= 1'b1;
                drive_ready <= 1'b0;
            end else if (enable && sample_tick) begin
                frame  <= parallel_data;
                data   <= column_data(parallel_data, 3'd7);
                column <= 3'd7;
                cycle  <= 5'd1;
                srclk  <= 1'b0;
                rclk   <= 1'b0;
                busy   <= 1'b1;
                zeroing <= 1'b0;
            end
        end
    end
endmodule
