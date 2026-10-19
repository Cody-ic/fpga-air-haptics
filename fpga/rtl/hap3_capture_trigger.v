`timescale 1ns/1ps
// Remote sampling: only the MG24 may attach ADC data to CAPTURE_META.
// A >=10 us pulse crosses the independent MCU clock domain. No fake samples.
module hap3_capture_trigger #(
    parameter integer CLK_HZ=50000000,
    parameter integer PULSE_CYCLES=(CLK_HZ/100000 > 2) ? CLK_HZ/100000 : 2
) (
    input wire clk, rst_n, start, consume,
    output reg trigger, busy, done, result_valid
);
    reg [31:0] remaining;
    always @(posedge clk) begin
        if (!rst_n) begin
            trigger<=0; busy<=0; done<=0; result_valid<=0; remaining<=0;
        end else begin
            done<=0;
            if (consume) result_valid<=0;
            if (start && !busy && !result_valid) begin
                trigger<=1; busy<=1; remaining<=PULSE_CYCLES;
            end else if (busy) begin
                if (remaining==1) begin
                    trigger<=0; busy<=0; result_valid<=1; done<=1;
                end else remaining<=remaining-1;
            end
        end
    end
endmodule
