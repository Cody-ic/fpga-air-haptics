`timescale 1ns/1ps
// Slowly refreshed metadata uses an iterative divider and serial binary-to-BCD,
// so decimal labels do not put a 32-bit division in the pixel critical path.
module preview_number #(parameter integer SCALE=1000)(
    input wire clk,rst_n,input wire [31:0] value,output reg [11:0] decimal
);
    reg start;
    wire done;
    wire [31:0] quotient;
    fix_div #(.NUM_BITS(32),.DEN_BITS(16)) divide(.clk(clk),.rst_n(rst_n),
        .start(start),.numer(value),.denom(SCALE[15:0]),.done(done),.quot(quotient));
    reg [1:0] state;
    reg [9:0] binary;
    reg [11:0] bcd;
    reg [3:0] count;
    reg [11:0] adjusted;
    integer i;
    always @* begin
        adjusted=bcd;
        for(i=0;i<3;i=i+1) if(bcd[4*i +:4]>=5) adjusted[4*i +:4]=bcd[4*i +:4]+4'd3;
    end
    always @(posedge clk) begin
        if(!rst_n) begin state<=0;start<=0;binary<=0;bcd<=0;count<=0;decimal<=0;end
        else begin
            start<=0;
            case(state)
                0:begin start<=1;state<=1;end
                1:if(done) begin binary<=quotient>999?10'd999:quotient[9:0];bcd<=0;count<=0;state<=2;end
                2:begin
                    bcd<={adjusted[10:0],binary[9]};binary<={binary[8:0],1'b0};
                    if(count==9) state<=3;else count<=count+1'b1;
                end
                3:begin decimal<=bcd;state<=0;end
            endcase
        end
    end
endmodule
