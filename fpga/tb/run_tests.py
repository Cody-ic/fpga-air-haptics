"""Icarus regression runner; build files and logs stay under .runtime."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iverilog", default=shutil.which("iverilog"))
    parser.add_argument("--bench", action="append", help="Default: every tb_*.v")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--uart-baud", type=int, help="Accelerate a UART bench in a generated copy; keep all assertions and clock/carrier settings")
    args = parser.parse_args()
    if not args.iverilog:
        parser.error("Provide --iverilog PATH")
    compiler = Path(args.iverilog).resolve()
    runtime = compiler.with_name("vvp.exe" if os.name == "nt" else "vvp")
    rtl = sorted((ROOT / "fpga/rtl").glob("*.v"))
    benches = args.bench or [p.stem for p in sorted((ROOT / "fpga/tb").glob("tb_*.v"))]
    def run(name):
        folder = ROOT / ".runtime/calibration/tests" / (name + (f"_uart{args.uart_baud}" if args.uart_baud else ""))
        folder.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, TMP=str(folder), TMPDIR=str(folder))
        output = folder / "simulation.vvp"
        bench = ROOT / "fpga/tb" / (name + ".v")
        if args.uart_baud:
            text, count = re.subn(r"(localparam\s+integer\s+BAUD\s*=\s*)115200", rf"\g<1>{args.uart_baud}", bench.read_text(encoding="utf-8"))
            if count != 1:
                raise ValueError(f"{name}: no unique UART baud declaration")
            bench = folder / (name + ".v")
            bench.write_text(text, encoding="utf-8")
        commands = [[str(compiler), "-g2012", "-Wall", "-s", name, "-o", str(output),
                     *map(str, rtl), str(bench)], [str(runtime), "-i", str(output)]]
        with (folder / "test.log").open("w", encoding="utf-8") as log:
            for command in commands:
                log.flush()
                try:
                    result = subprocess.run(command, cwd=ROOT / "fpga", env=env, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name}: exceeded {args.timeout}s; see {folder / 'test.log'}", flush=True)
                    return False
                if result.returncode:
                    print(f"FAIL {name}: see {folder / 'test.log'}", flush=True)
                    return False
        print(f"PASS {name}", flush=True)
        return True
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, benches))
    print(f"{sum(results)}/{len(results)} benches passed")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
