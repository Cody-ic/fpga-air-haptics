`timescale 1ns/1ps

// Independent check: sample actual output pins, extract their 40 kHz fundamental,
// and apply physical propagation delay. No golden phase codes are used here.
// Identical emitter response and polarity, spherical spreading, no reflections.
// This checks the phase convention; it does not predict calibrated sound pressure.
module tb_focus_propagation;
    localparam integer CHANNELS = 16;
    localparam integer PITCH_UM = 11000;
    localparam integer SAMPLES = 40000; // 32 carrier periods at 50 MHz / 40 kHz
    localparam real TWO_PI = 6.283185307179586;
    reg clk = 1'b0;
    always #10 clk = ~clk;
    reg rst_n = 1'b0;
    reg cfg_change = 1'b0;
    reg [31:0] height_um = 100000;
    reg signed [20:0] focus_x = 0, focus_y = 0;
    wire done, pub_sel;
    wire [7:0] table_addr, table_data;
    wire [CHANNELS-1:0] output_pins;

    hap2_phase #(.ROWS(4), .COLS(4), .PITCH_UM(PITCH_UM), .PIPE(4)) u_phase (
        .clk(clk), .rst_n(rst_n), .cfg_carrier_hz(32'd40000),
        .cfg_phase_steps(32'd64), .cfg_z_um(height_um), .cfg_change(cfg_change),
        .focus_x(focus_x), .focus_y(focus_y), .start(1'b0),
        .done(done), .pub_sel(pub_sel), .rd_sel(pub_sel), .rd_addr(8'd0),
        .rd2_addr(table_addr), .rd2_data(table_data)
    );
    hap2_out #(.ROWS(4), .COLS(4), .CLK_HZ(50000000), .ACC_BITS(32)) u_out (
        .clk(clk), .rst_n(rst_n), .cfg_carrier_hz(32'd40000),
        .cfg_phase_steps(32'd64), .cfg_change(cfg_change),
        .cfg_level(32'd100), .cfg_mod_hz(32'd0), .enable(1'b1),
        .tbl_new(done), .tbl_addr(table_addr), .tbl_data(table_data),
        .out_pos(output_pins)
    );

    real emitted_re [0:CHANNELS-1];
    real emitted_im [0:CHANNELS-1];
    real distance_um [0:CHANNELS-1];
    real x, y, theta, kr, cos_t, sin_t;
    real pressure_re, pressure_im, maximum_amplitude, coherence;
    integer ch, sample_i, checks = 0;

    task check_target;
        input integer x_um, y_um, z_um;
        begin
            @(negedge clk);
            focus_x = x_um * 2; focus_y = y_um * 2;
            height_um = z_um;
            cfg_change = 1'b1;
            @(negedge clk);
            cfg_change = 1'b0;
            // A configuration change triggers the complete phase sweep itself.
            wait(done);
            @(negedge clk);
            wait(u_out.phase_valid);
            repeat(10) @(negedge clk);
            for(ch=0; ch<CHANNELS; ch=ch+1) begin
                x = ((ch % 4) - 1.5) * PITCH_UM - x_um;
                y = ((ch / 4) - 1.5) * PITCH_UM - y_um;
                distance_um[ch] = $sqrt(x*x + y*y + $itor(z_um)*$itor(z_um));
                emitted_re[ch] = 0.0;
                emitted_im[ch] = 0.0;
            end
            // Fourier coefficient uses exp(-j*w*t); propagation is exp(-j*k*r).
            for(sample_i=0; sample_i<SAMPLES; sample_i=sample_i+1) begin
                @(posedge clk); #1;
                theta = TWO_PI * 40000.0 * sample_i / 50000000.0;
                cos_t = $cos(theta); sin_t = $sin(theta);
                for(ch=0; ch<CHANNELS; ch=ch+1) begin
                    if(output_pins[ch] === 1'b1) begin
                        emitted_re[ch] = emitted_re[ch] + cos_t;
                        emitted_im[ch] = emitted_im[ch] - sin_t;
                    end else if(output_pins[ch] !== 1'b0)
                        $fatal(1, "Unknown output pin value");
                end
            end
            pressure_re = 0.0; pressure_im = 0.0; maximum_amplitude = 0.0;
            for(ch=0; ch<CHANNELS; ch=ch+1) begin
                kr = TWO_PI * 40000.0 * distance_um[ch] / 343000000.0;
                pressure_re = pressure_re +
                    (emitted_re[ch]*$cos(kr) + emitted_im[ch]*$sin(kr))/distance_um[ch];
                pressure_im = pressure_im +
                    (emitted_im[ch]*$cos(kr) - emitted_re[ch]*$sin(kr))/distance_um[ch];
                maximum_amplitude = maximum_amplitude +
                    $sqrt(emitted_re[ch]*emitted_re[ch] + emitted_im[ch]*emitted_im[ch])/distance_um[ch];
            end
            if(maximum_amplitude <= 0.0) $fatal(1, "No emitted fundamental");
            coherence = $sqrt(pressure_re*pressure_re + pressure_im*pressure_im)/maximum_amplitude;
            checks = checks + 1;
            $display("Focus (%0d,%0d,%0d) um: coherent amplitude fraction %0.6f",
                     x_um, y_um, z_um, coherence);
            if(coherence < 0.995)
                $fatal(1, "Emitted phase does not compensate propagation delay");
        end
    endtask

    initial begin
        repeat(5) @(negedge clk);
        rst_n = 1'b1;
        check_target(0, 0, 50000);
        check_target(0, 0, 100000);
        check_target(20000, 10000, 100000);
        $display("Focus propagation checks passed: %0d", checks);
        $finish;
    end
    initial begin
        #5000000;
        $fatal(1, "Focus propagation test timed out");
    end
endmodule
