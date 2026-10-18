`timescale 1ns/1ps

// 8×8 R2: eight SN74LV595A devices, pitch 10.3 mm. No 64-pin expansion.
// Only the twelve control pins below are routed to J23; use the separate CST.
module board_shift_top #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD = 115200,
    parameter integer POR_MS = 10,
    parameter integer KEY_DEB_MS = 20,
    parameter integer STATE_MS = 500
) (
    input wire clk,
    input wire key_rst_n,
    input wire key0_n,
    input wire key1_n,
    input wire uart_rx_pin,
    output wire uart_tx_pin,
    output wire [7:0] data,
    output wire srclk,
    output wire rclk,
    output wire oe_n,
    output wire srclr_n,
    output wire [3:0] led,
    input wire adc_sdata,
    output wire adc_sclk,
    output wire adc_cs_n
);
    // At least 19 clocks between samples: 8 shift pulses + latch + settling.
    // 50 MHz / (64 * 19) = 41118 Hz; normal operation is 40 kHz / 64 steps.
    localparam integer MAX_SERIAL_CARRIER = CLK_HZ / (64 * 19);
    wire [63:0] parallel_data;
    wire sample_tick, drive_enable, fault;
    wire [2:0] core_led;

    // The serializer uses the core's synchronized power-on/key reset.
    wire rst_n;

    board_core #(
        .CLK_HZ(CLK_HZ), .BAUD(BAUD), .HW_ROWS(8), .HW_COLS(8),
        .HW_PITCH_UM(10300), .PH_PIPE(8), .POR_MS(POR_MS),
        .KEY_DEB_MS(KEY_DEB_MS), .STATE_MS(STATE_MS),
        .MAX_CARRIER_HZ(MAX_SERIAL_CARRIER), .MAX_PHASE_STEPS(64),
        .HAS_OUTPUT_FAULT(1)
    ) u_core (
        .clk(clk), .key_rst_n(key_rst_n), .key0_n(key0_n), .key1_n(key1_n),
        .uart_rx_pin(uart_rx_pin), .uart_tx_pin(uart_tx_pin),
        .array_pos(parallel_data), .led(core_led),
        .output_fault(fault), .sample_tick(sample_tick), .drive_enable(drive_enable),
        .adc_sdata(adc_sdata), .adc_sclk(adc_sclk), .adc_cs_n(adc_cs_n),
        .board_rst_n(rst_n)
    );

    hap2_shift595 u_shift (
        .clk(clk), .rst_n(rst_n), .enable(drive_enable),
        .sample_tick(sample_tick), .parallel_data(parallel_data),
        .data(data), .srclk(srclk), .rclk(rclk),
        .oe_n(oe_n), .srclr_n(srclr_n), .fault(fault)
    );

    // Low-active LEDs: output request / scan / walker / serializer fault.
    // OE remains off on a fault until STOP (or an envelope-off interval) flushes
    // a zero frame. The snapshot also reports output=0, reason=OUTPUT_FAULT.
    assign led = {~fault, core_led};
endmodule
