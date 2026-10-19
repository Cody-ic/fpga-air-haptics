`timescale 1ns/1ps
// Full serial path: 64-bit masks and remote ADC metadata. No real ADC simulated.
module tb_mg24_link;
    reg clk=0, reset_n=0, rx=1;
    wire tx, trigger;
    wire [7:0] data;
    wire srclk,rclk,oe_n,srclr_n;
    wire [3:0] led;
    wire [7:0] byte_rx;
    wire byte_valid;
    integer frames=0, checks=0, triggers=0, pulse_cycles=0, fd=0;
    string line="", last="", hello="", state_line="";
    reg [15:0] crc;
    reg [31:0] reported_crc;
    integer star,k;
    always #10 clk=~clk;
    board_shift_mg24_top #(.BAUD(5000000),.POR_MS(1),.STATE_MS(0)) dut(
        .clk(clk),.key_rst_n(reset_n),.key0_n(1'b1),.key1_n(1'b1),
        .mg24_rx(rx),.mg24_tx(tx),.mg24_trigger(trigger),.data(data),
        .srclk(srclk),.rclk(rclk),.oe_n(oe_n),.srclr_n(srclr_n),.led(led));
    uart_rx #(.CLK_HZ(50000000),.BAUD(5000000)) mon(
        .clk(clk),.rst_n(reset_n),.rx_line(tx),.rx_data(byte_rx),.rx_valid(byte_valid));
    function automatic [15:0] crc_byte(input [15:0] c,input [7:0] b);
        reg[15:0] x;integer j;
        begin x=c^{b,8'h0};for(j=0;j<8;j=j+1)x=x[15]?(x<<1)^16'h1021:x<<1;return x;end
    endfunction
    function automatic bit contains(input string s,input string token);
        integer i;
        begin contains=0;for(i=0;i<=s.len()-token.len();i=i+1)
            if(s.substr(i,i+token.len()-1)==token)contains=1;end
    endfunction
    task check(input bit ok,input string msg);
        begin checks++;if(!ok)$fatal(1,"%s",msg);end
    endtask
    always @(posedge clk) begin
        if(trigger)pulse_cycles++;
        if(trigger && pulse_cycles==1)triggers++;
        if(!trigger && pulse_cycles!=0)begin
            check(pulse_cycles==500,"remote trigger is held for exactly 10 us");pulse_cycles=0;
        end
        if(byte_valid)begin
            if(fd)$fwrite(fd,"%c",byte_rx);line={line,byte_rx};
            if(byte_rx==10)begin
                star=-1;crc=16'hffff;
                for(k=0;k<line.len();k++)if(line[k]=="*")star=k;
                check(star>0 && star+6==line.len(),"reply framing");
                for(k=0;k<star;k++)crc=crc_byte(crc,line[k]);
                check($sscanf(line.substr(star+1,star+4),"%h",reported_crc)==1 && reported_crc==crc,"reply CRC");
                last=line;
                if(contains(line," HELLO "))hello=line;
                if(contains(line," STATE "))state_line=line;
                line="";frames++;
            end
        end
    end
    task send(input string body,input integer count=2);
        string packet;reg[15:0] c;integer i,j,goal;
        begin goal=frames+count;c=16'hffff;
            for(i=0;i<body.len();i++)c=crc_byte(c,body[i]);
            packet={body,$sformatf("*%04X\n",c)};
            for(i=0;i<packet.len();i++)begin
                @(negedge clk);rx=0;repeat(10)@(negedge clk);
                for(j=0;j<8;j++)begin rx=packet[i][j];repeat(10)@(negedge clk);end
                rx=1;repeat(10)@(negedge clk);
            end
            wait(frames>=goal);repeat(20)@(negedge clk);
        end
    endtask
    string offsets, body;
    initial begin
        if($test$plusargs("UART_LOG"))fd=$fopen("mg24_link_uart.log","w");
        repeat(5)@(negedge clk);reset_n=1;repeat(50050)@(negedge clk);
        send("HAP3 CMD 1 HELLO");
        check(contains(hello,"receiver=MG24") && contains(hello,"CALIBRATION"),"MG24 backend identifies itself");
        check(contains(state_line,"channel_mask=18446744073709551615 "),"unsigned full 64-bit mask round-trips");
        send("HAP3 CMD 2 CONFIG carrier_hz=40000 phase_steps=64 cx_um=0 cy_um=0 z_um=70000 radius_um=20000 repeat_millihz=500 mod_hz=0 level=100 shape=POINT path_xy_um=NONE path_closed=1 scan_paths=NONE blank_us=2000 hw_rows=8 hw_cols=8 hw_pitch_um=10300 mapping=ROW_MAJOR_XY");
        offsets="";for(k=0;k<64;k++)offsets={offsets,(k==0?"":","),(k==63?"63":"0")};
        body={"HAP3 CMD 3 CALIBRATION action=trial geometry_id=f0e8e8e16397381dfcf31ea6 mask=9223372036854775808 offsets=",offsets};send(body);
        check(dut.u_board.u_core.u_top.calib_channel_mask==64'h8000000000000000,"channel 63 can be selected alone");
        check(dut.u_board.u_core.u_top.calib_phase_offsets[511:504]==63,"channel 63 correction is retained");
        check(contains(state_line,"channel_mask=9223372036854775808 "),"bit 63 is not printed as negative");
        body={"HAP3 CMD 4 CALIBRATION action=store geometry_id=f0e8e8e16397381dfcf31ea6 mask=18446744073709551615 offsets=",offsets};send(body);
        check(dut.u_board.u_core.u_top.calib_channel_mask==64'hffffffffffffffff,"full-mask store succeeds");
        body={"HAP3 CMD 5 CALIBRATION action=trial geometry_id=f0e8e8e16397381dfcf31ea6 mask=18446744073709551616 offsets=",offsets};send(body,1);
        check(contains(last," ERR ") && dut.u_board.u_core.u_top.calib_channel_mask==64'hffffffffffffffff,"uint64 overflow is rejected atomically");
        body={"HAP3 CMD 6 CALIBRATION action=trial geometry_id=f0e8e8e16397381dfcf31ea6 mask=4294967296 offsets=",offsets};send(body);
        check(dut.u_board.u_core.u_top.calib_channel_mask==64'h0000000100000000,"channel 32 is not truncated");
        send("HAP3 CMD 7 CAPTURE",1);
        check(triggers==1 && contains(last," CAPTURE_META ") && !contains(last,"raw="),"remote capture returns metadata, never synthetic ADC codes");
        check(contains(last,"tx_running=0"),"capture metadata records output-off state");
        send("HAP3 CMD 8 HELLO");
        check(oe_n && dut.u_board.u_core.u_top.calib_channel_mask==64'hffffffffffffffff,"reconnect restores mask while keeping outputs off");
        send("HAP3 CMD 9 STOP");
        if(fd)$fclose(fd);
        $display("MG24/64-channel serial checks passed: %0d frames=%0d",checks,frames);$finish;
    end
    initial begin #200000000;$fatal(1,"timeout");end
endmodule
