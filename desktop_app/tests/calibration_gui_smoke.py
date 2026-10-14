"""Visible Tk test of geometry import, complete Demo calibration and file reuse."""
import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

from desktop_app.app import App
from desktop_app.array_geometry import grid_geometry


def main():
    root = tk.Tk()
    app = App(root)
    root.geometry('1380x920+25+25')
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args[1])
    runtime = Path(__file__).resolve().parents[1]/'.runtime/calibration_gui'
    runtime.mkdir(parents=True, exist_ok=True)
    def wait(test, seconds=8):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update()
            if errors:
                raise errors[0]
            if test():
                return
            time.sleep(.02)
        raise AssertionError('GUI condition timed out')
    try:
        geometry = runtime/'sphere.json'
        grid_geometry(1, 2, 11, radius_mm=80).save(geometry)
        with patch('desktop_app.app.filedialog.askopenfilename', return_value=str(geometry)):
            app.load_geometry()
        app.connect()
        wait(lambda: app.ready and app.state is not None)
        assert app.state.array.mapping == 'EXPLICIT_XYZ'
        app.debug_mode.set(True)
        app.toggle_debug()
        app.tabs.select(app.measured_tab)
        panel = app.calibration_panel
        app.measurement_tabs.select(panel)
        wait(lambda: str(panel.start_button['state']) == 'normal')
        panel.start()
        wait(lambda: panel.busy)
        wait(lambda: str(app.apply_button['state']) == 'disabled')
        assert not app.can_play()
        wait(lambda: panel.processed, 25)
        assert panel.runner.error is None, panel.runner.error
        assert panel.result['simulated']
        assert app.state.phase_offsets == (0, 57)
        assert app.state.state == 'IDLE' and not app.state.output
        calibration = runtime/'calibration.json'
        with patch('desktop_app.calibration_panel.filedialog.asksaveasfilename', return_value=str(calibration)):
            panel.save()
        assert json.loads(calibration.read_text(encoding='utf-8'))['phase_offsets'] == [0, 57]
        with patch('desktop_app.calibration_panel.filedialog.askopenfilename', return_value=str(calibration)):
            panel.load()
        wait(lambda: panel.processed)
        assert panel.runner.error is None, panel.runner.error
        root.lift()
        root.attributes('-topmost', True)
        root.update()
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(runtime/'calibration-demo.png')
        root.attributes('-topmost', False)
        app.plane.set('XZ')
        app.preview()
        wait(lambda: app.forecast_plot.last_payload is not None)
        assert app.forecast_plot.last_payload[1].mapping == 'EXPLICIT_XYZ'
        app.disconnect()
        wait(lambda: not app.session.is_alive())
        print('Calibration GUI: 3D import/readback, calibration, stopped restore, save/load and XZ preview passed')
    finally:
        with patch('desktop_app.app.messagebox.askyesnocancel', return_value=False):
            app.close()
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(.02)


if __name__ == '__main__':
    main()
