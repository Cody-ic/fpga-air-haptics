"""Opt-in Tk + Demo validation of the contextual presentation workflow."""
from dataclasses import replace
import json
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch
from desktop_app.controller import Session
from desktop_app.model import BUTTON_OUTPUT_SETTINGS, Config, SHAPES
from desktop_app.transport import DemoTransport
from desktop_app.user_app import UserApp


def main(runtime_path=None):
    runtime = Path(runtime_path) if runtime_path else Path(__file__).resolve().parents[1]/'.runtime'/'user-gui'
    runtime.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = UserApp(root, preferences_path=runtime/'preferences.json')
    checks, errors = [], []
    hold = {'value':False}
    root.report_callback_exception = lambda kind,value,tb:errors.append(str(value))
    def check(condition, message):
        if not condition:
            raise AssertionError(message)
        checks.append(message)
    def pump(condition=lambda:False, duration=.25):
        deadline = time.monotonic()+duration
        while time.monotonic()<deadline:
            root.update()
            if condition():
                return True
            time.sleep(.01)
        return bool(condition())
    def demo_session(*args, **kwargs):
        transport = DemoTransport(array=kwargs.get('demo_array'))
        snapshot = transport.device.snapshot
        transport.device.snapshot = lambda:b'' if hold['value'] else snapshot()
        return Session(*args, **kwargs, factory=lambda:transport)
    def sent(verb):
        return sum(r['kind']=='tx' and (f' {verb} ' in r['text'] or f' {verb}*' in r['text']) for r in app.log_records)
    def stopped():
        return app.state is not None and app.state.state=='IDLE' and not app.busy
    def running():
        return app.state is not None and app.state.state=='RUNNING' and not app.busy and app.pending_presentation is None
    passed = False
    try:
        pump()
        check(app.page_name=='library', 'Start in the figure library')
        check('preview' not in app.pages and 'preview' not in app.nav_buttons, 'No independent preview page or navigation')
        check(not app.nav_buttons['playback'].winfo_manager(), 'No empty experience entry at startup')
        check(not app.confirmation_visible, 'Confirmation appears only on request')
        check(not hasattr(app,'level_slider'), 'User cannot adjust output level')
        check(app.get_config().level==100, 'Fixed full drive in user presentation')
        app.set_config(replace(Config(),level=30))
        check(app.get_config().level==100, 'Old default cannot restore attenuation')
        check(not app.session and str(app.start_button['state'])=='disabled', 'Startup never connects or starts a device')
        app.mode_choice.set('真实串口')
        app.port_choice.set('')
        app.connection_shortcut.invoke()
        pump(duration=.1)
        check(app.quick_connection.winfo_exists() and not app.session, 'Header asks for missing serial settings without opening a port')
        app.quick_connection.destroy()
        app.quick_connection = None
        app.show_page('editor')
        pump(duration=.15)
        editor = app.editor
        check(editor.dimension_panel.winfo_ismapped() and editor.relationship_panel.winfo_ismapped(), 'Dimensions and relations are visible independent panels')
        check(editor.dimension_panel.winfo_rootx() < editor.canvas.winfo_rootx() < editor.relationship_panel.winfo_rootx(), 'Drawing separates the dimension and relation panels')
        editor.tool_buttons['rectangle'].invoke()
        check(editor.tool.get()=='rectangle' and editor.tool_buttons['rectangle']['style']=='ActiveTool.TButton', 'Drawing tools have a visible selected button')
        editor.finish()
        check(editor.tool_buttons['select']['style']=='ActiveTool.TButton', 'Finishing restores selected tool styling')
        for kind, button in editor.relation_buttons.items():
            check(button.winfo_rootx()+button.winfo_width() <= editor.relationship_panel.winfo_rootx()+editor.relationship_panel.winfo_width(), f'Full relation button visible: {kind}')
        root.geometry('1280x860')
        pump(duration=.15)
        check(editor.dimension_panel.winfo_ismapped() and editor.relationship_panel.winfo_ismapped(), 'Both panels remain visible at minimum window size')
        for button in editor.relation_buttons.values():
            check(button.winfo_rootx()+button.winfo_width() <= editor.relationship_panel.winfo_rootx()+editor.relationship_panel.winfo_width(), 'Relation controls fit at minimum size')
        root.geometry('1680x1080')
        app.show_page('library')
        draft = app.draft_signature()
        app.show_page('replay')
        check(pump(lambda:app.replay.sequence is not None,8), 'Integrated replay loads the real bundled sequence')
        check(app.replay.scene is not None and pump(lambda:app.replay.scene.context_created,4), 'Integrated replay creates the embedded three-dimensional renderer')
        check(not app.replay.playing and not app.session, 'Opening replay neither plays nor connects hardware')
        app.replay.show_frame(60)
        app.replay.toggle()
        pump(duration=.2)
        check(app.replay.index>60, 'Replay advances along its own timeline')
        app.show_page('library')
        check(not app.replay.playing and app.draft_signature()==draft, 'Leaving replay pauses without changing the sketch')
        app.pick_shape('POINT')
        points = app.preset_preview.canvas.find_withtag('preview_path')
        check(points and app.preset_preview.canvas.type(points[0])=='oval', 'Point preview is a visible dot')
        app.pick_shape('TRIANGLE')
        check(app.page_name=='library' and not app.confirmation_visible, 'Selection alone does not open confirmation')
        app.present_button.invoke()
        check(app.confirmation_visible and app.confirmation.grab_current()==app.confirmation, 'Present opens contextual confirmation')
        check(not app.session, 'Opening confirmation sends nothing')
        app.vars['z_um'].set('70')
        check(app.get_config().z_um==70000, 'Actual distance editable before sending')
        app.confirm_presentation()
        check(app.page_name=='device' and not app.confirmation_visible and app.return_to_confirmation, 'Offline flow leads to connection with return intent')
        app.mode_choice.set('真实串口')
        app.port_choice.set('COM99')
        app.baud_choice.set('bad')
        app.update_controls()
        check(str(app.baud_picker['state'])=='normal', 'Serial baud remains editable')
        with patch('desktop_app.app.messagebox.showerror') as error, patch('desktop_app.app.Session') as factory:
            app.connect()
            check(error.called and not factory.called, 'Bad baud opens no port')
        app.baud_choice.set('115200')
        app.mode_choice.set('Demo 模拟设备')
        with patch('desktop_app.app.Session',side_effect=demo_session):
            app.connection_shortcut.invoke()
        check(pump(lambda:app.ready and app.confirmation_visible,5), 'Connection returns to confirmation without starting')
        check(app.user_device.get()=='断开连接', 'Header becomes a direct disconnect action')
        check(app.hardware.count==64 and not app.state.output, 'Demo is 8 by 8 and stays stopped')
        check(sent('CONFIG')==0 and sent('START')==0, 'Connection neither sends nor starts the draft')
        check(pump(lambda:(app.session_mirror.directory/'snapshot.json').exists(),2), 'Observer exposes the same readback')
        mirror = json.loads((app.session_mirror.directory/'snapshot.json').read_text(encoding='utf-8'))
        check(mirror['simulated'] and mirror['state']=='IDLE', 'Mirror preserves source identity')
        app.cancel_confirmation()
        pump(duration=.15)
        check(sent('CONFIG')==0 and sent('START')==0, 'Cancel sends no commands')
        app.apply_config()
        hold['value'] = True
        app.confirm_presentation()
        app.confirm_presentation()
        pump(duration=.65)
        check(app.pending_presentation is not None and not app.config_confirmed(), 'ACK is insufficient to confirm the figure')
        check(sent('CONFIG')==1 and sent('START')==0, 'Double click sends one config and does not start early')
        check(str(app.start_button['state'])=='disabled', 'No manual start during pending transfer')
        hold['value'] = False
        check(pump(running,4), 'Matching fresh readback starts the confirmed figure')
        check(sent('START')==1 and app.state.config.level==100, 'One intent starts once at fixed drive')
        check(app.page_name=='playback' and bool(app.nav_buttons['playback'].winfo_manager()), 'Current presentation appears only when useful')
        pump(duration=.2)
        first = app.playback.position_mm
        pump(duration=.3)
        check(first is not None and app.playback.position_mm!=first, 'Marker follows received positions')
        app.send('PAUSE')
        check(pump(lambda:app.state.state=='PAUSED' and not app.busy,3), 'Pause confirmed by device')
        first = app.playback.position_mm
        pump(duration=.2)
        check(first==app.playback.position_mm, 'Paused marker stays fixed')
        app.vars['radius_um'].set('18')
        app.update_controls()
        check(not app.can_play(), 'Edits cannot resume an obsolete figure')
        app.apply_config()
        check('替换' in app.apply_button['text'], 'Replacement is explicit')
        app.confirm_presentation()
        check(pump(lambda:running() and app.state.config.radius_um==18000,4), 'Replacement stops, transfers, confirms and starts')
        app.send('STOP')
        check(pump(stopped,3), 'Stop current output')
        starts = sent('START')
        app.vars['radius_um'].set('17')
        app.apply_config()
        hold['value'] = True
        app.confirm_presentation()
        pump(duration=.45)
        app.send('STOP')
        hold['value'] = False
        check(pump(stopped,3), 'Stop cancels queued intent')
        pump(duration=.35)
        check(app.pending_presentation is None and sent('START')==starts and not app.state.output, 'Late feedback cannot restart after Stop')
        app.apply_config()
        hold['value'] = True
        app.confirm_presentation()
        pump(duration=.25)
        app.vars['radius_um'].set('16')
        hold['value'] = False
        check(pump(lambda:app.pending_presentation is None and not app.busy,3), 'Changed draft cancels the pending operation')
        check(app.state.state=='IDLE' and sent('START')==starts, 'Changed draft never starts an obsolete figure')
        app.editor.transaction(lambda s:s.add_polyline([(-10,-8),(12,-8),(0,13)],True))
        app.vars['shape'].set(SHAPES['CUSTOM'])
        app.editor.finish()
        custom, document = app.get_config(), app.editor.sketch.document()
        target = runtime/'用户图形.json'
        with patch('desktop_app.app.filedialog.asksaveasfilename',return_value=str(target)):
            check(app.save_config(), 'Save user geometry')
        check(str(target.resolve()) in app.recent_files, 'Saved file discoverable in recent figures')
        app.set_config(Config())
        app.saved_signature = app.draft_signature()
        app.open_recent(str(target))
        check(app.get_config()==custom and app.editor.sketch.document()==document, 'Restore exact geometry')
        check(app.page_name=='editor' and not app.confirmation_visible, 'Opening a file edits without sending or confirmation')
        lower = runtime/'旧等级图形.json'
        lower.write_text(json.dumps({'schema':'haptics-config-3','config':replace(custom,level=30).wire(),'sketch':document},ensure_ascii=False),encoding='utf-8')
        app.open_recent(str(lower))
        check(app.get_config().level==100 and app.editor.sketch.document()==document, 'Legacy files cannot alter fixed drive')
        with patch('desktop_app.user_app.messagebox.showerror') as error:
            app.open_recent(str(runtime/'missing.json'))
            check(error.called and app.get_config()==custom, 'Missing file preserves the draft')
        with patch('desktop_app.app.messagebox.askyesnocancel',return_value=False):
            app.new_sketch()
        check(app.vars['z_um'].get()=='70', 'New sketch keeps actual distance')
        app.set_config(custom,document)
        app.editor.apply_button.invoke()
        check(app.confirmation_visible, 'Editor uses the same confirmation flow')
        app.confirm_presentation()
        check(pump(running,4), 'Custom figure confirms before automatic start')
        hold['value'] = True
        pump(duration=1.8)
        check(not app.playback.fresh and app.playback.position_mm is None, 'Stale feedback hides the marker')
        check(str(app.start_button['state'])=='disabled', 'Stale feedback disables resume')
        hold['value'] = False
        check(pump(lambda:app.playback.fresh,3), 'Fresh feedback restores display')
        app.send('STOP')
        check(pump(stopped,3), 'Leave simulated array stopped')
        app.apply_config()
        starts = sent('START')
        with patch.object(app.session,'command',return_value=False):
            app.confirm_presentation()
        pump(duration=.2)
        check(app.pending_presentation is None and bool(app.presentation_error) and sent('START')==starts, 'Failed write never starts a previously confirmed figure')
        app.apply_config()
        app.confirmation.geometry('900x650')
        pump(duration=.25)
        for widget in (app.apply_button,app.cancel_button):
            check(widget.winfo_rooty()+widget.winfo_height() <= app.confirmation.winfo_rooty()+app.confirmation.winfo_height(), 'Confirmation actions fit minimum size')
        app.cancel_confirmation()
        app.show_page('editor')
        app.editor.selected = [app.editor.sketch.edges[0]['id']]
        app.editor.refresh_properties()
        app.editor.size_kind.set('长度')
        app.editor.size_value.set('23')
        app.editor.dimension()
        check(app.editor.dimension_list.size()==1 and app.editor.relation_list.size()==0, 'Dimensions are listed separately from geometric relations')
        app.editor.dimension_list.selection_set(0)
        app.editor.remove_dimension()
        check(app.editor.dimension_list.size()==0, 'Dimension can be removed from its own panel')
        app.show_page('field')
        panel = app.acoustic_panel
        check(pump(lambda:panel.payload is not None,8), 'Sound-field demo computes a theoretical field')
        check(panel.notebook.tab(0,'text')=='理论声场', 'Theoretical field is labelled explicitly')
        with patch('desktop_app.app.messagebox.askyesnocancel',return_value=False):
            app.prepare_field_measurement()
        check(app.confirmation_visible and app.get_config().shape=='POINT' and app.get_config().mod_hz==0, 'Measurement setup uses a fixed focus and still needs confirmation')
        app.confirm_presentation()
        check(pump(running,4), 'Measurement focus starts only after matching confirmation')
        app.show_page('field')
        panel.notebook.select(panel.scan_tab)
        for x, y in [(0,0),(10,0),(0,10)]:
            panel.positions[0].set(str(x))
            panel.positions[1].set(str(y))
            check(pump(lambda:str(panel.capture_button['state'])=='normal',3), 'Receiver capture is ready')
            panel.acquire()
            check(pump(lambda:not panel.busy,5), 'Three raw receiver windows finish atomically')
        check(len(panel.scan_book.points)==9 and panel.scan_surface is not None, 'Three positions produce a bounded interpolated field')
        check('Demo' in panel.scan_status.get() and '插值' in panel.scan_status.get(), 'Simulated measurements and interpolation remain explicit')
        panel.notebook.select(panel.compare_tab)
        panel.refresh_comparison()
        check(pump(lambda:panel.comparison is not None,8), 'Theory and measurements compare at the same coordinates')
        check(len(panel.comparison.positions)==3, 'Comparison preserves all three measured positions')
        app.send('STOP')
        check(pump(stopped,3), 'Stop after sound-field demo')
        app.pick_shape('CIRCLE')
        check(app.get_config().mod_hz==BUTTON_OUTPUT_SETTINGS['mod_hz'], 'Leaving measurement setup restores normal touch modulation')
        app.show_page('replay')
        app.replay.show_frame(324)
        draft = app.draft_signature()
        app.replay.pressure_button.invoke()
        pressure = app.pressure_window
        frozen = pressure.raw.copy()
        app.replay.show_frame(300)
        check(pump(lambda:pressure.future is None,90) and pressure.result is not None, 'Selected pressure frame solves using the upstream algorithm')
        check((pressure.result.raw_kpa==frozen).all(), 'Pressure solve keeps the selected frame frozen')
        check(len(pressure.entries)==2 and pressure.result.config.level==100, 'Pressure presentation exposes size and distance without an output-level control')
        app.capabilities.discard('STATIC_PHASE')
        pump(duration=.15)
        check(str(pressure.send_button['state'])=='disabled', 'Pressure output requires firmware static-phase support')
        app.capabilities.add('STATIC_PHASE')
        check(pump(lambda:str(pressure.send_button['state'])=='normal',2), 'Supported static-phase firmware enables explicit upload')
        pressure.send_button.invoke()
        check(pump(lambda:app.config_confirmed() and not app.busy,4), 'Full static phase table is confirmed by device readback')
        check(app.state.state=='IDLE' and not app.state.output and app.draft_signature()==draft, 'Uploading a pressure frame does not start output or replace the sketch')
        app.navigate('playback')
        check(app.page_name=='playback' and app.can_play(), 'Confirmed pressure frame has accessible presentation controls')
        app.send('START')
        check(pump(running,4), 'Explicit pressure start works after matching readback')
        check(app.playback.position_mm is None and '压力单帧' in app.playback.heading.get(), 'Static pressure never shows a fictitious moving focus')
        app.send('PAUSE')
        check(pump(lambda:not app.busy and app.state.state=='PAUSED',3), 'Pressure presentation can pause')
        app.send('STOP')
        check(pump(stopped,3), 'Pressure presentation can stop')
        pressure.destroy()
        app.apply_config()
        app.confirm_presentation()
        check(pump(running,4) and app.state.config.phase_table=='NONE', 'Ordinary presentation exits static mode without losing the draft')
        app.send('STOP')
        check(pump(stopped,3), 'Leave integrated demo stopped')
        app.connection_shortcut.invoke()
        check(pump(lambda:not app.session.is_alive(),4), 'Header directly disconnects the active session')
        check(pump(lambda:app.user_device.get()=='连接设备',2), 'Header returns to the connect action')
        check(not app.pending_presentation, 'Disconnect cancels any presentation intent')
        check(not errors,'No Tk callback errors')
        passed = True
    except Exception as error:
        errors.append(str(error))
        raise
    finally:
        app.saved_signature = app.draft_signature()
        app.close()
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(.02)
        (runtime/'user-gui-smoke.json').write_text(json.dumps({'passed':passed,'checks':checks,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':passed,'checks':len(checks),'report':str(runtime/'user-gui-smoke.json')},ensure_ascii=False))
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
