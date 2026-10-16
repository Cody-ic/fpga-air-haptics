`timescale 1ns/1ps
module tb_cmd_stop;
    reg clk=0;
    always #10 clk=~clk;
    reg rst_n=0, frame_ok=0, frame_bad=0;
    reg [3:0] verb=0;
    reg play=0, stop=0, shape_done=0, plan_done=0;
    reg field_valid=0;
    reg [18:0] seen_mask=0;
    wire [1:0] mode, state, reply_kind;
    wire reply_valid, walk_start, walk_stop;
    integer checks=0;
    reg byte_valid=0;
    reg [7:0] byte_data=0;
    wire line_ready, line_dropped;
    wire [13:0] line_len;
    hap2_line_rx line_dut (.clk(clk), .rst_n(rst_n), .rx_valid(byte_valid),
        .rx_data(byte_data), .line_ready(line_ready), .line_dropped(line_dropped),
        .line_len(line_len), .rd_addr(13'd0));
    task send_line;
        input integer count;
        input dropped;
        integer n;
        begin
            byte_valid=1; byte_data="a";
            for(n=0;n<count;n=n+1) @(negedge clk);
            byte_data=10; @(negedge clk); byte_valid=0;
            check(line_dropped==dropped && line_ready==!dropped,"line length acceptance and rejection");
            if(!dropped) check(line_len==count,"long line length preserved without truncation");
            @(negedge clk);
        end
    endtask
    hap2_cmd dut (
        .clk(clk), .rst_n(rst_n), .frame_ok(frame_ok), .frame_bad(frame_bad),
        .header_ok(1'b1), .kind(2'd0), .seq(16'd42), .verb(verb),
        .verb_unknown(1'b0), .field_valid(field_valid), .field_id(5'd16),
        .field_value(32'd0), .field_enum(3'd0), .field_off(14'd0),
        .field_size(14'd0), .seen_mask(seen_mask), .array_rows(32'd4),
        .array_cols(32'd4), .array_pitch_um(32'd11000), .heartbeat_lost(1'b0),
        .traj_shape_done(shape_done), .traj_parse_ok(1'b0), .traj_parse_bad(1'b0),
        .traj_none(1'b0), .traj_plan_done(plan_done), .traj_plan_fault(1'b0),
        .bb_vld(1'b0), .bb_xmin(21'sd0), .bb_xmax(21'sd0),
        .bb_ymin(21'sd0), .bb_ymax(21'sd0), .loc_next(1'b0),
        .loc_play(play), .loc_stop(stop), .loc_shape(3'd0),
        .mode(mode), .run_state(state), .reply_valid(reply_valid),
        .reply_kind(reply_kind), .walk_start(walk_start), .walk_stop(walk_stop)
    );
    task check;
        input ok;
        input [8*100-1:0] label;
        begin checks=checks+1; if (!ok) $fatal(1,"%0s",label); end
    endtask
    task reset;
        begin rst_n=0; frame_ok=0; frame_bad=0; play=0; stop=0;
            field_valid=0; seen_mask=0;
            shape_done=0; plan_done=0; repeat(3) @(negedge clk);
            rst_n=1; @(negedge clk); end
    endtask
    task connected_local;
        begin hello;
            field_valid=1; seen_mask=19'h10000;
            @(negedge clk); field_valid=0; verb=3; frame_ok=1;
            @(negedge clk); frame_ok=0; seen_mask=0;
            check(mode==0,"MODE selects local after handshake");
        end
    endtask
    task hello;
        begin verb=0; frame_ok=1; @(negedge clk); frame_ok=0;
            @(negedge clk); end
    endtask
    task local_prepare;
        begin play=1; @(negedge clk); play=0;
            check(mode==0 && dut.pending==3,"first play starts local preparation"); end
    endtask
    task finish_shape;
        begin shape_done=1; @(negedge clk); shape_done=0;
            check(dut.pending==2,"shape transitions to plan"); end
    endtask
    integer stage;
    initial begin
        reset; hello;
        play=1; @(negedge clk); play=0;
        check(mode==1 && dut.pending==0,"connected remote session ignores local play");
        for(stage=0;stage<3;stage=stage+1) begin
            reset; connected_local; local_prepare;
            if(stage!=0) finish_shape;
            verb=6; frame_ok=1;
            if(stage==2) plan_done=1;
            @(negedge clk); frame_ok=0; plan_done=0;
            check(reply_valid && reply_kind==0,"STOP acknowledged while compiler busy");
            check(state==0 && !walk_start && walk_stop,"STOP overrides same-cycle auto play");
            if(stage==0) finish_shape;
            if(stage!=2) begin plan_done=1; @(negedge clk); plan_done=0; end
            repeat(3) @(negedge clk);
            check(state==0 && !dut.local_auto_run,"late completion cannot restart after STOP");
        end
        reset; local_prepare; finish_shape;
        stop=1; plan_done=1; @(negedge clk); stop=0; plan_done=0;
        check(state==0 && !walk_start && walk_stop,"local STOP wins over simultaneous plan completion");
        reset; connected_local; local_prepare;
        verb=6; frame_ok=1; frame_bad=1; @(negedge clk);
        frame_ok=0; frame_bad=0;
        check(dut.local_auto_run,"invalid STOP does not change playback request");
        finish_shape; plan_done=1; @(negedge clk); plan_done=0;
        check(state==1,"valid pending job survives invalid STOP");
        reset; local_prepare;
        verb=6; frame_ok=1; @(negedge clk); frame_ok=0;
        check(dut.local_auto_run,"unhandshaken STOP is rejected");
        reset;
        send_line(4370,0);
        send_line(8191,0);
        send_line(8192,1);
        send_line(20,0);
        $display("STOP regression passed: %0d",checks); $finish;
    end
endmodule
