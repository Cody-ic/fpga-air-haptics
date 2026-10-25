"""Build normal/scope/ADC or explicit U102 debug configuration; never flashes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def load_restore(path, calibration_z_mm=70, calibration_reference=0):
    """Validate measured input for an explicit reuse build, not a new calibration."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "firmware/xiao_mg24"))
    from firmware.xiao_mg24.calibrate_fpga import Settings, validate_document
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if calibration_z_mm not in (70, 100):
        raise ValueError("Supported measured reference heights are 70 and 100 mm")
    offsets = validate_document(doc, dict(rows=8, cols=8, pitch_um=10300),
                                Settings(position_um=(0, 0, calibration_z_mm*1000), level=30, reference=calibration_reference))
    measurements = doc.get("measurements", [])
    expected = 384 if doc.get("imported_and_reverified") else 1140
    if len(measurements) != expected or any(m.get("good") is not True for m in measurements):
        raise ValueError("Restore requires a complete, good measured calibration record")
    nominal = doc.get("theoretical_phases")
    if (not isinstance(nominal, list) or len(nominal) != 64
            or any(type(v) is not int or not 0 <= v < 64 for v in nominal)):
        raise ValueError("Restore requires the recorded 64-channel nominal phase table")
    from desktop_app.model import Config, ArraySpec, focus_phases
    expected_nominal = list(map(int, focus_phases(Config(), (0, 0, calibration_z_mm), ArraySpec(8,8,10300))))
    if nominal != expected_nominal:
        raise ValueError("Recorded nominal phases do not match the calibration position")
    packed = sum(v << (8 * i) for i, v in enumerate(offsets))
    return packed, dict(file=str(Path(path).resolve()), sha256=hashlib.sha256(raw).hexdigest(),
                        created_at=doc.get("created_at"), position_um=doc["position_um"],
                        level=doc["level"], reference=doc["reference"], phase_offsets=offsets,
                        quality_policy=doc.get("quality_policy", "relative_power_spread_15percent"),
                        calibration_build=doc.get("calibration_build"),
                        windows_per_reading=doc.get("windows_per_reading",1),
                        expected_center_phases=[(a+b) % 64 for a,b in zip(nominal, offsets)],
                        acoustic_reverified_this_build=False)


