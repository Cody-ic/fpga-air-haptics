"""Exercise the new tabletop inputs and live file reader using Demo only."""
import json
from pathlib import Path
import time
import tkinter as tk

from desktop_app.app import App
from desktop_app.model import Config
from desktop_app.field_scan import ScanBook


def main(runtime_path=None):
    runtime = Path(runtime_path) if runtime_path else Path(__file__).resolve().parents[1]/".runtime"/"coordinate_gui"
    runtime.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = App(root)
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args[1])
    panel = app.acoustic_panel

    def wait(condition, seconds=8):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update()
            if errors:
                raise errors[0]
            if condition():
                return
            time.sleep(.02)
        raise AssertionError(panel.scan_status.get())

    try:
        assert panel.current_position()[0] == (0., 0., 100.)
        panel.position_reference.set("托盘左前角 A")
        panel.change_coordinates()
        assert panel.current_position()[0] == (0., 0., 100.)
        assert tuple(float(v.get()) for v in panel.positions) == (98.727, 73.872, 24.7)
        app.set_config(Config(shape="POINT", z_um=100000, level=100, mod_hz=0))
        app.connect()
        wait(lambda: app.ready and app.state is not None)
        app.apply_config()
        wait(app.can_play)
        app.send("START")
        wait(lambda: app.state.output and not app.busy)
        app.tabs.select(app.acoustic_tab)
        panel.notebook.select(panel.scan_tab)
        wait(lambda: str(panel.capture_button["state"]) == "normal")
        panel.acquire()
        wait(lambda: len(panel.scan_book.points) == 3)
        point = panel.scan_book.points[0]
        assert point.position_mm == (0., 0., 100.)
        assert point.placement["receiver_table_mm"] == [132., 94., 24.7]
        assert point.placement["reference"] == "TRAY_A"
        assert "底面" in panel.scan_axis.get_title()
        panel.scan_book.save_csv(runtime/"tabletop-scan.csv")
        assert ScanBook.load_csv(runtime/"tabletop-scan.csv").points == panel.scan_book.points
        path = app.session.mirror.directory/"snapshot.json"
        wait(lambda: path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("last_capture") is not None)
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        assert snapshot["session"]["simulated"] is True
        assert snapshot["connected"] is True
        assert snapshot["last_state"]["output"] is True
        from PIL import ImageGrab
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(runtime/"tabletop-measurement.png")
        print("Tabletop inputs, captured coordinates, CSV and concurrent live snapshot passed")
    finally:
        if app.session:
            app.session.close()
            app.session.join(5)
        app.closing = True
        app.executor.shutdown(wait=False, cancel_futures=True)
        root.destroy()


if __name__ == "__main__":
    main()
