`timescale 1ns/1ps
module tb_cal_startup;
    localparam CLK_HZ=1000000, BAUD=100000, BIT_CYC=10;
    reg clk=0;
    always #5 clk=~clk;
    reg rst_n=0, rx=1;
    wire tx,on,released;
    wire [63:0] pos,neg;
    wire [511:0] saved;
    hap2_cal_startup #(.CLK_HZ(CLK_HZ),.BAUD(BAUD),.ROWS(8),.COLS(8),.PITCH_UM(10300),.Z_UM(70000),.SOURCE(1),
        .SETTLE_MS(3),.REPEAT_MS(3),.TIMEOUT_MS(500)) dut (
        .clk(clk),.rst_n(rst_n),.rx_line(rx),.boot_id(32'h12345678),.tx_line(tx),
        .out_pos(pos),.out_neg(neg),.output_on(on),.released(released),.saved_offsets(saved));
    wire received;
    wire [255:0] packet;
    hap2_cal_packet #(.CLK_HZ(CLK_HZ),.BAUD(BAUD)) monitor (
        .clk(clk),.rst_n(rst_n),.rx_line(tx),.tx_line(),.rx_valid(received),.rx_body(packet),
        .tx_start(1'b0),.tx_body(256'd0),.tx_busy(),.tx_done());
    reg [3:0] trim_blocks=0;
    reg saw_trim=0,saw_release=0,saw_reference=0,saw_pair=0;
    always @(posedge clk) begin
        if(rst_n) begin
            if(!on && (pos!=0 || neg!=0)) $fatal(1,"muted output");
            if(dut.stage==0 && (pos!=0 || neg!=0)) $fatal(1,"noise drives output");
            if((pos & ~dut.mask)!=0 || (neg & ~dut.mask)!=0) $fatal(1,"inactive channel driven");
            if(dut.stage==1 && pos[0]) saw_reference<=1;
            if(dut.stage>=3 && pos!=0) saw_pair<=1;
            if(received && packet[31:24]==8'h81) begin
                for(integer k=0;k<16;k=k+1)
                    if(packet[128+8*k +: 8]!=((packet[127:96]+k)*9)%64) $fatal(1,"trim UART readback channel %0d",k);
                trim_blocks[packet[101:100]]<=1;
                if(packet[127:96]==48) saw_trim<=1;
            end
            if(received && packet[31:24]==8'h82) saw_release<=1;
        end
    end
    function [15:0] crc_byte;
        input [15:0] crc; input [7:0] b;
        reg [15:0] c; integer j;
        begin
            c=crc^{b,8'd0};
            for(j=0;j<8;j=j+1) c=c[15] ? (c<<1)^16'h1021 : c<<1;
            crc_byte=c;
        end
    endfunction
    task byte_out;
        input [7:0] b;
        integer j;
        begin
            @(negedge clk); rx=0; repeat(BIT_CYC) @(negedge clk);
            for(j=0;j<8;j=j+1) begin rx=b[j]; repeat(BIT_CYC) @(negedge clk); end
            rx=1; repeat(BIT_CYC+2) @(negedge clk);
        end
    endtask
    task send;
        input [7:0] kind;
        input [31:0] boot,token,value,quality,a2,a3,a4;
        input corrupt;
        reg [255:0] body; reg [15:0] crc; integer j;
        begin
            body=0; body[15:0]=16'h5aa5; body[23:16]=2; body[31:24]=kind;
            body[63:32]=boot; body[95:64]=token; body[127:96]=value; body[159:128]=quality; body[191:160]=a2; body[223:192]=a3; body[255:224]=a4;
            crc=16'hffff;
            for(j=0;j<32;j=j+1) begin byte_out(body[8*j +: 8]); crc=crc_byte(crc,body[8*j +: 8]); end
            byte_out(crc[7:0]); byte_out(crc[15:8]^{7'd0,corrupt});
            repeat(8) @(negedge clk);
        end
    endtask
    real p,angle;
    integer n,k;
    reg [127:0] imported_block;
    reg [31:0] old_token;
    initial begin
        repeat(5) @(negedge clk); rst_n=1;
        repeat(2500) @(negedge clk);
        send(7,32'h12345678,0,0,0,70000,30,0,0);
        wait(dut.phase_ready);
        send(1,32'h12345677,0,0,0,0,0,0,0); // stale boot
        send(1,32'h12345678,0,0,0,0,0,0,0); // wrong source
        send(1,32'h12345678,0,0,0,0,0,0,1); // wrong CRC
        if(dut.attempted || on || pos!=0) $fatal(1,"invalid READY started calibration");
        byte_out(8'ha5); byte_out(8'h5a); byte_out(1); // partial packet must expire
        repeat(51000) @(negedge clk);
        // Reverify a full 64-channel candidate through real UART packets.
        // Exhaustive solve sequencing is covered by tb_cal_modes.
        for(n=0;n<4;n=n+1) begin
            for(k=0;k<16;k=k+1) imported_block[8*k +: 8]=((n*16+k)*9)%64;
            send(8,32'h12345678,0,n*16,imported_block[31:0],imported_block[63:32],
                 imported_block[95:64],imported_block[127:96],0);
            wait(dut.pending_type==0 && !dut.packet_busy);
        end
        send(1,32'h12345678,0,1,1,0,0,0,0);
        if(!dut.busy || !dut.attempted) $fatal(1,"did not auto-start on READY");
        old_token=0;
        for(n=0;n<384;n=n+1) begin
            wait(dut.state==2);
            if(dut.token!=old_token+1) $fatal(1,"token sequence");
            if(n==0) begin
                send(2,32'h12345678,99,2,1,0,0,0,0);
                if(dut.state!=2 || dut.token!=1) $fatal(1,"wrong token advanced state");
                send(2,32'h12345678,1,2,1,0,1,0,0);
                if(dut.state!=2 || dut.token!=1) $fatal(1,"wrong source advanced state");
                send(2,32'h12345678,1,2,1,1,999,0,0);
                if(dut.state!=2 || dut.token!=1) $fatal(1,"wrong condition advanced state");
            end
            angle=((dut.channel*9)%64-dut.trial[8*dut.channel +: 8])*6.283185307179586/64.0;
            if(dut.stage==0) p=2;
            else if(dut.stage==1) p=10002;
            else if(dut.stage==2) p=6402;
            else p=16402+16000*$cos(angle);
            old_token=dut.token;
            send(2,32'h12345678,old_token,$rtoi(p+0.5),1,1,(dut.stage<<8)|dut.channel,dut.repeat_number,0);
            if(n==0) begin
                send(2,32'h12345678,old_token,2,1,1,1,0,0);
                if(dut.token!=2 || dut.repeat_number!=1) $fatal(1,"duplicate submission advanced state");
            end
        end
        wait(dut.state==4);
        if(saved[15:8]!=9 || !saw_reference || !saw_pair || released) $fatal(1,"run/output integration");
        for(n=0;n<4;n=n+1) begin
            send(5,32'h12345678,0,n*16,0,0,0,0,0);
            wait(trim_blocks[n]);
        end
        wait(saw_trim);
        send(6,32'h12345678,old_token,0,0,0,0,0,0);
        wait(released);
        repeat(30) @(negedge clk);
        if(!saw_release || on || pos!=0 || saved[15:8]!=9) $fatal(1,"release must retain correction and mute");
        $display("PASS startup UART: readiness/boot/CRC/partial timeout/token/measurement/trim/release and channel masks");
        $finish;
    end
    initial begin #400000000; $fatal(1,"test deadline"); end
endmodule
