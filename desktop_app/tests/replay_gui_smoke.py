"""Exercise the real embedded renderer and offline controls; never connect hardware."""

import json
import os
from pathlib import Path
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import ImageGrab

from desktop_app.app import App


def main(output=None, density=1):
    output = Path(output or Path(__file__).resolve().parents[1]/'.runtime'/'replay-ui')
    output.mkdir(parents=True,exist_ok=True)
    root = tk.Tk()
    root.tk.call('tk','scaling',density*96/72)
    app = App(root)
    root.geometry(f'{round(1380*density)}x{round(920*density)}+12+12')
    errors=[]
    root.report_callback_exception=lambda kind,error,tb: errors.append(str(error))
    panel=app.replay
    def pump(seconds=.4):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            root.update()
            time.sleep(.005)
        root.update_idletasks()
        assert not errors, errors
    try:
        signature=app.draft_signature()
        with patch.object(app,'send') as send:
            app.navigate('replay')
            deadline=time.monotonic()+20
            while panel.sequence is None:
                assert time.monotonic()<deadline,panel.status.get()
                pump()
            pump()
            assert panel.scene.context_created
            assert not panel.playing
            for control in (panel.play_button,panel.next_button,panel.timeline,panel.speed_picker,app.header_stop,
                            panel.joints_toggle,panel.frame_label,panel.pressure_note):
                assert control.winfo_ismapped(),str(control)
                assert control.winfo_rootx()+control.winfo_width()<=root.winfo_rootx()+root.winfo_width(),str(control)
                assert control.winfo_rooty()+control.winfo_height()<=root.winfo_rooty()+root.winfo_height(),str(control)
                assert control.winfo_height() >= control.winfo_reqheight(),str(control)
            panel.show_frame(60)
            panel.play_button.invoke();pump(.45)
            assert panel.index>60
            panel.play_button.invoke();index=panel.index;pump(.15)
            assert panel.index==index
            panel.next_button.invoke();assert panel.index==index+1
            panel.previous_button.invoke();assert panel.index==index
            panel.seek(0);assert panel.scene.vertices is None
            assert '无手部标注' in panel.status.get()
            assert panel.scene.force is not None
            panel.show_frame(210)
            vertices=panel.sequence.vertices.copy()
            scene=panel.scene
            scene._press(SimpleNamespace(x=100,y=100))
            scene._orbit(SimpleNamespace(x=150,y=120))
            scene._pan(SimpleNamespace(x=160,y=130))
            scene._zoom(SimpleNamespace(delta=120))
            np.testing.assert_array_equal(panel.sequence.vertices,vertices)
            panel.toggle();app.navigate('drawing');pump()
            assert not panel.playing
            assert app.draft_signature()==signature
            app.navigate('replay');pump()
            panel.show_frame(int(np.argmax(panel.sequence.total_force_n)))
            panel.scene.reset_camera()
            pump()
            import_directory = os.environ.get('TOUCHSEE_REPLAY_TEST_DIRECTORY')
            if import_directory:
                expected = panel.sequence.force.copy()
                panel.load(Path(import_directory))
                deadline=time.monotonic()+30
                while panel.loading is not None:
                    assert time.monotonic()<deadline,'Parquet import timeout'
                    pump()
                assert '读取失败' not in panel.status.get(),panel.status.get()
                np.testing.assert_array_equal(panel.sequence.force,expected)
                assert not panel.playing
            send.assert_not_called()
            assert app.session is None
            root.lift();root.attributes('-topmost',True);pump(.15)
            x,y=root.winfo_rootx(),root.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'replay-{round(density*100)}.png')
            root.attributes('-topmost',False)
            durations=[]
            for index in range(180,210):
                start=time.perf_counter();panel.show_frame(index);durations.append(time.perf_counter()-start)
            report=dict(passed=True,scale_percent=round(density*100),renderer=scene.renderer,
                        frame_count=len(panel.sequence.frames),hand_frames=int(panel.sequence.hand_valid.sum()),
                        mean_frame_render_ms=round(np.mean(durations)*1000,2),hardware_connected=False,
                        official_parquet_import=bool(import_directory),
                        checks=['real data','play/pause/step/seek','missing annotation','camera controls',
                                'draft preserved','no output commands','navigation pauses replay','visible controls'])
            (output/f'replay-smoke-{round(density*100)}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        with patch('desktop_app.app.messagebox.askyesnocancel',return_value=False):
            app.close()
        for _ in range(100):
            try:root.update()
            except tk.TclError:break
            time.sleep(.01)
    return 0


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--scale',type=float,default=1)
    args=parser.parse_args()
    main(density=args.scale)
