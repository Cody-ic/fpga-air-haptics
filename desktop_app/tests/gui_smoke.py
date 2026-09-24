"""Opt-in visible GUI integration test: python -m desktop_app.tests.gui_smoke.

Runs the real Tk event loop; never opens a hardware port. Screenshots require
Pillow and are written only to the ignored desktop_app/.runtime directory.
"""

from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import tkinter as tk

from desktop_app.app import App
from desktop_app.editor import MAX_EDITOR_POINTS
from desktop_app.model import ARRAY_PRESETS, Config, SHAPES, encode_points, trajectory_point


def main():
    runtime = Path(__file__).resolve().parents[1] / ".runtime"
    runtime.mkdir(exist_ok=True)
    root = tk.Tk()
    app = App(root)
    root.geometry("1380x920+25+25")
    errors = []
    steps = []
    deadline = time.monotonic() + 40
    custom = None

    def fail(error):
        errors.append(str(error))
        traceback.print_exc()
        app.close()

    root.report_callback_exception = lambda kind, value, tb: fail("".join(traceback.format_exception(kind, value, tb)))

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    def capture(name):
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        time.sleep(.15)
        try:
            from PIL import ImageGrab
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(runtime / name)
        except ImportError:
            print("Pillow absent: screenshot skipped")
        finally:
            root.attributes("-topmost", False)

    def start():
        nonlocal custom
        check(app.tabs.select() == str(app.editor_tab), "Start directly in editor")
        check(not app.debug_mode.get(), "Start in user mode")
        check(all(app.tabs.tab(tab, "state") == "hidden" for tab in app.debug_tabs), "Hide debug tabs")
        check(not app.hardware_detail.winfo_ismapped(), "Hide engineering details in user mode")
        capture("demo-startup.png")
        app.tabs.select(5)
        capture("demo-device.png")
        app.tabs.select(app.editor_tab)
        single = Config(shape="CUSTOM", path_xy_um="13237:-17123")
        app.set_config(single)
        check(app.get_config() == single, "One-node custom figure survives UI roundtrip")
        app.set_config(replace(single, path_xy_um=encode_points([(i*200-6400, i*137-4384) for i in range(MAX_EDITOR_POINTS)])))
        x, y = app.editor.to_screen((22000, -21000))
        app.editor.press(SimpleNamespace(x=x, y=y))
        app.editor.release(None)
        check(len(app.editor.points) == MAX_EDITOR_POINTS, "Editor prevents a 65th control point")
        try:
            app.set_config(replace(single, path_xy_um=encode_points([(i, -i) for i in range(MAX_EDITOR_POINTS+1)])))
        except ValueError:
            pass
        else:
            raise AssertionError("Imported paths must respect the editor point limit")
        app.set_config(Config())
        app.tabs.select(app.editor_tab)
        root.update_idletasks()
        app.editor.draw()
        expected = []
        for point in ((-18123, -13789), (21671, -14235), (17333, 22879), (-19613, 19487)):
            x, y = map(round, app.editor.to_screen(point))
            expected.append(app.editor.from_screen(x, y))
            app.editor.canvas.event_generate("<Button-1>", x=x, y=y)
            app.editor.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        check(app.editor.points == expected, "Clicks preserve arbitrary coordinates without snapping")
        check(any(x % 1000 or y % 1000 for x, y in expected), "Non-grid coordinates survive")
        check(app.vars["shape"].get() == SHAPES["CUSTOM"], "Drawing selects custom figure")
        x, y = app.editor.locations[0]
        app.editor.press(SimpleNamespace(x=x, y=y))
        target_x, target_y = app.editor.to_screen((-19723, -16451))
        app.editor.motion(SimpleNamespace(x=target_x, y=target_y))
        app.editor.release(None)
        check(app.editor.points[0] == (-19723, -16451), "Drag moves freely")
        app.editor.undo()
        check(app.editor.points == expected, "Undo restores coordinate drag")
        x, y = app.editor.locations[-1]
        app.editor.remove(SimpleNamespace(x=x, y=y))
        check(len(app.editor.points) == 3, "Right-click deletes point")
        app.editor.undo()
        app.editor.selected = 0
        app.editor.x_entry.set("-18.321")
        app.editor.y_entry.set("-13.579")
        app.editor.move_coordinates()
        check(app.editor.points[0] == (-18321, -13579), "Exact coordinate editing")
        custom = app.get_config()
        app.editor.zoom(.8)
        root.geometry("1200x820+25+25")
        root.update_idletasks()
        check(app.get_config() == custom, "Zoom and resize preserve physical coordinates")
        root.geometry("1380x920+25+25")
        app.editor.fit()
        for label, size in ARRAY_PRESETS.items():
            if size is not None:
                app.demo_array_choice.set(label)
                app.select_hardware()
                check(app.get_config() == custom, "Hardware selection must not alter design")
        filename = runtime / "smoke-config.json"
        with patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(filename)):
            app.save_custom()
        app.editor.clear()
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(filename)):
            app.load_config()
        check(app.get_config() == custom, "File roundtrip")
        check(not app.debug_mode.get(), "Loading a figure stays in user mode")
        capture("demo-editor.png")
        app.debug_mode.set(True)
        app.toggle_debug()
        app.demo_array_choice.set("8 × 8 · 64 路")
        app.select_hardware()
        app.connect()

    def apply():
        check(not app.state.output, "Connection must not enable output")
        check(app.hardware.count == 64, "Installed array handshake")
        app.apply_custom()

    def play():
        check(app.state.config == custom, "Applied design readback")
        check(len(app.state.phases) == 64, "Use all installed emitters")
        app.send("START")

    def inspect_running():
        payload = app.actual_plot.last_payload
        check(payload[1].count == 64, "Render installed phase dimensions")
        check(payload[0] == custom, "Render design path separately")
        np.testing.assert_allclose(trajectory_point(custom, 0)[:2], np.array(custom.points_um()[0])/1000)
        snapshot_path = runtime / "smoke-phases.csv"
        with patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(snapshot_path)):
            app.export_snapshot()
        check(len(snapshot_path.read_text(encoding="utf-8-sig").splitlines()) == 65, "Export all phase channels")
        app.tabs.select(0)
        capture("demo-running.png")
        app.tabs.select(app.editor_tab)
        capture("demo-editor-debug.png")
        app.send("PAUSE")

    def stopped():
        app.send("STOP")

    def local():
        app.send("MODE", value="LOCAL")

    def button():
        app.send("_BUTTON", action="NEXT")

    def local_play():
        check(app.state.config.shape != "CUSTOM", "Physical selection must appear in readback")
        app.send("_BUTTON", action="PLAY")

    def disconnect_local():
        app.disconnect()

    def reconnect():
        app.demo_array_choice.set("4 × 4 · 16 路")
        app.select_hardware()
        app.connect()

    def incompatible():
        app.set_config(replace(custom, cx_um=100000))
        app.update_controls()
        check(str(app.apply_button["state"]) == "disabled", "Reject trajectory outside declared workspace")
        app.set_config(custom)
        app.update_controls()
        check(str(app.apply_button["state"]) == "normal", "Free-position drawing accepted on small array")
        app.apply_custom()

    def small_running():
        app.send("START")

    def mute():
        check(len(app.state.phases) == 16, "4x4 actual readback")
        check(app.state.config.points_um() == custom.points_um(), "4x4 preserves every point coordinate")
        app.debug_mode.set(False)
        app.toggle_debug()
        root.geometry("1100x780+25+25")
        capture("demo-minimum.png")
        check(app.editor.apply_button.winfo_ismapped(), "Send button visible at minimum size")
        check(app.editor.apply_button.winfo_rooty()+app.editor.apply_button.winfo_height()
              <= root.winfo_rooty()+root.winfo_height(), "Editor actions fit minimum window")
        app.send("_MUTE", value=True)

    def finish():
        check(not app.ready, "Lost telemetry must close connection")
        check("未知" in app.source_line.get(), "Disconnected state must be visibly unknown")
        check(any("超时" in record["text"] for record in app.log_records), "Timeout reported")
        app.close()

    steps.extend([
        (lambda: True, start),
        (lambda: app.ready and app.state is not None, apply),
        (lambda: app.state and app.state.revision == 1 and not app.busy, play),
        (lambda: app.state and app.state.output and app.actual_plot.last_payload is not None
         and app.actual_plot.last_payload[4] is True, inspect_running),
        (lambda: app.state.state == "PAUSED" and not app.busy, stopped),
        (lambda: app.state.state == "IDLE" and not app.busy, local),
        (lambda: app.state.mode == "LOCAL" and not app.busy, button),
        (lambda: app.state.config.shape != "CUSTOM", local_play),
        (lambda: app.state.output, disconnect_local),
        (lambda: not app.session.is_alive(), reconnect),
        (lambda: app.ready and app.state is not None, incompatible),
        (lambda: app.state.revision == 1 and not app.busy, small_running),
        (lambda: app.state.output and not app.busy and app.actual_plot.last_payload is not None
         and app.actual_plot.last_payload[1].count == 16 and app.actual_plot.last_payload[4], mute),
        (lambda: not app.session.is_alive(), finish),
    ])

    def run_step():
        try:
            if time.monotonic() > deadline:
                raise TimeoutError("GUI scenario exceeded 40 seconds")
            if steps and steps[0][0]():
                _, action = steps.pop(0)
                print("GUI:", action.__name__, flush=True)
                action()
            if not app.closing:
                root.after(150, run_step)
        except Exception as error:
            fail(error)

    root.after(500, run_step)
    root.mainloop()
    result = {"passed": not errors and not steps, "errors": errors, "remaining_steps": len(steps)}
    (runtime / "gui-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
