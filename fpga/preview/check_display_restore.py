"""Offline checks for observer CDC and restored transmitter/display integration."""
import argparse
from pathlib import Path
import sys
from check_preview import simulate
from generate_assets import generate

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from fpga.board.build_calibration import load_restore
from desktop_app.model import Config,ArraySpec,focus_phases
from desktop_app.protocol import encode

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--calibration-file',required=True)
    p.add_argument('--iverilog',default='D:/pld/.diagnostics/review-2026-10-15/iverilog/app/bin/iverilog.exe')
    a=p.parse_args();compiler=Path(a.iverilog)
    packed,info=load_restore(a.calibration_file,50)
    folder=ROOT/'.runtime/display-restore/tests';folder.mkdir(parents=True,exist_ok=True);generate(folder)
    src=ROOT/'.runtime/display-restore/z50/src'
    rtl=list(src.glob('*.v'))
    simulate(compiler,folder,'tb_observer_cdc',r'''`timescale 1ns/1ps
module tb_observer_cdc;
reg sc=0,dc=0,sr=0,dr=0;always #10 sc=~sc;always #10.204 dc=~dc;
reg [31:0] n=1;wire [127:0] payload={n,~n,n^32'h5a71bdef,n*32'd3};wire [127:0] out;
preview_observer_cdc #(.WIDTH(128)) dut(.source_clk(sc),.source_rst_n(sr),.dest_clk(dc),.dest_rst_n(dr),.source_data(payload),.dest_data(out));
always @(negedge sc) if(sr)n<=n+1;
integer checked=0;reg [31:0] last=0;
always @(negedge dc) if(dr && out!=0) begin
 if(out[95:64]!==~out[127:96] || out[63:32]!==(out[127:96]^32'h5a71bdef) || out[31:0]!==out[127:96]*32'd3)
  $fatal(1,"torn clock-domain snapshot");
 if(out[127:96]<last)$fatal(1,"snapshot went backwards");
 last=out[127:96];checked=checked+1;
end
initial begin
 #75;sr=1;#37;dr=1;#50000;
 sr=0;dr=0;#200;last=0;sr=1;#45;dr=1;#50000;
 if(checked<3000)$fatal(1,"insufficient CDC coverage");
 $display("PASS atomic snapshots, different clock phases, repeated reset");$finish;
end
endmodule
''',rtl)
    cfg=Config(shape='POINT',cx_um=0,cy_um=0,z_um=50000,radius_um=0,level=1,mod_hz=0)
    fields={**cfg.wire(),**ArraySpec(8,8,10300).wire()}
    cmds=[encode('CMD',1,'HELLO'),encode('CMD',2,'MODE',value='REMOTE'),
          encode('CMD',3,'CONFIG',**fields),encode('CMD',4,'START'),encode('CMD',5,'STOP')]
    bounds=[0]
    for c in cmds:bounds.append(bounds[-1]+len(c))
    (folder/'commands.hex').write_text(''.join(f'{b:02x}\n' for c in cmds for b in c),encoding='ascii')
    expected=sum(v<<(8*i) for i,v in enumerate(info['expected_center_phases']))
    nominal=list(map(int,focus_phases(cfg,(0,0,50),ArraySpec(8,8,10300))))
    theoretical=sum((v*16)<<(10*i) for i,v in enumerate(nominal))
    bench=r'''`timescale 1ns/1ps
module tb_display_restore;
reg clk=0,pixel=0,rst=0,rx=1;always #10 clk=~clk;always #10.204 pixel=~pixel;
wire tx;wire [7:0] sd;wire sclk,latch,oe,clear;wire [2:0] led;
wire [188:0] snap,observed;wire source_rst;wire valid,physical;wire [63:0] physical_array;
wire signed [20:0] fx,fy;wire [31:0] z,frames;reg boundary=0;integer ticks=0;
board_top #(.CAL_RESTORE_ENABLE(1),.CAL_RESTORE_OFFSETS(512'hPACKED),.CAL_Z_UM(50000),.BAUD(1000000),.POR_MS(1),.STATE_MS(0)) transmitter(
 .clk(clk),.key_rst_n(rst),.key0_n(1'b1),.key1_n(1'b1),.uart_rx_pin(rx),.uart_tx_pin(tx),
 .sr_data(sd),.sr_clk(sclk),.sr_latch(latch),.sr_oe_n(oe),.sr_clear_n(clear),.led(led),.view_snapshot(snap),.view_reset_n(source_rst));
preview_observer_cdc cdc(.source_clk(clk),.source_rst_n(source_rst),.dest_clk(pixel),.dest_rst_n(source_rst),.source_data(snap),.dest_data(observed));
theory_preview_core #(.EXTERNAL_CONTROL(1),.GRID(8),.STEP_25UM(120),.MIN_25UM(-420)) display(
 .clk(pixel),.rst_n(source_rst),.key_next(1'b0),.key_play(1'b0),.uart_rx_pin(1'b1),.frame_boundary(boundary),
 .external_snapshot(observed),.field_read_addr(12'd0),.field_valid(valid),.displayed_x(fx),.displayed_y(fy),.displayed_z(z),.frame_number(frames),
 .physical_output(physical),.physical_array(physical_array));
always @(posedge pixel) begin ticks<=ticks+1;boundary<=ticks%1000==0;end
reg [7:0] bytes[0:TOTAL-1];integer i,j,k;
localparam [511:0] EXPECTED=512'hEXPECTED_CODES;
localparam [639:0] THEORY=640'hTHEORY_CODES;
task send(input integer first,last);
begin
 for(i=first;i<last;i=i+1) begin
  @(negedge clk);rx=0;repeat(50)@(negedge clk);
  for(j=0;j<8;j=j+1)begin rx=bytes[i][j];repeat(50)@(negedge clk);end
  rx=1;repeat(50)@(negedge clk);
 end
 repeat(150000)@(negedge clk);
end endtask
initial begin
 $readmemh("commands.hex",bytes);#100;rst=1;repeat(100000)@(negedge clk);
 if(!oe || transmitter.output_on)$fatal(1,"boot emitted");
 SEND0 SEND1 SEND2
 if(transmitter.output_on || !oe)$fatal(1,"configuration emitted");
 SEND3
 if(!transmitter.output_on || oe)$fatal(1,"transmitter did not start");
 wait(valid);@(negedge pixel);
 if(fx!==0 || fy!==0 || z!==50000 || display.phases!==THEORY)$fatal(1,"theory target/phase mismatch");
 for(k=0;k<64;k=k+1) if(transmitter.u_top.u_out.code[k]!==EXPECTED[8*k+:8])$fatal(1,"calibrated emitter phase mismatch");
 if(physical!==0 || physical_array!==0)$fatal(1,"observer gained emitter control");
 SEND4
 if(transmitter.output_on || !oe || valid)$fatal(1,"stop failed");
 repeat(250000)@(negedge clk);
 if(valid || transmitter.output_on || !oe)$fatal(1,"stale frame/emission returned after STOP");
 rst=0;repeat(20)@(negedge clk);rst=1;repeat(100000)@(negedge clk);
 if(transmitter.output_on || !oe || valid)$fatal(1,"reset restarted output");
 $display("PASS 50 mm calibrated output, ideal display, stop invalidation, reset mute");$finish;
end
initial begin #100000000;$fatal(1,"integration timeout");end
endmodule
'''.replace('PACKED',f'{packed:0128x}').replace('TOTAL',str(bounds[-1])).replace('EXPECTED_CODES',f'{expected:0128x}').replace('THEORY_CODES',f'{theoretical:0160x}')
    for i in range(5):bench=bench.replace(f'SEND{i}',f'send({bounds[i]},{bounds[i+1]});')
    simulate(compiler,folder,'tb_display_restore',bench,rtl)

if __name__=='__main__':main()
