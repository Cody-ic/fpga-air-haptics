`timescale 1ns/1ps
// Waveshare C published EDID: 49 MHz, 1024 48 96 144 / 600 3 10 11.
// A field bank flips only at the beginning of vertical blank, never mid-map.
module preview_video(
    input wire clk,rst_n,
    input wire [7:0] field_value,
    input wire field_valid,
    input wire signed [20:0] focus_x,focus_y,
    input wire [31:0] z_um,field_cycles,frame_number,
    input wire [1:0] run_state,
    output wire frame_boundary,
    output wire [11:0] field_addr,
    output reg [7:0] red,green,blue,
    output reg de,hs,vs
);
    reg [10:0] x;
    reg [9:0] y;
    always @(posedge clk) begin
        if(!rst_n) begin x<=0;y<=0;end
        else if(x==1311) begin x<=0;y<=y==623?0:y+1'b1;end
        else x<=x+1'b1;
    end
    assign frame_boundary=x==0 && y==600;
    wire inside_map=x>=32 && x<544 && y>=56 && y<568;
    wire [8:0] map_x=x-32,map_y=y-56;
    assign field_addr={6'd63-map_y[8:3],map_x[8:3]};
    // Text is a static monochrome overlay, not precomputed sound-field images.
    reg [15:0] text_rom[0:38399];
    initial $readmemh("labels.hex",text_rom);
    wire [19:0] text_pixel={y,x[9:0]};
    reg [15:0] text_word;
    reg [3:0] text_bit;
    reg [10:0] x_d;
    reg [9:0] y_d;
    reg map_d,active_d,hs_d,vs_d;
    always @(posedge clk) begin
        text_word<=text_rom[y<600 ? text_pixel[19:4] : 0];
        text_bit<=text_pixel[3:0];
        x_d<=x;y_d<=y;map_d<=inside_map;
        active_d<=x<1024 && y<600;
        hs_d<=!(x>=1072 && x<1168);vs_d<=!(y>=603 && y<613);
    end
    function [23:0] palette;
        input [7:0] v;
        reg [7:0] t;
        begin
            t={v[5:0],2'b00};
            case(v[7:6])
                0:palette={8'd8+(t>>5),8'd16+(t>>2),8'd36+(t>>1)};
                1:palette={8'd15,8'd80+(t>>1),8'd164-(t>>3)};
                2:palette={8'd16+(t>>1)+(t>>2),8'd208+(t>>4),8'd132-(t>>2)};
                3:palette={8'd208+(t>>3),8'd224-(t>>2),8'd68-(t>>3)};
            endcase
        end
    endfunction
    // BCD-free numeric readout: small decimal digits calculated from stable frame metadata.
    function [34:0] digit;
        input [3:0] n;
        begin
            case(n)
                0:digit=35'b01110_10001_10011_10101_11001_10001_01110;
                1:digit=35'b00100_01100_00100_00100_00100_00100_01110;
                2:digit=35'b01110_10001_00001_00010_00100_01000_11111;
                3:digit=35'b11110_00001_00001_01110_00001_00001_11110;
                4:digit=35'b00010_00110_01010_10010_11111_00010_00010;
                5:digit=35'b11111_10000_10000_11110_00001_00001_11110;
                6:digit=35'b01110_10000_10000_11110_10001_10001_01110;
                7:digit=35'b11111_00001_00010_00100_01000_01000_01000;
                8:digit=35'b01110_10001_10001_01110_10001_10001_01110;
                9:digit=35'b01110_10001_10001_01111_00001_00001_01110;
                default:digit=0;
            endcase
        end
    endfunction
    // Metadata changes once per completed field; divide constants outside pixel color path.
    wire [11:0] z_decimal,ms_decimal;
    preview_number #(.SCALE(1000)) z_text(.clk(clk),.rst_n(rst_n),.value(z_um),.decimal(z_decimal));
    preview_number #(.SCALE(49000)) ms_text(.clk(clk),.rst_n(rst_n),.value(field_cycles),.decimal(ms_decimal));
    wire [19:0] x_positive=$signed(focus_x)+21'sd189000;
    wire [19:0] y_positive=$signed(focus_y)+21'sd189000;
    reg marker_start;
    wire marker_done;
    wire [19:0] mx,my;
    reg [10:0] marker_x,marker_y;
    reg marker_busy;
    fix_div #(.NUM_BITS(20),.DEN_BITS(10)) marker_div_x(.clk(clk),.rst_n(rst_n),
        .start(marker_start),.numer(x_positive),.denom(10'd750),.done(marker_done),.quot(mx));
    fix_div #(.NUM_BITS(20),.DEN_BITS(10)) marker_div_y(.clk(clk),.rst_n(rst_n),
        .start(marker_start),.numer(y_positive),.denom(10'd750),.quot(my));
    always @(posedge clk) begin
        if(!rst_n) begin marker_busy<=0;marker_start<=0;marker_x<=288;marker_y<=312;end
        else begin
            marker_start<=0;
            if(!marker_busy) begin marker_start<=1;marker_busy<=1;end
            if(marker_done) begin marker_x<=11'd36+mx[10:0];marker_y<=11'd564-my[10:0];marker_busy<=0;end
        end
    end
    reg [23:0] color;
    reg [34:0] glyph;
    reg [2:0] nx,ny;
    reg [1:0] slot;
    reg [5:0] digit_x;
    reg [4:0] digit_y;
    wire [7:0] legend_value=x_d-590;
    always @* begin
        color=24'h0b1424;glyph=0;nx=0;ny=0;slot=0;digit_x=0;digit_y=0;
        if(map_d) begin
            color=field_valid?palette(field_value):24'h081024;
            // Axes are reference lines, not an assumed peak.
            if(x_d==288 || y_d==312) color={color[23:17],1'b1,color[15:9],1'b1,color[7:1],1'b1};
        end
        if(x_d>=590 && x_d<846 && y_d>=410 && y_d<434)
            color=palette(legend_value);
        if((x_d==31 || x_d==544) && y_d>=55 && y_d<=568) color=24'h456078;
        if((y_d==55 || y_d==568) && x_d>=31 && x_d<=544) color=24'h456078;
        if(x_d>=590 && x_d<610 && y_d>=122 && y_d<142)
            color=run_state==1?24'h40dda5:run_state==2?24'hf4bc54:24'h66758a;
        if(x_d>=790 && x_d<844 && ((y_d>=202 && y_d<223)||(y_d>=274 && y_d<295))) begin
            slot=x_d>=826?2:x_d>=808?1:0;
            digit_x=x_d-(slot==2?826:slot==1?808:790);
            digit_y=y_d>=274?y_d-274:y_d-202;
            nx=digit_x>=15?5:digit_x>=12?4:digit_x>=9?3:digit_x>=6?2:digit_x>=3?1:0;
            ny=digit_y>=18?6:digit_y>=15?5:digit_y>=12?4:digit_y>=9?3:digit_y>=6?2:digit_y>=3?1:0;
            if(y_d>=274) glyph=digit(slot==0?ms_decimal[11:8]:slot==1?ms_decimal[7:4]:ms_decimal[3:0]);
            else glyph=digit(slot==0?z_decimal[11:8]:slot==1?z_decimal[7:4]:z_decimal[3:0]);
            if(nx<5 && glyph[34-ny*5-nx]) color=24'h62e4c7;
        end
        if(text_word[text_bit]) color=24'hd9e6f5;
        if(map_d && field_valid &&
           ((x_d==marker_x && y_d>=marker_y-5 && y_d<=marker_y+5) ||
            (y_d==marker_y && x_d>=marker_x-5 && x_d<=marker_x+5))) color=24'hffffff;
    end
    always @(posedge clk) begin
        if(!rst_n) begin red<=0;green<=0;blue<=0;de<=0;hs<=1;vs<=1;end
        else begin
            {red,green,blue}<=active_d?color:24'd0;de<=active_d;hs<=hs_d;vs<=vs_d;
        end
    end
endmodule
