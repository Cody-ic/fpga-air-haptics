"""Build an explicitly calibrated or uncalibrated transmitter with theory display.

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
from video_constraints import append_video_constraints

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from fpga.board.build_calibration import load_restore

def make_top(packed,z_mm,restore=True,calibration_level=30):
    if not restore:
        packed=0
    text=(ROOT/'fpga/preview/theory_preview_top.v').read_text(encoding='utf-8')
    def replace(old,new):
        nonlocal text
        if text.count(old)!=1:raise ValueError('Top template changed: '+old)
        text=text.replace(old,new)
    # Combined control images always expose the ordinary display, regardless
    # of a locally selected standalone video-link diagnostic macro.
    text,count=re.subn(r'(^`define FPGA_VIDEO_LINK_TEST[ \t]+)[01]([ \t]*$)',
                       lambda m:m[1]+'0'+m[2],text,flags=re.MULTILINE)
    if count!=1:raise ValueError('Video link test macro missing or ambiguous')
    replace('module theory_preview_top(', 'module calibration_display_top(')
    replace('// Standalone PREVIEW image. Shift-register outputs are constant-disabled at the pads.',
            '// Transmitter stays on 50 MHz; display observes atomic snapshots on 49 MHz.')
    replace('    wire boundary,valid;',f'''    wire [188:0] source_snapshot,display_snapshot;
    wire source_reset_n;
    board_top #(.CAL_RESTORE_ENABLE({int(restore)}),.CAL_Z_UM({z_mm*1000}),.CAL_LEVEL({calibration_level}),
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

def configure_board(text,restore,startup_calibration=None):
    # Only an explicitly selected startup image retains CAL2. Other images
    # must not inherit standalone debug mode or calibration by accident.
    for macro,value in (('FPGA_CALIBRATION_ENABLE',int(restore or startup_calibration is not None)),
                        ('FPGA_CALIBRATION_SOURCE',int(startup_calibration=='adc')),('FPGA_DEBUG_U102',0)):
        text,count=re.subn(r'(^`define '+macro+r'[ \t]+)[01]([ \t]*$)',
                           lambda match:match[1]+str(value)+match[2],text,flags=re.MULTILINE)
        if count!=1:raise ValueError('Macro missing or ambiguous: '+macro)
    return text


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--calibration-file',help='Restore a complete measured calibration file')
    mode.add_argument('--uncalibrated',action='store_true',help='Explicitly build with zero phase corrections')
    mode.add_argument('--startup-calibration',choices=('adc','scope'),
                      help='Retain CAL2 on-board solving and remeasurement; starts waiting, not emitting')
    p.add_argument('--calibration-z-mm',type=int,choices=(50,70,100))
    p.add_argument('--calibration-level',type=int,choices=range(1,101),default=30)
    p.add_argument('--calibration-reference',type=int,choices=range(64),default=0)
    p.add_argument('--gowin',default='D:/tools/Gowin-1.9.12.04/IDE/bin/gw_sh.exe')
    p.add_argument('--output-root',type=Path,help='Build root, preferably an ASCII path on Windows')
    p.add_argument('--prepare-only',action='store_true')
    args=p.parse_args(argv)
    if args.calibration_file and args.calibration_z_mm is None:
        p.error('--calibration-file requires --calibration-z-mm')
    if args.uncalibrated and args.calibration_z_mm is not None:
        p.error('--calibration-z-mm applies only to --calibration-file')
    if args.uncalibrated and (args.calibration_level!=30 or args.calibration_reference!=0):
        p.error('Calibration level/reference apply only to restore or startup calibration')
    if args.startup_calibration:
        if args.calibration_z_mm is None:
            args.calibration_z_mm=50
        if args.calibration_reference!=0:
            p.error('Startup reference defaults to channel 0; select another reference through CAL2 SETUP')
    return args


def main(argv=None):
    args=parse_args(argv)
    restore=bool(args.calibration_file)
    startup=args.startup_calibration
    packed,info=load_restore(args.calibration_file,args.calibration_z_mm,args.calibration_reference,args.calibration_level) if restore else (0,None)
    output_root=args.output_root.resolve() if args.output_root else ROOT/'.runtime/display-restore'
    folder=output_root/(f'z{args.calibration_z_mm}' if restore else
                        f'startup_{startup}_z{args.calibration_z_mm}' if startup else 'uncalibrated')
    project='display_restore' if restore else f'display_startup_{startup}' if startup else 'display_uncalibrated'
    src=folder/'src';src.mkdir(parents=True,exist_ok=True)
    for path in [*(ROOT/'fpga/rtl').glob('*.v'),*(ROOT/'fpga/preview').glob('*.v')]:
        shutil.copy2(path,src/path.name)
    board=src/'board_top.v'
    board.write_text(configure_board(board.read_text(encoding='utf-8'),restore,startup),encoding='utf-8')
    (src/'calibration_display_top.v').write_text(
        make_top(packed,args.calibration_z_mm if restore or startup else 70,restore=restore,
                 calibration_level=args.calibration_level),encoding='utf-8')
    if startup:
        generate(src,preview_only=False,startup_calibration=True)
    else:
        generate(src,preview_only=False)
    cst=(ROOT/'fpga/board/tang_mega_138k_pro.cst').read_text(encoding='utf-8')
    cst=append_video_constraints(cst)
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
        f'create_project -name {project} -dir {tcl(folder)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force',
        f'add_file {{*}}[lsort [glob -directory {tcl(src)} *.v]]',
        f'add_file {tcl(src/"display.cst")}',f'add_file {tcl(src/"display.sdc")}',
        'set_option -top_module calibration_display_top','set_option -output_base_name calibration_display_top',
        'set_option -use_cpu_as_gpio 1','set_option -use_sspi_as_gpio 1',
        'set_option -resource_sharing 0','set_option -num_critical_paths 50',
        'run syn','run pnr','exit','']),encoding='utf-8')
    output=folder/project/'impl/pnr/calibration_display_top.fs'
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
    manifest.write_text(json.dumps(dict(mode=project,calibration_applied=restore,restored_calibration=info,
        startup_calibration=(dict(source=startup,source_code=int(startup=='adc'),
            default_position_um=[0,0,args.calibration_z_mm*1000],default_level=args.calibration_level,
            default_reference=0,measurement_required=True,solver_on_fpga=True,
            verification_required=True,entry_protocol='CAL2',business_protocol_after_release='HAP3',
            cal_profile_after_release=1,retention='volatile_until_reset',
            boot_state='waiting_for_setup_and_ready_no_emission') if startup else None),
        synthesized=True,routed=True,physical_output_supported=True,auto_start=False,
        timing=timing,output=str(output),sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(src.iterdir())}),indent=2),encoding='utf-8')
    print('BUILD PASS',output)

if __name__=='__main__':main()
