`timescale 1ns/1ps
module tb_calibration_protocol;
    reg clk=0,rst_n=0,rx=1;
    reg local_play=0;
    wire tx;
    wire[7:0] ch;
    wire ch_valid;
    integer frames=0,fd=0,checks=0;
    integer star,k;
    reg [15:0] frame_crc;
    reg [31:0] wire_crc;
    string line="";
    reg reconnect_guard=0;
    always #10 clk=~clk;
    always @(negedge clk) if(reconnect_guard && (dut.array_pos & 16'hfffe)!=0)
        $fatal(1,"reconnecting must never pulse a previously masked channel");
    hap2_top #(.STATE_MS(0)) dut(.clk(clk),.rst_n(rst_n),.uart_rx_pin(rx),.uart_tx_pin(tx),
        .boot_id(32'h12345678),.key_next(1'b0),.key_play(local_play),.key_stop(1'b0),
        .output_fault(1'b0),.adc_sdata(1'b0));
    uart_rx #(.CLK_HZ(50000000),.BAUD(115200)) mon(.clk(clk),.rst_n(rst_n),
        .rx_line(tx),.rx_data(ch),.rx_valid(ch_valid));
    always @(posedge clk) if(ch_valid) begin
        if(fd!=0)$fwrite(fd,"%c",ch);line={line,ch};
        if(ch==10)begin
            star=-1;frame_crc=16'hffff;
            for(k=0;k<line.len();k=k+1)if(line[k]=="*")star=k;
            check(star>0 && star+6==line.len(),"reply framing is valid");
            for(k=0;k<star;k=k+1)frame_crc=crc_byte(frame_crc,line[k]);
            check($sscanf(line.substr(star+1,star+4),"%h",wire_crc)==1 && wire_crc==frame_crc,
                  "entire reply passes CRC");
            frames=frames+1;
            line="";
        end
    end
    function automatic[15:0] crc_byte(input[15:0] c,input[7:0] b);
        reg[15:0] x;integer j;
        begin x=c^{b,8'h0};for(j=0;j<8;j=j+1)x=x[15]?(x<<1)^16'h1021:x<<1;return x;end
    endfunction
    task send(input string body,input integer response_frames=2);
        string packet;reg[15:0] crc;integer i,j,goal;
        begin
            goal=frames+response_frames;crc=16'hffff;
            for(i=0;i<body.len();i=i+1)crc=crc_byte(crc,body[i]);
            packet={body,$sformatf("*%04X\n",crc)};
            for(i=0;i<packet.len();i=i+1)begin
                @(negedge clk);rx=0;repeat(434)@(negedge clk);
                for(j=0;j<8;j=j+1)begin rx=packet[i][j];repeat(434)@(negedge clk);end
                rx=1;repeat(434)@(negedge clk);
            end
            wait(frames>=goal);repeat(20)@(negedge clk);
        end
    endtask
    task send_config(input integer seq,input integer steps);
        begin send($sformatf("HAP3 CMD %0d CONFIG carrier_hz=40000 phase_steps=%0d cx_um=0 cy_um=0 z_um=70000 radius_um=20000 repeat_millihz=500 mod_hz=0 level=100 shape=POINT path_xy_um=NONE path_closed=1 scan_paths=NONE blank_us=2000 hw_rows=4 hw_cols=4 hw_pitch_um=11000 mapping=ROW_MAJOR_XY",seq,steps));end
    endtask
    task check(input ok,input string message);
        begin checks=checks+1;if(ok!==1'b1)$fatal(1,"%s",message);end
    endtask
    initial begin
        if($test$plusargs("UART_LOG"))fd=$fopen("calibration_protocol_uart.log","w");
        repeat(4)@(negedge clk);rst_n=1;
        send("HAP3 CMD 1 HELLO");send_config(2,64);
        send("HAP3 CMD 3 CALIBRATION action=trial geometry_id=d3e4d118b6b2ca2469907382 mask=1 offsets=0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0");
        send("HAP3 CMD 4 START");
        check(dut.output_on && dut.calib_channel_mask==16'h1,"single-channel trial is active");
        reconnect_guard=1;
        send("HAP3 CMD 5 HELLO");
        reconnect_guard=0;
        check(!dut.output_on && dut.cmd_run==0 && !dut.traj_ready,
              "new remote handshake stops the old session and requires CONFIG");
        check(dut.calib_channel_mask==16'hffff,"mask restores only while output is stopped");
        send("HAP3 CMD 6 START");
        check(!dut.output_on,"reconnect cannot restart the previous trajectory");
        send("HAP3 CMD 7 STOP");send_config(8,64);
        send("HAP3 CMD 9 CALIBRATION action=store geometry_id=d3e4d118b6b2ca2469907382 mask=65535 offsets=63,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0");
        check(dut.calib_phase_offsets[7:0]==63,"valid saved correction is active");
        send_config(10,8);
        check(dut.calib_phase_offsets==0 && dut.sn_phase_offsets==0 && dut.sn_steps==8,
              "changing phase resolution removes incompatible saved codes from STATE");
        send_config(11,64);
        check(dut.calib_phase_offsets==0,"old table stays invalid after changing back");
        send("HAP3 CMD 12 CALIBRATION action=trial geometry_id=d3e4d118b6b2ca2469907382 mask=1 offsets=512,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0",1);
        check(dut.cmd_rev==6 && dut.calib_phase_offsets==0 && dut.calib_channel_mask==16'hffff,
              "out-of-range 512 is rejected atomically by the complete UART path");
        send("HAP3 CMD 13 MODE value=LOCAL");
        @(negedge clk);local_play=1;repeat(1100000)@(negedge clk);
        local_play=0;repeat(1100000)@(negedge clk);
        check(dut.output_on,"local key starts independent playback");
        send("HAP3 CMD 14 HELLO");
        check(dut.cmd_mode==0 && dut.cmd_run==1 && dut.output_on,
              "handshake preserves independent LOCAL playback");
        send("HAP3 CMD 15 STOP");
        check(!dut.output_on,"final STOP disables local output");
        if(fd!=0)$fclose(fd);
        $display("Calibration UART checks passed: %0d frames=%0d",checks,frames);$finish;
    end
    initial begin #2_000_000_000;$fatal(1,"timeout frames=%0d",frames);end
endmodule
