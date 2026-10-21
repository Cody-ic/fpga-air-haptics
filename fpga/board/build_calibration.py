"""Build normal/scope/ADC configurations without editing source macros or flashing."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def check_timing(report):
    match = re.search(r"sys_clk\s+50\.000\(MHz\)\s+([0-9.]+)\(MHz\)", report)
    summaries = re.findall(r"sys_clk\s+(setup|hold)\s+(-?[0-9.]+)\s+(\d+)", report)
    if (not match or float(match[1]) < 50 or {s[0] for s in summaries} != {"setup", "hold"}
            or any(float(tns) < 0 or int(count) != 0 for _, tns, count in summaries)):
        raise ValueError("50 MHz setup/hold timing did not pass")
    return float(match[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("normal", "scope", "adc", "all"), default="all")
    parser.add_argument("--gowin", default=r"D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe")
    parser.add_argument("--syn-only", action="store_true")
    args = parser.parse_args()
    modes = ("normal", "scope", "adc") if args.mode == "all" else (args.mode,)
    for mode in modes:
        folder = ROOT / ".runtime/fpga-calibration" / mode
        staging = folder / "rtl"
        staging.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "fpga/rtl").glob("*.v"):
            shutil.copy2(source, staging / source.name)
        top = staging / "board_top.v"
        text = top.read_text(encoding="utf-8")
        for key, value in (("ENABLE", int(mode != "normal")), ("SOURCE", int(mode == "adc"))):
            text = re.sub(r"(`define FPGA_CALIBRATION_" + key + r" )\d+", rf"\g<1>{value}", text)
        top.write_text(text, encoding="utf-8")
        def tcl_path(path):
            return "{" + str(path.resolve()).replace("\\", "/") + "}"
        script = folder / "build.tcl"
        script.write_text("\n".join([
            f"create_project -name calibration_{mode} -dir {tcl_path(folder)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force",
            f"add_file {{*}}[lsort [glob -directory {tcl_path(staging)} *.v]]",
            f"add_file {tcl_path(ROOT / 'fpga/board/tang_mega_138k_pro.cst')}",
            f"add_file {tcl_path(ROOT / 'fpga/board/tang_mega_138k_pro.sdc')}",
            "set_option -top_module board_top", "set_option -output_base_name board_top",
            "set_option -use_cpu_as_gpio 1", "set_option -use_sspi_as_gpio 1",
            "set_option -resource_sharing 0", "set_option -num_critical_paths 100",
            "run syn", *( [] if args.syn_only else ["run pnr"]), "exit", ""
        ]), encoding="utf-8")
        expected = folder / ("calibration_" + mode) / ("impl/gwsynthesis/board_top.vg" if args.syn_only else "impl/pnr/board_top.fs")
        # Remove only this generated file, so an old artifact cannot make a
        # failed vendor build look successful.
        if expected.exists():
            expected.chmod(stat.S_IWRITE | stat.S_IREAD)
            expected.unlink()
        (folder / "manifest.json").unlink(missing_ok=True)
        with (folder / "build.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([args.gowin, str(script)], cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for line in process.stdout:
                decoded = line.decode("utf-8", errors="replace")
                print(decoded, end="", flush=True)
                log.write(decoded)
            code = process.wait()
        # Some vendor command errors can still exit 0; require the expected output.
        if code or not expected.is_file():
            raise SystemExit(f"{mode}: build failed; see {folder / 'build.log'}")
        fmax = None
        if not args.syn_only:
            report = expected.with_suffix(".tr").read_text(encoding="utf-8", errors="replace")
            try:
                fmax = check_timing(report)
            except ValueError:
                raise SystemExit(f"{mode}: 50 MHz timing failed; see {expected.with_suffix('.tr')}")
        manifest = dict(mode=mode, calibration_enable=int(mode != "normal"), source=int(mode == "adc"),
                        fmax_mhz=fmax, output=str(expected), sha256=hashlib.sha256(expected.read_bytes()).hexdigest(),
                        sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(staging.glob('*.v'))})
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"BUILD PASS {mode}: {expected}", flush=True)


if __name__ == "__main__":
    main()
