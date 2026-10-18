`timescale 1ns/1ps

// ADCS7476 short-window capture controller.
// At 50 MHz, SAMPLE_CYCLES=125 gives 400 kS/s and SCLK_HALF=2 gives 12.5 MHz.
// One warm-up conversion is discarded, then exactly SAMPLES 12-bit words are saved.
module hap3_adc_capture #(
    parameter integer SAMPLES = 200,
    parameter integer SAMPLE_CYCLES = 125,
    parameter integer SCLK_HALF = 2
) (
    input  wire clk,
    input  wire rst_n,
    input  wire start,
    input  wire consume,
    input  wire adc_sdata,
    output reg  adc_sclk,
    output reg  adc_cs_n,
    output reg  busy,
    output reg  result_valid,
    output reg  done,
    input  wire [7:0] rd_addr,
    output reg [11:0] rd_data
);
    localparam integer TOTAL = SAMPLES + 1;
    reg [11:0] samples [0:SAMPLES-1];
    reg [15:0] period_count;
    reg [7:0] half_count;
    reg [5:0] edge_count;
    reg [10:0] shift;
    reg [8:0] conversion;
    reg active;

    always @(posedge clk) rd_data <= samples[rd_addr];

    always @(posedge clk) begin
        if (!rst_n) begin
            adc_sclk <= 1'b0;
            adc_cs_n <= 1'b1;
            busy <= 1'b0;
            result_valid <= 1'b0;
            done <= 1'b0;
            period_count <= 0;
            half_count <= 0;
            edge_count <= 0;
            shift <= 0;
            conversion <= 0;
            active <= 1'b0;
        end else begin
            done <= 1'b0;
            if (consume) result_valid <= 1'b0;

            if (start && !busy && !result_valid) begin
                busy <= 1'b1;
                active <= 1'b1;
                adc_cs_n <= 1'b0;
                adc_sclk <= 1'b0;
                period_count <= 0;
                half_count <= 0;
                edge_count <= 0;
                shift <= 0;
                conversion <= 0;
            end else if (active) begin
                if (half_count + 1 >= SCLK_HALF) begin
                    half_count <= 0;
                    adc_sclk <= ~adc_sclk;
                    if (!adc_sclk) begin
                        // Rising edge: the converter's current serial bit is stable.
                        shift <= {shift[9:0], adc_sdata};
                        edge_count <= edge_count + 1'b1;
                        if (edge_count == 6'd15) begin
                            adc_cs_n <= 1'b1;
                            adc_sclk <= 1'b0;
                            active <= 1'b0;
                            period_count <= 0;
                            if (conversion != 0)
                                samples[conversion[7:0]-1'b1] <= {shift, adc_sdata};
                            if (conversion + 1 >= TOTAL) begin
                                busy <= 1'b0;
                                result_valid <= 1'b1;
                                done <= 1'b1;
                            end
                        end
                    end
                end else half_count <= half_count + 1'b1;
            end else if (busy) begin
                if (period_count + 1 >= SAMPLE_CYCLES - 16*SCLK_HALF*2) begin
                    active <= 1'b1;
                    adc_cs_n <= 1'b0;
                    adc_sclk <= 1'b0;
                    period_count <= 0;
                    half_count <= 0;
                    edge_count <= 0;
                    shift <= 0;
                    conversion <= conversion + 1'b1;
                end
                else period_count <= period_count + 1'b1;
            end
        end
    end
endmodule
