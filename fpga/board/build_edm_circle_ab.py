"""Isolated two-button circle comparison. Does not program or emit.

G15: ordinary circle. F15: phase-only EDM circle. Either stops an active run.
All production RTL and measured calibration remain unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from fpga.board.build_calibration import load_restore,check_timing
from fpga.board.build_edm_center_test import replace_once,digest
from fpga.board.edm_circle_model import FOLDER,UNIT,baseline,index
from desktop_app.model import Config,ArraySpec
from desktop_app.protocol import encode,decode


def sub(text,old,new):
    if text.count(old)!=1: raise ValueError('Expected one exact source anchor: '+old[:90])
    return text.replace(old,new)


def prepare(args):
    packed,measured=load_restore(args.calibration_file)
    model=json.loads((FOLDER/'model.json').read_bytes())
    assert model['phase_only'] and len(model['phase_deltas'])==128
    stage=FOLDER/'rtl';stage.mkdir(parents=True,exist_ok=True)
    for f in (ROOT/'fpga/rtl').glob('*.v'):shutil.copy2(f,stage/f.name)
    p=stage/'board_top.v';s=p.read_text(encoding='utf-8')
    s=replace_once(s,r'(`define FPGA_CALIBRATION_ENABLE )\d+',1)
    for key,value in dict(CAL_RESTORE_ENABLE=1,BUTTON_FOCUS_ENABLE=1,BUTTON_FOCUS_MS=6000,
            BUTTON_FOCUS_Z_UM=70000,BUTTON_FOCUS_LEVEL=100,BUTTON_FOCUS_SHAPE=3,
            BUTTON_FOCUS_RADIUS_UM=20000,BUTTON_FOCUS_REPEAT_MILLIHZ=500,
            WS_X_MIN_UM=-20000,WS_X_MAX_UM=20000,WS_Y_MIN_UM=-20000,WS_Y_MAX_UM=20000,
            WS_Z_MIN_UM=70000,WS_Z_MAX_UM=70000).items():
        s=replace_once(s,r'(parameter integer\s+'+key+r'\s*=\s*)-?\d+',value)
    s=replace_once(s,r"(parameter \[511:0\] CAL_RESTORE_OFFSETS = )512'd0",f"512'h{packed:0128x}")
    p.write_text(s,encoding='utf-8')
    p=stage/'hap2_cmd.v';s=p.read_text(encoding='utf-8')
    s=sub(s,'    input  wire            loc_next,','    output wire            narrow_active,\n    input  wire            loc_next,')
    s=sub(s,'    reg button_busy;','    reg button_busy;\n    reg narrow_mode;\n    assign narrow_active = narrow_mode && button_busy;')
    s=sub(s,"            button_busy    <= 1'b0;","            button_busy    <= 1'b0;\n            narrow_mode <= 1'b0;")
    s=sub(s,'if (loc_play && !loc_next && !loc_stop && !button_busy &&',
              'if ((loc_play ^ loc_next) && !loc_stop && !button_busy &&')
    s=sub(s,"                    button_busy <= 1'b1;","                    button_busy <= 1'b1;\n                    narrow_mode <= loc_next;")
    s=sub(s,'if (loc_next || loc_stop ||\n                    (loc_play && (button_busy || run_state != R_IDLE)) ||',
        'if (loc_stop || (loc_play && loc_next) ||\n                    ((loc_play || loc_next) && (button_busy || run_state != R_IDLE)) ||')
    p.write_text(s,encoding='utf-8')
    p=stage/'hap2_top.v';s=p.read_text(encoding='utf-8')
    s=sub(s,'    wire        loc_next, loc_play, loc_stop;',
        '    wire        loc_next, loc_play, loc_stop;\n    wire narrow_active;')
    s=sub(s,'        .loc_next         (loc_next),','        .narrow_active    (narrow_active),\n        .loc_next         (loc_next),')
    s=sub(s,'    ) u_phase (','    ) u_phase (\n        .narrow_enable(narrow_active && cfg_shape==3 && cfg_cx_um==0 && cfg_cy_um==0 &&\n                       cfg_radius_um==20000 && cfg_z_um==70000 && cfg_phase_steps==64 && cfg_carrier_hz==40000),')
    p.write_text(s,encoding='utf-8')
    p=stage/'hap2_phase.v';s=p.read_text(encoding='utf-8')
    s=sub(s,'    input  wire                      clk,','    input wire narrow_enable,\n    input  wire                      clk,')
    constants='\n'.join(f"        edm_ux[{j}]=16'sd{x}; edm_uy[{j}]=16'sd{y};".replace("16'sd-","-16'sd")
                         for j,(x,y) in enumerate(UNIT))
    rompath=str((FOLDER/'delta.hex').resolve()).replace('\\','/')
    block=f'''
    // Experimental shaping ROM, distinct from measured calibration offsets.
    // The normal spherical focus is still computed online for every position.
    reg [5:0] edm_delta [0:8191];
    reg signed [15:0] edm_ux [0:127], edm_uy [0:127];
    initial begin
        $readmemh("{rompath}",edm_delta);
{constants}
    end
    reg narrow_q;
    reg [6:0] edm_scan,edm_best;
    reg signed [38:0] edm_best_dot;
    wire signed [38:0] edm_dot = $signed(fx_q)*$signed(edm_ux[edm_scan]) +
                                $signed(fy_q)*$signed(edm_uy[edm_scan]);
    wire [5:0] edm_channel = base_ch + lane_wr;
    wire [7:0] edm_code = (lane_code[lane_wr]+edm_delta[{{edm_best,edm_channel}}]) & 8'h3f;
'''
    s=sub(s,'    wire all_sq_done = &sq_done_v;',block+'\n    wire all_sq_done = &sq_done_v;')
    s=sub(s,"    localparam [2:0] P_END   = 3'd5;","    localparam [2:0] P_END   = 3'd5;\n    localparam [2:0] P_EDM = 3'd6;")
    s=sub(s,"            busy      <= 1'b1;","            busy      <= 1'b1;\n            narrow_q <= narrow_enable;\n            edm_scan <= 0; edm_best <= 0; edm_best_dot <= -39'sd274877906943;")
    s=sub(s,'        if (!rst_n) begin\n            p_state     <= P_IDLE;',
        '        if (!rst_n) begin\n            p_state     <= P_IDLE;\n            narrow_q <= 0; edm_scan<=0; edm_best<=0; edm_best_dot<=0;')
    s=sub(s,'end else if (force_sweep || (start && need_sweep)) begin',
              'end else if (force_sweep || narrow_enable!=narrow_q || (start && need_sweep)) begin')
    s=sub(s,'                        begin_sweep;\n                        p_state <= P_GO;',
        '                        begin_sweep;\n                        p_state <= narrow_enable ? P_EDM : P_GO;')
    s=sub(s,'                P_GO: begin','''                P_EDM: begin
                    if(edm_dot > edm_best_dot) begin
                        edm_best_dot <= edm_dot; edm_best <= edm_scan;
                    end
                    if(edm_scan==127) p_state<=P_GO;
                    else edm_scan<=edm_scan+1'b1;
                end
                P_GO: begin''')
    s=sub(s,'                        wr_data <= lane_code[lane_wr];',
        '                        wr_data <= narrow_q ? edm_code : lane_code[lane_wr];')
    p.write_text(s,encoding='utf-8')
    for suffix in ('cst','sdc'):
        shutil.copy2(ROOT/f'fpga/board/tang_mega_138k_pro.{suffix}',FOLDER/f'tang_mega_138k_pro.{suffix}')
    path=lambda p:'{'+str(p.resolve()).replace('\\','/')+'}'
    (FOLDER/'build.tcl').write_text('\n'.join([
        f'create_project -name edm_circle_ab -dir {path(FOLDER)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force',
        f'add_file {{*}}[lsort [glob -directory {path(stage)} *.v]]',
        *[f'add_file {path(FOLDER/f"tang_mega_138k_pro.{x}")}' for x in ('cst','sdc')],
        'set_option -top_module board_top','set_option -output_base_name board_top',
        'set_option -use_cpu_as_gpio 1','set_option -use_sspi_as_gpio 1',
        'set_option -resource_sharing 0','set_option -num_critical_paths 100','run syn','run pnr','exit','']),encoding='utf-8')
    m=dict(mode='experimental_edm_circle_ab',measured_calibration=measured,
        geometry=dict(z_um=70000,radius_um=20000,cx_um=0,cy_um=0),
        level=100,repeat_millihz=500,button_deadline_ms=6000,
        ordinary_pin='G15',narrow_pin='F15',stop='either button while busy; K16 reset',
        model_sha256=digest(FOLDER/'model.json'),rom_sha256=digest(FOLDER/'delta.hex'),
        sources={p.name:digest(p) for p in sorted(stage.glob('*.v'))},
        constraints={p.name:digest(p) for p in FOLDER.glob('tang_mega_138k_pro.*')},
        output=str(FOLDER/'edm_circle_ab/impl/pnr/board_top.fs'),acoustic_validation=False)
    (FOLDER/'manifest.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print('PREPARED two-button circle comparison',flush=True)


def manifest():
    m=json.loads((FOLDER/'manifest.json').read_bytes())
    for name,sha in m['sources'].items():assert digest(FOLDER/'rtl'/name)==sha
    for name,sha in m['constraints'].items():assert digest(FOLDER/name)==sha
    assert digest(FOLDER/'model.json')==m['model_sha256']
    assert digest(FOLDER/'delta.hex')==m['rom_sha256']
    c=m['measured_calibration'];assert digest(c['file'])==c['sha256']
    return m


def test(args):
    m=manifest();model=json.loads((FOLDER/'model.json').read_bytes())
    offsets=m['measured_calibration']['phase_offsets']
    original=(ROOT/'fpga/tb/tb_button_focus.v').read_text(encoding='utf-8')
    results=[]
    for narrow in (0,1):
        folder=FOLDER/f'test{narrow}';folder.mkdir(exist_ok=True)
        tb=original
        if narrow:
            tb=sub(tb,'.key_next(stop),.key_play(play),','.key_next(play),.key_play(stop),')
        # Trace the published double-buffered table independently of UART.
        tb=sub(tb,'    integer lines=0,fd,guard,i,j,k,b,offset=0,n;', '''    integer phase_fd,pc;
    integer phase_tables=0,shaped_tables=0;
    initial phase_fd=$fopen("phase_tables.txt","w");
    always @(negedge clk) if(rst_n && dut.u_phase.done) begin
        $fwrite(phase_fd,"%0d %0d %0d %0d %0d",$signed(dut.ph_pub_fx),$signed(dut.ph_pub_fy),dut.ph_pub_fz_um,dut.u_phase.narrow_q,dut.u_phase.edm_best);
        for(pc=0;pc<64;pc=pc+1) $fwrite(phase_fd," %0d",dut.u_phase.ram[{dut.ph_pub_sel,pc[7:0]}]);
        $fwrite(phase_fd,"\\n");$fflush(phase_fd);
        phase_tables=phase_tables+1;
        if(dut.u_phase.narrow_q) shaped_tables=shaped_tables+1;
    end
    integer lines=0,fd,guard,i,j,k,b,offset=0,n;''')
        tb=sub(tb,'        $fclose(fd);',f'''        if(phase_tables<50 || ({narrow} && shaped_tables<50)) $fatal(1,"insufficient phase coverage");
        // Both keys at idle must not arm either mode.
        play=1;stop=1;repeat(15000) @(negedge clk);check_off;
        play=0;stop=0;repeat(6000) @(negedge clk);
        // Switch to the OTHER key after a full stop, then stop with the first.
        stop=1;repeat(6000) @(negedge clk);stop=0;repeat(6000) @(negedge clk);
        check_center100;
        if(dut.u_cmd.narrow_mode!={1-narrow}) $fatal(1,"opposite button selected wrong mode");
        press_play;check_off;
        $fclose(phase_fd);
        $fclose(fd);''')
        tb=sub(tb,'            if(!on || dut.cfg_z_um!=FOCUS_Z_UM',
                  '            if(!on || dut.cfg_z_um!=FOCUS_Z_UM') # exact anchor existence
        (folder/'tb_button_focus.v').write_text(tb,encoding='utf-8')
        cfg=Config(shape='POINT',cx_um=0,cy_um=0,z_um=70000,radius_um=0,level=100,mod_hz=0)
        fields={**cfg.wire(),**ArraySpec(8,8,10300).wire()}
        commands=[('HELLO',{}),('MODE',dict(value='REMOTE')),('CONFIG',fields),('SNAP',{}),
                  ('START',{}),('STOP',{}),('CONFIG',{**fields,'carrier_hz':39000}),('START',{}),('SNAP',{})]
        frames=[encode('CMD',i+1,v,**d) for i,(v,d) in enumerate(commands)]
        (folder/'commands.hex').write_text('\n'.join(f'{b:02x}' for b in b''.join(frames)),encoding='ascii')
        (folder/'lengths.hex').write_text('\n'.join(f'{len(f):08x}' for f in frames),encoding='ascii')
        packed=sum(v<<(8*i) for i,v in enumerate(offsets))
        expected=sum(((int(v)+off)%64)<<(8*i) for i,(v,off) in enumerate(zip(baseline(0,0),offsets)))
        compiler=Path(args.iverilog);sim=folder/'simulation.vvp'
        with (folder/'test.log').open('w',encoding='utf-8') as log:
            subprocess.run([str(compiler),'-g2012','-s','tb_button_focus',
                f"-Ptb_button_focus.OFFSETS=512'h{packed:0128x}",f"-Ptb_button_focus.EXPECTED=512'h{expected:0128x}",
                f"-Ptb_button_focus.EXPECTED100=512'h{expected:0128x}",'-Ptb_button_focus.FOCUS_Z_UM=70000',
                '-Ptb_button_focus.CIRCLE_TEST=1','-o',str(sim),
                *map(str,sorted((FOLDER/'rtl').glob('*.v'))),str(folder/'tb_button_focus.v')],
                cwd=folder,env=dict(os.environ,TMP=str(folder),TMPDIR=str(folder)),
                stdout=log,stderr=subprocess.STDOUT,check=True)
            subprocess.run([str(compiler.with_name('vvp.exe')),'-i',str(sim)],cwd=folder,
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
        seen=set();count=0;shaped=0
        for line in (folder/'phase_tables.txt').read_text().splitlines():
            vals=list(map(int,line.split()));x,y,z,enabled,idx=vals[:5]
            expect=baseline(x,y,z)
            if enabled:
                assert idx==index(x,y),(idx,index(x,y),x,y)
                expect=(expect+model['phase_deltas'][idx])%64
                seen.add(idx);shaped+=1
            assert list(expect)==vals[5:],(x,y,enabled,'phase mismatch')
            count+=1
        uart=[decode(l) for l in (folder/'uart.log').read_bytes().splitlines(keepends=True)]
        assert any(f.kind=='ERR' and f.seq==7 for f in uart)
        assert any(f.fields.get('reason')=='HEARTBEAT_TIMEOUT' for f in uart)
        for f in uart:
            if f.verb!='STATE' or f.fields.get('output')!='1':continue
            st=f.fields;x,y,z=[int(st[k]) for k in ('fx_um','fy_um','fz_um')]
            candidates=[]
            for hx in (2*x,2*x+1):
                for hy in (2*y,2*y+1):
                    c=baseline(hx,hy,z)
                    if narrow and st['shape']=='CIRCLE': c=(c+model['phase_deltas'][index(hx,hy)])%64
                    candidates.append(((c+offsets)%64).tolist())
            assert list(map(int,st['phases'].split(','))) in candidates,'UART phase mismatch'
        if narrow:assert len(seen)==128,('missing anchors',len(seen))
        results.append(dict(narrow=narrow,published_tables=count,shaped_tables=shaped,anchors=len(seen)))
        print('PASS',results[-1],flush=True)
    (FOLDER/'test_result.json').write_text(json.dumps(dict(passed=True,sources=m['sources'],
        model_sha256=m['model_sha256'],rom_sha256=m['rom_sha256'],results=results,
        timeout_stop_reset_held_key_both_keys_mode_switch=True),indent=2),encoding='utf-8')


def build(args):
    m=manifest();t=json.loads((FOLDER/'test_result.json').read_bytes())
    assert t['passed'] and t['sources']==m['sources']
    out=Path(m['output'])
    if out.exists():raise FileExistsError('Existing build will not be overwritten')
    with (FOLDER/'build.log').open('w',encoding='utf-8') as log:
        subprocess.run([args.gowin,str(FOLDER/'build.tcl')],cwd=FOLDER,
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    assert out.is_file()
    m['fmax_mhz']=check_timing(out.with_suffix('.tr').read_text(encoding='utf-8',errors='replace'))
    m['sha256']=digest(out)
    (FOLDER/'manifest.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print('BUILD PASS',m['fmax_mhz'],m['sha256'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','test','build'])
    p.add_argument('--calibration-file',default='D:/pld/output/calibration/8x8_70mm_calibration_20261024_1227.json')
    p.add_argument('--iverilog',default='D:/pld/.diagnostics/review-2026-10-15/iverilog/app/bin/iverilog.exe')
    p.add_argument('--gowin',default='D:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe')
    args=p.parse_args();{'prepare':prepare,'test':test,'build':build}[args.action](args)
