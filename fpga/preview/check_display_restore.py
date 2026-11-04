"""Offline checks for observer CDC and calibrated/uncalibrated display integration."""
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


def startup_bench(z_um, level, source):
    """Real CAL2 packets and read-only observer; never submit a valid READY."""
    return r'''`timescale 1ns/1ps
module tb_startup_display;
reg clk=0,pixel=0,rst=0,rx=1;always #10 clk=~clk;always #10.204 pixel=~pixel;
wire tx;wire [7:0] sd;wire sclk,latch,oe,clear;wire [2:0] led;
wire [188:0] snap,observed;wire source_rst;wire valid,physical;wire [63:0] physical_array;
wire signed [20:0] fx,fy;wire [31:0] z;reg boundary=0;integer ticks=0;
board_top #(.CAL_RESTORE_ENABLE(0),.CAL_Z_UM(DEFAULT_Z),.CAL_LEVEL(DEFAULT_LEVEL),
 .BAUD(1000000),.POR_MS(1),.STATE_MS(0)) transmitter(
 .clk(clk),.key_rst_n(rst),.key0_n(1'b0),.key1_n(1'b0),.uart_rx_pin(rx),.uart_tx_pin(tx),
 .sr_data(sd),.sr_clk(sclk),.sr_latch(latch),.sr_oe_n(oe),.sr_clear_n(clear),.led(led),
 .view_snapshot(snap),.view_reset_n(source_rst));
preview_observer_cdc cdc(.source_clk(clk),.source_rst_n(source_rst),.dest_clk(pixel),.dest_rst_n(source_rst),.source_data(snap),.dest_data(observed));
theory_preview_core #(.EXTERNAL_CONTROL(1),.GRID(8),.STEP_25UM(120),.MIN_25UM(-420)) display(
 .clk(pixel),.rst_n(source_rst),.key_next(1'b0),.key_play(1'b0),.uart_rx_pin(1'b1),.frame_boundary(boundary),
 .external_snapshot(observed),.field_read_addr(12'd0),.field_valid(valid),.displayed_x(fx),.displayed_y(fy),.displayed_z(z),
 .physical_output(physical),.physical_array(physical_array));
wire received;wire [255:0] packet;
hap2_cal_packet #(.CLK_HZ(50000000),.BAUD(1000000)) monitor(
 .clk(clk),.rst_n(source_rst),.rx_line(tx),.tx_line(),.rx_valid(received),.rx_body(packet),
 .tx_start(1'b0),.tx_body(256'd0),.tx_busy(),.tx_done());
reg saw_status=0,saw_setup=0;
reg allow_measurement=0;
// Accelerate only settle/repeat delays; protocol, output gates and solver stay real.
defparam transmitter.u_top.startup_calibration.u_startup.SETTLE_MS=3;
defparam transmitter.u_top.startup_calibration.u_startup.REPEAT_MS=3;
always @(posedge clk) if(received) begin
 if(packet[31:24]==8'h80) begin
  saw_status<=1;
  if(packet[127:96]!==0 || packet[134]!==1'b0 || packet[130]!==SOURCE_CODE)
   $fatal(1,"startup status/source/output flag mismatch");
 end
 if(packet[31:24]==8'h83) saw_setup<=1;
end
always @(posedge pixel) begin ticks<=ticks+1;boundary<=ticks%1000==0;end
always @(negedge clk) if(source_rst) begin
 if((!allow_measurement && (!oe || transmitter.output_on)) || transmitter.u_top.cal_released || transmitter.u_top.business_rst_n)
  $fatal(1,"startup/invalid command released business or emission");
 if(valid || physical || physical_array!=0 || snap[0] || snap[18:3]!=0)
  $fatal(1,"CAL2 observer fabricated a full-array field or gained output control");
end
function [15:0] crc_byte(input [15:0] crc,input [7:0] value);
 reg [15:0] c;integer j;
 begin c=crc^{value,8'd0};for(j=0;j<8;j=j+1)c=c[15]?(c<<1)^16'h1021:c<<1;crc_byte=c;end
endfunction
task byte_out(input [7:0] value);
 integer j;
 begin
  @(negedge clk);rx=0;repeat(50)@(negedge clk);
  for(j=0;j<8;j=j+1)begin rx=value[j];repeat(50)@(negedge clk);end
  rx=1;repeat(50)@(negedge clk);
 end
endtask
task send(input [7:0] kind,input [31:0] a0,a1,a2,a3,a4);
 reg [255:0] body;reg [15:0] crc;integer j;
 begin
  body=0;body[15:0]=16'h5aa5;body[23:16]=2;body[31:24]=kind;
  body[63:32]=transmitter.boot_q;body[127:96]=a0;body[159:128]=a1;
  if(kind==2)body[95:64]=transmitter.u_top.startup_calibration.u_startup.token;
  body[191:160]=a2;body[223:192]=a3;body[255:224]=a4;crc=16'hffff;
  for(j=0;j<32;j=j+1)begin byte_out(body[8*j+:8]);crc=crc_byte(crc,body[8*j+:8]);end
  byte_out(crc[7:0]);byte_out(crc[15:8]);repeat(10000)@(negedge clk);
 end
endtask
initial begin
 #100;rst=1;wait(source_rst);repeat(100000)@(negedge clk);
 if(!saw_status || observed[2:1]!==3 || z!==DEFAULT_Z)$fatal(1,"CAL2 waiting state not observed");
 send(6,0,0,0,0,0); // RELEASE before any successful measurement must fail.
 send(3,0,0,0,0,0); // PING is a read/keepalive action, never a GO command.
 send(7,5000,-2500,70000,20,0); // SETUP alone still cannot emit.
 wait(transmitter.u_top.startup_calibration.u_startup.phase_ready);
 repeat(10000)@(negedge clk);
 if(!saw_setup || fx!==10000 || fy!==-5000 || z!==70000 || observed[2:1]!==3)
  $fatal(1,"read-only CAL2 target observation mismatch");
 send(1,WRONG_SOURCE,0,0,0,0); // Wrong acquisition source is not accepted as READY.
 repeat(100000)@(negedge clk);
 if(transmitter.u_top.startup_calibration.u_startup.attempted)$fatal(1,"wrong READY started measurement");
 allow_measurement=1;
 send(1,SOURCE_VALUE,0,0,0,0);
 // Supply three valid background windows, then observe the real reference
 // emitter test. The display must show output while still withholding a field.
 repeat(3) begin
  wait(transmitter.u_top.startup_calibration.u_startup.state==2);
  send(2,2,1,SOURCE_VALUE,
    (transmitter.u_top.startup_calibration.u_startup.stage<<8)|transmitter.u_top.startup_calibration.u_startup.channel,
    transmitter.u_top.startup_calibration.u_startup.repeat_number);
 end
 wait(transmitter.u_top.startup_calibration.u_startup.stage==1 && transmitter.output_on);
 repeat(10000)@(negedge clk);
 if(oe || observed[2:1]!==1 || valid)$fatal(1,"CAL2 output status hidden or fabricated field shown");
 send(4,0,0,0,0,0);
 repeat(10000)@(negedge clk);allow_measurement=0;
 if(!oe || observed[2:1]!==3)$fatal(1,"ABORT did not mute or return to calibration display");
 rst=0;repeat(20)@(negedge clk);rst=1;wait(source_rst);repeat(100000)@(negedge clk);
 if(z!==DEFAULT_Z || observed[2:1]!==3)$fatal(1,"reset lost startup waiting/target");
 $display("PASS CAL2 boot mute, held keys, premature RELEASE rejection, SETUP observation, wrong READY, output observation, ABORT and reset mute");$finish;
end
initial begin #60000000;$fatal(1,"startup display timeout");end
endmodule
'''.replace('DEFAULT_Z',str(z_um)).replace('DEFAULT_LEVEL',str(level)).replace(
        'SOURCE_CODE',"1'b1" if source=='adc' else "1'b0").replace('WRONG_SOURCE','0' if source=='adc' else '1').replace(
        'SOURCE_VALUE','1' if source=='adc' else '0')


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--calibration-file')
    mode.add_argument('--uncalibrated',action='store_true')
    mode.add_argument('--startup-calibration',choices=('adc','scope'))
    p.add_argument('--calibration-z-mm',type=int,choices=(50,70,100),default=50,
                   help='Measured reference height; must match the prepared build')
    p.add_argument('--calibration-level',type=int,choices=range(1,101),default=30,
                   help='Recorded measurement level, not playback level; current release uses 20')
    p.add_argument('--calibration-reference',type=int,choices=range(64),default=0)
    p.add_argument('--output-root',type=Path,help='Root used by build_display_restore.py')
    p.add_argument('--iverilog',default='D:/pld/.diagnostics/review-2026-10-15/iverilog/app/bin/iverilog.exe')
    a=p.parse_args(argv)
    if a.uncalibrated and (a.calibration_z_mm!=50 or a.calibration_level!=30 or a.calibration_reference!=0):
        p.error('Calibration height/level/reference apply only to --calibration-file')
    return a


