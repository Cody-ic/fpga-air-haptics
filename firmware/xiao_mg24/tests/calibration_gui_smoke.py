"""Opt-in real Tk workflow smoke; fake endpoints only, never opens a serial port."""
import copy
import json
from pathlib import Path
import sys
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fpga_calibration_gui as gui
import calibrate_fpga
from test_calibration import FakeLink


def main():
    folder = gui.ROOT / ".runtime/calibration/gui-smoke"
    folder.mkdir(parents=True, exist_ok=True)
    gui.configure_display()
    root = tk.Tk()
    app = gui.CalibrationWindow(root)
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args[1])
    links = []
    def link_factory(_):
        link = FakeLink()
        async def close():
            pass
        link.close = close
        links.append(link)
        return link
    # Mark every saved/notified synthetic test result as simulated, so it
    # cannot accidentally be imported into a real calibration session.
    async def synthetic(link, receiver, bluetooth, record, settings, manual, notify, imported):
        def mark(value):
            value = copy.deepcopy(value)
            if "document" in value:
                value["document"]["simulated"] = True
            return value
        return await calibrate_fpga.calibrate(link, receiver, bluetooth, lambda v: record(mark(v)),
                                             settings, manual, lambda v: notify(mark(v)), imported)
    def wait(predicate, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            if errors:
                raise errors[0]
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError("GUI condition timed out")
    try:
        with patch.object(gui, "SerialConnection", return_value=object()), patch.object(gui, "FpgaLink", side_effect=link_factory), \
             patch.object(gui, "calibrate", side_effect=synthetic), patch.object(gui.messagebox, "showerror") as error_box:
            app.start()
            assert error_box.called and not links  # placement confirmation required
            app.fpga_port.set("FAKE_NO_HARDWARE")
            app.fixed.set(True)
            app.start()
            for index in range(24):
                wait(lambda: app.current_token is not None)
                assert app.current_token == index + 1
                app.vpp.set("100")
                app.clean.set(True)
                app.submit()
                assert str(app.submit_button["state"]) == "disabled"
            wait(lambda: app.result is not None and not app.worker.is_alive())
            wait(lambda: str(app.start_button["state"]) == "normal")
            assert app.result["simulated"] is True
            assert app.result["phase_offsets"] == [0, 9]
            with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(folder / "synthetic.json")):
                app.save()
            assert json.loads((folder / "synthetic.json").read_text(encoding="utf-8"))["simulated"] is True
            # Exercise stop while an actual Tk input is pending.
            app.start()
            wait(lambda: app.current_token is not None)
            app.stop()
            wait(lambda: not app.worker.is_alive())
            wait(lambda: str(app.start_button["state"]) == "normal")
            assert links[-1].sent[-1][0] == calibrate_fpga.ABORT
            assert app.current_token is None
            app.status.set("窗口自检通过：合成端点，未连接硬件。人工录入、停止与保存流程已检查。")
            root.update()
            from PIL import ImageGrab
            image = ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                        root.winfo_rootx() + root.winfo_width(),
                                        root.winfo_rooty() + root.winfo_height()))
            image.save(folder / "window.png")
        print("PASS real Tk: placement check, 24 manual entries, result save, cancellation, UI recovery")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
