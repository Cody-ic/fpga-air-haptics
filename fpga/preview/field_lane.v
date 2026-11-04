`timescale 1ns/1ps
// One spherical point-source contribution. Coordinates: 25 um; phase: 1/1024 turn.
// Only the sine table is precomputed. Distances, spreading and interference run in RTL.
module field_lane(
    input wire clk, rst_n, start,
    input wire signed [15:0] dx, dy,
    input wire [15:0] z,
    input wire [19:0] propagation_scale,
    input wire [9:0] source_phase,
    output reg done,
    output reg signed [28:0] re, im
);
    reg [3:0] state;
    reg [31:0] xx, yy, zz, squared;
    reg sqrt_start, div_start;
    wire sqrt_done, div_done;
    wire [15:0] distance;
    wire [24:0] reciprocal;
    reg [35:0] phase_product;
    reg [9:0] angle;
    reg signed [11:0] sine, cosine;
    reg signed [11:0] trig [0:1023];
    initial $readmemh("sine.hex", trig);
    fix_sqrt #(.IN_BITS(32),.OUT_BITS(16)) sq(
        .clk(clk),.rst_n(rst_n),.start(sqrt_start),.value(squared),
        .done(sqrt_done),.result(distance));
    fix_div #(.NUM_BITS(25),.DEN_BITS(16)) inv(
        .clk(clk),.rst_n(rst_n),.start(div_start),.numer(25'd16777216),
        .denom(distance == 0 ? 16'd1 : distance),.done(div_done),.quot(reciprocal));
    always @(posedge clk) begin
        if (!rst_n) begin
            state<=0; done<=0; sqrt_start<=0; div_start<=0; re<=0; im<=0;
            xx<=0; yy<=0; zz<=0; squared<=0; phase_product<=0; angle<=0;
            sine<=0; cosine<=0;
        end else begin
            done<=0; sqrt_start<=0; div_start<=0;
            case(state)
                0: if(start) begin xx<=dx*dx; yy<=dy*dy; zz<=z*z; state<=1; end
                1: begin squared<=xx+yy+zz; state<=2; end
                2: begin sqrt_start<=1; state<=3; end
                3: if(sqrt_done) begin
                    phase_product<=distance*propagation_scale; div_start<=1; state<=4;
                end
                4: begin angle<=phase_product[19:10]+source_phase; state<=5; end
                5: begin sine<=trig[angle]; cosine<=trig[(angle+10'd256)&10'h3ff]; state<=6; end
                6: if(div_done) begin
                    re<=$signed({1'b0,reciprocal[15:0]})*cosine;
                    im<=$signed({1'b0,reciprocal[15:0]})*sine;
                    done<=1; state<=0;
                end
                default: state<=0;
            endcase
        end
    end
endmodule
