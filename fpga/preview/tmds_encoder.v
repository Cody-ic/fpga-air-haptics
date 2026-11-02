`timescale 1ns/1ps
// DVI-compatible TMDS video coding; blanking resets running disparity.
module tmds_encoder(input wire clk,rst_n,de,c0,c1,input wire [7:0] data,
                    output reg [9:0] symbol);
    reg [8:0] q;
    integer ones,i,balance;
    reg invert_xor;
    reg signed [5:0] disparity;
    always @* begin
        ones=0;
        for(i=0;i<8;i=i+1) ones=ones+data[i];
        invert_xor=ones>4 || (ones==4 && !data[0]);
        q[0]=data[0];
        for(i=1;i<8;i=i+1) q[i]=q[i-1]^data[i]^invert_xor;
        q[8]=!invert_xor;
        ones=0;
        for(i=0;i<8;i=i+1) ones=ones+q[i];
        balance=2*ones-8;
    end
    always @(posedge clk) begin
        if(!rst_n) begin symbol<=10'b1101010100;disparity<=0;end
        else if(!de) begin
            disparity<=0;
            case({c1,c0})
                0:symbol<=10'b1101010100;1:symbol<=10'b0010101011;
                2:symbol<=10'b0101010100;3:symbol<=10'b1010101011;
            endcase
        end else if(disparity==0 || balance==0) begin
            symbol<={~q[8],q[8],q[8]?q[7:0]:~q[7:0]};
            disparity<=disparity+(q[8]?balance:-balance);
        end else if((disparity>0 && balance>0)||(disparity<0 && balance<0)) begin
            symbol<={1'b1,q[8],~q[7:0]};
            disparity<=disparity+2*q[8]-balance;
        end else begin
            symbol<={1'b0,q[8],q[7:0]};
            disparity<=disparity-2*(1-q[8])+balance;
        end
    end
endmodule
