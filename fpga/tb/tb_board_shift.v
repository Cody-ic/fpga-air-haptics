`timescale 1ns/1ps

// Board-level regression for the assembled 8x8 PCB's eight 595 DATA lanes.
// Requests/responses use real UART pins. The direct 4x4 profile is exercised
// separately to ensure serial transport limits do not leak into that build.
module tb_board_shift;
    localparam integer CLK_HZ=50_000_000, BAUD=115200;
    localparam integer BIT_CYC=(CLK_HZ+BAUD/2)/BAUD;
    reg clk=0;
    always #10 clk=~clk;
    reg reset_n=0;
    reg key0_n=1,key1_n=1;
    reg rx8=1, rx4=1;
    wire tx8,tx4,srclk,rclk,oe_n,srclr_n;
    wire [7:0] data;
    wire [3:0] led8;
    wire [15:0] direct_out;
    board_shift_top #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.POR_MS(1),
        .KEY_DEB_MS(1),.STATE_MS(0)) dut (
        .clk(clk),.key_rst_n(reset_n),.key0_n(key0_n),.key1_n(key1_n),
        .uart_rx_pin(rx8),.uart_tx_pin(tx8),.srclk(srclk),.rclk(rclk),
        .oe_n(oe_n),.srclr_n(srclr_n),.data(data),.led(led8)
    );
    board_top #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.POR_MS(1),
        .KEY_DEB_MS(1),.STATE_MS(0)) direct_dut (
        .clk(clk),.key_rst_n(reset_n),.key0_n(1'b1),.key1_n(1'b1),
        .uart_rx_pin(rx4),.uart_tx_pin(tx4),.array_pos(direct_out)
    );

    wire [7:0] byte8,byte4;
    wire valid8,valid4,error8,error4;
    uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) read8 (.clk(clk),.rst_n(1'b1),
        .rx_line(tx8),.rx_data(byte8),.rx_valid(valid8),.rx_error(error8));
    uart_rx #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) read4 (.clk(clk),.rst_n(1'b1),
        .rx_line(tx4),.rx_data(byte4),.rx_valid(valid4),.rx_error(error4));

    integer checks=0;
    integer frames8=0,frames4=0;
    string line8="",line4="";
    string ack8,ack4,state8,state4,last8;
    integer shifts=0,latches=0,frame_shifts=0;
    reg [7:0] shift_reg[0:7];
    reg [7:0] latch_reg[0:7];
    reg [63:0] physical_latches;
    integer waveform_rises=0;
    reg measure_wave=0;
    time previous_wave_rise=0;

    task check;
        input ok;
        input string label;
        begin checks=checks+1; if(ok !== 1'b1) $fatal(1,"%0s",label); end
    endtask
    function automatic [15:0] crc_byte;
        input [15:0] crc_in;
        input [7:0] value;
        reg [15:0] crc;
        integer bit_i;
        begin
            crc=crc_in^{value,8'h00};
            for(bit_i=0;bit_i<8;bit_i=bit_i+1)
                crc=crc[15] ? (crc<<1)^16'h1021 : crc<<1;
            crc_byte=crc;
        end
    endfunction
    function automatic string field_value;
        input string text;
        input string key;
        integer i,j;
        begin
            field_value="";
            for(i=0;i+key.len()<=text.len();i=i+1)
                if(text.substr(i,i+key.len()-1)==key) begin
                    j=i+key.len();
                    while(j<text.len() && text[j]!=" " && text[j]!="*") j=j+1;
                    field_value=text.substr(i+key.len(),j-1);
                    return field_value;
                end
        end
    endfunction
    task receive_line;
        input integer board;
        input string text;
        integer i,star;
        reg [15:0] crc;
        reg [31:0] checksum;
        begin
            star=-1;crc=16'hffff;
            for(i=0;i<text.len();i=i+1) if(text[i]=="*" && star<0) star=i;
            check(star>0 && star+6==text.len(),"board UART frame structure");
            for(i=0;i<star;i=i+1) crc=crc_byte(crc,text[i]);
            check($sscanf(text.substr(star+1,star+4),"%h",checksum)==1
                && checksum=={16'd0,crc},"board UART frame CRC");
            if(board==8) begin
                last8=text;
                frames8=frames8+1;
                if(text.substr(0,8)=="HAP3 ACK ") ack8=text;
                if(text.substr(0,15)=="HAP3 TEL 0 STATE") state8=text;
            end else begin
                frames4=frames4+1;
                if(text.substr(0,8)=="HAP3 ACK ") ack4=text;
                if(text.substr(0,15)=="HAP3 TEL 0 STATE") state4=text;
            end
        end
    endtask
    always @(posedge clk) begin
        if(error8 || error4) $fatal(1,"board UART framing error");
        if(valid8) begin
            line8={line8,byte8};
            if(byte8==10) begin receive_line(8,line8);line8="";end
        end
        if(valid4) begin
            line4={line4,byte4};
            if(byte4==10) begin receive_line(4,line4);line4="";end
        end
    end

    // Independent physical 595 model: eight parallel lanes, MSB first.
    genvar row;
    generate for(row=0;row<8;row=row+1) begin:g_595
        initial begin shift_reg[row]=0;latch_reg[row]=0;end
        always @(posedge srclk or negedge srclr_n)
            if(!srclr_n) shift_reg[row]<=0;
            else shift_reg[row]<={shift_reg[row][6:0],data[row]};
        always @(posedge rclk) latch_reg[row]<=shift_reg[row];
        always @* physical_latches[row*8+:8]=latch_reg[row];
    end endgenerate
    always @(posedge srclk) begin
        check(!rclk,"shift clock cannot rise during latch pulse");
        shifts=shifts+1;frame_shifts=frame_shifts+1;
    end
    always @(negedge srclr_n) frame_shifts=0;
    always @(posedge rclk) begin
        // A fault can abandon an unlatched partial frame. While OE is disabled,
        // eight fresh zero bits flush that partial content; normal enabled
        // frames still require exactly eight clocks between latches.
        check(!srclk && frame_shifts>=8 && (oe_n || frame_shifts==8),
            "active frame has eight serial bits and disabled flush is complete");
        latches=latches+1;frame_shifts=0;
    end
    always @(posedge latch_reg[0][0]) begin
        if(measure_wave && !oe_n) begin
            if(previous_wave_rise != 0)
                check($time-previous_wave_rise>=24000 && $time-previous_wave_rise<=26000,
                    "physical 595 output is a 40 kHz carrier");
            previous_wave_rise=$time;
            waveform_rises=waveform_rises+1;
        end
    end

    task send_byte;
        input integer board;
        input [7:0] value;
        integer bit_i;
        begin
            @(negedge clk);if(board==8) rx8=0;else rx4=0;
            repeat(BIT_CYC) @(negedge clk);
            for(bit_i=0;bit_i<8;bit_i=bit_i+1) begin
                if(board==8) rx8=value[bit_i];else rx4=value[bit_i];
                repeat(BIT_CYC) @(negedge clk);
            end
            if(board==8) rx8=1;else rx4=1;
            repeat(BIT_CYC) @(negedge clk);
        end
    endtask
    task send_command;
        input integer board;
        input string body;
        reg [15:0] crc;
        string packet;
        integer i;
        begin
            crc=16'hffff;
            for(i=0;i<body.len();i=i+1) crc=crc_byte(crc,body[i]);
            packet={body,$sformatf("*%04X\n",crc)};
            for(i=0;i<packet.len();i=i+1) send_byte(board,packet[i]);
        end
    endtask
    task wait_frames;
        input integer board;
        input integer wanted;
        integer guard;
        begin
            guard=0;
            while(((board==8) ? frames8 : frames4)<wanted && guard<8_000_000) begin
                @(negedge clk);guard=guard+1;
            end
            check(guard<8_000_000,"board replies within UART budget");
        end
    endtask
    task send_config;
        input integer board,seq,carrier,steps;
        string body;
        begin
            body=$sformatf("HAP3 CMD %0d CONFIG carrier_hz=%0d phase_steps=%0d cx_um=0 cy_um=0 z_um=70000 radius_um=20000 repeat_millihz=500 mod_hz=0 level=100 shape=POINT path_xy_um=NONE path_closed=1 scan_paths=NONE blank_us=2000 hw_rows=%0d hw_cols=%0d hw_pitch_um=%0d mapping=ROW_MAJOR_XY",
                seq,carrier,steps,board,board,(board==8)?10300:11000);
            send_command(board,body);
        end
    endtask
    task bad_serial_config;
        input integer seq,carrier,steps;
        integer previous,reply_seq;
        begin
            previous=frames8;
            send_config(8,seq,carrier,steps);wait_frames(8,previous+1);
            $display("serial limit seq=%0d carrier=%0d steps=%0d reply=%0s max=%0d rev=%0d",seq,carrier,steps,last8,
                dut.u_core.u_top.u_cmd.MAX_PHASE_STEPS,dut.u_core.u_top.cmd_rev);
            check($sscanf(last8,"HAP3 ERR %d CONFIG",reply_seq)==1 && reply_seq==seq
                && field_value(last8,"code=")=="BAD_CONFIG","unsupported serial timing is BAD_CONFIG");
            check(dut.u_core.u_top.cmd_rev==1
                && dut.u_core.u_top.cfg_carrier_hz==40000
                && dut.u_core.u_top.cfg_phase_steps==64,
                "rejected serial timing leaves full accepted config unchanged");
        end
    endtask
    task press_key;
        input play_key;
        begin
            @(negedge clk);if(play_key) key1_n=0;else key0_n=0;
            repeat(60000) @(negedge clk);
            if(play_key) key1_n=1;else key0_n=1;
            repeat(60000) @(negedge clk);
        end
    endtask

    integer frames_before,latch_before;
    string boot_before;
    initial begin
        repeat(5) @(negedge clk);
        check(oe_n && !srclr_n,"external reset disables OE and clears shift registers");
        reset_n=1;
        repeat(50050) @(negedge clk);
        check(oe_n && srclr_n && physical_latches==0,"POR finishes with zero latches and OE disabled");
        send_command(8,"HAP3 CMD 1 HELLO");wait_frames(8,2);
        check(field_value(ack8,"hw_rows=")=="8" && field_value(ack8,"hw_cols=")=="8"
            && field_value(ack8,"hw_pitch_um=")=="10300","shift board declares actual 8x8 10.3 mm geometry");
        boot_before=field_value(ack8,"boot=");
        check(field_value(ack8,"caps=")=="CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,SCAN_PATHS,CALIBRATION",
            "8x8 advertises complete 64-channel calibration");
        check(field_value(state8,"channel_mask=")=="18446744073709551615" && field_value(state8,"phase_offsets=")!=""
            && dut.u_core.u_top.calib_channel_mask==64'hffffffffffffffff,
            "8x8 reports the full unsigned 64-channel mask");
        frames_before=frames8;send_command(8,"HAP3 CMD 2 MODE value=REMOTE");wait_frames(8,frames_before+2);
        check(field_value(state8,"mode=")=="REMOTE" && field_value(state8,"output=")=="0",
            "MODE REMOTE remains idle");
        frames_before=frames8;send_config(8,3,40000,64);wait_frames(8,frames_before+2);
        check(field_value(ack8,"applied=")=="1" && field_value(state8,"rev=")=="1"
            && field_value(state8,"phase_steps=")=="64", "40 kHz / 64 phase slots accepted");
        bad_serial_config(4,40000,128);
        frames_before=frames8;
        send_command(8,"HAP3 CMD 21 CALIBRATION action=trial geometry_id=f0e8e8e16397381dfcf31ea6 mask=1 offsets=0");
        wait_frames(8,frames_before+1);
        check(field_value(last8,"code=")=="BAD_CALIBRATION" && dut.u_core.u_top.cmd_rev==1,
            "8x8 rejects an incomplete 64-channel correction table atomically");
        bad_serial_config(5,40000,256);
        bad_serial_config(6,80000,64);
        frames_before=frames8;send_command(8,"HAP3 CMD 7 SNAP");wait_frames(8,frames_before+2);
        check(field_value(state8,"rev=")=="1" && field_value(state8,"carrier_hz=")=="40000"
            && field_value(state8,"phase_steps=")=="64","STATE confirms rejected settings never became active");

        // Rejected CONFIG fields remain in shadow registers. LOCAL NEXT must
        // inherit the accepted config instead of reviving those rejected limits.
        frames_before=frames8;send_command(8,"HAP3 CMD 13 MODE value=LOCAL");wait_frames(8,frames_before+2);
        press_key(0);wait(dut.u_core.u_top.cmd_rev==2);
        check(dut.u_core.u_top.cfg_carrier_hz==40000 && dut.u_core.u_top.cfg_phase_steps==64,
            "LOCAL NEXT cannot revive the rejected carrier or phase count");
        frames_before=frames8;send_command(8,"HAP3 CMD 14 SNAP");wait_frames(8,frames_before+2);
        check(field_value(state8,"mode=")=="LOCAL" && field_value(state8,"rev=")=="2"
            && field_value(state8,"carrier_hz=")=="40000" && field_value(state8,"phase_steps=")=="64",
            "local next is reflected by a valid serial-safe STATE");
        frames_before=frames8;send_command(8,"HAP3 CMD 15 MODE value=REMOTE");wait_frames(8,frames_before+2);
        frames_before=frames8;send_config(8,16,40000,64);wait_frames(8,frames_before+2);

        send_command(4,"HAP3 CMD 1 HELLO");wait_frames(4,2);
        frames_before=frames4;send_command(4,"HAP3 CMD 2 MODE value=REMOTE");wait_frames(4,frames_before+2);
        frames_before=frames4;send_config(4,3,80000,256);wait_frames(4,frames_before+2);
        check(field_value(ack4,"applied=")=="1" && field_value(state4,"rev=")=="1"
            && field_value(state4,"carrier_hz=")=="80000" && field_value(state4,"phase_steps=")=="256",
            "direct 4x4 retains 80 kHz / 256-slot capability");

        frames_before=frames8;send_command(8,"HAP3 CMD 8 START");wait_frames(8,frames_before+2);
        check(field_value(state8,"state=")=="RUNNING" && field_value(state8,"output=")=="1"
            && !oe_n,"START enables real serial output and reports it");
        latch_before=latches;measure_wave=1;
        repeat(25000) @(negedge clk);
        measure_wave=0;
        check(latches-latch_before>1000 && waveform_rises>=15,
            "595 pins continuously serialize and reproduce carrier pulses");
        force dut.u_shift.fault=1'b1;#1;
        check(oe_n,"serializer fault immediately disables physical output");
        frames_before=frames8;send_command(8,"HAP3 CMD 12 SNAP");wait_frames(8,frames_before+2);
        check(field_value(state8,"output=")=="0"
            && field_value(state8,"reason=")=="OUTPUT_FAULT",
            "STATE reports physical output fault instead of claiming active output");
        release dut.u_shift.fault;
        frames_before=frames8;send_command(8,"HAP3 CMD 9 STOP");wait_frames(8,frames_before+2);
        check(oe_n && field_value(state8,"state=")=="IDLE" && field_value(state8,"output=")=="0",
            "STOP disables OE and is confirmed by STATE");
        repeat(25) @(negedge clk);
        check(physical_latches==0,"STOP also replaces the output latch with zeros");

        frames_before=frames8;send_command(8,"HAP3 CMD 10 START");wait_frames(8,frames_before+2);
        check(!oe_n,"restart accepts a fresh complete frame");
        @(negedge clk);reset_n=0;#1;
        check(oe_n && !srclr_n,"reset during output immediately disables OE");
        repeat(5) @(negedge clk);reset_n=1;
        repeat(50050) @(negedge clk);
        check(oe_n && physical_latches==0,"reset cannot automatically restart output");
        frames_before=frames8;send_command(8,"HAP3 CMD 11 HELLO");wait_frames(8,frames_before+2);
        check(field_value(state8,"state=")=="IDLE" && field_value(state8,"output=")=="0"
            && field_value(ack8,"boot=")!=boot_before,"fresh handshake confirms reset and disabled output");

        // Fresh boot still has no trajectory. A rejected CONFIG followed by the
        // first local PLAY must prepare from accepted defaults, not bad shadow.
        frames_before=frames8;send_config(8,17,80000,256);wait_frames(8,frames_before+1);
        check(field_value(last8,"code=")=="BAD_CONFIG" && dut.u_core.u_top.cmd_rev==0,
            "fresh boot rejects invalid serial config before any local preparation");
        frames_before=frames8;send_command(8,"HAP3 CMD 18 MODE value=LOCAL");wait_frames(8,frames_before+2);
        check(!dut.u_core.u_top.traj_ready,"first local PLAY starts without a precompiled trajectory");
        press_key(1);wait(dut.u_core.u_top.cmd_rev==1);
        check(dut.u_core.u_top.cfg_carrier_hz==40000 && dut.u_core.u_top.cfg_phase_steps==64
            && dut.u_core.u_top.cfg_z_um==150000 && dut.u_core.u_top.cfg_level==30
            && dut.u_core.u_top.cfg_mod_hz==200
            && dut.u_core.u_top.cmd_run==1 && !dut.u_shift.fault,
            "first local PLAY prepares and runs accepted serial-safe defaults");
        frames_before=frames8;send_command(8,"HAP3 CMD 19 SNAP");wait_frames(8,frames_before+2);
        check(field_value(state8,"mode=")=="LOCAL" && field_value(state8,"state=")=="RUNNING"
            && field_value(state8,"carrier_hz=")=="40000" && field_value(state8,"phase_steps=")=="64",
            "first local PLAY reports the accepted physical timing");
        frames_before=frames8;send_command(8,"HAP3 CMD 20 STOP");wait_frames(8,frames_before+2);
        check(oe_n && field_value(state8,"output=")=="0","final local STOP disables physical output");
        $display("8x8 serial board regression passed: checks=%0d frames8=%0d frames4=%0d latches=%0d",
            checks,frames8,frames4,latches);
        $finish;
    end
    initial begin #3_000_000_000; $fatal(1,"board shift regression timeout");end
endmodule
