"""Build the isolated preview image. Never downloads to a board."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from generate_assets import generate
from video_constraints import append_video_constraints

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gowin", default="D:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe")
    parser.add_argument("--syn-only", action="store_true")
    parser.add_argument("--link-test", action="store_true", help="Static video link diagnostic; UART and array outputs disabled")
    parser.add_argument("--output-root", type=Path, help="Build directory; prefer an ASCII path on Windows")
    parser.add_argument("--prepare-only", action="store_true", help="Generate sources and project without running Gowin")
    args = parser.parse_args()
    folder = args.output_root.resolve() if args.output_root else ROOT / ".runtime/theory-preview/build"
    project = "video_link_test" if args.link_test else "preview"
    source = folder / "src"
    source.mkdir(parents=True, exist_ok=True)
    for path in [*(ROOT / "fpga/rtl").glob("*.v"), *(ROOT / "fpga/preview").glob("*.v")]:
        shutil.copy2(path,source/path.name)
    top = source / "theory_preview_top.v"
    top_text, count = re.subn(r"(^`define FPGA_VIDEO_LINK_TEST[ \t]+)[01]([ \t]*$)",
        lambda m: m[1] + str(int(args.link_test)) + m[2],
        top.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if count != 1:
        raise ValueError("FPGA_VIDEO_LINK_TEST macro missing or ambiguous")
    top.write_text(top_text, encoding="utf-8")
    generate(source)
    # Preserve current array pad mapping; append the official Pro DVI0 mapping.
    cst = (ROOT / "fpga/board/tang_mega_138k_pro.cst").read_text(encoding="utf-8")
    cst = append_video_constraints(cst)
    (source/"preview.cst").write_text(cst,encoding="utf-8")
    (source/"preview.sdc").write_text("""create_clock -name sys_clk -period 20 [get_ports {clk}]
create_generated_clock -name pixel_clk -source [get_ports {clk}] -master_clock sys_clk -multiply_by 49 -divide_by 50 [get_nets {pixel_clk}]
create_generated_clock -name serial_clk -source [get_ports {clk}] -master_clock sys_clk -multiply_by 49 -divide_by 10 [get_nets {serial_clk}]
""",encoding="ascii")
    def tcl(path):
        return "{"+path.resolve().as_posix()+"}"
    script=folder/"build.tcl"
    script.write_text("\n".join([
        f"create_project -name {project} -dir {tcl(folder)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force",
        f"add_file {{*}}[lsort [glob -directory {tcl(source)} *.v]]",
        f"add_file {tcl(source/'preview.cst')}",f"add_file {tcl(source/'preview.sdc')}",
        "set_option -top_module theory_preview_top","set_option -output_base_name theory_preview_top",
        "set_option -use_cpu_as_gpio 1","set_option -use_sspi_as_gpio 1",
        "set_option -resource_sharing 0","set_option -num_critical_paths 30",
        "run syn",*(["run pnr"] if not args.syn_only else []),"exit",""]),encoding="utf-8")
    # readmemh paths are resolved from the source directory by synthesis.
    for asset in source.glob("*.hex"):
        shutil.copy2(asset,folder/asset.name)
    output=folder/project/"impl"/("gwsynthesis/theory_preview_top.vg" if args.syn_only else "pnr/theory_preview_top.fs")
    manifest=folder/"manifest.json"
    manifest.unlink(missing_ok=True)
    if args.prepare_only:
        print(f"PREPARED: {folder}")
        return
    if output.exists():
        output.chmod(0o666)
        output.unlink()
    with (folder/"build.log").open("w",encoding="utf-8") as log:
        result=subprocess.run([args.gowin,str(script)],cwd=source,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode or not output.is_file():
        raise SystemExit(f"BUILD FAILED: {folder/'build.log'}")
    log_text=(folder/'build.log').read_text(encoding='utf-8',errors='replace')
    if re.search(r'(cannot find|not found|no objects|does not match|invalid.*constraint)',log_text,re.I):
        raise SystemExit(f"UNRESOLVED CONSTRAINT: {folder/'build.log'}")
    timing=None
    if not args.syn_only:
        report=output.with_suffix(".tr").read_text(encoding="utf-8",errors="replace")
        timing=re.findall(r"(pixel_clk|serial_clk)\s+(setup|hold)\s+(-?[\d.]+)\s+(\d+)",report)
        violated=re.findall(r"<Numbers of (?:Setup|Hold) Violated Endpoints>:(\d+)",report)
        negative_slacks=re.findall(r"Slack\s*:\s*(-[\d.]+)",report)
        if ({(r[0],r[1]) for r in timing} != {(c,k) for c in ('pixel_clk','serial_clk') for k in ('setup','hold')}
            or any(float(r[2])<0 or int(r[3]) for r in timing)
            or len(violated)!=2 or any(int(v) for v in violated) or negative_slacks):
            raise SystemExit(f"TIMING NOT VERIFIED: {output.with_suffix('.tr')}")
    manifest.write_text(json.dumps(dict(mode="video_link_test" if args.link_test else "theory_preview",
        video_link_test=args.link_test,uart_available=not args.link_test,
        preview_only=True,downloaded=False,physical_output_disabled=True,
        synthesized=True,routed=not args.syn_only,timing=timing,output=str(output),
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.iterdir())}),indent=2),encoding="utf-8")
    print(f"BUILD PASS: {output}")


if __name__=="__main__":
    main()
