"""Numerical, video and path integration checks with no board attached."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import numpy as np
from PIL import Image
from generate_assets import generate

ROOT=Path(__file__).resolve().parents[2]


def simulate(compiler,folder,name,bench,rtl):
    source=folder/f"{name}.v"
    source.write_text(bench,encoding="utf-8")
    program=folder/f"{name}.vvp"
    with (folder/f"{name}.log").open("w",encoding="utf-8") as log:
        for cmd in ([str(compiler),"-g2012","-Wall","-s",name,"-o",str(program),*map(str,rtl),str(source)],
                    [str(compiler.with_name("vvp.exe" if os.name=="nt" else "vvp")),str(program)]):
            result=subprocess.run(cmd,cwd=folder,env=dict(os.environ,TMP=str(folder),TMPDIR=str(folder)),
                                  stdout=log,stderr=subprocess.STDOUT,timeout=600)
            if result.returncode:
                raise RuntimeError(f"{name} failed: {folder/f'{name}.log'}")
    print(f"PASS {name}",flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iverilog",default="D:/pld/.diagnostics/review-2026-10-15/iverilog/app/bin/iverilog.exe")
    parser.add_argument("--part",choices=("field","video","integration","animation","tmds","reset","all"),default="all")
    parser.add_argument("--primlib",default="D:/Gowin/Gowin_V1.9.12.04_x64/IDE/simlib/gw5a/prim_sim.v")
    parser.add_argument("--grid",type=int,choices=(32,64),default=32)
    args=parser.parse_args()
    compiler=Path(args.iverilog)
    folder=ROOT/".runtime/theory-preview/tests"
    folder.mkdir(parents=True,exist_ok=True)
    generate(folder)
    rtl=[*(ROOT/"fpga/rtl").glob("*.v"),*(ROOT/"fpga/preview").glob("*.v")]
    if args.part in ("all","reset"):
        # Verify the RTL reset driver together with the installed vendor serializer.
        primitive=Path(args.primlib).read_text(encoding="utf-8",errors="replace")
        primitive_file=folder/"reset_primitive.v"
        serializer_model=re.search(r"module OSER10\s*\(.*?endmodule",primitive,re.S)
        if serializer_model is None:raise RuntimeError("OSER10 primitive model missing")
        reset_flop_model=re.search(r"module DFFSE\s*\(.*?endmodule",primitive,re.S)
        if reset_flop_model is None:raise RuntimeError("DFFSE primitive model missing")
        primitive_file.write_text(serializer_model.group()+"\n"+reset_flop_model.group()+
                                 "\nmodule reset_global; reg GSRO=1; endmodule\n",encoding="utf-8")
        simulate(compiler,folder,"tb_serializer_reset",'''module tb_serializer_reset;
reset_global GSR();
reg clk=0,request=1;always #2.041 clk=~clk;
wire [3:0] reset;reg [3:0] expected=4'hf;integer checks=0;
preview_serializer_reset dut(.pixel_clk(clk),.request(request),.reset(reset));
always @(posedge clk) begin
 expected={4{request}};#0.1;
 if(reset!==expected) $fatal(1,"serializer reset latency/init mismatch");
 checks=checks+1;
end
initial begin
 #1;if(reset!==4'hf) $fatal(1,"serializer reset not asserted on configuration");
 #19.3;request=0;#33.7;request=1;#12.8;request=0;
 #25.9;request=1;#8.8;request=0;#50;
 if(checks<30 || reset!==0) $fatal(1,"incomplete reset check");
 $display("PASS serializer reset INIT, registered latency, repeated requests");$finish;
end
endmodule
''',rtl+[primitive_file])
        simulate(compiler,folder,"tb_serializer",'''module tb_serializer;
reset_global GSR();
reg fast=0,pixel=0,request=1;
always #2.041 fast=~fast;
always #10.205 pixel=~pixel;
wire [3:0] reset;reg [9:0] word=0;wire [9:0] symbols[0:3];wire [3:0] q;
assign symbols[0]=word;assign symbols[1]=~word;
assign symbols[2]=word^10'h155;assign symbols[3]=10'b1111100000;
always @(posedge pixel) word<=word+1'b1;
preview_serializer_reset reset_driver(.pixel_clk(pixel),.request(request),.reset(reset));
genvar g;
generate for(g=0;g<4;g=g+1) begin:lanes
 OSER10 ser(.Q(q[g]),.PCLK(pixel),.FCLK(fast),.RESET(reset[g]),
 .D0(symbols[g][0]),.D1(symbols[g][1]),.D2(symbols[g][2]),.D3(symbols[g][3]),.D4(symbols[g][4]),
 .D5(symbols[g][5]),.D6(symbols[g][6]),.D7(symbols[g][7]),.D8(symbols[g][8]),.D9(symbols[g][9]));
end endgenerate
reg previous_clock=0;reg [9:0] a,b,c,last_word;integer bit_index=10,warmup=0,checked=0;
always @(fast) begin
 #0.1;
 if(reset) begin bit_index=10;warmup=0;previous_clock=0;end
 else begin
  if(previous_clock && !q[3]) bit_index=0;
  previous_clock=q[3];
  if(bit_index<10) begin
   a[bit_index]=q[0];b[bit_index]=q[1];c[bit_index]=q[2];bit_index=bit_index+1;
   if(bit_index==10) begin
    warmup=warmup+1;
    if(warmup>6) begin
     if(^a===1'bx || b!==~a || c!==(a^10'h155)) $fatal(1,"serializer lane/bit alignment");
     if(a!==((last_word+10'd1)&10'h3ff)) $fatal(1,"serializer lost/repeated symbol");
     checked=checked+1;
    end
    last_word=a;
   end
  end
 end
end
initial begin
 #60.7;request=0;#2500.3;request=1;#41.3;request=0;#2500;
 if(checked<200) $fatal(1,"insufficient serialized words");
 $display("PASS %0d serializer words, clock alignment, all lanes, repeated reset",checked);$finish;
end
endmodule
''',rtl+[primitive_file])
    if args.part in ("all","field"):
        cases=[(0,0,70,40000,64),(18000,-12000,70,40000,64),(-15000,9000,100,60000,128)]
        emitters=np.array([[(c-3.5)*10300,(r-3.5)*10300,0] for r in range(8) for c in range(8)])
        vectors=[]
        for x,y,z,f,steps in cases:
            distance=np.linalg.norm(emitters-np.array([x,y,z*1000]),axis=1)
            codes=np.floor(np.mod(-distance*f/343e6,1)*steps+0.5).astype(int)%steps
            vectors.append(sum(int(code*1024//steps)<<(10*i) for i,code in enumerate(codes)))
        (folder/"phases.hex").write_text("".join(f"{v:0160x}\n" for v in vectors),encoding="ascii")
        # 32x32 samples over the same 189 mm viewport; test includes off-axis and changed height/frequency.
        bench='''module tb_field;
reg clk=0;always #10 clk=~clk;
reg rst_n=0,start=0;reg [639:0] phases;reg [31:0] z=70000,f=40000;
wire done,we;wire [11:0] address;wire [7:0] value;
reg [639:0] table_ph[0:2];integer k,out,count,cycles;
field_engine #(.GRID(32),.STEP_25UM(240),.MIN_25UM(-3720)) dut(
 .clk(clk),.rst_n(rst_n),.start(start),.phases(phases),.z_um(z),.carrier_hz(f),
 .done(done),.write_en(we),.write_addr(address),.write_value(value));
always @(posedge clk) if(rst_n) begin
 cycles=cycles+1;
 if(we) begin
   if(address!==count || ^value===1'bx) $fatal(1,"address or unknown");
   $fwrite(out,"%d\\n",value);count=count+1;
 end
end
initial begin
 $readmemh("phases.hex",table_ph);repeat(5) @(negedge clk);rst_n=1;
 for(k=0;k<3;k=k+1) begin
  phases=table_ph[k];z=k==2?100000:70000;f=k==2?60000:40000;
  out=$fopen($sformatf("field_%0d.txt",k),"w");count=0;cycles=0;
  @(negedge clk);start=1;@(negedge clk);start=0;
  // Changing the input phase bus must not change an in-flight snapshot.
  phases=~phases;
  wait(done);@(negedge clk);
  if(count!=1024) $fatal(1,"incomplete field");
  $display("case %0d cycles %0d",k,cycles);$fclose(out);
 end
 $finish;
end
initial begin #100000000;$fatal(1,"timeout");end
endmodule
'''
        if args.grid==64:
            bench=bench.replace('.GRID(32),.STEP_25UM(240),.MIN_25UM(-3720)',
                                '.GRID(64),.STEP_25UM(120),.MIN_25UM(-3780)')
            bench=bench.replace('count!=1024','count!=4096').replace('#100000000','#500000000')
        simulate(compiler,folder,"tb_field",bench,rtl)
        metrics=[]
        for k,(x,y,z,f,steps) in enumerate(cases):
            values=np.loadtxt(folder/f"field_{k}.txt").reshape(args.grid,args.grid)
            grid=np.arange(args.grid)*(3000 if args.grid==64 else 6000)-(94500 if args.grid==64 else 93000)
            xx,yy=np.meshgrid(grid,grid)
            distance=np.linalg.norm(emitters-np.array([x,y,z*1000]),axis=1)
            phases=np.floor(np.mod(-distance*f/343e6,1)*steps+0.5)%steps*2*math.pi/steps
            pressure=np.zeros(xx.shape,dtype=complex)
            for emitter,phase in zip(emitters,phases):
                r=np.sqrt((xx-emitter[0])**2+(yy-emitter[1])**2+(z*1000)**2)
                pressure+=np.exp(1j*(2*math.pi*r*f/343e6+phase))/r
            expected=np.abs(pressure)/(64/(z*1000))*256
            error=float(np.max(np.abs(values-np.clip(expected,0,255))))
            if error>4:
                raise AssertionError(f"case {k} floating reference error {error:.3f} > 4/255")
            metrics.append(dict(case=k,max_error_codes=error,peak=int(values.max())))
        (folder/"numerical.json").write_text(json.dumps(metrics,indent=2),encoding="utf-8")
        print(metrics,flush=True)
    if args.part in ("all","video"):
        if not (folder/"field_0.txt").exists():
            raise RuntimeError("Run field checks first")
        # Render exactly the RGB output of the video RTL, not a Python reconstruction of its UI.
        values=np.loadtxt(folder/"field_0.txt",dtype=int)
        size=math.isqrt(values.size)
        grid=values.reshape(size,size).repeat(64//size,0).repeat(64//size,1)
        (folder/"render_field.hex").write_text("".join(f"{v:02x}\n" for v in grid.flat),encoding="ascii")
        simulate(compiler,folder,"tb_video",'''module tb_video;
reg clk=0;always #10 clk=~clk;
reg rst_n=0;wire [11:0] addr;reg [7:0] value;reg [7:0] mem[0:4095];
wire [7:0] r,g,b;wire de,hs,vs,boundary;
integer out,n,lines,active_count;
preview_video dut(.clk(clk),.rst_n(rst_n),.field_value(value),.field_valid(1'b1),
 .focus_x(21'sd0),.focus_y(21'sd0),.z_um(32'd70000),.field_cycles(32'd2035818),
 .frame_number(32'd1),.run_state(2'd1),.frame_boundary(boundary),.field_addr(addr),
 .red(r),.green(g),.blue(b),.de(de),.hs(hs),.vs(vs));
always @(posedge clk) value<=mem[addr];
initial begin
 $readmemh("render_field.hex",mem);repeat(5) @(negedge clk);rst_n=1;
 wait(boundary);@(negedge clk);
 out=$fopen("screen.ppm","wb");$fwrite(out,"P6\\n1024 600\\n255\\n");n=0;
 repeat(1312*624) begin
  @(negedge clk);
  if(de) begin
   if(^{r,g,b}===1'bx) $fatal(1,"unknown RGB");
   $fwrite(out,"%c%c%c",r,g,b);n=n+1;
  end
 end
 if(n!=1024*600) $fatal(1,"active pixels %0d",n);
 $fclose(out);$display("PASS raster");$finish;
end
endmodule
''',rtl)
        with Image.open(folder/"screen.ppm") as im:
            im.save(folder/"screen.png")
    if args.part in ("all","integration"):
        integration(compiler,folder,rtl)
    if args.part=="animation":
        movie_folder=folder/"animation"
        movie_folder.mkdir(exist_ok=True)
        generate(movie_folder)
        integration(compiler,movie_folder,rtl,movie=True)
        render_movie(movie_folder)
    if args.part in ("all","tmds"):
        import random
        rng=random.Random(20261025)
        vectors=[(int(i%65!=0)<<10)|((i%4)<<8)|rng.randrange(256) for i in range(2048)]
        (folder/"symbols_in.hex").write_text("".join(f"{v:03x}\n" for v in vectors),encoding="ascii")
        simulate(compiler,folder,"tb_tmds",'''module tb_tmds;
reg clk=0;always #10 clk=~clk;reg rst_n=0;reg [10:0] data=0;
reg [10:0] mem[0:2047];wire [9:0] symbol;integer i,out;
tmds_encoder dut(.clk(clk),.rst_n(rst_n),.de(data[10]),.c1(data[9]),.c0(data[8]),.data(data[7:0]),.symbol(symbol));
initial begin
 $readmemh("symbols_in.hex",mem);out=$fopen("symbols_out.txt","w");
 repeat(4) @(negedge clk);rst_n=1;
 for(i=0;i<2048;i=i+1) begin data=mem[i];@(negedge clk);$fwrite(out,"%d\\n",symbol);end
 $fclose(out);$finish;
end
endmodule
''',rtl)
        disparity=0
        for source,symbol in zip(vectors,map(int,(folder/"symbols_out.txt").read_text().split())):
            if not source&1024:
                assert symbol==[0b1101010100,0b0010101011,0b0101010100,0b1010101011][source>>8&3]
                disparity=0
                continue
            q=(symbol&255)^(255 if symbol&512 else 0)
            recovered=q&1
            for i in range(1,8):
                recovered|=(((q>>i)^(q>>(i-1))^(0 if symbol&256 else 1))&1)<<i
            assert recovered==source&255
            disparity+=symbol.bit_count()*2-10
            assert abs(disparity)<=8
        print("PASS 2048 TMDS symbols: byte roundtrip, controls, DC balance",flush=True)


def integration(compiler,folder,rtl,movie=False):
    sys.path.insert(0,str(ROOT))
    from desktop_app.protocol import encode
    config=dict(carrier_hz=40000,phase_steps=64,cx_um=0,cy_um=0,z_um=70000,
                radius_um=20000,repeat_millihz=10000,mod_hz=0,level=100,
                shape="CUSTOM",path_xy_um="NONE",path_closed=1,
                scan_paths="-20000:-10000,17000:5000,3000:19000|-12000:20000,16000:-13000",
                blank_us=2000,hw_rows=8,hw_cols=8,hw_pitch_um=10300,mapping="ROW_MAJOR_XY")
    if movie:
        config["repeat_millihz"]=40000
    commands=[encode("CMD",1,"HELLO"),encode("CMD",2,"CONFIG",**config),
              encode("CMD",3,"START"),encode("CMD",4,"PAUSE"),
              encode("CMD",5,"START"),encode("CMD",6,"STOP")]
    boundaries=[0]
    for data in commands:boundaries.append(boundaries[-1]+len(data))
    (folder/"commands.hex").write_text("".join(f"{b:02x}\n" for data in commands for b in data),encoding="ascii")
    sends=[f"send_range({boundaries[i]},{boundaries[i+1]});" for i in range(len(commands))]
    bench='''module tb_preview_path;
reg clk=0;always #10 clk=~clk;reg rst_n=0,rx=1;
wire tx,valid,physical;wire [63:0] array_p;wire [1:0] state;
wire [31:0] frames;integer tick=0;reg boundary=0;
reg [7:0] data[0:TOTAL-1];integer i,j,n,out,count;reg [31:0] before_frame;
reg signed [20:0] old_x,old_y;reg changed=0;
theory_preview_core #(.CLK_HZ(49000000),.BAUD(1000000),.GRID(8),.STEP_25UM(240),.MIN_25UM(-840)) dut(
 .clk(clk),.rst_n(rst_n),.key_next(1'b0),.key_play(1'b0),.uart_rx_pin(rx),.uart_tx_pin(tx),
 .frame_boundary(boundary),.field_read_addr(12'd0),.field_valid(valid),
 .frame_number(frames),.run_state(state),.physical_output(physical),.physical_array(array_p));
always @(posedge clk) begin
 tick<=tick+1;boundary<=tick%1000==0;
 if(rst_n && (physical!==0 || array_p!==64'd0)) $fatal(1,"preview drove physical output");
end
task send_range(input integer first,last);
 integer k,b;
 begin
  for(k=first;k<last;k=k+1) begin
   @(negedge clk);rx=0;repeat(49) @(negedge clk);
   for(b=0;b<8;b=b+1) begin rx=data[k][b];repeat(49) @(negedge clk);end
   rx=1;repeat(49) @(negedge clk);
  end
  repeat(80000) @(negedge clk);
 end
endtask
initial begin
 $readmemh("commands.hex",data);repeat(8) @(negedge clk);rst_n=1;
 SEND0
 SEND1
 if(dut.revision==0 || dut.standalone.control.cfg_shape!=7 || dut.standalone.control.traj_stk!=2)
  $fatal(1,"custom multi-stroke config not accepted");
 SEND2
 if(state!=1) $fatal(1,"preview did not start");
 before_frame=frames;old_x=dut.displayed_x;old_y=dut.displayed_y;
 wait(frames>=before_frame+4);@(negedge clk);
 if(dut.displayed_x==old_x && dut.displayed_y==old_y) $fatal(1,"field snapshot did not move");
 if(dut.phases===640'd0) $fatal(1,"missing phases");
 SEND3
 if(state!=2) $fatal(1,"pause failed");
 repeat(80000) @(negedge clk);before_frame=frames;
 repeat(80000) @(negedge clk);
 if(frames!=before_frame) $fatal(1,"paused field kept advancing");
 SEND4
 if(state!=1) $fatal(1,"resume failed");
 SEND5
 if(state!=0 || valid) $fatal(1,"stop retained field");
 $display("PASS custom path, moving frames, pause/resume/stop, no physical output");$finish;
end
initial begin #100000000;$fatal(1,"integration timeout");end
endmodule
'''.replace("TOTAL",str(boundaries[-1]))
    for i,send in enumerate(sends):bench=bench.replace(f"SEND{i}",send)
    if movie:
        # Smaller 48 mm central viewport for the long path movie, retaining the
        # production 3 mm sample spacing. Full 64x64 arithmetic is checked above.
        bench=bench.replace('.GRID(8),.STEP_25UM(240),.MIN_25UM(-840)',
                            '.GRID(16),.STEP_25UM(120),.MIN_25UM(-900)')
        bench=bench.replace('before_frame+4','before_frame+10')
        bench=bench.replace('repeat(80000) @(negedge clk);before_frame=frames;',
                            'repeat(600000) @(negedge clk);before_frame=frames;')
        bench=bench.replace('#100000000','#500000000')
        bench=bench.replace('initial begin\n $readmemh("commands.hex",data);', '''
integer movie_file,movie_count=0,pixel;
always @(negedge clk) if(rst_n && dut.state==6 && boundary && movie_count<12) begin
 movie_file=$fopen($sformatf("movie_%02d.txt",movie_count),"w");
 $fwrite(movie_file,"%d %d %d %d\\n",dut.fx_q,dut.fy_q,dut.z_q,dut.active_q);
 for(pixel=0;pixel<256;pixel=pixel+1) $fwrite(movie_file,"%d\\n",dut.image[{~dut.front,12'd0}+pixel]);
 $fclose(movie_file);movie_count=movie_count+1;
end
initial begin
 $readmemh("commands.hex",data);''')
    simulate(compiler,folder,"tb_preview_path",bench,rtl)


def render_movie(folder):
    from PIL import ImageDraw,ImageFont
    font=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",20)
    small=ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",16)
    palette=[]
    for v in range(256):
        t=(v&63)*4
        palette.append([(8+(t>>5),16+(t>>2),36+(t>>1)),(15,80+(t>>1),164-(t>>3)),
                        (16+(t>>1)+(t>>2),208+(t>>4),132-(t>>2)),(208+(t>>3),224-(t>>2),68-(t>>3))][v>>6])
    images=[]
    metrics=[]
    emitters=np.array([[(c-3.5)*10300,(r-3.5)*10300,0] for r in range(8) for c in range(8)])
    xx,yy=np.meshgrid(np.arange(16)*3000-22500,np.arange(16)*3000-22500)
    for path in sorted(folder.glob("movie_*.txt")):
        tokens=list(map(int,path.read_text().split()))
        fx,fy,z,active=tokens[:4]
        data=np.array(tokens[4:],dtype=np.uint8).reshape(16,16)
        if active:
            # Check the entire UART -> path -> phase -> field chain against an
            # independent ideal, unquantized model at the captured focus.
            distances=np.linalg.norm(emitters-np.array([fx/2,fy/2,z]),axis=1)
            pressure=np.zeros(xx.shape,dtype=complex)
            for emitter,distance in zip(emitters,distances):
                radius=np.sqrt((xx-emitter[0])**2+(yy-emitter[1])**2+z*z)
                pressure+=np.exp(1j*2*math.pi*40000/343e6*(radius-distance))/radius
            expected=np.clip(np.abs(pressure)/(64/z)*256,0,255)
            error=float(np.abs(data.astype(float)-expected).max())
            if error>5:
                raise AssertionError(f"{path.name}: integrated field error {error:.3f} > 5/255")
            metrics.append(dict(frame=path.name,focus_um=[fx/2,fy/2],max_error_codes=error))
        if not active:data[:]=0
        rgb=np.array(palette,dtype=np.uint8)[data[::-1]]
        tile=Image.fromarray(rgb).resize((512,512),Image.Resampling.NEAREST)
        im=Image.new("RGB",(592,652),"#0b1424")
        im.paste(tile,(40,72))
        draw=ImageDraw.Draw(im)
        draw.text((40,16),"板内逻辑仿真｜自定义多段图形",font=font,fill="#d9e6f5")
        draw.text((40,44),f"目标 ({fx/2000:.1f}, {fy/2000:.1f}) mm  ·  Z={z/1000:g} mm",font=small,fill="#d9e6f5")
        if active:
            mx=40+(fx/2+24000)/48000*512
            my=72+(24000-fy/2)/48000*512
            draw.line((mx-5,my,mx+5,my),fill="white",width=1)
            draw.line((mx,my-5,mx,my+5),fill="white",width=1)
        draw.text((40,595),"中心区域 48×48 mm · 板内逻辑计算结果慢放",font=small,fill="#d9e6f5")
        draw.text((40,622),"仅预览 / 实际发射关闭 / 非实测声场",font=small,fill="#90a9bd")
        images.append(im)
    if len(images)<8:raise AssertionError("too few moving frames")
    if len(metrics)<6:raise AssertionError("too few active field samples")
    (folder/"numerical.json").write_text(json.dumps(metrics,indent=2),encoding="utf-8")
    images[0].save(folder/"custom_path.gif",save_all=True,append_images=images[1:],duration=180,loop=0)


if __name__=="__main__":
    main()
