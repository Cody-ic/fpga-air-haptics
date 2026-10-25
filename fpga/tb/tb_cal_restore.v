`timescale 1ns/1ps
// Run with tools in fpga/board/check_restore.py; UART is accelerated for simulation.
module tb_cal_restore #(
    parameter [511:0] OFFSETS=512'd0,
    parameter [511:0] EXPECTED=512'd0
);
    reg clk=0,rst_n=0,rx=1;
    // Lower simulated clock preserves real UART/carrier/watchdog ratios and
    // reduces event count. The synthesized board still runs at 50 MHz.
    always #100 clk=~clk;
    wire tx,on;
    wire [63:0] waves;
    hap2_top #(.CAL_ENABLE(1),.CAL_RESTORE_ENABLE(1),.CAL_RESTORE_OFFSETS(OFFSETS),
        .HW_ROWS(8),.HW_COLS(8),.HW_PITCH_UM(10300),.PH_PIPE(8),
        .CLK_HZ(5000000),.BAUD(1000000),.STATE_MS(0),.HB_MS(30)) dut (
        .clk(clk),.rst_n(rst_n),.uart_rx_pin(rx),.uart_tx_pin(tx),.boot_id(32'd99),
        .key_next(1'b0),.key_play(1'b0),.key_stop(1'b0),
        .mode_local(),.focus_x(),.focus_y(),.scan_on(),.stroke_index(),
        .output_on(on),.beat_pulse(),.walk_running(),.array_pos(waves),.array_neg());
    wire byte_valid;
    wire [7:0] byte_in;
    uart_rx #(.CLK_HZ(5000000),.BAUD(1000000)) monitor (
        .clk(clk),.rst_n(rst_n),.rx_line(tx),.rx_data(byte_in),.rx_valid(byte_valid),.rx_error());
    integer lines=0,fd,guard,i,j,k,b,offset=0,n;
    reg [7:0] commands[0:8191];
    reg [31:0] lengths[0:8];
    reg [63:0] seen;
    always @(posedge clk) if(byte_valid) begin
        $fwrite(fd,"%c",byte_in);
        $fflush(fd);
        if(byte_in==10) lines<=lines+1;
    end
    task send;
        input integer index,reply_lines;
        integer start_lines;
        begin
            start_lines=lines;
            for(j=0;j<lengths[index];j=j+1) begin
                b=commands[offset+j];
                @(negedge clk);rx=0;repeat(5) @(negedge clk);
                for(k=0;k<8;k=k+1) begin rx=(b>>k)&1;repeat(5) @(negedge clk);end
                rx=1;repeat(5) @(negedge clk);
            end
            offset=offset+lengths[index];guard=0;
            while(lines<start_lines+reply_lines && guard<120000) begin
                @(negedge clk);guard=guard+1;
            end
            if(lines<start_lines+reply_lines) $fatal(1,"UART reply deadline index=%0d",index);
            repeat(100) @(negedge clk);
        end
    endtask
    task check_phases;
        begin
            for(i=0;i<64;i=i+1) begin
                if(dut.u_out.code[i]!==EXPECTED[8*i +: 8])
                    $fatal(1,"drive phase mismatch channel=%0d actual=%0d expected=%0d",i,dut.u_out.code[i],EXPECTED[8*i +: 8]);
                if(dut.snap_ph[i]!==EXPECTED[8*i +: 8]) $fatal(1,"snapshot phase mismatch %0d",i);
            end
        end
    endtask
    initial begin
        fd=$fopen("uart.log","w");
        $readmemh("commands.hex",commands);
        $readmemh("lengths.hex",lengths);
        repeat(5) @(negedge clk);rst_n=1;
        repeat(1000) @(negedge clk);
        if(on || waves!=0 || !dut.cal_released) $fatal(1,"restore must boot idle into normal endpoint");
        send(0,2); // HELLO
        send(1,2); // MODE REMOTE
        send(2,2); // POINT center, 70 mm, level 100
        send(3,2); // SNAP after phase engine settled
        check_phases;
        if(on || waves!=0) $fatal(1,"configuration must not start output");
        send(4,2); // START
        seen=0;
        repeat(3000) begin @(negedge clk);seen=seen|waves;end
        if(!on || seen!==64'hffffffffffffffff) $fatal(1,"all channels must emit at full-level center focus");
        send(5,2); // STOP
        if(on || waves!=0) $fatal(1,"STOP must mute");
        send(6,1); // incompatible 39 kHz CONFIG
        if(dut.cfg_carrier_hz!=40000 || on) $fatal(1,"incompatible config must not apply");
        send(7,2); // START again
        repeat(160000) @(negedge clk);
        if(on || waves!=0 || dut.cmd_run!=0) $fatal(1,"missing heartbeat must stop output");
        send(8,2); // SNAP watchdog result
        @(negedge clk);rst_n=0;repeat(5) @(negedge clk);rst_n=1;
        repeat(1000) @(negedge clk);
        if(on || waves!=0 || dut.cal_offsets!==OFFSETS) $fatal(1,"reset must mute and retain built-in table");
        $fclose(fd);
        $display("PASS restored calibration: 64 drive/snapshot phases, boot/reset mute, full-level output, STOP, config guard, watchdog");
        $finish;
    end
    initial begin #300000000;$fatal(1,"simulation deadline");end
endmodule
