`timescale 1ns/1ps
// Unit-level collision tests. UART framing is covered by tb_static_phase.
module tb_stop_commit;
    reg clk=0,rst_n=0,frame_ok=0,plan_done=0,plan_fault=0;
    reg [15:0] seq=8;
    wire reply,walk_start,walk_stop;
    wire [1:0] kind,run_state;
    wire [3:0] verb,code;
    wire [15:0] reply_seq;
    always #10 clk=~clk;
    hap2_cmd dut(.clk(clk),.rst_n(rst_n),.frame_ok(frame_ok),.frame_bad(1'b0),
        .header_ok(1'b1),.kind(2'd0),.seq(seq),.verb(4'd6),.verb_unknown(1'b0),
        .field_valid(1'b0),.seen_mask(20'd0),.phase_table(128'd0),
        .array_rows(32'd4),.array_cols(32'd4),.array_pitch_um(32'd11000),
        .heartbeat_lost(1'b0),.traj_shape_done(1'b0),.traj_parse_ok(1'b0),
        .traj_parse_bad(1'b0),.traj_none(1'b0),.traj_plan_done(plan_done),.traj_plan_fault(plan_fault),
        .bb_vld(1'b0),.loc_play(1'b0),.loc_stop(1'b0),.loc_next(1'b0),.loc_shape(3'd0),
        .reply_valid(reply),.reply_kind(kind),.reply_verb(verb),.reply_seq(reply_seq),.reply_code(code),
        .run_state(run_state),.walk_start(walk_start),.walk_stop(walk_stop));
    task stage;
        input local_case;
        begin
            @(negedge clk);
            // Deposit a compiler-in-flight state to target the exact collision edge.
            dut.connected=1; dut.pending=2; dut.local_cfg=local_case;
            dut.local_auto_run=local_case; dut.lat_seq=7; dut.lat_verb=2;
            dut.mode=local_case?0:1; dut.run_state=0;
            frame_ok=1;
        end
    endtask
    task stopped;
        begin
            @(negedge clk);
            if(!reply || kind!=0 || verb!=6 || reply_seq!=8 || run_state!=0 || walk_start || !walk_stop)
                $fatal(1,"STOP lost to compiler commit");
            frame_ok=0;plan_done=0;plan_fault=0;
        end
    endtask
    initial begin
        repeat(3) @(negedge clk);rst_n=1;
        stage(1);plan_done=1;stopped;
        @(negedge clk);if(run_state!=0 || walk_start) $fatal(1,"local auto-start survived STOP");
        stage(0);plan_done=1;stopped;
        @(negedge clk);
        if(!reply || kind!=0 || verb!=2 || reply_seq!=7) $fatal(1,"CONFIG ACK lost on STOP edge");
        stage(0);plan_fault=1;stopped;
        @(negedge clk);
        if(!reply || kind!=1 || verb!=2 || reply_seq!=7 || code!=3) $fatal(1,"CONFIG error lost on STOP edge");
        $display("PASS STOP/commit collision, local auto-play revoked, both command replies retained");
        $finish;
    end
endmodule
