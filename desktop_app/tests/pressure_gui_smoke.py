"""Exercise pressure UI with an in-memory device; real transports forbidden."""
import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

import numpy as np
from PIL import ImageGrab

from desktop_app.app import App
from desktop_app.model import ArraySpec


def main(output=None, density=1):
    output=Path(output or '.runtime/pressure-ui')
    output.mkdir(parents=True,exist_ok=True)
    with patch('desktop_app.controller.SerialTransport',side_effect=AssertionError('No hardware')), \
         patch('desktop_app.controller.BleTransport',side_effect=AssertionError('No hardware')):
        root=tk.Tk()
        root.tk.call('tk','scaling',density*96/72)
        errors=[]
        root.report_callback_exception=lambda kind,error,tb: errors.append(str(error))
        app=App(root)
        def pump():
            root.update();time.sleep(.01)
            assert not errors, errors
        def until(condition, timeout=90):
            deadline=time.monotonic()+timeout
            while not condition():
                assert time.monotonic()<deadline,app.status_line.get()
                pump()
            pump()
        try:
            app.navigate('replay')
            until(lambda:app.replay.sequence is not None)
            app.replay.show_frame(324)
            draft=app.draft_signature()
            app.replay.pressure_button.invoke()
            panel=app.pressure_window
            assert panel.frame_id==325
            frozen=panel.raw.copy()
            app.replay.show_frame(300)
            until(lambda:panel.future is None)
            assert panel.result is not None,panel.status.get()
            assert panel.result.mapping['centre_spacing_mm']==35
            assert panel.result.metrics['mode']=='focus'
            np.testing.assert_array_equal(panel.result.raw_kpa,frozen)
            assert str(panel.send_button['state'])=='disabled'
            assert app.session is None
            panel.demo_button.invoke()
            until(lambda:app.ready and app.state is not None)
            assert app.hardware==ArraySpec(8,8,10300)
            app.capabilities.discard('STATIC_PHASE')
            until(lambda:str(panel.send_button['state'])=='disabled')
            app.capabilities.add('STATIC_PHASE')
            until(lambda:str(panel.send_button['state'])=='normal')
            panel.send_button.invoke()
            until(lambda:app.config_confirmed() and not app.busy)
            assert app.state.config==panel.result.config
            assert app.state.state=='IDLE' and not app.state.output
            assert app.draft_signature()==draft
            assert app.apply_button['text']=='改发绘图草稿'
            def fits(widget):
                assert widget.winfo_ismapped(),str(widget)
                assert widget.winfo_rootx() >= panel.winfo_rootx(),str(widget)
                assert widget.winfo_rooty() >= panel.winfo_rooty(),str(widget)
                assert widget.winfo_rootx()+widget.winfo_width() <= panel.winfo_rootx()+panel.winfo_width(),str(widget)
                assert widget.winfo_rooty()+widget.winfo_height() <= panel.winfo_rooty()+panel.winfo_height(),str(widget)
            for widget in (panel.generate_button,panel.original_button,panel.send_button,panel.demo_button,
                           panel.playback_button,panel.mode_picker,panel.status_label,panel.connection_label,*panel.entries):
                fits(widget)
            panel.figure.savefig(output/'comparison.png',dpi=140)
            panel.lift();pump()
            ImageGrab.grab(bbox=(panel.winfo_rootx(),panel.winfo_rooty(),
                                panel.winfo_rootx()+panel.winfo_width(),panel.winfo_rooty()+panel.winfo_height())).save(output/'pressure-window.png')
            (output/'config.json').write_text(json.dumps(dict(panel.result.config.wire(),**panel.result.array.wire())),encoding='utf-8')
            panel.mode.set('区域还原（原算法）')
            assert panel.result is None and str(panel.send_button['state'])=='disabled'
            panel.original_button.invoke()
            assert panel.result is None and str(panel.send_button['state'])=='disabled'
            panel.generate_button.invoke()
            until(lambda:panel.future is None)
            assert abs(panel.result.mapping['centre_spacing_mm']-26.487056)<1e-5
            assert panel.result.metrics['mode']=='regions'
            assert app.state.config != panel.result.config  # Regeneration must not upload.
            panel.destroy()
            assert app.can_play()
            app.send('START');until(lambda:not app.busy and app.state.state=='RUNNING')
            assert app.state.output and app.playback.position_mm is None
            assert '压力单帧' in app.playback.heading.get()
            app.send('PAUSE');until(lambda:not app.busy and app.state.state=='PAUSED')
            assert not app.state.output and app.can_play()
            app.send('START');until(lambda:not app.busy and app.state.state=='RUNNING')
            app.send('STOP');until(lambda:not app.busy and app.state.state=='IDLE')
            app.apply_config();until(lambda:not app.busy and app.config_confirmed() and app.state.config.phase_table=='NONE')
            assert app.apply_button['text']=='发送到设备'
            assert app.draft_signature()==draft
            app.navigate('replay');app.replay.show_frame(0)
            app.replay.pressure_button.invoke();panel=app.pressure_window
            until(lambda:panel.future is None)
            assert panel.result is None and str(panel.send_button['state'])=='disabled'
            panel.destroy()
            (output/'result.json').write_text(json.dumps(dict(passed=True,frame=325,channels=64,
                frozen_selection=True,legacy_device_blocked=True,automatic_start=False,
                start_pause_resume_stop=True,drawing_preserved=True,hardware_connected=False,
                density=density,footer_visible=True,spacing_reset_without_upload=True,
                focus_mode_switch_without_upload=True),indent=2))
            print('PASS pressure GUI',output)
            return 0
        finally:
            if app.session:
                app.session.close();app.session.join(3)
            app.executor.shutdown(wait=False,cancel_futures=True)
            root.destroy()


if __name__=='__main__':
    main()
