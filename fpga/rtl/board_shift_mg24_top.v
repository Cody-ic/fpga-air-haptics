`timescale 1ns/1ps
// MG24 is the sole control link. On-board debug UART is not electrically shared.
module board_shift_mg24_top #(
    parameter integer CLK_HZ=50000000, BAUD=115200, POR_MS=10, STATE_MS=500
) (
    input wire clk, key_rst_n, key0_n, key1_n,
    input wire mg24_rx, output wire mg24_tx, mg24_trigger,
    output wire [7:0] data, output wire srclk, rclk, oe_n, srclr_n,
    output wire [3:0] led
);
    board_shift_top #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .POR_MS(POR_MS),
        .STATE_MS(STATE_MS), .CAPTURE_BACKEND(1)) u_board (
        .clk(clk), .key_rst_n(key_rst_n), .key0_n(key0_n), .key1_n(key1_n),
        .uart_rx_pin(mg24_rx), .uart_tx_pin(mg24_tx), .led(led),
        .data(data), .srclk(srclk), .rclk(rclk), .oe_n(oe_n), .srclr_n(srclr_n), .adc_sdata(1'b0), .adc_sclk(), .adc_cs_n(mg24_trigger)
    );
endmodule
