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
    profiles = parser.add_mutually_exclusive_group()
    profiles.add_argument("--channel-test", action="store_true",
                          help="Build the existing sequential diagnostic profile")
    profiles.add_argument("--pin-test", action="store_true",
                          help="Build the parameterized fixed-pin diagnostic profile")
    parser.add_argument("--test-channel", type=int, choices=range(16),
                        help="PinTest logical channel (2=PA8, 11=PB11); default from pin_test_config.h")
    parser.add_argument("--test-on-ms", type=int,
                        help="PinTest burst duration; default from pin_test_config.h")
    args = parser.parse_args()
    if not args.pin_test and (args.test_channel is not None or args.test_on_ms is not None):
        parser.error("--test-channel/--test-on-ms require --pin-test")
    if args.test_on_ms is not None and not 1 <= args.test_on_ms <= 0x7fffffff:
        parser.error("--test-on-ms must be positive and less than 2^31 milliseconds")
    gcc = find_gcc(args.gcc)
    output = ROOT / "build"
    if args.pin_test:
        output /= "pin_test"
    elif args.channel_test:
        output /= "channel_test"
    output.mkdir(parents=True, exist_ok=True)
    flags = ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-O2", "-g3",
             "-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-common",
             "-ffunction-sections", "-fdata-sections", "-fno-math-errno", "-fstack-usage",
             "-DUSE_HAL_DRIVER", "-DSTM32F103xB",
             "-DF103_CHANNEL_TEST=" + str(int(args.channel_test or args.pin_test)),
             "-DF103_PIN_TEST=" + str(int(args.pin_test))]
    if args.test_channel is not None:
        flags.append("-DF103_PIN_TEST_CHANNEL=" + str(args.test_channel))
    if args.test_on_ms is not None:
        flags.append("-DF103_PIN_TEST_ON_MS=" + str(args.test_on_ms))
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
                    "-Wl,--gc-sections,-Map=" + (output / "haptics_f103c8.map").relative_to(ROOT).as_posix()
                    + ",--print-memory-usage",
                    *objects, "-Wl,--start-group", "-lc", "-lm", "-Wl,--end-group", "-o", str(elf.relative_to(ROOT))],
                   cwd=ROOT, check=True)
    subprocess.run([str(gcc.with_name("arm-none-eabi-size" + gcc.suffix)), str(elf.relative_to(ROOT))],
                   cwd=ROOT, check=True)
    print(elf)


if __name__ == "__main__":
    main()