def check_timing(report):
    match = re.search(r"sys_clk\s+50\.000\(MHz\)\s+([0-9.]+)\(MHz\)", report)
    summaries = re.findall(r"sys_clk\s+(setup|hold)\s+(-?[0-9.]+)\s+(\d+)", report)
    if (not match or float(match[1]) < 50 or {s[0] for s in summaries} != {"setup", "hold"}
            or any(float(tns) < 0 or int(count) != 0 for _, tns, count in summaries)):
        raise ValueError("50 MHz setup/hold timing did not pass")
    return float(match[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("normal", "scope", "adc", "u102_debug", "restore", "all"), default="all")
    parser.add_argument("--calibration-file", help="Required only for explicit restore mode; reuses measurements without acoustic re-verification")
    parser.add_argument("--gowin", default=r"D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe")
    parser.add_argument("--syn-only", action="store_true")
    parser.add_argument("--cal-null-scaled-qc", action="store_true", help="Explicit ADC build with bounded absolute near-null repeatability criterion")
    button = parser.add_mutually_exclusive_group()
    button.add_argument("--button-focus", action="store_true", help="Restore build with 3 s / level 1 physical center-test button")
    button.add_argument("--button-line", action="store_true", help="Restore build with 6 s / level 100 / 0.5 Hz physical line-test button")
    button.add_argument("--button-circle", action="store_true", help="Restore build with 6 s / level 100 / 0.5 Hz physical circle-test button")
    parser.add_argument("--button-radius-mm", type=int, choices=range(1,31), default=20,
                        help="Line half-length or circle radius, default 20 mm")
    parser.add_argument("--button-z-mm", type=int, choices=(70,100), default=100,
                        help="Physical button target height (independent of calibration height)")
    parser.add_argument("--calibration-z-mm", type=int, choices=(70,100), default=70,
                        help="Explicit reference height of the measured restore file")
    parser.add_argument("--calibration-reference", type=int, choices=range(64), default=0,
                        help="Explicit reference channel of the measured restore file")
    args = parser.parse_args()
    button_motion = args.button_line or args.button_circle
    button_enabled = args.button_focus or button_motion
    if args.button_radius_mm != 20 and not button_motion:
        parser.error("--button-radius-mm requires --button-line or --button-circle")
    if args.cal_null_scaled_qc and args.mode != "adc":
        parser.error("--cal-null-scaled-qc requires explicit adc mode")
    if button_enabled and args.mode != "restore":
        parser.error("Button tests require explicit restore mode")
    if args.button_z_mm != 100 and not button_enabled:
        parser.error("--button-z-mm requires a button test")
    if args.calibration_z_mm != 70 and args.mode != "restore":
        parser.error("--calibration-z-mm applies to explicit restore mode only")
    if args.calibration_reference != 0 and args.mode != "restore":
        parser.error("--calibration-reference applies to explicit restore mode only")
    if (args.mode == "restore") != bool(args.calibration_file):
        parser.error("--mode restore requires --calibration-file; other modes must omit it")
    packed, restored = load_restore(args.calibration_file, args.calibration_z_mm, args.calibration_reference) if args.calibration_file else (0, None)
    modes = ("normal", "scope", "adc") if args.mode == "all" else (args.mode,)
    for mode in modes:
        folder = ROOT / ".runtime/fpga-calibration-8x8" / (mode + ("_button_circle" if args.button_circle else "_button_line" if args.button_line else "_button_focus" if args.button_focus else "")
                    + ("_z100" if args.calibration_z_mm == 100 else "")
                    + (f"_ref{args.calibration_reference}" if args.calibration_reference else "")
                    + ("_null_qc" if args.cal_null_scaled_qc else "")
                    + ("_focus70" if button_enabled and args.button_z_mm==70 else "")
                    + (f"_r{args.button_radius_mm}" if button_motion and args.button_radius_mm!=20 else ""))
        staging = folder / "rtl"
        staging.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "fpga/rtl").glob("*.v"):
            shutil.copy2(source, staging / source.name)
        top = staging / "board_top.v"
        text = top.read_text(encoding="utf-8")
        if args.cal_null_scaled_qc:
            text, count = re.subn(r"(parameter integer CAL_NULL_SCALED_QC = )0", r"\g<1>1", text)
            if count != 1:
                raise ValueError("Near-null QC parameter missing or ambiguous")
        cal_enable = int(mode in ("scope", "adc", "restore"))
        debug_u102 = int(mode == "u102_debug")
        for key, value in (("ENABLE", cal_enable), ("SOURCE", int(mode == "adc"))):
            text = re.sub(r"(`define FPGA_CALIBRATION_" + key + r" )\d+", rf"\g<1>{value}", text)
        text = re.sub(r"(`define FPGA_DEBUG_U102 )\d+", rf"\g<1>{debug_u102}", text)
        if restored is not None:
            text, n1 = re.subn(r"(parameter integer CAL_RESTORE_ENABLE = )0", r"\g<1>1", text)
            text, n2 = re.subn(r"(parameter \[511:0\] CAL_RESTORE_OFFSETS = )512'd0",
                              rf"\g<1>512'h{packed:0128x}", text)
            if (n1, n2) != (1, 1):
                raise ValueError("Restore parameters missing or ambiguous")
            text, n3 = re.subn(r"(parameter integer CAL_Z_UM\s*=\s*)70000",
                              rf"\g<1>{args.calibration_z_mm*1000}", text)
            if n3 != 1:
                raise ValueError("Calibration height parameter missing or ambiguous")
        top.write_text(text, encoding="utf-8")
        if button_enabled:
            text, count = re.subn(r"(parameter integer BUTTON_FOCUS_ENABLE = )0", r"\g<1>1", text)
            if count != 1:
                raise ValueError("Button focus parameter missing or ambiguous")
            text, count = re.subn(r"(parameter integer BUTTON_FOCUS_Z_UM = )100000",
                                 rf"\g<1>{args.button_z_mm*1000}", text)
            if count != 1:
                raise ValueError("Button focus height missing or ambiguous")
            if button_motion:
                for key, old, new in (("MS",3000,6000), ("LEVEL",1,100),
                                      ("SHAPE",0,3 if args.button_circle else 1), ("RADIUS_UM",0,args.button_radius_mm*1000)):
                    text, count = re.subn(r"(parameter integer BUTTON_FOCUS_"+key+r" = )"+str(old)+r"\b",
                                         rf"\g<1>{new}", text)
                    if count != 1:
                        raise ValueError("Button motion parameter missing or ambiguous: "+key)
            top.write_text(text, encoding="utf-8")
        constraints = []
        for suffix in ("cst", "sdc"):
            source = ROOT / f"fpga/board/tang_mega_138k_pro.{suffix}"
            target = folder / source.name
            shutil.copy2(source, target)
            constraints.append(target)
        def tcl_path(path):
            return "{" + str(path.resolve()).replace("\\", "/") + "}"
        script = folder / "build.tcl"
        script.write_text("\n".join([
            f"create_project -name calibration_{mode} -dir {tcl_path(folder)} -pn GW5AST-LV138FPG676AC1/I0 -device_version B -force",
            f"add_file {{*}}[lsort [glob -directory {tcl_path(staging)} *.v]]",
            *[f"add_file {tcl_path(path)}" for path in constraints],
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
        manifest = dict(board="R2_8x8_shift", rows=8, cols=8, pitch_um=10300, cal_z_um=args.calibration_z_mm*1000,
                        connector="X4_silk_labels", synthesis_only=args.syn_only,
                        constraints={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in constraints},
                        mode=mode, calibration_enable=cal_enable, source=int(mode == "adc"),
                        debug_u102=debug_u102,
                        calibration_quality_policy=(restored["quality_policy"] if restored else
                            "near_null_pair_power_floor_1over16" if args.cal_null_scaled_qc else "relative_power_spread_15percent"),
                        button_focus=(dict(z_um=args.button_z_mm*1000, level=100 if button_motion else 1,
                                           deadline_ms=6000 if button_motion else 3000,
                                           shape="CIRCLE" if args.button_circle else "LINE_X" if args.button_line else "POINT",
                                           radius_um=args.button_radius_mm*1000 if button_motion else 0, repeat_millihz=500,
                                           play_pin="G15", stop_pin="F15") if button_enabled else None),
                        restored_calibration=restored,
                        debug_waveform=(dict(channel=3, component="U102", frequency_hz=40000,
                                             duty_percent=50) if debug_u102 else None),
                        fmax_mhz=fmax, output=str(expected), sha256=hashlib.sha256(expected.read_bytes()).hexdigest(),
                        sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(staging.glob('*.v'))})
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"BUILD PASS {mode}: {expected}", flush=True)


if __name__ == "__main__":
    main()
