"""Check static display numerics, stale-frame rejection and actual video pixels offline."""
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from check_preview import simulate
from generate_assets import generate

ROOT = Path(__file__).resolve().parents[2]


def main():
    folder = ROOT / '.runtime/display-alignment-tests'
    folder.mkdir(parents=True, exist_ok=True)
    generate(folder)
    compiler = Path('D:/pld/.diagnostics/review-2026-10-15/iverilog/app/bin/iverilog.exe')
    rtl = [*(ROOT / 'fpga/rtl').glob('*.v'), *(ROOT / 'fpga/preview').glob('*.v')]
    fixture = json.loads((ROOT / 'fpga/tb/vectors/pressure_frame_325.json').read_text())
    codes = fixture['phase_codes']
    packed = sum(v << (8*i) for i, v in enumerate(codes))
    bench = r'''`timescale 1ns/1ps
module tb_display_alignment;
reg clk=0;always #10 clk=~clk;
reg rst=0;wire [767:0] snap;
reg [15:0] rev=1;reg [1:0] run=1;reg active=1;
reg [511:0] table_codes=512'hCODES;
wire valid;wire [31:0] frames;reg boundary=0;
wire displayed_static,physical;wire [63:0] waves;wire [31:0] level,speed,modulation,carrier;
wire sm,oe,lm;integer t=0,k,fd;reg [31:0] old_frame;
assign snap={table_codes,1'b1,active,1'b0,32'd1000,32'd0,
               21'd0,21'd0,32'd50000,32'd40000,32'd64,32'd100,rev,run,active};
always @(posedge clk) begin t<=t+1;boundary<=t%1000==0;end
theory_preview_core #(.EXTERNAL_CONTROL(1),.GRID(16),.STEP_25UM(480),.MIN_25UM(-3600)) dut(
 .clk(clk),.rst_n(rst),.key_next(1'b0),.key_play(1'b0),.uart_rx_pin(1'b1),
 .external_snapshot(snap),.frame_boundary(boundary),.field_read_addr(12'd0),
 .field_valid(valid),.frame_number(frames),.displayed_static(displayed_static),
 .physical_output(physical),.physical_array(waves),.status_static(sm),.status_output(oe),
 .status_local(lm),.status_level(level),.status_repeat(speed),.status_mod(modulation),.status_carrier(carrier));
always @(negedge clk) if(rst && (physical!==0 || waves!==0)) $fatal(1,"observer drove output");
initial begin
 repeat(5)@(negedge clk);rst=1;
 wait(valid);@(negedge clk);
 if(!displayed_static || !sm || !oe || lm || level!=100 || speed!=1000 || modulation!=0 || carrier!=40000)
   $fatal(1,"status not aligned");
 for(k=0;k<64;k=k+1) if(dut.phases[10*k+:10]!=={table_codes[8*k+:6],4'b0})
   $fatal(1,"nominal phase table mismatch");
 fd=$fopen("static_field.txt","w");
 for(k=0;k<256;k=k+1) begin
   if(^dut.image[{dut.front,k[11:0]}]===1'bx)$fatal(1,"unknown field pixel");
   $fwrite(fd,"%d\n",dut.image[{dut.front,k[11:0]}]);
 end
 $fclose(fd);
 // Abandon an in-flight frame; quick restart must not resurrect its old table.
 wait(dut.state==5);@(negedge clk);run=0;active=0;
 repeat(3)@(negedge clk);
 if(valid || oe)$fatal(1,"stop status stale");
 rev=2;table_codes=0;run=1;active=1;old_frame=frames;
 wait(frames!=old_frame);@(negedge clk);
 if(valid)$fatal(1,"obsolete image published after restart");
 wait(valid);@(negedge clk);
 if(dut.phases!==640'd0)$fatal(1,"new static table not applied");
 run=2;active=0;repeat(3)@(negedge clk);
 if(oe || !valid)$fatal(1,"pause status lost or retained output");
 run=0;repeat(3)@(negedge clk);if(valid)$fatal(1,"stop retained field");
 $display("PASS static field, status, stop/restart invalidation, pause and observer isolation");$finish;
end
initial begin #500000000;$fatal(1,"timeout");end
endmodule
'''.replace('CODES', f'{packed:0128x}')
    if '--video-only' not in sys.argv:
        simulate(compiler, folder, 'tb_display_alignment', bench, rtl)
    values = np.loadtxt(folder / 'static_field.txt').reshape(16, 16)
    axis = np.arange(16)*12000-90000
    xx, yy = np.meshgrid(axis, axis)
    pressure = np.zeros_like(xx, dtype=complex)
    for i, code in enumerate(codes):
        ex, ey = (i % 8-3.5)*10300, (i // 8-3.5)*10300
        distance = np.sqrt((xx-ex)**2+(yy-ey)**2+50000**2)
        pressure += np.exp(1j*(2*math.pi*distance*40000/343e6+code*2*math.pi/64))/distance
    expected = np.clip(np.abs(pressure)/(64/50000)*256, 0, 255)
    error = float(np.max(np.abs(values-expected)))
    assert error < 4, error
    (folder / 'render_field.hex').write_text(''.join(f'{int(v):02x}\n' for v in values.repeat(4, 0).repeat(4, 1).flat))
    video = r'''module tb_display_video;
reg clk=0;always #10 clk=~clk;
reg rst=0;wire [11:0] addr;reg [7:0] value;reg [7:0] mem[0:4095];
wire [7:0] r,g,b;wire de,hs,vs,boundary;integer out,n;
reg [31:0] speed=200000,modulation=1000;
preview_video dut(.clk(clk),.rst_n(rst),.field_value(value),.field_valid(1'b1),
 .focus_x(21'd0),.focus_y(21'd0),.z_um(32'd50000),.field_cycles(32'd2035818),
 .frame_number(32'd1),.run_state(2'd1),.static_mode(1'b1),.marker_visible(1'b0),
 .output_enabled(1'b1),.local_mode(1'b0),.level(32'd100),.repeat_millihz(speed),
 .mod_hz(modulation),.carrier_hz(32'd40000),.frame_boundary(boundary),.field_addr(addr),
 .red(r),.green(g),.blue(b),.de(de),.hs(hs),.vs(vs));
always @(posedge clk)value<=mem[addr];
initial begin
 $readmemh("render_field.hex",mem);repeat(5)@(negedge clk);rst=1;
 repeat(1000)@(negedge clk);
 if(dut.repeat_decimal!==24'h200000 || dut.mod_decimal!==24'h001000)$fatal(1,"frequency range saturated");
 speed=1000;modulation=0;
 wait(boundary);@(negedge clk);
 if(dut.level_decimal!==24'h000100 || dut.carrier_decimal!==24'h040000 || dut.z_decimal!==24'h000050)
   $fatal(1,"decimal metadata mismatch");
 out=$fopen("screen.ppm","wb");$fwrite(out,"P6\n1024 600\n255\n");n=0;
 repeat(1312*624) begin
  @(negedge clk);if(de)begin
   if(^{r,g,b}===1'bx)$fatal(1,"unknown RGB");
   $fwrite(out,"%c%c%c",r,g,b);n=n+1;
  end
 end
 if(n!=1024*600)$fatal(1,"raster count");
 $fclose(out);$display("PASS static raster and decimal metadata");$finish;
end
endmodule
'''
    simulate(compiler, folder, 'tb_display_video', video, rtl)
    with Image.open(folder / 'screen.ppm') as picture:
        picture.save(folder / 'screen.png')
    report = dict(static_frame=325, numerical_samples=256, raster_source_grid=16, max_error_codes=error, hardware_connected=False)
    (folder / 'result.json').write_text(json.dumps(report, indent=2))
    print(report)


if __name__ == '__main__':
    main()
