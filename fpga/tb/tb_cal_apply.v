`timescale 1ns/1ps
// Check the actual normal output table and STATE snapshot after startup handoff.
module tb_cal_apply;
    reg clk=0,rst_n=0;
    always #5 clk=~clk;
    wire [1:0] pos,neg;
    wire on;
    hap2_top #(.CLK_HZ(1000000),.BAUD(100000),.HW_ROWS(1),.HW_COLS(2),
               .PH_PIPE(1),.CAL_ENABLE(1),.STATE_MS(0)) dut (
        .clk(clk),.rst_n(rst_n),.uart_rx_pin(1'b1),.uart_tx_pin(),.boot_id(32'd17),
        .key_next(1'b0),.key_play(1'b0),.key_stop(1'b0),.mode_local(),.focus_x(),.focus_y(),
        .scan_on(),.stroke_index(),.output_on(on),.beat_pulse(),.walk_running(),.array_pos(pos),.array_neg(neg));
    initial begin
        repeat(3) @(negedge clk); rst_n=1;
        repeat(3000) @(negedge clk);
        if(pos!=0 || on || dut.business_rst_n) $fatal(1,"startup must hold normal business reset");
        // The UART startup test exercises how this bank is obtained. Here test
        // the handoff boundary independently with a known nonzero bank.
        force dut.cal_offsets=16'h0900;
        force dut.cal_released=1'b1;
        force dut.config_changed=1'b1;
        repeat(2) @(negedge clk); force dut.config_changed=1'b0;
        wait(dut.ph_done);
        wait(dut.u_out.phase_valid);
        repeat(5) @(negedge clk);
        if(dut.u_out.code[1] != ((dut.u_out.code[0]+9)&63)) $fatal(1,"effective output phase mismatch");
        force dut.state_capture=1'b1;
        @(negedge clk); force dut.state_capture=1'b0;
        wait(!dut.snap_copy);
        if(dut.snap_ph[1] != ((dut.snap_ph[0]+9)&63)) $fatal(1,"STATE must report effective phases");
        force dut.cmd_run=2'd1; force dut.scan_on=1'b1;
        repeat(3) @(negedge clk);
        if(!on) $fatal(1,"compatible config should enable output");
        force dut.cfg_carrier_hz=32'd41000;
        repeat(3) @(negedge clk);
        if(on || pos!=0 || neg!=0) $fatal(1,"unsupported config must mute");
        $display("PASS calibration handoff: effective output/snapshot phases, unsupported config mute");
        $finish;
    end
    initial begin #10000000; $fatal(1,"deadline"); end
endmodule
