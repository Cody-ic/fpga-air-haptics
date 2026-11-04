`timescale 1ns/1ps
// Run with tools in fpga/board/check_restore.py; UART is accelerated for simulation.
module tb_button_focus #(
    parameter integer FOCUS_Z_UM=100000,
    parameter integer LINE_TEST=0,
    parameter integer CIRCLE_TEST=0,
    parameter integer LINE_RADIUS_UM=20000,
    parameter [511:0] OFFSETS=512'd0,
    parameter [511:0] EXPECTED=512'd0,
    parameter [511:0] EXPECTED100=512'd0
);
    localparam integer MOTION_TEST=LINE_TEST || CIRCLE_TEST;
    localparam integer TEST_SHAPE=CIRCLE_TEST ? 3 : LINE_TEST ? 1 : 0;
    localparam integer TEST_MS=MOTION_TEST ? 6 : 2;
    localparam integer TEST_LEVEL=MOTION_TEST ? 100 : 1;
    // Accelerate only the line period and deadline; keep the real clock and
    // complete trajectory/phase/output engines. 5 ms per loop fits in 6 ms.
    localparam integer TEST_REPEAT=MOTION_TEST ? 200000 : 500;
    reg clk=0,rst_n=0,rx=1;
    reg play=0,stop=0;
    // Use the real 50 MHz clock: level 1 is only 0.5% duty and cannot be
    // represented reliably at 5 MHz. Shorten timers and accelerate UART only.
    // Debounce is independently covered by tb_local.
    always #10 clk=~clk;
    wire tx,on;
    wire [63:0] waves;
    hap2_top #(.CAL_ENABLE(1),.CAL_RESTORE_ENABLE(1),.CAL_RESTORE_OFFSETS(OFFSETS),
        .HW_ROWS(8),.HW_COLS(8),.HW_PITCH_UM(10300),.PH_PIPE(8),
        .BUTTON_FOCUS_ENABLE(1),.BUTTON_FOCUS_Z_UM(FOCUS_Z_UM),.BUTTON_FOCUS_MS(TEST_MS),.KEY_DEB_MS(0),
        .BUTTON_FOCUS_LEVEL(TEST_LEVEL),.BUTTON_FOCUS_SHAPE(TEST_SHAPE),
        .BUTTON_FOCUS_RADIUS_UM(MOTION_TEST ? LINE_RADIUS_UM : 0),.BUTTON_FOCUS_REPEAT_MILLIHZ(TEST_REPEAT),
        .CLK_HZ(50000000),.BAUD(10000000),.STATE_MS(0),.HB_MS(3)) dut (
        .clk(clk),.rst_n(rst_n),.uart_rx_pin(rx),.uart_tx_pin(tx),.boot_id(32'd99),
        .key_next(stop),.key_play(play),.key_stop(1'b0),
        .mode_local(),.focus_x(),.focus_y(),.scan_on(),.stroke_index(),
        .output_on(on),.beat_pulse(),.walk_running(),.array_pos(waves),.array_neg());
    wire byte_valid;
    wire [7:0] byte_in;
    uart_rx #(.CLK_HZ(50000000),.BAUD(10000000)) monitor (
        .clk(clk),.rst_n(rst_n),.rx_line(tx),.rx_data(byte_in),.rx_valid(byte_valid),.rx_error());
    integer lines=0,fd,guard,i,j,k,b,offset=0,n;
    reg [7:0] commands[0:8191];
    reg [31:0] lengths[0:8];
    reg [63:0] seen;
    integer motion_min=100000,motion_max=-100000,last_x=0,motion_samples=0;
    reg track_motion=0,moved_positive=0,moved_negative=0;
    always @(negedge clk) if(track_motion && on && dut.cfg_shape==1) begin
        if($signed(dut.ph_pub_fx)<-2*LINE_RADIUS_UM || $signed(dut.ph_pub_fx)>2*LINE_RADIUS_UM || dut.ph_pub_fy!=0)
            $fatal(1,"line trajectory escaped bounds");
        if($signed(dut.ph_pub_fx)<motion_min) motion_min=$signed(dut.ph_pub_fx);
        if($signed(dut.ph_pub_fx)>motion_max) motion_max=$signed(dut.ph_pub_fx);
        if(motion_samples>0 && $signed(dut.ph_pub_fx)>last_x) moved_positive=1;
        if(motion_samples>0 && $signed(dut.ph_pub_fx)<last_x) moved_negative=1;
        last_x=$signed(dut.ph_pub_fx);motion_samples=motion_samples+1;
    end
    reg [3:0] circle_quadrants=0;
    reg signed [63:0] circle_x,circle_y,circle_r2;
    // Digital enable precedes the first phase table. Output stage remains
    // muted until phase_valid; inspect the published circle only then.
    always @(negedge clk) if(track_motion && on && dut.u_out.phase_valid && dut.ph_pub_valid && dut.cfg_shape==3) begin
        circle_x=$signed(dut.ph_pub_fx);
        circle_y=$signed(dut.ph_pub_fy);
        circle_r2=circle_x*circle_x+circle_y*circle_y;
        // Published positions use half-micrometres. Permit 1% radial error
        // for the polygon interpolation, while requiring every quadrant.
        if(circle_r2 < (64'sd4*LINE_RADIUS_UM*LINE_RADIUS_UM*98/100) ||
           circle_r2 > (64'sd4*LINE_RADIUS_UM*LINE_RADIUS_UM*102/100))
            $fatal(1,"circle radius escaped bounds x=%0d y=%0d r2=%0d",circle_x,circle_y,circle_r2);
        if(circle_x>0 && circle_y>0) circle_quadrants[0]=1;
        if(circle_x<0 && circle_y>0) circle_quadrants[1]=1;
        if(circle_x<0 && circle_y<0) circle_quadrants[2]=1;
        if(circle_x>0 && circle_y<0) circle_quadrants[3]=1;
    end
    always @(posedge clk) if(byte_valid) begin
        $fwrite(fd,"%c",byte_in);
        $fflush(fd);
        if(byte_in==10) lines<=lines+1;
    end
    task send;
        input integer index,reply_lines;
        integer start_lines,idx;
        begin
            start_lines=lines;offset=0;
            for(idx=0;idx<index;idx=idx+1) offset=offset+lengths[idx];
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
    task press_play;
        begin play=1;repeat(6000) @(negedge clk);play=0;repeat(6000) @(negedge clk);end
    endtask
    task check_center100;
        integer ready_cycles;
        begin
            // A circle compiles 128 segments, unlike the two-segment line.
            // Allow bounded preparation; do not change the DUT deadline.
            ready_cycles=0;
            while(CIRCLE_TEST && !on && dut.u_cmd.button_busy && ready_cycles<100000) begin
                @(negedge clk);ready_cycles=ready_cycles+1;
            end
            if(!on || dut.cfg_z_um!=FOCUS_Z_UM || dut.cfg_cx_um!=0 || dut.cfg_cy_um!=0 ||
               dut.cfg_shape!=TEST_SHAPE || dut.cfg_level!=TEST_LEVEL || dut.cfg_mod_hz!=0 ||
               dut.cfg_radius_um!=(MOTION_TEST ? LINE_RADIUS_UM : 0) || dut.cfg_repeat_millihz!=TEST_REPEAT)
                $fatal(1,"button configuration/output mismatch on=%0d z=%0d shape=%0d level=%0d radius=%0d repeat=%0d busy=%0d pending=%0d",on,dut.cfg_z_um,dut.cfg_shape,dut.cfg_level,dut.cfg_radius_um,dut.cfg_repeat_millihz,dut.u_cmd.button_busy,dut.u_cmd.pending);
            if(!MOTION_TEST) for(i=0;i<64;i=i+1)
                if(dut.u_out.code[i]!==EXPECTED100[8*i +: 8])
                    $fatal(1,"100 mm phase mismatch ch=%0d actual=%0d expected=%0d",i,dut.u_out.code[i],EXPECTED100[8*i +: 8]);
        end
    endtask
    task check_off;
        begin
            if(on || waves!=0 || dut.cmd_run!=0 || dut.u_cmd.button_busy)
                $fatal(1,"expected stopped button test");
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
        // No HELLO is required for a physical button. A held key fires once.
        track_motion=MOTION_TEST;
        play=1;repeat(15000) @(negedge clk);
        check_center100;
        seen=0;repeat(10000) begin @(negedge clk);seen=seen|waves;end
        if(seen!==64'hffffffffffffffff) $fatal(1,"button must drive all 64 channels");
        if(MOTION_TEST) begin
            repeat(200000) @(negedge clk);
        end
        repeat(TEST_MS*50000) @(negedge clk);check_off;
        track_motion=0;
        if(LINE_TEST && (!moved_positive || !moved_negative || motion_min>-(LINE_RADIUS_UM*19/10) || motion_max<(LINE_RADIUS_UM*19/10)))
            $fatal(1,"line must reach both ends and reverse without host heartbeat");
        if(CIRCLE_TEST && circle_quadrants!=4'b1111)
            $fatal(1,"circle must visit all four quadrants without host heartbeat");
        if(dut.cmd_mode!=1) $fatal(1,"button timeout must restore REMOTE");
        repeat(TEST_MS*50000+10000) @(negedge clk);check_off;
        play=0;repeat(6000) @(negedge clk);
        // Second press stops; separate stop key also stops.
        press_play;check_center100;
        press_play;check_off;
        press_play;check_center100;
        stop=1;repeat(6000) @(negedge clk);check_off;
        stop=0;repeat(6000) @(negedge clk);
        // Serial STOP works while the button is emitting.
        send(0,2);
        press_play;check_center100;
        if(MOTION_TEST) send(8,2); // Snapshot requires HELLO; the button does not.
        send(5,2);check_off;
        // Hold completion to exercise STOP while preparing, then complete on
        // the cancellation cycle: there must be no late automatic restart.
        force dut.u_cmd.traj_plan_done=0;
        press_play;
        if(on || !dut.u_cmd.button_busy) $fatal(1,"expected pending button preparation");
        send(5,2);check_off;
        force dut.u_cmd.traj_plan_done=1;
        @(negedge clk);release dut.u_cmd.traj_plan_done;
        repeat(15000) @(negedge clk);check_off;
        // Physical stop coincident with successful commit has final priority.
        force dut.u_cmd.traj_plan_done=0;
        press_play;
        force dut.u_cmd.traj_plan_done=1;
        force dut.u_cmd.loc_next=1;
        @(negedge clk);release dut.u_cmd.traj_plan_done;release dut.u_cmd.loc_next;
        repeat(15000) @(negedge clk);check_off;
        // Reset while running keeps restored phase corrections but silences.
        press_play;check_center100;
        rst_n=0;repeat(5) @(negedge clk);rst_n=1;
        repeat(15000) @(negedge clk);check_off;
        if(dut.cal_offsets!==OFFSETS) $fatal(1,"reset lost calibration offsets");
        $display("PASS button: z=%0d level=%0d line=%0d; no-handshake start, held-key one-shot, timeout, repeat-stop, stop key, serial STOP, pending cancellation, reset",FOCUS_Z_UM,TEST_LEVEL,LINE_TEST);
        if(LINE_TEST) $display("PASS moving line: both directions, full span, local operation beyond host heartbeat; phase snapshot checked by Python");
        if(CIRCLE_TEST) $display("PASS moving circle: radius, all four quadrants, local operation beyond host heartbeat; phase snapshot checked by Python");
        $fclose(fd);
        $display("PASS restored calibration: 64 drive/snapshot phases, boot/reset mute, full-level output, STOP, config guard, watchdog");
        $finish;
    end
    initial begin #600000000;$fatal(1,"simulation deadline");end
endmodule
