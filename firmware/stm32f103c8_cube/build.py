"""Build this CubeMX/CubeIDE project without flashing a connected device."""
import argparse
import glob
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def find_gcc(explicit=None):
    choices = [explicit, os.environ.get("ARM_GCC"), shutil.which("arm-none-eabi-gcc")]
    choices += sorted(glob.glob("D:/STM32Dev/STM32CubeIDE*/STM32CubeIDE/plugins/"
                               "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin/arm-none-eabi-gcc.exe"))
    for candidate in choices:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit("Set ARM_GCC or pass --gcc with the Arm GNU compiler path.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gcc")
    args = parser.parse_args()
    gcc = find_gcc(args.gcc)
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    flags = ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-O2", "-g3",
             "-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-common",
             "-ffunction-sections", "-fdata-sections", "-fno-math-errno", "-fstack-usage",
             "-DUSE_HAL_DRIVER", "-DSTM32F103xB"]
    includes = ["Core/Inc", "Drivers/STM32F1xx_HAL_Driver/Inc",
                "Drivers/STM32F1xx_HAL_Driver/Inc/Legacy",
                "Drivers/CMSIS/Device/ST/STM32F1xx/Include", "Drivers/CMSIS/Include"]
    flags += ["-I" + path for path in includes]
    sources = sorted((ROOT / "Core/Src").glob("*.c"))
    sources += sorted((ROOT / "Core/Startup").glob("*.s"))
    sources += sorted((ROOT / "Drivers/STM32F1xx_HAL_Driver/Src").glob("*.c"))
    objects = []
    for source in sources:
        obj = output / (source.stem + ".o")
        subprocess.run([str(gcc), *flags, "-c", str(source.relative_to(ROOT)), "-o", str(obj.relative_to(ROOT))],
                       cwd=ROOT, check=True)
        objects.append(str(obj.relative_to(ROOT)))
    elf = output / "haptics_f103c8.elf"
    subprocess.run([str(gcc), *flags, "-Thaptics_memory.ld", "--specs=nosys.specs", "--specs=nano.specs",
                    "-Wl,--gc-sections,-Map=build/haptics_f103c8.map,--print-memory-usage",
                    *objects, "-Wl,--start-group", "-lc", "-lm", "-Wl,--end-group", "-o", str(elf.relative_to(ROOT))],
                   cwd=ROOT, check=True)
    subprocess.run([str(gcc.with_name("arm-none-eabi-size" + gcc.suffix)), str(elf.relative_to(ROOT))],
                   cwd=ROOT, check=True)
    print(elf)


if __name__ == "__main__":
    main()
