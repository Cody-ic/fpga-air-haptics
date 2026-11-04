`timescale 1ns/1ps
// 8x8 / 10.3 mm ideal array, eight parallel columns. One immutable phase snapshot
// per field. Output is normalized amplitude, NOT pressure in Pa or intensity.
// Reference = 64/z for unit sources, independent of focus/individual frame maxima.
module field_engine #(
    parameter integer GRID=64, STEP_25UM=120, MIN_25UM=-3780
)(
    input wire clk,rst_n,start,
    input wire [639:0] phases,
    input wire [31:0] z_um, carrier_hz,
    output reg busy,done,
    output reg write_en,
    output reg [11:0] write_addr,
    output reg [7:0] write_value
);
    reg [4:0] state;
    reg [639:0] phase_q;
    reg [15:0] z_q;
    reg [19:0] scale_q;
    reg [23:0] reference_q;
    reg [5:0] gx,gy;
    reg [2:0] row;
    reg lane_start, constant_start, reference_start, magnitude_start, normalize_start;
    wire [7:0] lane_done;
    wire signed [28:0] lane_re[0:7],lane_im[0:7];
    reg signed [34:0] acc_re,acc_im;
    reg signed [34:0] pair_re[0:3],pair_im[0:3],half_re[0:1],half_im[0:1],row_re,row_im;
    integer pair_index;
    wire signed [15:0] py=MIN_25UM+$signed({1'b0,gy})*STEP_25UM;
    wire signed [15:0] emitter_y=-1442+$signed({1'b0,row})*412;
    genvar c;
    generate for(c=0;c<8;c=c+1) begin:col
        wire signed [15:0] px=MIN_25UM+$signed({1'b0,gx})*STEP_25UM;
        localparam signed [15:0] EMITTER_X=-1442+c*412;
        wire signed [15:0] dx=px-EMITTER_X;
        wire [5:0] ch={row,3'b000}+c;
        field_lane lane(.clk(clk),.rst_n(rst_n),.start(lane_start),
            .dx(dx),.dy(py-emitter_y),.z(z_q),
            .propagation_scale(scale_q),.source_phase(phase_q[10*ch +:10]),
            .done(lane_done[c]),.re(lane_re[c]),.im(lane_im[c]));
    end endgenerate
    reg [51:0] constant_numer;
    reg [19:0] z_numer;
    reg z_start;
    wire z_done;
    wire [19:0] z_quot;
    fix_div #(.NUM_BITS(20),.DEN_BITS(5)) z_div(.clk(clk),.rst_n(rst_n),
        .start(z_start),.numer(z_numer),.denom(5'd25),.done(z_done),.quot(z_quot));
    wire [51:0] constant_quot;
    wire constant_done,reference_done,magnitude_done,normalize_done;
    wire [24:0] reference_quot;
    fix_div #(.NUM_BITS(52),.DEN_BITS(24)) constant_div(.clk(clk),.rst_n(rst_n),
        .start(constant_start),.numer(constant_numer),.denom(24'd13720000),
        .done(constant_done),.quot(constant_quot));
    fix_div #(.NUM_BITS(25),.DEN_BITS(16)) reference_div(.clk(clk),.rst_n(rst_n),
        .start(reference_start),.numer(25'd16777216),.denom(z_q),
        .done(reference_done),.quot(reference_quot));
    reg signed [23:0] scaled_re,scaled_im;
    reg [47:0] re2,im2,power_q;
    wire [23:0] magnitude;
    wire [31:0] normalized;
    fix_sqrt #(.IN_BITS(48),.OUT_BITS(24)) mag(.clk(clk),.rst_n(rst_n),
        .start(magnitude_start),.value(power_q),.done(magnitude_done),.result(magnitude));
    fix_div #(.NUM_BITS(32),.DEN_BITS(24)) norm(.clk(clk),.rst_n(rst_n),
        .start(normalize_start),.numer({magnitude,8'd0}),.denom(reference_q),
        .done(normalize_done),.quot(normalized));
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=0;busy<=0;done<=0;write_en<=0;write_addr<=0;write_value<=0;
            lane_start<=0;constant_start<=0;reference_start<=0;magnitude_start<=0;normalize_start<=0;
            phase_q<=0;z_q<=2800;scale_q<=0;reference_q<=1;gx<=0;gy<=0;row<=0;
            acc_re<=0;acc_im<=0;constant_numer<=0;scaled_re<=0;scaled_im<=0;re2<=0;im2<=0;power_q<=0;
            z_numer<=70000;z_start<=0;row_re<=0;row_im<=0;
        end else begin
            done<=0;write_en<=0;lane_start<=0;constant_start<=0;reference_start<=0;z_start<=0;
            magnitude_start<=0;normalize_start<=0;
            case(state)
                0: if(start) begin
                    phase_q<=phases;z_numer<=z_um[19:0];constant_numer<={carrier_hz,20'd0};
                    busy<=1;gx<=0;gy<=0;write_addr<=0;state<=1;
                end
                1: begin z_start<=1;state<=18;end
                18: if(z_done) begin z_q<=z_quot[15:0];constant_start<=1;state<=2;end
                2: if(constant_done) begin scale_q<=constant_quot[19:0];reference_start<=1;state<=3;end
                3: if(reference_done) begin reference_q<=reference_quot[17:0]*64;state<=4;end
                4: begin acc_re<=0;acc_im<=0;row<=0;state<=5;end
                5: begin lane_start<=1;state<=6;end
                6: if(&lane_done) begin
                    for(pair_index=0;pair_index<4;pair_index=pair_index+1) begin
                        pair_re[pair_index]<=$signed(lane_re[2*pair_index])+$signed(lane_re[2*pair_index+1]);
                        pair_im[pair_index]<=$signed(lane_im[2*pair_index])+$signed(lane_im[2*pair_index+1]);
                    end
                    state<=14;
                end
                14: begin
                    half_re[0]<=pair_re[0]+pair_re[1];half_re[1]<=pair_re[2]+pair_re[3];
                    half_im[0]<=pair_im[0]+pair_im[1];half_im[1]<=pair_im[2]+pair_im[3];state<=15;
                end
                15: begin row_re<=half_re[0]+half_re[1];row_im<=half_im[0]+half_im[1];state<=16;end
                16: begin acc_re<=acc_re+row_re;acc_im<=acc_im+row_im;state<=7;end
                7: if(row==7) begin scaled_re<=acc_re>>>11;scaled_im<=acc_im>>>11;state<=8;end
                   else begin row<=row+1'b1;state<=5;end
                8: begin re2<=scaled_re*scaled_re;im2<=scaled_im*scaled_im;state<=9;end
                9: begin power_q<=re2+im2;state<=10;end
                10: begin magnitude_start<=1;state<=11;end
                11: if(magnitude_done) begin normalize_start<=1;state<=12;end
                12: if(normalize_done) begin
                    write_value<=normalized>255 ? 8'd255 : normalized[7:0];write_en<=1;state<=13;
                end
                13: if(gx==GRID-1 && gy==GRID-1) begin done<=1;busy<=0;state<=0;end
                    else begin
                        write_addr<=write_addr+1'b1;
                        if(gx==GRID-1) begin gx<=0;gy<=gy+1'b1;end else gx<=gx+1'b1;
                        state<=4;
                    end
                default: state<=0;
            endcase
        end
    end
endmodule
