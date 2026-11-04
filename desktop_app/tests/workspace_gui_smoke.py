"""Workspace navigation and display-scale regression checks, with no hardware.

Run with python -m desktop_app.tests.workspace_gui_smoke. Screenshots and the
machine-readable report are written under .runtime/workspace-ui.
"""

import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

from desktop_app.app import App


def main(runtime_path=None):
    output = Path(runtime_path) if runtime_path else Path(__file__).resolve().parents[1] / '.runtime' / 'workspace-ui'
    output.mkdir(parents=True, exist_ok=True)
    progress_path = output/'workspace-progress.log'
    progress_path.write_text('', encoding='utf-8')

    def progress(message):
        with progress_path.open('a', encoding='utf-8') as stream:
            stream.write(message+'\n')

    results = []
    for density in (1, 1.25, 1.5, 2):
        progress(f'{density}: create window')
        root = tk.Tk()
        root.tk.call('tk', 'scaling', density * 96 / 72)
        app = App(root)
        errors = []
        root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))

        def pump(seconds=.35):
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert not errors, errors

        def fits(widget):
            assert widget.winfo_ismapped(), (f'{widget}: control not visible; page={app.tabs.select()}, '
                                            f'mode={app.mode_choice.get()}, layout={app.connection_layout}, '
                                            f'serial={app.serial_options.winfo_geometry()}, '
                                            f'grid={app.serial_options.grid_info()}')
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            assert x >= root.winfo_rootx() and y >= root.winfo_rooty(), str(widget)
            assert x + widget.winfo_width() <= root.winfo_rootx()+root.winfo_width(), str(widget)
            assert y + widget.winfo_height() <= root.winfo_rooty()+root.winfo_height(), str(widget)

        def capture(name):
            from PIL import ImageGrab
            root.lift()
            root.attributes('-topmost', True)
            pump()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(output/name)
            root.attributes('-topmost', False)

        try:
            app.editor.transaction(lambda sketch: sketch.add_polyline([(-18, -15), (18, -15), (0, 20)], True))
            app.choose_shape('CUSTOM')
            config, draft = app.get_config(), app.draft_signature()
            root.geometry(f'{round(1100*density)}x{round(780*density)}+20+20')
            pump()
            with patch.object(app, 'send') as send:
                for page in ('drawing', 'playback', 'experiment', 'drawing'):
                    app.nav_buttons[page].invoke()
                    pump()
                    assert app.page_title.get() == {'drawing': '掌心绘图', 'playback': '播放查看', 'experiment': '实验与设备'}[page]
                    for widget in (app.header_stop, app.apply_button, app.start_button, app.pause_button, app.stop_button):
                        fits(widget)
                    assert app.get_config() == config and app.draft_signature() == draft
                send.assert_not_called()
            fits(app.editor.tool_buttons['erase'])
            fits(app.editor.property_tabs)
            assert app.editor.canvas.winfo_width() >= 420*density
            assert app.editor.canvas.winfo_height() >= 220*density
            assert app.inspector_tabs.select() == str(app.object_host)
            app.inspector_tabs.select(app.parameters_host)
            pump()
            for child in app.settings.nametowidget(app.settings.select()).winfo_children():
                fits(child)
            app.inspector_tabs.select(app.object_host)
            capture(f'editor-minimum-{round(density*100)}.png')
            progress(f'{density}: drawing and navigation passed')
            app.navigate('experiment')
            app.mode_choice.set('真实串口')
            app.update_controls()
            pump()
            for widget in (app.connection_picker, app.baud_picker, app.connect_button, app.disconnect_button):
                fits(widget)
            progress(f'{density}: connection controls passed')
            app.debug_mode.set(True)
            app.toggle_debug()
            app.tabs.select(app.measured_tab)
            pump()
            fits(app.receiver_panel.capture_button)
            fits(app.receiver_panel.export_button)
            app.measurement_tabs.select(app.calibration_panel)
            pump()
            fits(app.calibration_panel.load_button)
            progress(f'{density}: measurement and calibration controls passed')
            app.debug_mode.set(False)
            app.toggle_debug()
            pump()
            assert app.tabs.select() == str(app.device_tab)
            assert app.page_title.get() == '实验与设备'

            if density == 2:
                app.mode_choice.set('Demo 模拟设备')
                app.update_controls()
                app.connect()
                deadline = time.monotonic()+8
                while not app.ready:
                    assert time.monotonic() < deadline
                    pump()
                root.geometry(f'{round(1380*density)}x{round(920*density)}+20+20')
                app.navigate('drawing')
                pump()
                capture('01-drawing.png')
                app.apply_config()
                deadline = time.monotonic()+8
                while not app.can_play():
                    assert time.monotonic() < deadline
                    pump()
                assert app.tabs.select() == str(app.playback_tab)
                assert app.page_title.get() == '播放查看'
                capture('02-playback.png')
                app.navigate('experiment')
                capture('03-device.png')
            results.append({'scale_percent': round(density*100), 'passed': True,
                            'checks': 'navigation, draft preservation, persistent actions, editor, parameters, connection, measurement, calibration'})
        finally:
            progress(f'{density}: closing')
            with patch('desktop_app.app.messagebox.askyesnocancel', return_value=False):
                app.close()
            while True:
                try:
                    root.update()
                    if not root.winfo_exists():
                        break
                except tk.TclError:
                    break
                time.sleep(.02)
            progress(f'{density}: closed')
    (output/'workspace-smoke.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
