`timescale 1ns/1ps
// Run with tools in fpga/board/check_restore.py; UART is accelerated for simulation.
module tb_static_phase #(
    parameter integer COUNT=32, BYTES=8192, CHECK_WATCHDOG=1,
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
        .CLK_HZ(5000000),.BAUD(1000000),.STATE_MS(0),.HB_MS(100)) dut (
        .clk(clk),.rst_n(rst_n),.uart_rx_pin(rx),.uart_tx_pin(tx),.boot_id(32'd99),
        .key_next(1'b0),.key_play(1'b0),.key_stop(1'b0),
        .mode_local(),.focus_x(),.focus_y(),.scan_on(),.stroke_index(),
        .output_on(on),.beat_pulse(),.walk_running(),.array_pos(waves),.array_neg());
    wire byte_valid;
    wire [7:0] byte_in;
    uart_rx #(.CLK_HZ(5000000),.BAUD(1000000)) monitor (
        .clk(clk),.rst_n(rst_n),.rx_line(tx),.rx_data(byte_in),.rx_valid(byte_valid),.rx_error());
    integer lines=0,fd,guard,i,j,k,b,offset=0,n;
    reg [7:0] commands[0:BYTES-1];
    reg [31:0] lengths[0:COUNT-1];
    reg [7:0] replies[0:COUNT-1];
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
            while(lines<start_lines+reply_lines && guard<600000) begin
                @(negedge clk);guard=guard+1;
            end
            if(lines<start_lines+reply_lines) $fatal(1,"UART reply deadline index=%0d pending=%0d parse_busy=%0d plan_busy=%0d",index,dut.u_cmd.pending,dut.scan_parse_busy,dut.traj_plan_busy);
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
    integer command_i;
    initial begin
        fd=$fopen("uart.log","w");
        $readmemh("commands.hex",commands);
        $readmemh("lengths.hex",lengths);
        $readmemh("replies.hex",replies);
        repeat(5) @(negedge clk);rst_n=1;
        repeat(1000) @(negedge clk);
        if(on || waves!=0) $fatal(1,"boot must mute");
        for(command_i=0;command_i<COUNT;command_i=command_i+1) begin
            if(CHECK_WATCHDOG && command_i==COUNT-1) repeat(510000) @(negedge clk);
            send(command_i,replies[command_i]);
            if(dut.cfg_static) check_phases;
            if(dut.cmd_run!=1 && (on || waves!=0)) $fatal(1,"idle/pause/stop must mute");
            if(command_i==2) begin
                seen=0;
                repeat(1000) begin @(negedge clk);seen=seen|waves;end
                if(seen!==64'hffffffffffffffff) $fatal(1,"static channels did not emit");
                if(dut.view_active || dut.view_level!=0) $fatal(1,"static mode must suppress single focus preview");
            end
        end
        $fclose(fd);
        $display("PASS %0d command transactions; all configured static drive/snapshot phases matched",COUNT);
        $finish;
    end
    initial begin #1000000000;$fatal(1,"simulation deadline");end
endmodule
