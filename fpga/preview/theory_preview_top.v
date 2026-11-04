`timescale 1ns/1ps
// ==================== VIDEO LINK TEST SWITCH ====================
// 0: theory preview; 1: static bars/checkerboard, no UART or emission.
// build_preview.py --link-test sets this macro in its generated source copy.
`define FPGA_VIDEO_LINK_TEST 0
// ================================================================
// Standalone PREVIEW image. Shift-register outputs are constant-disabled at the pads.
module theory_preview_top(
    input wire clk,key_rst_n,key0_n,key1_n,uart_rx_pin,
    output wire uart_tx_pin,
    output wire [7:0] sr_data,
    output wire sr_clk,sr_latch,sr_oe_n,sr_clear_n,
    output wire [2:0] led,
    output wire tmds_clk_p,tmds_clk_n,
    output wire [2:0] tmds_data_p,tmds_data_n
);
    wire pixel_clk,serial_clk,locked,rst_n;
    // 50 MHz / 2 * 49 = 1225 MHz VCO; /5 = 245 MHz; /25 = 49 MHz.
    PLL #(.FCLKIN("50"),.IDIV_SEL(2),.FBDIV_SEL(1),.MDIV_SEL(49),
        .ODIV0_SEL(5),.ODIV1_SEL(25),.CLKOUT0_EN("TRUE"),.CLKOUT1_EN("TRUE")) video_pll(
        .CLKIN(clk),.CLKFB(1'b0),.RESET(1'b0),.PLLPWD(1'b0),.RESET_I(1'b0),.RESET_O(1'b0),
        .CLKOUT0(serial_clk),.CLKOUT1(pixel_clk),.LOCK(locked),
        .ENCLK0(1'b1),.ENCLK1(1'b1),.ENCLK2(1'b0),.ENCLK3(1'b0),.ENCLK4(1'b0),.ENCLK5(1'b0),.ENCLK6(1'b0),
        .FBDSEL(6'd0),.IDSEL(6'd0),.MDSEL(7'd0),.MDSEL_FRAC(3'd0),
        .ODSEL0(7'd0),.ODSEL0_FRAC(3'd0),.ODSEL1(7'd0),.ODSEL2(7'd0),.ODSEL3(7'd0),
        .ODSEL4(7'd0),.ODSEL5(7'd0),.ODSEL6(7'd0),.DT0(4'd0),.DT1(4'd0),.DT2(4'd0),.DT3(4'd0),
        .PSSEL(3'd0),.PSDIR(1'b0),.PSPULSE(1'b0),.SSCPOL(1'b0),.SSCON(1'b0),
        .SSCMDSEL(7'd0),.SSCMDSEL_FRAC(3'd0),.ICPSEL(6'd0),.LPFRES(3'd0),.LPFCAP(2'd0));
    por_reset #(.CLK_HZ(49000000)) por(.clk(pixel_clk),.ext_rst_n(key_rst_n && locked),.rst_n(rst_n));
    wire boundary,valid;
    wire [11:0] address;
    wire [7:0] value,r,g,b;
    wire signed [20:0] fx,fy;
    wire [31:0] z,cycles,frames;
    wire [1:0] run_state;
    wire de,hs,vs;
    generate if (`FPGA_VIDEO_LINK_TEST) begin:link_test
        assign uart_tx_pin=1'b1;
        assign valid=0;assign value=0;assign fx=0;assign fy=0;
        assign z=0;assign cycles=0;assign frames=0;assign run_state=0;
    end else begin:theory
    theory_preview_core core(.clk(pixel_clk),.rst_n(rst_n),.key_next(~key0_n),.key_play(~key1_n),
        .uart_rx_pin(uart_rx_pin),.uart_tx_pin(uart_tx_pin),.frame_boundary(boundary),
        .field_read_addr(address),.field_read_value(value),.field_valid(valid),
        .displayed_x(fx),.displayed_y(fy),.displayed_z(z),.field_cycles(cycles),
        .frame_number(frames),.run_state(run_state));
    end endgenerate
    preview_video #(.LINK_TEST(`FPGA_VIDEO_LINK_TEST)) video(.clk(pixel_clk),.rst_n(rst_n),.field_value(value),.field_valid(valid),
        .focus_x(fx),.focus_y(fy),.z_um(z),.field_cycles(cycles),.frame_number(frames),
        .run_state(run_state),.frame_boundary(boundary),.field_addr(address),
        .red(r),.green(g),.blue(b),.de(de),.hs(hs),.vs(vs));
    wire [9:0] symbols[0:3];
    tmds_encoder enc_b(.clk(pixel_clk),.rst_n(rst_n),.de(de),.c0(hs),.c1(vs),.data(b),.symbol(symbols[0]));
    tmds_encoder enc_g(.clk(pixel_clk),.rst_n(rst_n),.de(de),.c0(1'b0),.c1(1'b0),.data(g),.symbol(symbols[1]));
    tmds_encoder enc_r(.clk(pixel_clk),.rst_n(rst_n),.de(de),.c0(1'b0),.c1(1'b0),.data(r),.symbol(symbols[2]));
    assign symbols[3]=10'b1111100000;
    wire [3:0] serial;
    wire [3:0] serializer_reset;
    preview_serializer_reset reset_driver(.pixel_clk(pixel_clk),.request(!rst_n),.reset(serializer_reset));
    genvar i;
    generate for(i=0;i<4;i=i+1) begin:lane
        OSER10 serializer(.Q(serial[i]),.D0(symbols[i][0]),.D1(symbols[i][1]),.D2(symbols[i][2]),
            .D3(symbols[i][3]),.D4(symbols[i][4]),.D5(symbols[i][5]),.D6(symbols[i][6]),
            .D7(symbols[i][7]),.D8(symbols[i][8]),.D9(symbols[i][9]),
            .PCLK(pixel_clk),.FCLK(serial_clk),.RESET(serializer_reset[i]));
        if(i<3) begin:data_lane
            TLVDS_OBUF buf_d(.I(serial[i]),.O(tmds_data_p[i]),.OB(tmds_data_n[i]));
        end else begin:clock_lane
            TLVDS_OBUF buf_c(.I(serial[i]),.O(tmds_clk_p),.OB(tmds_clk_n));
        end
    end endgenerate
    assign sr_data=0;assign sr_clk=0;assign sr_latch=0;assign sr_oe_n=1;assign sr_clear_n=0;
    assign led=~{valid,run_state==1,locked};
endmodule
