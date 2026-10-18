`timescale 1ns/1ps
module tb_state_calibration;
    reg clk=0,rst_n=0;
    integer i,checks=0;
    always #10 clk=~clk;
    hap2_top #(.STATE_MS(0)) dut (
        .clk(clk),.rst_n(rst_n),.uart_rx_pin(1'b1),.boot_id(32'd1),
        .key_next(1'b0),.key_play(1'b0),.key_stop(1'b0),.output_fault(1'b0),.adc_sdata(1'b0));
    task check(input ok,input string message);
        begin checks=checks+1;if(ok!==1'b1)$fatal(1,"%s",message);end
    endtask
    initial begin
        repeat(4)@(negedge clk);rst_n=1;
        repeat(10)@(negedge clk);
        force dut.ph_pub_valid=1;
        force dut.ph_rd_data=0;
        force dut.calib_phase_offsets=0;
        force dut.state_capture=1;
        @(negedge clk);release dut.state_capture;
        repeat(10)@(negedge clk);
        force dut.calib_phase_offsets={16{8'd10}};
        force dut.cfg_phase_steps=8;
        wait(!dut.snap_copy);@(negedge clk);
        check(dut.sn_phase_offsets==0 && dut.sn_steps==64,"scalar snapshot is frozen");
        for(i=0;i<16;i=i+1)check(dut.snap_ph[i]==0,"phase copy uses frozen offsets and resolution");
        force dut.calib_done=1;
        force dut.calib_valid=1;
        force dut.state_capture=1;
        @(negedge clk);
        check(dut.sn_rev==1,"completion and capture share the committed revision");
        release dut.calib_done;release dut.calib_valid;release dut.state_capture;
        release dut.calib_phase_offsets;release dut.cfg_phase_steps;
        $display("Calibration STATE checks passed: %0d",checks);$finish;
    end
    initial begin #100000;$fatal(1,"snapshot timeout");end
endmodule
