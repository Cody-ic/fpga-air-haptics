"""Real Tk smoke for field/scan workflows; only an in-memory Demo device."""
import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

import numpy as np
from matplotlib.backend_bases import MouseEvent

from desktop_app.app import App
from desktop_app.field_scan import ScanBook
from desktop_app.model import Config


def main(runtime_path=None):
    runtime = Path(runtime_path) if runtime_path else Path(__file__).resolve().parents[1]/".runtime"/"acoustic_gui"
    runtime.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = App(root)
    panel = app.acoustic_panel
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args[1])

    def wait(condition, seconds=6):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update()
            if errors:
                raise errors[0]
            if condition():
                return
            time.sleep(.02)
        raise AssertionError(f"GUI timed out: {panel.status.get()} / {panel.scan_status.get()}")

    def screenshot(name):
        root.update()
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(runtime/name)
        root.attributes("-topmost", False)

    try:
        app.set_config(Config(shape="POINT", z_um=100000, level=100, mod_hz=0))
        assert panel.current_position()[0] == (0., 0., 100.)
        panel.coordinate_mode.set("阵列坐标（旧方式）")
        panel.change_coordinates()
        assert not app.debug_mode.get()
        assert app.tabs.tab(app.acoustic_tab, "state") == "normal"
        app.tabs.select(app.acoustic_tab)
        panel.toggle_parameters()
        root.update()
        assert panel.profile_row.winfo_ismapped()
        panel.select_profile(8, 10.3)
        panel.toggle_parameters()
        root.update()
        assert not panel.profile_row.winfo_ismapped()
        wait(lambda: panel.payload is not None and panel.payload[0][0].count == 64)
        assert panel.payload[0][0].pitch_um == 10300
        assert panel.payload[1].peak > 45
        with patch("desktop_app.acoustic_panel.filedialog.asksaveasfilename", return_value=str(runtime/"offline-field.csv")):
            panel.export_prediction()
        screenshot("offline-8x8-field.png")
        root.geometry("1100x780")
        root.update()
        screenshot("field-minimum-window.png")
        root.geometry("1380x920")
        root.update()
        panel.select_profile(4, 11)
        wait(lambda: panel.payload is not None and panel.payload[0][0].count == 16)
        app.connect()
        wait(lambda: app.ready and app.state is not None)
        wait(lambda: panel.payload is not None and panel.payload[1].peak == 0)
        app.apply_config()
        wait(app.can_play)
        app.send("START")
        wait(lambda: app.state.output and not app.busy)
        app.tabs.select(app.acoustic_tab)
        wait(lambda: panel.payload is not None and panel.payload[1].peak > 10)
        assert "Demo" in panel.status.get()
        assert panel.payload[0][0].pitch_um == 11000
        panel.select_profile(8, 10.3)
        assert app.preview_array().count == 16
        prediction_path = runtime/"predicted-field.csv"
        with patch("desktop_app.acoustic_panel.filedialog.asksaveasfilename", return_value=str(prediction_path)):
            panel.export_prediction()
        assert "THEORY_NOT_MEASUREMENT" in prediction_path.read_text(encoding="utf-8-sig")
        panel.plane.set("XZ")
        wait(lambda: panel.payload is not None and panel.payload[0][6].plane == "XZ")
        screenshot("demo-xz-field.png")
        # Exercise asynchronous plotting while telemetry continuously moves.
        app.send("STOP")
        wait(lambda: not app.busy and not app.state.output)
        app.set_config(Config(shape="CIRCLE", level=100, mod_hz=0))
        app.apply_config()
        wait(app.can_play)
        app.send("START")
        wait(lambda: app.state.output and not app.busy)
        app.tabs.select(app.acoustic_tab)
        wait(lambda: panel.payload is not None and panel.payload[0][1].shape == "CIRCLE"
             and panel.payload[1].peak > 0)
        app.send("STOP")
        wait(lambda: not app.busy and not app.state.output)
        app.set_config(Config(shape="POINT", z_um=100000, level=100, mod_hz=0))
        app.apply_config()
        wait(app.can_play)
        app.send("START")
        wait(lambda: app.state.output and not app.busy)
        app.tabs.select(app.acoustic_tab)
        panel.notebook.select(panel.scan_tab)
        wait(lambda: str(panel.capture_button["state"]) == "normal")
        panel.acquire()
        wait(lambda: panel.busy and len(panel.pending_scan["captures"]) == 1)
        assert len(panel.scan_book.points) == 0
        assert "尚未保存" in panel.wave_status.get()
        assert "Demo" in panel.wave_axis.get_title()
        np.testing.assert_allclose(panel.wave_axis.lines[0].get_ydata(),
                                   panel.wave_point.capture.analyze()["mv"])
        wait(lambda: len(panel.scan_book.points) == 3)
        assert panel.scan_book.context["simulated"] is True
        assert panel.wave_index == 2 and "记录回放" in panel.wave_status.get()
        panel.positions[0].set("10")
        wait(lambda: str(panel.capture_button["state"]) == "normal")
        panel.acquire()
        wait(lambda: len(panel.scan_book.points) == 6)
        assert len(panel.scan_book.plane_points(100)) == 2
        assert panel.scan_book.prediction_model["phase_direction"] == 1
        path = runtime/"demo-scan.csv"
        panel.scan_book.save_csv(path)
        loaded = ScanBook.load_csv(path)
        assert loaded.points == panel.scan_book.points
        assert loaded.prediction_model == panel.scan_book.prediction_model
        assert "Demo" in panel.scan_status.get()
        assert len(panel.wave_selector["values"]) == 6
        # View an earlier saved window, preserving the acquisition conversion.
        panel.wave_selector.current(0)
        panel.wave_selector.event_generate("<<ComboboxSelected>>")
        root.update()
        assert panel.wave_index == 0
        first = panel.scan_book.points[0]
        expected = first.capture.analyze(first.vref_mv, first.divider)
        app.receiver_panel.vref.set("3600")
        panel.wave_span.set("完整窗口")
        panel.wave_range.event_generate("<<ComboboxSelected>>")
        root.update()
        np.testing.assert_allclose(panel.wave_axis.lines[0].get_ydata(), expected["mv"])
        np.testing.assert_allclose(panel.wave_axis.lines[1].get_ydata(),
                                   np.asarray(expected["filtered_mv"])+expected["mean_mv"])
        assert panel.wave_axis.get_xlim() == (0., 497.5)
        # Clicking a map position selects its latest window, including duplicates.
        root.update()
        # Editing z before displaying a new plane must not relabel the old map.
        panel.positions[2].set("")
        x, y = panel.scan_axis.transData.transform((0, 0))
        panel.scan_figure.pick(MouseEvent("button_press_event", panel.scan_canvas, x, y, button=1))
        assert panel.wave_index == 2
        panel.positions[2].set("100")
        panel.wave_span.set("前 100 μs")
        panel.render_wave()
        app.receiver_panel.vref.set("3300")
        screenshot("demo-measured-points.png")
        root.geometry("1100x780")
        root.update()
        screenshot("measured-wave-minimum-window.png")
        root.geometry("1380x920")
        root.update()
        panel.notebook.select(panel.compare_tab)
        wait(lambda: panel.comparison is not None)
        result = panel.comparison
        assert result.reference == (0., 0., 100.)
        assert result.measured_ratio[0] == result.predicted_ratio[0] == 1
        assert result.rmse == abs(result.measured_ratio[1]-result.predicted_ratio[1])
        assert "Demo" in panel.compare_status.get()
        screenshot("matched-coordinate-comparison.png")
        root.geometry("1100x780")
        root.update()
        screenshot("comparison-minimum-window.png")
        root.geometry("1380x920")
        root.update()
        panel.positions[2].set("")
        wait(lambda: panel.comparison is None)
        panel.positions[2].set("200")
        wait(lambda: "此 z 平面没有测点" in panel.compare_status.get())
        panel.positions[2].set("100")
        wait(lambda: panel.comparison is not None)
        assert len(panel.compare_ref_selector["values"]) == 2
        panel.compare_ref_selector.current(1)
        panel.compare_ref_selector.event_generate("<<ComboboxSelected>>")
        wait(lambda: panel.comparison is not None and panel.comparison.reference == (10., 0., 100.))
        panel.compare_direction.set("y 剖面")
        panel.compare_fixed.set("10")
        wait(lambda: panel.comparison is not None and panel.comparison.direction == "y")
        assert len(panel.comparison.profile_indices) == 1
        # Changing today's draft/model does not rewrite yesterday's recording.
        panel.source.set("待发图形")
        panel.diameter.set("0")
        panel.phase_encoding.set("相位码表示提前")
        panel.refresh_comparison()
        wait(lambda: panel.comparison is not None and panel.compare_key is not None)
        assert panel.comparison.model == loaded.prediction_model
        with patch("desktop_app.acoustic_panel.filedialog.asksaveasfilename", return_value=str(runtime/"comparison.csv")):
            panel.export_comparison()
        assert "measured_ratio" in (runtime/"comparison.csv").read_text(encoding="utf-8-sig")
        panel.source.set("设备回读")
        panel.diameter.set("10")
        panel.phase_encoding.set("自动识别")
        panel.notebook.select(panel.scan_tab)
        wait(lambda: str(panel.capture_button["state"]) == "normal")
        panel.acquire()
        assert panel.busy
        app.send("STOP")
        wait(lambda: not app.busy and not app.state.output)
        assert not panel.busy and len(panel.scan_book.points) == 6
        assert panel.wave_index == 5 and "记录回放" in panel.wave_status.get()
        with patch("desktop_app.acoustic_panel.filedialog.askopenfilename", return_value=str(path)), \
                patch("desktop_app.acoustic_panel.messagebox.askyesno", return_value=True):
            panel.import_scan()
        assert panel.scan_book.imported and "导入记录" in panel.wave_status.get()
        assert panel.wave_point == loaded.points[-1]
        app.tabs.select(app.acoustic_tab)
        panel.notebook.select(panel.compare_tab)
        wait(lambda: panel.comparison is not None and "导入记录" in panel.compare_status.get())
        assert panel.comparison.model == loaded.prediction_model
        panel.notebook.select(panel.scan_tab)
        np.testing.assert_allclose(panel.wave_axis.lines[0].get_ydata(),
                                   loaded.points[-1].capture.analyze()["mv"])
        screenshot("imported-point-wave.png")
        app.tabs.select(app.acoustic_tab)
        panel.notebook.select(0)
        wait(lambda: panel.payload is not None and panel.payload[1].peak == 0)
        app.disconnect()
        wait(lambda: not app.session.is_alive())
        wait(lambda: panel.payload is None)
        assert "过期" in panel.status.get()
        assert panel.wave_point == loaded.points[-1]
        with patch("desktop_app.acoustic_panel.messagebox.askyesno", return_value=True):
            panel.new_scan()
        assert panel.wave_point is None and not panel.wave_axis.lines
        (runtime/"result.json").write_text(json.dumps(dict(passed=True, raw_windows=6,
            checks=["offline 8x8/13mm", "device 4x4/11mm", "XY/XZ", "source labels",
                    "moving-focus worker", "three windows per point", "CSV raw roundtrip", "STOP cancels partial point",
                    "live window before commit", "saved window raw and 40kHz traces", "100us/full window",
                    "map click selects latest window", "historic vref preserved", "imported waveform replay",
                    "same-coordinate normalization", "reference switching", "X/Y measured sections",
                    "recorded output/model independent of current draft", "paired comparison CSV",
                    "STOP/disconnect retain history, new scan clears it", "stale readback cleared"]), indent=2), encoding="utf-8")
        print("Acoustic GUI smoke passed")
    finally:
        root.update_idletasks()
        with patch("desktop_app.app.messagebox.askyesnocancel", return_value=False):
            app.close()
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(.02)


if __name__ == "__main__":
    main()
