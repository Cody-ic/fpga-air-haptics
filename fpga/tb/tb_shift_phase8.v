`timescale 1ns/1ps
// Check geometry and actual serialized edge phases, including an off-centre
// receiver so a Y mirror cannot accidentally pass a symmetric centre test.
module tb_shift_phase8;
    reg clk=0,rst_n=0,configure=0,enable=0;
    always #10 clk=~clk;
    wire done,sel,valid;
    reg [7:0] addr=0;
    wire [7:0] code,read_addr,out_code;
    wire [63:0] waves;
    hap2_phase #(.ROWS(8),.COLS(8),.PITCH_UM(10300),.PIPE(8)) phase (
        .clk(clk),.rst_n(rst_n),.cfg_carrier_hz(32'd40000),.cfg_phase_steps(32'd64),
        .cfg_z_um(32'd70000),.cfg_change(configure),.focus_x(21'sd22000),.focus_y(-21'sd12000),
        .start(1'b0),.busy(),.done(done),.sweep_cnt(),.pub_sel(sel),.pub_valid(valid),
        .rd_sel(sel),.rd_addr(addr),.rd_data(code),.rd2_addr(read_addr),.rd2_data(out_code),
        .pub_fx(),.pub_fy(),.pub_fz_um());
    hap2_out #(.ROWS(8),.COLS(8),.CLK_HZ(50000000),.ACC_BITS(32)) output_stage (
        .clk(clk),.rst_n(rst_n),.cfg_carrier_hz(32'd40000),.cfg_phase_steps(32'd64),
        .cfg_change(configure),.cfg_level(32'd30),.cfg_mod_hz(32'd0),.enable(enable),
        .tbl_new(done),.tbl_addr(read_addr),.tbl_data(out_code),.out_pos(waves),.out_neg());
    wire [7:0] data;
    wire srclk,latch,oe_n,clear_n;
    hap2_shift8 shift(.clk(clk),.rst_n(rst_n),.enable(enable),.waves(waves),
        .sr_data(data),.sr_clk(srclk),.sr_latch(latch),.sr_oe_n(oe_n),.sr_clear_n(clear_n));
    reg [7:0] shifts[0:7];
    reg [63:0] physical=0;
    wire [63:0] driven=oe_n ? 64'd0 : physical;
    always @(posedge srclk)
        for(integer lane=0;lane<8;lane=lane+1) shifts[lane]<={shifts[lane][6:0],data[lane]};
    always @(posedge latch)
        for(integer lane=0;lane<8;lane=lane+1) physical[lane*8 +: 8]<=shifts[lane];
    integer codes[0:63];
    time first_edge[0:63];
    time last_edge[0:63];
    integer edge_count[0:63];
    reg measuring=0;
    genvar ch;
    generate for(ch=0;ch<64;ch=ch+1) begin : monitor
        always @(posedge driven[(7-ch/8)*8+ch%8]) begin
            if(measuring) begin
                if(edge_count[ch]==0) first_edge[ch]=$time;
                last_edge[ch]=$time;
                edge_count[ch]=edge_count[ch]+1;
            end
        end
    end endgenerate
    integer i,expected,diff;
    real x,y,distance,turns,edge_error,period;
    initial begin
        for(i=0;i<64;i=i+1) edge_count[i]=0;
        repeat(4) @(negedge clk); rst_n=1; configure=1;
        @(negedge clk); configure=0;
        wait(done);
        for(i=0;i<64;i=i+1) begin
            @(negedge clk); addr=i;
            repeat(2) @(negedge clk); codes[i]=code;
            x=(i%8-3.5)*10300-11000;
            y=(i/8-3.5)*10300+6000;
            distance=$sqrt(x*x+y*y+70000.0*70000.0);
            turns=distance*40000.0/343000000.0;
            expected=$rtoi((1.0-(turns-$floor(turns)))*64+0.5)%64;
            diff=(codes[i]-expected+64)%64;
            if(diff!=0) $fatal(1,"70mm off-centre phase channel=%0d got=%0d expected=%0d",i,codes[i],expected);
        end
        repeat(200) @(negedge clk); enable=1;
        repeat(2500) @(negedge clk); measuring=1;
        repeat(25000) @(negedge clk); measuring=0;
        for(i=0;i<64;i=i+1) begin
            if(edge_count[i]<19) $fatal(1,"missing carrier pulses %0d",i);
            period=real'(last_edge[i]-first_edge[i])/(edge_count[i]-1);
            if(period<24980 || period>25020) $fatal(1,"carrier period %f",period);
            // Signed conversion avoids time's unsigned subtraction wrapping.
            edge_error=real'(first_edge[i])-real'(first_edge[0])-(codes[i]-codes[0])*25000.0/64;
            while(edge_error>12500) edge_error=edge_error-25000;
            while(edge_error< -12500) edge_error=edge_error+25000;
            if(edge_error>400 || edge_error< -400) $fatal(1,"serialized relative phase %0d error=%f ns",i,edge_error);
        end
        $display("PASS 8x8/10.3mm/70mm: all 64 off-centre theoretical phases and physical 40kHz edges within 400ns quantization bound");
        $finish;
    end
    initial begin #1000000; $fatal(1,"deadline"); end
endmodule
