# Repository Guidelines

## Project Structure & Release Scope

`README.md` is the entry point. `docs/REPOSITORY_MAP.md` classifies directories; `docs/SOFTWARE_RELEASE.md` and `release/software.json` define candidate `1.0.0rc1`.

`desktop_app/` contains the Python user workspace, sketch compiler, pressure solver, transports, and `tests/`. `fpga/rtl/`, `fpga/tb/`, `fpga/board/`, and `fpga/preview/` hold control logic, simulation, board constraints, and video. `firmware/xiao_mg24/` supplies independent receiver acquisition and calibration tools. `hardware/` holds PCB and mechanical sources. STM32 firmware, `mobile_app/`, and `experiments/` remain auxiliary.

## Build, Test, and Development Commands

Run from the repository root:

- `python -m pip install -r desktop_app/requirements.txt`: install desktop/replay dependencies.
- `python -m desktop_app`: open the disconnected user workspace.
- `python -m desktop_app --demo`: simulate an idle device.
- `python -m desktop_app --developer`: open engineering tools.
- `python -m unittest discover -s desktop_app/tests -v`: test desktop/numerical behavior.
- `python -m unittest discover -s firmware/xiao_mg24/tests -v`: test acquisition.
- `python -m desktop_app.tests.user_gui_smoke`: check visible interactions offline.
- `python tools/build_final_software.py --help`: inspect build options.

Use ASCII build paths. Keep `.runtime/`, `build/`, and `dist/` ignored. Rebuild executables after runtime edits.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case`, and explicit units such as `_um` and `_millihz`. Keep Tk operations on its UI thread and I/O in workers. User interfaces describe actions; engineering details belong in developer mode. Keep protocol semantics aligned across implementations. Write Chinese UTF-8 Markdown; preserve filenames.

## Testing Guidelines

Use `unittest` files named `test_*.py`; no coverage threshold exists. Verify atomic configuration, failure recovery, coordinate preservation, quantized-phase objectives, and measured-data provenance. Run GUI checks after interaction changes. Check Markdown links and units. Label simulation, digital readback, theory, receiver voltage, and human observations separately.

## Commit & Pull Request Guidelines

Use focused imperative messages such as `docs: clarify release profiles`. Explain behavior, rationale, validation, and interface changes; include UI screenshots. Exclude credentials, personal files, and runtime artifacts. Compare parallel branches before integrating; do not overwrite newer code with older PR snapshots.

## Project Constraints

Target Tang with an 8×8, 10.3 mm array. Release firmware requires matching measured compensation; zero-compensation and video-link tests are diagnostics. Apply compensation exactly once. MG24 uses independent `MG24_RX1`; transmitter control uses HAP3. Do not imply the main GUI already owns a second MG24 connection or that touchscreen support exists.

Drawings use physical coordinates, not emitter-grid indices. Preserve 32-stroke/256-coordinate paths, legacy 64-point imports, output-off jumps, original geometry, and dimensions. Preserve receiver analog hardware; adapt software first. Build scripts must not flash or start emission automatically.
