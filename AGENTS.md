# Repository Guidelines

## Project Structure & Module Organization

This repository prepares the 2026 FPGA haptic graphics project. `README.md` is the entry point; `设计思路.md` records current architecture; `FPGA赛道选题建议(1).md` preserves historical research. `CLAUDE.md` provides project context.

`desktop_app/` contains the Python application: `app.py` handles Tk UI, `sketch_editor.py` handles sketch interactions, `sketch.py` solves geometry and compiles paths, `editor.py` clips canvas lines, `model.py` supplies the reference model, and `protocol.py`, `controller.py`, `transport.py`, `ble_transport.py`, and `demo.py` handle communication. Tests live in `desktop_app/tests/`. Wire semantics belong in `desktop_app/PROTOCOL.md`; BLE mapping and pending decisions belong in `desktop_app/BLE.md`. HAP3 is a host-side draft, not yet agreed with FPGA firmware. No HDL, PCB, or hardware measurements exist yet.

## Build, Test, and Development Commands

`mobile_app/` is the Flutter Android companion. `lib/` holds touch sketches, models, protocol, BLE/Demo sessions and the library; `test/` holds Dart checks and Python reference vectors. See `mobile_app/README.md` for Android setup and known limits.

Run from the repository root with Python 3.10+ and Tk:

- `python -m pip install -r desktop_app/requirements.txt`: install dependencies.
- `python -m desktop_app --demo`: connect a simulated device, initially idle.
- `python -m unittest discover -s desktop_app/tests -v`: run automated checks.
- `python -m desktop_app.tests.gui_smoke`: exercise the visible interface; Pillow enables screenshots.
- `powershell -File desktop_app/build_windows.ps1 -Python <python.exe>`: build the Windows EXE and portable ZIP after installing `requirements-build.txt`.
- `rg --files --hidden`: inspect workspace files.
- In `mobile_app/`: `flutter pub get`, `flutter analyze`, `flutter test`; `dart format lib test` formats Dart.
- `powershell -File mobile_app/build_android.ps1`: build the internal-test APK using an ASCII staging directory on Windows.

Quote PowerShell paths containing spaces or parentheses. Keep `dist/`, `build/` and `.runtime/` ignored. The desktop shortcut targets the EXE; rebuild after desktop runtime edits. Mobile releases currently use local debug signing. No HDL build is configured.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case`, and explicit unit suffixes such as `_um` and `_millihz`. Keep Tk operations on the UI thread and serial/BLE I/O in workers. Normal interface text should explain user actions; engineering details belong in debug mode or advanced connection settings.

Use standard Dart formatting, two-space indentation and `flutter_lints` for mobile. Keep protocol semantics aligned with Python; changing either requires cross-language fixture checks. Mobile imports preserve compiled desktop paths, not desktop CAD constraints.

Write research in Chinese and Markdown in UTF-8. Preserve filenames, numbered sections, and technical abbreviations. Use ATX headings, hyphen bullets, and blank lines. Use absolute dates. Cite new external technical claims in section 9; distinguish vendor publication years and historical rules from confirmed current information.

## Testing Guidelines

Use `unittest` files named `test_*.py`; no coverage threshold exists. Test coordinate preservation, frame corruption, atomic configuration, readback, and failure recovery. Run GUI checks after interaction changes. Verify Markdown links, tables, units, and source claims. Screenshots and exports belong in ignored runtime directories.

## Commit & Pull Request Guidelines

Use focused imperative messages, consistent with `docs: add system design plan`. PRs should explain behavior, rationale, interface changes, validation, and relevant issues; include screenshots for UI changes. Exclude personal files, credentials, and runtime outputs.

## Project Constraints

Tang Mega 60K is selected; hardware starts at 4×4 then expands to 8×8. Drawings use arbitrary coordinates, never emitter-grid indices. Current software compiles sketches to at most 32 strokes and 256 coordinates, with one moving focus. Preserve legacy 64-point imports. Keep shared-edge subtraction and output-off transfers explicit; save original geometry, relations and dimensions separately from the wire plan. FPGA owns eventual high-speed scanning; Demo is a slow path demonstration. Preserve all coordinates when switching arrays. Distinguish simulated states, digital readback, theory, and measurements; prioritize single-focus validation before extensions.
