`timescale 1ns/1ps

// Calibration-only startup endpoint (CAL2); normal HAP3 remains reset until
// successful verification and host RELEASE. No wireless transport exists here.
module hap2_cal_startup #(
    parameter integer CLK_HZ=50000000, BAUD=115200,
    parameter integer NULL_SCALED_QC=0,
    parameter integer ROWS=4, COLS=4, PITCH_UM=11000, SOURCE=0,
    parameter integer Z_UM=100000, LEVEL=30, DEAD_CYC=0,
    parameter integer SETTLE_MS=200, REPEAT_MS=220, TIMEOUT_MS=6000, LINK_MS=3000
) (
    input wire clk,rst_n,rx_line,
    input wire [31:0] boot_id,
    output wire tx_line,
    output wire [ROWS*COLS-1:0] out_pos,out_neg,
    output wire output_on,
    output reg released,
    output wire [8*ROWS*COLS-1:0] saved_offsets
);
    localparam integer N=ROWS*COLS;
    wire packet_valid,packet_busy,packet_done;
    wire [255:0] packet;
    reg send_packet;
    reg [255:0] reply;
    hap2_cal_packet #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) u_link (
        .clk(clk),.rst_n(rst_n && !released),.rx_line(rx_line),.tx_line(tx_line),
        .rx_valid(packet_valid),.rx_body(packet),.tx_start(send_packet),
        .tx_body(reply),.tx_busy(packet_busy),.tx_done(packet_done));
    wire [7:0] command=packet[31:24];
    wire [31:0] request_boot=packet[63:32],request_token=packet[95:64];
    wire [31:0] v0=packet[127:96],v1=packet[159:128],v2=packet[191:160];
    wire [31:0] v3=packet[223:192],v4=packet[255:224];
    wire addressed=packet_valid && request_boot==boot_id && !released;
    wire no_args=packet[255:96]==0;
    wire idle_command=!busy && !released && !release_pending && !release_sending;
    reg signed [31:0] x_um,y_um;
    reg [31:0] z_um,level;
    reg [7:0] reference;
    reg configured,phase_ready,configure_phase,attempted;
    reg begin_run,abort_run,measure_valid,measure_good,import_run;
    reg [31:0] measure_power;
    reg [8*N-1:0] imported;
    reg [N-1:0] imported_mask;
    wire busy,saved_valid,changed,committed,tx_on;
    wire [2:0] state;
    wire [3:0] stage,error;
    wire [7:0] channel;
    wire [1:0] repeat_number;
    wire [31:0] token;
    wire [N-1:0] mask;
    wire [8*N-1:0] trial;
    reg [31:0] silent_ms,ms_div;
    wire ms_tick=ms_div>=CLK_HZ/1000-1;
    hap2_calibrate #(.CHANNELS(N),.SOURCE(SOURCE),.CLK_HZ(CLK_HZ),.NULL_SCALED_QC(NULL_SCALED_QC),
        .SETTLE_MS(SETTLE_MS),.REPEAT_MS(REPEAT_MS),.TIMEOUT_MS(TIMEOUT_MS)) u_cal (
        .clk(clk),.rst_n(rst_n),.begin_run(begin_run),.abort_run(abort_run),.clear_saved(1'b0),
        .reference(reference),.import_run(import_run),.import_offsets(imported),
        .measure_valid(measure_valid),.measure_good(measure_good),.measure_power(measure_power),
        .busy(busy),.saved_valid(saved_valid),.changed(changed),.committed(committed),
        .state(state),.stage(stage),.channel(channel),.error(error),.repeat_number(repeat_number),
        .token(token),.tx_on(tx_on),.channel_mask(mask),.trial_offsets(trial),.saved_offsets(saved_offsets));

    wire ph_done,ph_valid,ph_sel;
    wire [7:0] read_addr,nominal,nominal_copy;
    reg [7:0] copy_addr;
    reg copy_active,copy_wait;
    reg [8*N-1:0] baseline;
    wire signed [20:0] fx=x_um[20:0]<<<1,fy=y_um[20:0]<<<1;
    hap2_phase #(.ROWS(ROWS),.COLS(COLS),.PITCH_UM(PITCH_UM),.PIPE(1)) u_phase (
        .clk(clk),.rst_n(rst_n),.cfg_carrier_hz(32'd40000),.cfg_phase_steps(32'd64),
        .cfg_z_um(z_um),.cfg_change(configure_phase),.focus_x(fx),.focus_y(fy),.start(1'b0),
        .busy(),.done(ph_done),.sweep_cnt(),.pub_sel(ph_sel),.pub_valid(ph_valid),
        .rd_sel(ph_sel),.rd_addr(copy_addr),.rd_data(nominal_copy),
        .rd2_addr(read_addr),.rd2_data(nominal),.pub_fx(),.pub_fy(),.pub_fz_um());
    reg [7:0] offset_q;
    reg [1:0] reload;
    always @(posedge clk) begin
        if(!rst_n) begin offset_q<=0; reload<=0; end
        else begin
            offset_q<=trial[8*read_addr +: 8];
            reload<={reload[0],changed || ph_done};
        end
    end
    wire [7:0] corrected=(nominal+offset_q)&8'h3f;
    wire [N-1:0] waves_pos,waves_neg;
    assign output_on=tx_on && phase_ready && !released && !abort_run;
    hap2_out #(.ROWS(ROWS),.COLS(COLS),.CLK_HZ(CLK_HZ),.ACC_BITS(32),.DEAD_CYC(DEAD_CYC)) u_out (
        .clk(clk),.rst_n(rst_n),.cfg_carrier_hz(32'd40000),.cfg_phase_steps(32'd64),
        .cfg_change(changed || configure_phase),.cfg_level(level),.cfg_mod_hz(32'd0),
        .enable(output_on),.tbl_new(reload[1]),.tbl_addr(read_addr),.tbl_data(corrected),
        .out_pos(waves_pos),.out_neg(waves_neg));
    assign out_pos=waves_pos & mask & {N{output_on}};
    assign out_neg=waves_neg & mask & {N{output_on}};

    reg [31:0] report_ms;
    reg [7:0] pending_type;
    reg [159:0] pending_args;
    reg release_pending,release_sending;
    reg [255:0] next_reply;
    integer i;
    reg upload_valid;
    always @* begin
        upload_valid=v0<N && v0[3:0]==0;
        for(i=0;i<16;i=i+1)
            if(v0+i<N && packet[128+8*i +: 8]>63) upload_valid=0;
        next_reply=0;
        next_reply[15:0]=16'h5aa5; next_reply[23:16]=2;
        next_reply[31:24]=release_pending ? 8'h82 : pending_type!=0 ? pending_type : 8'h80;
        next_reply[63:32]=boot_id; next_reply[95:64]=token;
        next_reply[127:96]={4'd0,error,channel,4'd0,stage,5'd0,state};
        next_reply[159:128]=({30'd0,repeat_number}<<8) | (output_on ? 32'd64:0) |
            (import_run ? 32'd32:0) | (configured && phase_ready ? 32'd16:0) |
            (attempted ? 32'd8:0) | (SOURCE==1 ? 32'd4:0) | (saved_valid ? 32'd2:0) | (busy ? 32'd1:0);
        next_reply[191:160]={reference,level[7:0],ROWS[7:0],COLS[7:0]};
        next_reply[223:192]=z_um; next_reply[255:224]=PITCH_UM;
        if(pending_type!=0 && !release_pending) next_reply[255:96]=pending_args;
    end
    wire valid_setup=$signed(v0)>=-100000 && $signed(v0)<=100000 &&
        $signed(v1)>=-100000 && $signed(v1)<=100000 && v2>=20000 && v2<=300000 &&
        v3>=1 && v3<=100 && v4<N;
    always @(posedge clk) begin
        if(!rst_n) begin
            x_um<=0; y_um<=0; z_um<=Z_UM; level<=LEVEL; reference<=0;
            configured<=0; phase_ready<=0; configure_phase<=0; attempted<=0;
            begin_run<=0; abort_run<=0; measure_valid<=0; measure_good<=0; measure_power<=0;
            import_run<=0; imported<=0; imported_mask<=0;
            silent_ms<=0; ms_div<=0; report_ms<=100; send_packet<=0; reply<=0;
            released<=0; pending_type<=0; pending_args<=0; release_pending<=0; release_sending<=0;
            baseline<=0; copy_addr<=0; copy_active<=0; copy_wait<=0;
        end else if(!released) begin
            begin_run<=0; abort_run<=0; measure_valid<=0; send_packet<=0; configure_phase<=0;
            if(ms_tick) begin
                ms_div<=0;
                if(busy && silent_ms<LINK_MS) silent_ms<=silent_ms+1;
                if(report_ms<100) report_ms<=report_ms+1;
            end else ms_div<=ms_div+1;
            if(busy && silent_ms>=LINK_MS) abort_run<=1;
            if(ph_done) begin copy_active<=1; copy_addr<=0; copy_wait<=0; end
            else if(copy_active) begin
                if(copy_wait) begin
                    baseline[8*copy_addr +: 8]<=nominal_copy;
                    copy_wait<=0;
                    if(copy_addr==N-1) begin copy_active<=0; phase_ready<=1; end
                    else copy_addr<=copy_addr+1;
                end else copy_wait<=1;
            end
            if(!packet_busy && !send_packet && !release_sending &&
               (report_ms>=100 || pending_type!=0 || release_pending)) begin
                reply<=next_reply; send_packet<=1; report_ms<=0;
                if(release_pending) begin release_sending<=1; release_pending<=0; end
                else pending_type<=0;
            end
            if(addressed) begin
                case(command)
                    1: if(idle_command && (state==0 || state==5) && configured && phase_ready &&
                          request_token==0 && v0==SOURCE && v1<=1 && v2==0 && v3==0 && v4==0 &&
                          (v1==0 || (&imported_mask && imported[8*reference +: 8]==0))) begin
                        begin_run<=1; import_run<=v1[0]; attempted<=1; silent_ms<=0;
                    end
                    2: if(busy && state==2 && request_token==token && v1<=1 && v2==SOURCE &&
                          v3=={16'd0,4'd0,stage,channel} && v4=={30'd0,repeat_number} && !measure_valid) begin
                        measure_power<=v0; measure_good<=v1[0]; measure_valid<=1; silent_ms<=0;
                    end
                    3: if(request_token==0 && no_args) silent_ms<=0;
                    4: if(request_token==0 && no_args) abort_run<=1;
                    5: if(!busy && request_token==0 && v0<N && v0[3:0]==0 && v1<=1 &&
                          v2==0 && v3==0 && v4==0 && pending_type==0 &&
                          (v1==1 ? phase_ready : saved_valid)) begin
                        pending_type<=8'h81; pending_args<=0;
                        pending_args[31:0]<=v0|(v1<<16);
                        // Protocol bases are aligned to 16 channels. Constant
                        // byte slices avoid sixteen independent full-table
                        // barrel selectors when the array grows to 64 lanes.
                        for(i=0;i<N;i=i+1) if(v0==(i/16)*16)
                            pending_args[32+8*(i%16) +: 8]<=v1==1 ? baseline[8*i +: 8] : saved_offsets[8*i +: 8];
                    end
                    6: if(state==4 && saved_valid && request_token==token && no_args && pending_type==0)
                        release_pending<=1;
                    7: if(idle_command && !begin_run && state!=4 && request_token==0 && valid_setup && pending_type==0) begin
                        x_um<=v0; y_um<=v1; z_um<=v2; level<=v3; reference<=v4[7:0];
                        configured<=1; phase_ready<=0; configure_phase<=1; imported_mask<=0;
                        pending_type<=8'h83; pending_args<=packet[255:96];
                    end
                    8: if(idle_command && state!=4 && configured && request_token==0 && upload_valid && pending_type==0) begin
                        for(i=0;i<N;i=i+1) if(v0==(i/16)*16) begin
                            imported[8*i +: 8]<=packet[128+8*(i%16) +: 8]; imported_mask[i]<=1;
                        end
                        pending_type<=8'h85; pending_args<=packet[255:96];
                    end
                    default: begin end
                endcase
            end
            if(packet_done && release_sending) begin released<=1; send_packet<=0; end
        end
    end
endmodule
