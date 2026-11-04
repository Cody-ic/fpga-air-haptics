`timescale 1ns/1ps
// Portable pixel-domain core. The serial host sends complete paths, never field frames.
module theory_preview_core #(
    parameter integer CLK_HZ=49000000, BAUD=115200, KEY_DEB_MS=20,
    parameter integer GRID=64, STEP_25UM=120, MIN_25UM=-3780,
    parameter integer EXTERNAL_CONTROL=0
)(
    input wire clk,rst_n,key_next,key_play,
    input wire uart_rx_pin,
    output wire uart_tx_pin,
    input wire frame_boundary,
    input wire [11:0] field_read_addr,
    output reg [7:0] field_read_value,
    output reg field_valid,
    output reg signed [20:0] displayed_x,displayed_y,
    output reg [31:0] displayed_z,
    output reg [31:0] field_cycles,
    output reg [31:0] frame_number,
    output wire [1:0] run_state,
    output wire physical_output,
    output wire [63:0] physical_array,
    input wire [188:0] external_snapshot
);
    wire signed [20:0] fx,fy;
    wire [31:0] z,carrier,steps,level;
    wire [15:0] revision;
    wire active;
    reg [31:0] boot_counter=0;
    reg [31:0] boot_id=0;
    reg was_reset=1;
    always @(posedge clk) begin
        boot_counter<=boot_counter+1'b1;
        was_reset<=!rst_n;
        if(rst_n && was_reset) boot_id<=boot_counter;
    end
    generate if(EXTERNAL_CONTROL) begin:observer
        assign {fx,fy,z,carrier,steps,level,revision,run_state,active}=external_snapshot;
        assign uart_tx_pin=1'b1;
        assign physical_output=0;
        assign physical_array=0;
    end else begin:standalone
    hap2_top #(.PREVIEW_ONLY(1),.CLK_HZ(CLK_HZ),.BAUD(BAUD),
        .HW_ROWS(8),.HW_COLS(8),.HW_PITCH_UM(10300),.PH_PIPE(8),
        .TICK_CYC(CLK_HZ/100000),.KEY_DEB_MS(KEY_DEB_MS),
        .WS_X_MIN_UM(-90000),.WS_X_MAX_UM(90000),
        .WS_Y_MIN_UM(-90000),.WS_Y_MAX_UM(90000)) control(
        .clk(clk),.rst_n(rst_n),.uart_rx_pin(uart_rx_pin),.uart_tx_pin(uart_tx_pin),
        .boot_id(boot_id),.key_next(key_next),.key_play(key_play),.key_stop(1'b0),
        .focus_x(fx),.focus_y(fy),.view_z_um(z),.view_carrier_hz(carrier),
        .view_phase_steps(steps),.view_level(level),.view_revision(revision),
        .view_run_state(run_state),.view_active(active),
        .output_on(physical_output),.array_pos(physical_array));
    end endgenerate

    reg signed [20:0] fx_q,fy_q;
    reg [31:0] z_q,carrier_q,steps_q;
    reg [15:0] revision_q;
    reg active_q;
    reg phase_start;
    wire phase_done;
    reg [7:0] phase_addr;
    wire [7:0] phase_data;
    reg [639:0] phases;
    // Own phase engine works on a frozen trajectory sample. It cannot stall the
    // high-rate path walker or tear a field by reading a changing publication bank.
    hap2_phase #(.ROWS(8),.COLS(8),.PITCH_UM(10300),.PIPE(8)) phase(
        .clk(clk),.rst_n(rst_n),.cfg_carrier_hz(carrier_q),.cfg_phase_steps(steps_q),
        .cfg_z_um(z_q),.cfg_change(phase_start),.focus_x(fx_q),.focus_y(fy_q),
        .start(1'b0),.done(phase_done),.rd2_addr(phase_addr),.rd2_data(phase_data),
        .rd_sel(1'b0),.rd_addr(8'd0));
    reg engine_start;
    wire engine_done,write_en;
    wire [11:0] write_addr;
    wire [7:0] write_value;
    field_engine #(.GRID(GRID),.STEP_25UM(STEP_25UM),.MIN_25UM(MIN_25UM)) engine(
        .clk(clk),.rst_n(rst_n),.start(engine_start),.phases(phases),
        .z_um(z_q),.carrier_hz(carrier_q),.done(engine_done),
        .write_en(write_en),.write_addr(write_addr),.write_value(write_value));
    reg [7:0] image[0:8191];
    reg front;
    always @(posedge clk) begin
        if(write_en) image[{~front,write_addr}]<=write_value;
        field_read_value<=image[{front,field_read_addr}];
    end
    reg [3:0] state;
    reg [31:0] cycles;
    function [9:0] phase1024;
        input [7:0] code;
        input [31:0] count;
        begin
            case(count)
                8:phase1024={code[2:0],7'd0};16:phase1024={code[3:0],6'd0};
                32:phase1024={code[4:0],5'd0};64:phase1024={code[5:0],4'd0};
                128:phase1024={code[6:0],3'd0};default:phase1024={code,2'd0};
            endcase
        end
    endfunction
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=0;front<=0;field_valid<=0;phase_start<=0;engine_start<=0;
            fx_q<=0;fy_q<=0;z_q<=70000;carrier_q<=40000;steps_q<=64;revision_q<=0;
            active_q<=0;phase_addr<=0;phases<=0;displayed_x<=0;displayed_y<=0;
            displayed_z<=70000;field_cycles<=0;frame_number<=0;cycles<=0;
        end else begin
            phase_start<=0;engine_start<=0;
            if(state!=0 && state!=6) cycles<=cycles+1'b1;
            // Invalidate stale configuration immediately, including an in-flight old image.
            if(revision!=revision_q || run_state==0 || run_state==3) field_valid<=0;
            if(run_state==0 || run_state==3) begin
                active_q<=0;
                // No valid field is shown while stopped; label the current
                // configured plane rather than the reset/default frame height.
                displayed_x<=fx;displayed_y<=fy;displayed_z<=z;
            end
            case(state)
                0: if(revision!=0 && run_state==1) begin
                    fx_q<=fx;fy_q<=fy;z_q<=z;carrier_q<=carrier;steps_q<=steps;
                    revision_q<=revision;active_q<=active;cycles<=0;state<=1;
                end
                1: begin phase_start<=1;state<=2;end
                2: if(phase_done) begin phase_addr<=0;state<=3;end
                3: state<=4;
                4: begin
                    phases[10*phase_addr +:10]<=phase1024(phase_data,steps_q);
                    if(phase_addr==63) begin state<=5;engine_start<=1;end
                    else begin phase_addr<=phase_addr+1'b1;state<=3;end
                end
                5: if(engine_done) begin field_cycles<=cycles;state<=6;end
                6: if(frame_boundary) begin
                    front<=~front;field_valid<=active_q && revision==revision_q && (run_state==1 || run_state==2);
                    displayed_x<=fx_q;displayed_y<=fy_q;displayed_z<=z_q;
                    frame_number<=frame_number+1'b1;state<=0;
                end
                default:state<=0;
            endcase
        end
    end
endmodule
