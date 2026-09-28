"""Opt-in real Tk / Demo capture smoke, no hardware access."""
import csv
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

from desktop_app.app import App


def main():
    runtime=Path(__file__).resolve().parents[1]/".runtime"/"receiver_gui"
    runtime.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();app=App(root)
    errors=[]
    root.report_callback_exception=lambda *args:errors.append(args[1])

    def wait(condition, seconds=5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            root.update()
            if errors:raise errors[0]
            if condition():return
            time.sleep(.025)
        raise AssertionError("GUI condition timed out")

    try:
        panel=app.receiver_panel
        assert str(panel.capture_button["state"])=="disabled"
        app.connect();wait(lambda:app.ready and app.state is not None)
        app.debug_mode.set(True);app.toggle_debug()
        app.tabs.select(app.measured_tab)
        wait(lambda:str(panel.capture_button["state"])=="normal")
        panel.acquire();wait(lambda:panel.capture is not None)
        assert panel.capture.simulated and not panel.capture.tx_running
        assert "Demo" in panel.axis.get_title()
        app.apply_config();wait(app.can_play)
        app.send("START");wait(lambda:app.state.state=="RUNNING" and not app.busy)
        app.tabs.select(app.measured_tab)
        old=panel.received
        panel.continuous.set(True)
        wait(lambda:panel.received>old and panel.capture.tx_running)
        assert panel.analysis()["peak40_mv"]>20
        panel.continuous.set(False)
        filename=runtime/"demo-capture.csv";panel.save_csv(filename)
        with filename.open(encoding="utf-8-sig",newline="") as stream:rows=list(csv.DictReader(stream))
        assert len(rows)==200 and {r['source'] for r in rows}=={'DEMO_SYNTHETIC'}
        assert rows[0]['adc_code']==str(panel.capture.raw[0])
        root.update();root.lift();root.attributes('-topmost',True);root.update()
        from PIL import ImageGrab
        x,y=root.winfo_rootx(),root.winfo_rooty()
        ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(runtime/'receiver-demo.png')
        root.attributes('-topmost',False)
        app.capabilities.discard('ADC_CAPTURE');app.update_controls()
        assert str(panel.capture_button['state'])=='disabled'
        assert '不支持' in panel.notice.get()
        app.capabilities.add('ADC_CAPTURE')
        app.disconnect();wait(lambda:not app.session.is_alive())
        app.update_controls()
        assert str(panel.capture_button['state'])=='disabled'
        assert '历史' in panel.notice.get()
        print('Receiver GUI: capture before/after play, synthetic label, CSV, capability gate, disconnect passed')
    finally:
        with patch('desktop_app.app.messagebox.askyesnocancel',return_value=False):app.close()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:root.update()
            except tk.TclError:break
            time.sleep(.02)


if __name__=='__main__':main()
