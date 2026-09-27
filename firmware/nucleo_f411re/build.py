"""Reproducible CLI build; no CubeIDE workspace generation or network needed."""
import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def find_arm(explicit=None):
    choices = [explicit, os.environ.get("ARM_GCC"), shutil.which("arm-none-eabi-gcc")]
    choices += sorted(glob.glob("D:/STM32Dev/STM32CubeIDE*/STM32CubeIDE/plugins/"
                                "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin/arm-none-eabi-gcc.exe"))
    for candidate in choices:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise SystemExit("找不到 Arm GNU 工具链。设置 ARM_GCC，或使用 --gcc 完整路径。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gcc")
    args = parser.parse_args()
    gcc = find_arm(args.gcc)
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    flags = ["-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=hard",
             "-O2", "-g3", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-common",
             "-ffunction-sections", "-fdata-sections", "-fno-math-errno", "-fstack-usage",
             "-Iinclude", "-Ivendor"]
    objects = []
    for source in sorted((ROOT / "src").glob("*")):
        if source.suffix not in (".c", ".S"):
            continue
        target = out / (source.stem + ".o")
        subprocess.run([str(gcc), *flags, "-c", str(source), "-o", str(target)], cwd=ROOT, check=True)
        objects.append(str(target))
    elf = out / "haptics_f411re.elf"
    subprocess.run([str(gcc), *flags, "-Tstm32f411re.ld", "--specs=nosys.specs",
                    "-Wl,--gc-sections,-Map=build/haptics_f411re.map,--print-memory-usage",
                    *objects, "-Wl,--start-group", "-lc", "-lm", "-Wl,--end-group", "-o", str(elf)], cwd=ROOT, check=True)
    suffix = ".exe" if gcc.suffix == ".exe" else ""
    objcopy = gcc.with_name("arm-none-eabi-objcopy" + suffix)
    for fmt, extension in (("binary", "bin"), ("ihex", "hex")):
        subprocess.run([str(objcopy), "-O", fmt, str(elf), str(out / (elf.stem + "." + extension))], check=True)
    subprocess.run([str(gcc.with_name("arm-none-eabi-size" + suffix)), str(elf)], check=True)
    report = {"compiler": subprocess.check_output([str(gcc), "--version"], text=True).splitlines()[0],
              "hardware_tested": False, "outputs": {}}
    for extension in ("elf", "bin", "hex"):
        path = out / (elf.stem + "." + extension)
        report["outputs"][path.name] = {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (out / "build-info.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
