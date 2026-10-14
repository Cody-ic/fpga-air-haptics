"""Visible BLE UI test using GATT fixtures only; no radio is opened."""

import asyncio
import json
from pathlib import Path
import sys
import time
import tkinter as tk
import traceback
from types import SimpleNamespace
from unittest.mock import patch

from desktop_app.app import App
from desktop_app.ble_settings import BleSettings
from desktop_app.ble_transport import BleProfile, BleScan
from desktop_app.tests.ble_fakes import FakeClientFactory


def main(runtime_path=None):
    if sys.platform == "win32":
        # Import the real native backends even when running the fixture, so the
        # frozen self-test catches missing WinRT extensions without scanning.
        import bleak.backends.winrt.client
        import bleak.backends.winrt.scanner
    runtime = Path(runtime_path) if runtime_path else Path(__file__).resolve().parents[1] / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = App(root)
    root.title("触见 · BLE 自动化测试（无真实硬件）")
    root.geometry("1380x920+25+25")
    factory = FakeClientFactory(respond=True)
    ticks = []
    errors = []
    scans = []
    deadline = time.monotonic() + 25

    class Scanner:
        @staticmethod
        async def discover(**kwargs):
            await asyncio.sleep(0.25)
            return {"A": (SimpleNamespace(address="TEST-A", name="TouchSee 测试设备"),
                          SimpleNamespace(local_name=None, rssi=-40))}

    def scanner():
        scan = BleScan(scanner_type=Scanner)
        scans.append(scan)
        return scan

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    def capture(name):
        root.update_idletasks()
        try:
            from PIL import ImageGrab
            root.lift()
            root.attributes("-topmost", True)
            root.update()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(runtime / name)
        except ImportError:
            pass
        finally:
            root.attributes("-topmost", False)

    def start():
        root.geometry("1100x780+25+25")
        app.mode_choice.set("BLE 蓝牙")
        app.update_controls()
        root.update_idletasks()
        check(not app.baud_picker.winfo_ismapped(), "BLE must not expose UART baud as link speed")
        check(str(app.demo_array_picker["state"]) == "disabled", "BLE array must come from HELLO")
        with patch("desktop_app.app.messagebox.showinfo") as info:
            app.connect()
            check(info.called and app.session is None, "Require explicit BLE device selection")
        app.scan_button.invoke()
        root.after(40, lambda: ticks.append(app.ble_scan is not None))
        check(str(app.connect_button["state"]) == "disabled", "No connection while scanning")

    def scanned():
        check(ticks == [True], "Tk stayed responsive during asynchronous scan")
        check(not app.ble_choice.get(), "Do not auto-select unrelated nearby devices")
        app.ble_choice.set(next(iter(app.ble_peers)))
        root.update_idletasks()
        right_edge = app.disconnect_button.winfo_rootx()+app.disconnect_button.winfo_width()
        window_edge = root.winfo_rootx()+root.winfo_width()
        check(app.disconnect_button.winfo_rootx()+app.disconnect_button.winfo_width() <=
              root.winfo_rootx()+root.winfo_width(),
              f"BLE controls fit minimum width: controls={right_edge}, window={window_edge}")
        root.geometry("1380x920+25+25")
        capture("ble-connection.png")
        app.configure_ble()
        dialog = next(child for child in root.winfo_children() if isinstance(child, BleSettings))
        dialog.variables["service_uuid"].set("invalid")
        with patch("desktop_app.ble_settings.messagebox.showerror") as error:
            dialog.apply()
            check(error.called and dialog.winfo_exists(), "Reject invalid UUID before changing profile")
        dialog.set_profile(BleProfile(chunk_size=20))
        dialog.apply()
        check(app.ble_profile.chunk_size == 20, "Apply explicit module packet size")
        app.baud_choice.set("invalid-but-unused-for-ble")
        app.connect_button.invoke()
        check(app.session is not None and not app.session.is_demo, "BLE creates real-source Session")
        check(str(app.ble_settings_button["state"]) == "disabled", "Cannot modify active GATT profile")

    def connected():
        check(not app.can_play(), "HELLO plus STATE alone never enables playback")
        check("BLE" in app.playback.source.get(), "Readback identifies BLE, not serial")
        factory.client.hold_state = True
        app.choose_shape("TRIANGLE")
        app.apply_button.invoke()

    def acknowledged():
        check(not app.can_play(), "CONFIG ACK alone never unlocks BLE playback")
        factory.client.hold_state = False

    def confirmed():
        check(app.state.config.shape == "TRIANGLE", "GATT notifications restore the actual drawing")
        app.start_button.invoke()

    def running():
        check(app.playback.position_mm is not None, "BLE STATE drives the palm marker")
        capture("ble-playback-fixture.png")
        factory.client.loop.call_soon_threadsafe(factory.client.drop)

    def dropped():
        check(not app.ready and not app.can_play(), "Connection loss disables playback")
        check(app.playback.position_mm is None, "Connection loss removes stale palm position")
        check(app.confirmed_config is None, "Connection loss invalidates the configuration receipt")
        app.connect()

    def reconnected():
        check(not app.can_play(), "Reconnect must not resume output or retain old CONFIG approval")
        check(app.state.state == "IDLE", "New fixture connection starts idle")
        app.disconnect()

    def cancel_scan():
        app.scan_ble()
        app.scan_ble()

    def close_while_scanning():
        check(not app.ble_peers, "Cancelled scan does not repopulate old devices")
        app.scan_ble()
        with patch("desktop_app.app.messagebox.askyesnocancel", return_value=False):
            app.close()

    steps = [(lambda: True, start),
             (lambda: app.ble_scan is None and bool(app.ble_peers), scanned),
             (lambda: app.ready and app.state is not None, connected),
             (lambda: app.await_revision is not None, acknowledged),
             (lambda: app.can_play(), confirmed),
             (lambda: app.state.output and not app.busy, running),
             (lambda: not app.session.is_alive() and not app.ready, dropped),
             (lambda: app.ready and app.state is not None, reconnected),
             (lambda: not app.session.is_alive() and not app.ready, cancel_scan),
             (lambda: app.ble_scan is None, close_while_scanning)]

    def fail(error):
        errors.append(str(error))
        traceback.print_exc()
        with patch("desktop_app.app.messagebox.askyesnocancel", return_value=False):
            app.close()

    def tick():
        try:
            if time.monotonic() > deadline:
                raise TimeoutError("BLE GUI test exceeded 25 seconds")
            if steps and steps[0][0]():
                _, action = steps.pop(0)
                print("BLE GUI:", action.__name__, flush=True)
                action()
            if not app.closing:
                root.after(80, tick)
        except Exception as error:
            fail(error)

    root.report_callback_exception = lambda kind, value, tb: fail("".join(traceback.format_exception(kind, value, tb)))
    with patch("desktop_app.ble_transport.BleakClient", factory), patch("desktop_app.app.BleScan", scanner):
        root.after(200, tick)
        root.mainloop()
    check(all(not job.is_alive() for job in scans), "Scan workers stopped on close")
    result = {"passed": not errors and not steps, "errors": errors, "remaining_steps": len(steps),
              "transport": "mock GATT; no physical BLE tested"}
    (runtime / "gui-ble-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
