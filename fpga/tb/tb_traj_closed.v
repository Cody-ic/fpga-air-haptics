`timescale 1ns/1ps

// Exact endpoint regression: real planner and walker, several complete laps.
// TICK_CYC=8 accelerates simulation; beat counts and Q16 steps are unchanged.
module tb_traj_closed;
    localparam integer PT_BITS = 21;
    reg clk = 1'b0;
    always #10 clk = ~clk;
    reg rst_n = 1'b0;
    reg plan_start = 1'b0, walk_start = 1'b0, walk_stop = 1'b0, walk_hold = 1'b0;
    reg tbl_sel = 1'b0;
    reg [8:0] npt, pub_moves = 0;
    reg [5:0] nst;
    reg [31:0] repeat_millihz, blank_us;
    reg signed [PT_BITS-1:0] ptx [0:255], pty [0:255];
    reg [8:0] starts [0:31], lengths [0:31];
    reg signed [PT_BITS-1:0] pub_x0 = 0, pub_y0 = 0;
    wire [7:0] pt_addr;
    wire [4:0] st_addr;
    reg signed [PT_BITS-1:0] pt_x, pt_y;
    wire done, fault, running, beat, scan;
    wire [31:0] laps;
    wire [8:0] moves;
    wire signed [PT_BITS-1:0] x0, y0, fx, fy;
    wire [5:0] stroke;
    always @(posedge clk) begin
        pt_x <= ptx[pt_addr];
        pt_y <= pty[pt_addr];
    end

    hap2_traj #(.TICK_CYC(8)) dut (
        .clk(clk), .rst_n(rst_n), .plan_start(plan_start), .tbl_sel(tbl_sel),
        .walk_start(walk_start), .walk_stop(walk_stop), .walk_hold(walk_hold),
        .walk_x0(pub_x0), .walk_y0(pub_y0), .walk_moves(pub_moves),
        .point_count(npt), .stroke_count(nst), .pt_addr(pt_addr), .pt_x(pt_x), .pt_y(pt_y),
        .st_addr(st_addr), .st_start(starts[st_addr]), .st_len(lengths[st_addr]),
        .cfg_repeat_millihz(repeat_millihz), .cfg_blank_us(blank_us),
        .plan_done(done), .plan_fault(fault), .laps_beats(laps), .move_count(moves),
        .traj_x0(x0), .traj_y0(y0), .focus_x(fx), .focus_y(fy),
        .scan_on(scan), .stroke_index(stroke), .beat_pulse(beat), .walk_running(running)
    );

    integer checks = 0, expected_moves, expected_laps;
    integer b [0:511], ex [0:511], ey [0:511], es [0:511];
    reg escan [0:511];
    task check;
        input ok;
        input [8*100-1:0] label;
        begin
            checks = checks + 1;
            if (ok !== 1'b1) $fatal(1, "%0s", label);
        end
    endtask

    task compile_plan;
        input accept;
        integer guard;
        begin
            @(negedge clk); plan_start = 1;
            @(negedge clk); plan_start = 0;
            guard = 0;
            while (!done && !fault && guard < 500000) begin
                @(negedge clk); guard = guard + 1;
            end
            check(guard < 500000, "planner completed");
            check(done === accept && fault === !accept, "planner acceptance matches the requested case");
            if (accept) begin
                pub_moves = moves;
                pub_x0 = x0; pub_y0 = y0;
                tbl_sel = ~tbl_sel;
            end
            repeat (3) @(negedge clk);
        end
    endtask

    // Expected destinations are taken directly from the input geometry, not
    // reconstructed from the DUT's rounded steps or endpoint table.
    task check_table;
        integer s, j, k, next_s, sum, bank;
        begin
            expected_moves = 0;
            for (s = 0; s < nst; s = s + 1) begin
                if (lengths[s] == 1) begin
                    ex[expected_moves] = ptx[starts[s]];
                    ey[expected_moves] = pty[starts[s]];
                    es[expected_moves] = s;
                    escan[expected_moves] = 1;
                    expected_moves = expected_moves + 1;
                end else begin
                    for (j = 1; j < lengths[s]; j = j + 1) begin
                        ex[expected_moves] = ptx[starts[s]+j];
                        ey[expected_moves] = pty[starts[s]+j];
                        es[expected_moves] = s;
                        escan[expected_moves] = 1;
                        expected_moves = expected_moves + 1;
                    end
                end
                next_s = (s+1 == nst) ? 0 : s+1;
                ex[expected_moves] = ptx[starts[next_s]];
                ey[expected_moves] = pty[starts[next_s]];
                es[expected_moves] = s;
                escan[expected_moves] = 0;
                expected_moves = expected_moves + 1;
            end
            check(pub_moves == expected_moves, "point/singleton/blank rows retain their count");
            bank = tbl_sel ? 512 : 0;
            sum = 0;
            for (k = 0; k < expected_moves; k = k + 1) begin
                b[k] = dut.u_plan.beats_tab[bank+k];
                sum = sum + b[k];
                check(b[k] > 0, "no zero-beat row is published");
                check($signed(dut.u_plan.endx_tab[bank+k]) == ex[k], "published endpoint x is the original geometric endpoint");
                check($signed(dut.u_plan.endy_tab[bank+k]) == ey[k], "published endpoint y is the original geometric endpoint");
                check(dut.u_plan.scan_tab[bank+k] == escan[k], "blank rows have output off");
                check(dut.u_plan.stroke_tab[bank+k] == es[k], "stroke identity is unchanged");
                if (!escan[k]) check(b[k] == blank_us/10, "blank duration is unchanged");
            end
            expected_laps = 100000000 / repeat_millihz;
            check(sum == expected_laps && laps == expected_laps, "all row durations add up to one exact lap");
        end
    endtask

    task begin_walk;
        integer guard;
        begin
            walk_start = 1; @(negedge clk); walk_start = 0;
            guard = 0;
            while (!running && guard < 20) begin @(negedge clk); guard = guard+1; end
            check(guard < 20, "walker starts");
            check(fx == pub_x0 && fy == pub_y0, "START returns to the published exact start");
        end
    endtask

    task stop_walk;
        begin
            walk_stop = 1; @(negedge clk); walk_stop = 0;
            check(!running && !scan && !beat, "STOP disables the walker and scanning");
            check(fx == pub_x0 && fy == pub_y0, "STOP returns to the published exact start");
        end
    endtask

    task walk_laps;
        input integer count;
        input pause_once;
        integer pulses, m, left, next_m, i;
        reg signed [36:0] saved_x, saved_y;
        reg [15:0] saved_tick;
        reg [23:0] saved_left;
        begin
            begin_walk;
            pulses = 0; m = 0; left = b[0];
            while (pulses < count*expected_laps) begin
                @(negedge clk);
                if (beat) begin
                    pulses = pulses + 1;
                    if (left == 1) begin
                        check(fx == ex[m] && fy == ey[m], "every segment ends at the exact signed coordinate");
                        check(dut.u_walk.acc_x[15:0] == 0 && dut.u_walk.acc_y[15:0] == 0, "endpoint correction removes fractional residue");
                        next_m = (m+1 == expected_moves) ? 0 : m+1;
                        check(scan == escan[next_m] && stroke == es[next_m], "scan gate and stroke advance without an extra beat");
                        m = next_m; left = b[m];
                    end else begin
                        check(scan == escan[m] && stroke == es[m], "scan gate and stroke remain stable inside a row");
                        left = left-1;
                    end
                    if (pulses % expected_laps == 0)
                        check(fx == pub_x0 && fy == pub_y0, "complete laps do not accumulate drift");
                    if (pause_once && pulses == 17) begin
                        walk_hold = 1;
                        saved_x = dut.u_walk.acc_x; saved_y = dut.u_walk.acc_y;
                        saved_tick = dut.u_walk.tick; saved_left = dut.u_walk.beats_left;
                        repeat (50) begin
                            @(negedge clk);
                            check(!beat && running, "pause emits no beats and retains the session");
                            check(dut.u_walk.acc_x == saved_x && dut.u_walk.acc_y == saved_y &&
                                  dut.u_walk.tick == saved_tick && dut.u_walk.beats_left == saved_left,
                                  "pause freezes both coordinates and timing");
                        end
                        walk_hold = 0;
                    end
                end
            end
            stop_walk;
        end
    endtask

    integer s, j;
    initial begin
        repeat (5) @(negedge clk); rst_n = 1;
        npt = 4; nst = 1; starts[0] = 0; lengths[0] = 4;
        repeat_millihz = 500; blank_us = 2000;
        ptx[0]=0; pty[0]=0; ptx[1]=20000; pty[1]=0;
        ptx[2]=0; pty[2]=10000; ptx[3]=0; pty[3]=0;
        compile_plan(1); check_table; walk_laps(3,1);

        // Negative coordinates, a multi-segment open stroke, a single-point
        // stroke and both nonzero-distance blank transfers.
        npt=4; nst=2; starts[0]=0; lengths[0]=3; starts[1]=3; lengths[1]=1;
        repeat_millihz=20000; blank_us=3333;
        ptx[0]=-40000; pty[0]=-30000; ptx[1]=-12345; pty[1]=-21111;
        ptx[2]=-9000; pty[2]=3333; ptx[3]=15000; pty[3]=-7000;
        compile_plan(1); check_table;
        begin_walk; repeat(37*8) @(negedge clk); stop_walk;
        walk_laps(4,1);

        // Full 256-point / 32-stroke capacity, each small loop closes and
        // blank rows keep their original timing between separate strokes.
        npt=256; nst=32; repeat_millihz=1000; blank_us=100;
        for(s=0;s<32;s=s+1) begin
            starts[s]=s*8; lengths[s]=8;
            for(j=0;j<8;j=j+1) begin
                ptx[s*8+j]=-100000+s*5000;
                pty[s*8+j]=-10000+s*500;
            end
            ptx[s*8+1]=ptx[s*8]+300;
            ptx[s*8+2]=ptx[s*8]+700; pty[s*8+2]=pty[s*8]+200;
            ptx[s*8+3]=ptx[s*8]+1000; pty[s*8+3]=pty[s*8]+800;
            ptx[s*8+4]=ptx[s*8]+400; pty[s*8+4]=pty[s*8]+1200;
            ptx[s*8+5]=ptx[s*8]-100; pty[s*8+5]=pty[s*8]+800;
            ptx[s*8+6]=ptx[s*8]-200; pty[s*8+6]=pty[s*8]+300;
        end
        compile_plan(1); check_table; walk_laps(2,0);

        // The inactive bank is partly rewritten before a zero-length segment
        // fails. The previously published endpoints must still be usable.
        npt=3; nst=1; starts[0]=0; lengths[0]=3;
        ptx[0]=0; pty[0]=0; ptx[1]=20000; pty[1]=10000;
        ptx[2]=20000; pty[2]=10000;
        compile_plan(0);
        check(pub_moves == 256 && pub_x0 == -100000 && pub_y0 == -10000,
              "rejected reconfiguration preserves published trajectory metadata");
        walk_laps(2,0);
        $display("Closed trajectory checks passed: %0d",checks);
        $finish;
    end
    initial begin
        #250000000;
        $fatal(1,"closed trajectory test timed out");
    end
endmodule
