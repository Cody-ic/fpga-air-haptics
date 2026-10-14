"""Refresh the self-contained CubeIDE copy of the shared protocol/geometry core."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=ROOT)
    args = parser.parse_args()
    shared = ROOT.parent / "nucleo_f411re"
    for source, target in (
        ("include/haptics.h", "Core/Inc/core_haptics.h"),
        ("include/array_geometry.h", "Core/Inc/array_geometry.h"),
        ("src/haptics.c", "Core/Src/haptics.c"),
        ("src/geometry.c", "Core/Src/geometry.c"),
    ):
        path = args.project / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared / source, path)


if __name__ == "__main__":
    main()