def main(argv=None):
    a=parse_args(argv);compiler=Path(a.iverilog)
    z_mm=a.calibration_z_mm
    z_um=z_mm*1000
    packed,info=(0,None) if a.uncalibrated or a.startup_calibration else load_restore(
        a.calibration_file,z_mm,a.calibration_reference,a.calibration_level)
    output_root=a.output_root.resolve() if a.output_root else ROOT/'.runtime/display-restore'
    folder=output_root/('uncalibrated-tests' if a.uncalibrated else 'tests')
    folder.mkdir(parents=True,exist_ok=True);generate(folder,preview_only=False)
    src=output_root/('uncalibrated' if a.uncalibrated else
                    f'startup_{a.startup_calibration}_z{z_mm}' if a.startup_calibration else f'z{z_mm}')/'src'
    rtl=list(src.glob('*.v'))
    if not rtl:raise ValueError('Prepare matching display build sources first: '+str(src))
    if a.startup_calibration:
        simulate(compiler,folder,'tb_startup_display',startup_bench(z_um,a.calibration_level,a.startup_calibration),rtl)
        return
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
    cfg=Config(shape='POINT',cx_um=0,cy_um=0,z_um=z_um,radius_um=0,level=1,mod_hz=0)
    fields={**cfg.wire(),**ArraySpec(8,8,10300).wire()}
    cmds=[encode('CMD',1,'HELLO'),encode('CMD',2,'MODE',value='REMOTE'),
          encode('CMD',3,'CONFIG',**fields),encode('CMD',4,'START'),encode('CMD',5,'STOP')]
    bounds=[0]
    for c in cmds:bounds.append(bounds[-1]+len(c))
    (folder/'commands.hex').write_text(''.join(f'{b:02x}\n' for c in cmds for b in c),encoding='ascii')
    nominal=list(map(int,focus_phases(cfg,(0,0,z_mm),ArraySpec(8,8,10300))))
    expected=sum(v<<(8*i) for i,v in enumerate(nominal if a.uncalibrated else info['expected_center_phases']))
    theoretical=sum((v*16)<<(10*i) for i,v in enumerate(nominal))
    bench=r'''`timescale 1ns/1ps
module tb_display_restore;
reg clk=0,pixel=0,rst=0,rx=1;always #10 clk=~clk;always #10.204 pixel=~pixel;
wire tx;wire [7:0] sd;wire sclk,latch,oe,clear;wire [2:0] led;
wire [188:0] snap,observed;wire source_rst;wire valid,physical;wire [63:0] physical_array;
wire signed [20:0] fx,fy;wire [31:0] z,frames;reg boundary=0;integer ticks=0;
board_top #(.CAL_RESTORE_ENABLE(RESTORE_ENABLED),.CAL_RESTORE_OFFSETS(512'hPACKED),.CAL_Z_UM(TARGET_Z_UM),.BAUD(1000000),.POR_MS(1),.STATE_MS(0)) transmitter(
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
 if(fx!==0 || fy!==0 || z!==TARGET_Z_UM || display.phases!==THEORY)$fatal(1,"theory target/phase mismatch");
 for(k=0;k<64;k=k+1) if(transmitter.u_top.u_out.code[k]!==EXPECTED[8*k+:8])$fatal(1,"calibrated emitter phase mismatch");
 if(physical!==0 || physical_array!==0)$fatal(1,"observer gained emitter control");
 SEND4
 if(transmitter.output_on || !oe || valid)$fatal(1,"stop failed");
 repeat(250000)@(negedge clk);
 if(valid || transmitter.output_on || !oe)$fatal(1,"stale frame/emission returned after STOP");
 rst=0;repeat(20)@(negedge clk);rst=1;repeat(100000)@(negedge clk);
 if(transmitter.output_on || !oe || valid)$fatal(1,"reset restarted output");
 $display("PASS target z=%0d um configured output, ideal display, stop invalidation, reset mute",TARGET_Z_UM);$finish;
end
initial begin #100000000;$fatal(1,"integration timeout");end
endmodule
'''.replace('RESTORE_ENABLED',str(int(not a.uncalibrated))).replace('PACKED',f'{packed:0128x}').replace('TOTAL',str(bounds[-1])).replace('EXPECTED_CODES',f'{expected:0128x}').replace('THEORY_CODES',f'{theoretical:0160x}').replace('TARGET_Z_UM',str(z_um))
    for i in range(5):bench=bench.replace(f'SEND{i}',f'send({bounds[i]},{bounds[i+1]});')
    simulate(compiler,folder,'tb_display_restore',bench,rtl)

if __name__=='__main__':main()
