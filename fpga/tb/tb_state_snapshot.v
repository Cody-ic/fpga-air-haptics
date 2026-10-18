`timescale 1ns/1ps

// Real 115200-8N1 UART regression, run once with ROWS/COLS=4 and once with 8.
// A long STATE remains immutable across two subsequent accepted CONFIGs.
// Strings/CRC are generated here; no external vectors or GUI are required.
module tb_state_snapshot #(
    parameter integer ROWS = 4,
    parameter integer COLS = ROWS,
    parameter integer PITCH = (ROWS == 8) ? 10300 : 11000
);
    localparam integer CLK_HZ = 50_000_000;
    localparam integer BAUD = 115200;
    localparam integer BIT_CYC = (CLK_HZ + BAUD/2) / BAUD;
    reg clk = 1'b0;
    always #10 clk = ~clk;
    reg rst_n = 1'b0;
    reg rx_line = 1'b1;
    wire tx_line;
    hap2_top #(.HW_ROWS(ROWS), .HW_COLS(COLS), .HW_PITCH_UM(PITCH),
        .PH_PIPE((COLS >= 8) ? 8 : 4), .STATE_MS(0)) dut (
        .clk(clk), .rst_n(rst_n), .uart_rx_pin(rx_line), .uart_tx_pin(tx_line),
        .boot_id(32'h12345678), .key_next(1'b0), .key_play(1'b0), .key_stop(1'b0)
    );
    wire [7:0] tx_byte;
    wire tx_valid, tx_error;
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) read_tx (
        .clk(clk), .rst_n(rst_n), .rx_line(tx_line),
        .rx_data(tx_byte), .rx_valid(tx_valid), .rx_error(tx_error)
    );

    integer checks = 0;
    integer frames = 0;
    integer states = 0;
    integer config_acks = 0;
    integer config_errors = 0;
    integer fd;
    string line = "";
    string path_a, path_b, path_c;

    task check;
        input ok;
        input string label;
        begin
            checks = checks + 1;
            if (ok !== 1'b1) $fatal(1, "%0s", label);
        end
    endtask

    function automatic [15:0] crc_byte;
        input [15:0] crc_in;
        input [7:0] data;
        reg [15:0] value;
        integer bit_i;
        begin
            value = crc_in ^ {data, 8'h00};
            for (bit_i=0; bit_i<8; bit_i=bit_i+1)
                value = value[15] ? (value << 1) ^ 16'h1021 : value << 1;
            crc_byte = value;
        end
    endfunction

    function automatic string field_value;
        input string text;
        input string key;
        integer i, j;
        begin
            field_value = "";
            for (i=0; i+key.len()<=text.len(); i=i+1) begin
                if (text.substr(i, i+key.len()-1) == key) begin
                    j = i + key.len();
                    while (j < text.len() && text[j] != " " && text[j] != "*") j=j+1;
                    field_value = text.substr(i+key.len(), j-1);
                    return field_value;
                end
            end
        end
    endfunction

    function automatic integer field_number;
        input string text;
        input string key;
        string value;
        integer number;
        begin
            value = field_value(text,key);
            if ($sscanf(value, "%d", number) != 1) number=-1;
            field_number=number;
        end
    endfunction

    task receive_line;
        input string text;
        reg [15:0] crc;
        reg [31:0] sent_crc;
        integer i, star, revision, phase_n;
        reg [31:0] sample_no;
        string path, phases;
        begin
            star = -1;
            crc = 16'hffff;
            for(i=0;i<text.len();i=i+1)
                if(text[i] == "*" && star < 0) star=i;
            check(star > 0 && star+6 == text.len(), "complete frame and four CRC digits");
            for(i=0;i<star;i=i+1) crc=crc_byte(crc,text[i]);
            check($sscanf(text.substr(star+1,star+4), "%h", sent_crc)==1,
                  "hexadecimal CRC");
            check(sent_crc == {16'd0,crc}, "real UART frame CRC");
            frames=frames+1;
            if(text.substr(0,8) == "HAP3 ACK " && field_value(text,"applied=") == "1"
                    && text.substr(11,16) == "CONFIG") config_acks=config_acks+1;
            if(text.substr(0,8) == "HAP3 ERR ") config_errors=config_errors+1;
            if(field_value(text,"proto=") == "3") begin
                check(field_value(text,"x_min_um=")=="-100000"
                    && field_value(text,"y_min_um=")=="-100000",
                    "signed workspace bounds survive the unsigned counter converter");
            end
            if(text.substr(0,15) == "HAP3 TEL 0 STATE") begin
                states=states+1;
                path=field_value(text,"scan_paths=");
                revision=field_number(text,"rev=");
                sample_no=field_number(text,"sample=");
                phases=field_value(text,"phases=");
                phase_n=1;
                for(i=0;i<phases.len();i=i+1) if(phases[i]==",") phase_n=phase_n+1;
                check(phase_n == ROWS*COLS, "all physical-array phase values present");
                case(states)
                    1: begin
                        check(sample_no==65535, "sample reaches 65535");
                        check(path=="NONE" && revision==0, "first STATE is NONE");
                    end
                    2: begin
                        check(sample_no==65536, "sample crosses 65535 without truncation");
                        check(path==path_a && revision==1, "maximum valid custom path round trips");
                    end
                    3: check(path==path_a && revision==1,
                        "old STATE text survives TWO accepted CONFIGs during transmission");
                    4,5: check(path==path_c && revision==3,
                        "later STATE uses latest committed custom path and revision");
                    6: check(path=="NONE" && revision==4,
                        "preset NONE does not leak the previous custom path");
                    7: check(path=="NONE" && revision==4,
                        "rejected 4096-byte text leaves committed preset unchanged");
                    8: begin
                        check(path==path_b && revision==5,
                            "same-cycle commit/capture reports matching scalar and text configuration");
                        check(field_value(text,"sample=")=="2147483648",
                            "sample remains unsigned across 2^31");
                    end
                    9: check(field_value(text,"sample=")=="4294967295",
                        "full uint32 sample prints all ten decimal digits");
                    default: $fatal(1,"unexpected STATE");
                endcase
                $display("STATE rows=%0d n=%0d sample=%0d rev=%0d text_bytes=%0d",
                    ROWS,states,sample_no,revision,path.len());
            end
        end
    endtask

    always @(posedge clk) begin
        if(tx_error) $fatal(1,"UART transmit framing error");
        if(tx_valid) begin
            line={line,tx_byte};
            if(fd != 0) $fwrite(fd,"%c",tx_byte);
            if(tx_byte == 10) begin receive_line(line); line=""; end
        end
        if(rst_n && dut.u_traj.parse_busy && dut.u_traj.txt_snap_valid)
            check(dut.u_traj.txt_write_sel != dut.u_traj.txt_snap_sel,
                "parser never overwrites the bank locked by STATE");
    end

    task send_byte;
        input [7:0] value;
        integer bit_i;
        begin
            @(negedge clk); rx_line=0; repeat(BIT_CYC) @(negedge clk);
            for(bit_i=0;bit_i<8;bit_i=bit_i+1) begin
                rx_line=value[bit_i]; repeat(BIT_CYC) @(negedge clk);
            end
            rx_line=1; repeat(BIT_CYC) @(negedge clk);
        end
    endtask

    task send_command;
        input string body;
        reg [15:0] crc;
        string packet;
        integer i;
        begin
            crc=16'hffff;
            for(i=0;i<body.len();i=i+1) crc=crc_byte(crc,body[i]);
            packet={body,$sformatf("*%04X\n",crc)};
            for(i=0;i<packet.len();i=i+1) send_byte(packet[i]);
        end
    endtask

    task send_config;
        input integer seq;
        input string shape;
        input string path;
        string body;
        begin
            body=$sformatf("HAP3 CMD %0d CONFIG carrier_hz=40000 phase_steps=64 cx_um=0 cy_um=0 z_um=70000 radius_um=20000 repeat_millihz=500 mod_hz=0 level=30 shape=%0s path_xy_um=NONE path_closed=1 scan_paths=%0s blank_us=2000 hw_rows=%0d hw_cols=%0d hw_pitch_um=%0d mapping=ROW_MAJOR_XY",
                seq,shape,path,ROWS,COLS,PITCH);
            send_command(body);
        end
    endtask

    integer i;
    reg same_cycle_done = 1'b0;
    initial begin
        // Optional capture in the caller's runtime directory; normal repository
        // regression runs do not leave generated logs beside the sources.
        fd=0;
        if($test$plusargs("UART_LOG"))
            fd=$fopen($sformatf("state_snapshot_%0dx%0d_uart.log",ROWS,COLS),"w");
        path_a="";
        // 256 legal points, exactly 4095 bytes. Leading zeroes preserve the
        // largest legal spelling while keeping coordinates inside the workspace.
        for(i=0;i<256;i=i+1) begin
            if(i!=0) path_a={path_a,","};
            path_a={path_a,(i%2 == 0) ? "-010000:-010000" : "-010001:-010000"};
        end
        path_b="0:0,10000:0";
        path_c="0:0,0:20000,20000:20000";
        check(path_a.len()==4095, "maximum valid text uses the last useful RAM bytes");
        repeat(10) @(negedge clk); rst_n=1;
        // Seed only the counter; all protocol operations and CONFIGs use UART.
        force dut.u_tx.sample=32'd65534;
        @(negedge clk); release dut.u_tx.sample;
        send_command("HAP3 CMD 1 HELLO"); wait(states==1);
        send_config(2,"CUSTOM",path_a); wait(states==2);
        send_command("HAP3 CMD 3 SNAP");
        send_config(4,"CUSTOM",path_b);
        send_config(5,"CUSTOM",path_c);
        wait(dut.cmd_rev==3);
        check(states==2, "both new CONFIGs accepted before old long STATE completes");
        wait(states==5);
        send_config(6,"POINT","NONE"); wait(states==6);
        // Last address (4095) is exercised by a 4096-byte rejected text; the
        // final '-' cannot finish a coordinate. It must not poison the snapshot.
        send_config(7,"CUSTOM",{path_a,"-"}); wait(config_errors==1);
        check(dut.u_traj.txt_ram[{dut.u_traj.txt_write_sel,12'hfff}]==8'h2d,
            "4096th input byte reaches the last RAM address without wrapping");
        send_command("HAP3 CMD 8 SNAP"); wait(states==7);
        force dut.u_tx.sample=32'h7fffffff;
        @(negedge clk); release dut.u_tx.sample;
        fork
            begin
                wait(dut.traj_txt_commit);
                // Align the capture event with a real UART CONFIG commit.
                // The scalar registers already contain rev 5 at this edge.
                @(negedge clk); force dut.u_tx.state_capture=1'b1;
                @(negedge clk);
                check(dut.sn_rev==5 && dut.sn_shape==7,
                    "same-cycle scalar snapshot sees the committed config");
                check(dut.u_traj.txt_snap_valid && dut.u_traj.txt_snap_len==path_b.len()
                    && dut.u_traj.txt_snap_sel==dut.u_traj.txt_sel,
                    "same-cycle text snapshot selects that exact committed config");
                release dut.u_tx.state_capture;
                same_cycle_done=1'b1;
            end
            begin send_config(9,"CUSTOM",path_b); end
        join
        wait(states==8);
        force dut.u_tx.sample=32'hfffffffe;
        @(negedge clk); release dut.u_tx.sample;
        send_command("HAP3 CMD 10 SNAP"); wait(states==9);
        check(same_cycle_done, "same-cycle commit/capture case executed");
        check(config_acks==5 && config_errors==1, "all accepted/rejected CONFIG replies preserved");
        if(fd != 0) $fclose(fd);
        $display("STATE snapshot regression passed rows=%0d cols=%0d checks=%0d frames=%0d",
            ROWS,COLS,checks,frames);
        $finish;
    end
    initial begin #4_000_000_000; $fatal(1,"STATE regression timeout"); end
endmodule
