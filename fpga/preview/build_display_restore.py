"""Build measured-calibration transmitter with a read-only theory display.

No programming or emission. The original preview-only build stays available.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from generate_assets import generate

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from fpga.board.build_calibration import load_restore

def make_top(packed,z_mm):
    text=(ROOT/'fpga/preview/theory_preview_top.v').read_text(encoding='utf-8')
    def replace(old,new):
        nonlocal text
        if text.count(old)!=1:raise ValueError('Top template changed: '+old)
        text=text.replace(old,new)
    replace('module theory_preview_top(', 'module calibration_display_top(')
    replace('// Standalone PREVIEW image. Shift-register outputs are constant-disabled at the pads.',
            '// Transmitter stays on 50 MHz; display observes atomic snapshots on 49 MHz.')
    replace('    wire boundary,valid;',f'''    wire [188:0] source_snapshot,display_snapshot;
    wire source_reset_n;
    board_top #(.CAL_RESTORE_ENABLE(1),.CAL_Z_UM({z_mm*1000}),
        .CAL_RESTORE_OFFSETS(512'h{packed:0128x})) transmitter(
        .clk(clk),.key_rst_n(key_rst_n),.key0_n(key0_n),.key1_n(key1_n),
        .uart_rx_pin(uart_rx_pin),.uart_tx_pin(uart_tx_pin),
        .sr_data(sr_data),.sr_clk(sr_clk),.sr_latch(sr_latch),
        .sr_oe_n(sr_oe_n),.sr_clear_n(sr_clear_n),.led(led),
        .view_snapshot(source_snapshot),.view_reset_n(source_reset_n));
    preview_observer_cdc observer_cdc(.source_clk(clk),.source_rst_n(source_reset_n),
        .dest_clk(pixel_clk),.dest_rst_n(rst_n),.source_data(source_snapshot),.dest_data(display_snapshot));
    wire boundary,valid;''')
    replace('theory_preview_core core(', 'theory_preview_core #(.EXTERNAL_CONTROL(1)) core(')
    replace('.key_next(~key0_n),.key_play(~key1_n)', '.key_next(1\'b0),.key_play(1\'b0)')
    replace('.uart_rx_pin(uart_rx_pin),.uart_tx_pin(uart_tx_pin),.frame_boundary(boundary)',
            ".uart_rx_pin(1'b1),.uart_tx_pin(),.external_snapshot(display_snapshot),.frame_boundary(boundary)")
    replace('    assign sr_data=0;assign sr_clk=0;assign sr_latch=0;assign sr_oe_n=1;assign sr_clear_n=0;', '')
    replace('    assign led=~{valid,run_state==1,locked};', '')
    return text

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--calibration-file',required=True)
    p.add_argument('--calibration-z-mm',type=int,choices=(50,70,100),required=True)
    p.add_argument('--gowin',default='D:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe')
    p.add_argument('--prepare-only',action='store_true')
    args=p.parse_args()
    packed,info=load_restore(args.calibration_file,args.calibration_z_mm)
    folder=ROOT/'.runtime/display-restore'/f'z{args.calibration_z_mm}'
    src=folder/'src';src.mkdir(parents=True,exist_ok=True)
    for path in [*(ROOT/'fpga/rtl').glob('*.v'),*(ROOT/'fpga/preview').glob('*.v')]:
        shutil.copy2(path,src/path.name)
    board=src/'board_top.v'
    text,n=re.subn(r'(`define FPGA_CALIBRATION_ENABLE )0',r'\g<1>1',board.read_text(encoding='utf-8'))
    if n!=1:raise ValueError('Calibration macro missing')
    board.write_text(text,encoding='utf-8')
    (src/'calibration_display_top.v').write_text(make_top(packed,args.calibration_z_mm),encoding='utf-8')
    generate(src)
    cst=(ROOT/'fpga/board/tang_mega_138k_pro.cst').read_text(encoding='utf-8')
    for name,pair in [('tmds_data_p[0]','AB24,AC24'),('tmds_data_p[1]','V24,W24'),
                      ('tmds_data_p[2]','AA22,AA23'),('tmds_clk_p','Y22,Y23')]:
        cst+=f'\nIO_LOC "{name}" {pair};\nIO_PORT "{name}" PULL_MODE=NONE DRIVE=3.5;\n'
    (src/'display.cst').write_text(cst,encoding='utf-8')
    sdc=(ROOT/'fpga/board/tang_mega_138k_pro.sdc').read_text(encoding='utf-8')
    sdc+='''
create_generated_clock -name pixel_clk -source [get_ports {clk}] -master_clock sys_clk -multiply_by 49 -divide_by 50 [get_nets {pixel_clk}]
create_generated_clock -name serial_clk -source [get_ports {clk}] -master_clock sys_clk -multiply_by 49 -divide_by 10 [get_nets {serial_clk}]
# Only first-stage handshake synchronizers are exempt. Bundled data is held
# across the round trip; explicitly bound its route to one 50 MHz period.
set_false_path -to [get_pins {observer_cdc/request_meta_s0/D observer_cdc/ack_meta_s0/D}]
set_max_delay 20 -from [get_cells {observer_cdc/held_data*}] -to [get_cells {observer_cdc/dest_data*}]
# Destination CE is asserted only after two acknowledgement synchronizer stages;
# the source cannot update again until the next request makes a round trip.
# Same-edge hold analysis does not describe this handshake. Keep max-delay setup.
set_false_path -hold -from [get_cells {observer_cdc/held_data*}] -to [get_cells {observer_cdc/dest_data*}]
'''
    (src/'display.sdc').write_text(sdc,encoding='utf-8')
    def tcl(path):return '{'+path.resolve().as_posix()+'}'
    script=folder/'build.tcl'
    script.write_text('\n'.join([
        f'create_project -name display_restore -dir {tcl(folder)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force',
        f'add_file {{*}}[lsort [glob -directory {tcl(src)} *.v]]',
        f'add_file {tcl(src/"display.cst")}',f'add_file {tcl(src/"display.sdc")}',
        'set_option -top_module calibration_display_top','set_option -output_base_name calibration_display_top',
        'set_option -use_cpu_as_gpio 1','set_option -use_sspi_as_gpio 1',
        'set_option -resource_sharing 0','set_option -num_critical_paths 50',
        'run syn','run pnr','exit','']),encoding='utf-8')
    output=folder/'display_restore/impl/pnr/calibration_display_top.fs'
    manifest=folder/'manifest.json';manifest.unlink(missing_ok=True)
    if args.prepare_only:
        print('PREPARED',folder);return
    if output.exists():output.chmod(0o666);output.unlink()
    with (folder/'build.log').open('w',encoding='utf-8') as log:
        result=subprocess.run([args.gowin,str(script)],cwd=src,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode or not output.is_file():raise RuntimeError('Build failed: '+str(folder/'build.log'))
    report=output.with_suffix('.tr').read_text(encoding='utf-8',errors='replace')
    timing=re.findall(r'(sys_clk|pixel_clk|serial_clk)\s+(setup|hold)\s+(-?[\d.]+)\s+(\d+)',report)
    if ({(r[0],r[1]) for r in timing}!={(c,k) for c in ('sys_clk','pixel_clk','serial_clk') for k in ('setup','hold')}
        or any(float(r[2])<0 or int(r[3]) for r in timing) or re.search(r'Slack\s*:\s*-',report)):
        raise RuntimeError('Timing not verified: '+str(output.with_suffix('.tr')))
    log=(folder/'build.log').read_text(encoding='utf-8',errors='replace')
    if re.search(r'(cannot find|not found|no objects|does not match|invalid.*constraint)',log,re.I):
        raise RuntimeError('Unresolved constraint/object: inspect build.log')
    manifest.write_text(json.dumps(dict(mode='display_restore',restored_calibration=info,
        synthesized=True,routed=True,physical_output_supported=True,auto_start=False,
        timing=timing,output=str(output),sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(src.iterdir())}),indent=2),encoding='utf-8')
    print('BUILD PASS',output)

if __name__=='__main__':main()
