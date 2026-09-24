"""Opt-in visible GUI integration test: python -m desktop_app.tests.gui_smoke.

Runs the real Tk event loop; never opens a hardware port. Screenshots require
Pillow and are written only to the ignored desktop_app/.runtime directory.
"""

from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import tkinter as tk

from desktop_app.app import App
from desktop_app.controller import Session
from desktop_app.transport import DemoTransport
from desktop_app.model import ARRAY_PRESETS, Config, SHAPES, encode_points, trajectory_point
from desktop_app.sketch import Sketch, rotation_matrix
from desktop_app.palm import HAND_OUTLINE, PALM_OUTLINE


def main():
    runtime = Path(__file__).resolve().parents[1] / ".runtime"
    runtime.mkdir(exist_ok=True)
    root = tk.Tk()
    app = App(root)
    root.geometry("1380x920+25+25")
    errors = []
    steps = []
    deadline = time.monotonic() + 60
    custom = None
    running_position = None
    running_sample = None
    paused_position = None
    paused_sample = None
    hold_feedback = {"value": False}

    def deferred_session(*args, **kwargs):
        transport = DemoTransport(array=kwargs.get("demo_array"))
        snapshot = transport.device.snapshot

        def delayed_snapshot():
            raw = snapshot()
            return b"" if hold_feedback["value"] else raw

        transport.device.snapshot = delayed_snapshot
        return Session(*args, **kwargs, factory=lambda: transport)

    def fail(error):
        errors.append(str(error))
        traceback.print_exc()
        with patch("desktop_app.app.messagebox.askyesnocancel", return_value=False):
            app.close()

    root.report_callback_exception = lambda kind, value, tb: fail("".join(traceback.format_exception(kind, value, tb)))

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    def capture(name):
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        time.sleep(.15)
        try:
            from PIL import ImageGrab
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(runtime / name)
        except ImportError:
            print("Pillow absent: screenshot skipped")
        finally:
            root.attributes("-topmost", False)

    def start():
        nonlocal custom
        check(app.tabs.select() == str(app.editor_tab), "Start directly in editor")
        check(not app.debug_mode.get(), "Start in user mode")
        check(all(app.tabs.tab(tab, "state") == "hidden" for tab in app.debug_tabs), "Hide debug tabs")
        check(not app.hardware_detail.winfo_ismapped(), "Hide engineering details in user mode")
        capture("demo-startup.png")
        app.tabs.select(5)
        capture("demo-device.png")
        app.tabs.select(app.editor_tab)
        single = Config(shape="CUSTOM", path_xy_um="13237:-17123")
        app.set_config(single)
        check(app.get_config() == single, "One-node custom figure survives UI roundtrip")
        app.set_config(Config())
        app.tabs.select(app.editor_tab)
        root.update_idletasks()
        editor = app.editor

        def event(point, shift=False):
            x, y = editor.to_screen(point)
            return SimpleNamespace(x=x, y=y, state=1 if shift else 0)

        def tool(name):
            editor.tool.set(name)
            editor.choose_tool()

        def click(point):
            editor.press(event(point))
            editor.release(event(point))

        check(editor.canvas.find_withtag('palm_reference'), 'Faint palm reference is visible while drawing')
        for point in PALM_OUTLINE:
            x, y = editor.to_screen(point)
            check(editor.left <= x <= editor.right and editor.top <= y <= editor.bottom,
                  'Entire palm fits inside the initial drawing surface; fingers may extend beyond it')
        check(editor.from_screen((editor.left+editor.right)/2, editor.top+1) is not None,
              'Drawing near the top is allowed')
        drawable_fraction = (editor.right-editor.left)*(editor.bottom-editor.top)/(editor.canvas.winfo_width()*editor.canvas.winfo_height())
        check(drawable_fraction > .85, 'At least 85 percent of the canvas accepts points')
        capture('demo-hand-editor.png')
        fingertip = HAND_OUTLINE[np.argmax(HAND_OUTLINE[:, 1])]
        check(editor.to_screen(fingertip)[1] < editor.top, 'Palm view naturally crops the upper fingers')
        original_scale = editor.scale
        editor.zoom(2.4)
        x, y = editor.to_screen(fingertip)
        check(editor.left <= x <= editor.right and editor.top <= y <= editor.bottom,
              'Zooming out reveals actual fingers rather than empty background')
        check(abs(editor.scale-original_scale/2.4) < 1e-8, 'Hand and drawing use the same zoom transform')
        capture('demo-hand-zoomed.png')
        editor.fit()
        tool('polyline')
        click((-20, 0))
        click((-5, 0))
        editor.finish()
        tool('polyline')
        click((5, 3))
        click((20, 8))
        editor.finish()
        click((-5, 0))
        editor.press(event((5, 3), shift=True))
        editor.release(event((5, 3), shift=True))
        check(len(editor.selected_nodes) == 2, 'Shift selects two endpoints independently of edges')
        check(str(editor.relation_buttons['coincident']['state']) == 'normal', 'Coincidence enabled for distinct endpoints')
        check(str(editor.relation_buttons['parallel']['state']) == 'disabled', 'Point selection cannot set line relations')
        editor.relation_buttons['coincident'].invoke()
        nodes = editor.selected_nodes[:]
        np.testing.assert_allclose(editor.sketch.nodes[nodes[0]], editor.sketch.nodes[nodes[1]], atol=.002)
        check(str(editor.relation_buttons['coincident']['state']) == 'disabled', 'Existing coincidence is disabled')
        editor.property_tabs.select(1)
        capture('demo-sketch-relations.png')
        editor.relation_list.selection_set(0)
        editor.relation_list.event_generate('<<ListboxSelect>>')
        root.update()
        editor.remove_relation_button.invoke()
        check(not editor.sketch.constraints, 'Selected coincidence is removable')
        editor.undo()
        check(editor.sketch.constraints[0]['kind'] == 'coincident', 'Undo restores removed coincidence')
        origin = np.array(editor.sketch.nodes[nodes[0]])
        editor.press(event(origin))
        editor.motion(event(origin+[1, 2]))
        editor.release(event(origin+[1, 2]))
        np.testing.assert_allclose(editor.sketch.nodes[nodes[0]], editor.sketch.nodes[nodes[1]], atol=.002)
        # Drag-to-snap creates the same persistent relation, rather than moving once.
        editor.restore((Sketch().document(), None))
        editor.transaction(lambda s: (s.add_polyline([(-20, 0), (-5, 0)]), s.add_polyline([(5, 5), (20, 10)])))
        editor.press(event((-5, 0)))
        editor.motion(event((5, 5)))
        editor.release(event((5, 5)))
        check(any(c['kind'] == 'coincident' for c in editor.sketch.constraints), 'Dropping a point on another adds coincidence')
        editor.restore((Sketch().document(), None))
        editor.property_tabs.select(0)

        tool('rectangle')
        editor.press(event((-18.123, -13.789)))
        editor.motion(event((17.321, 15.987)))
        editor.release(event((17.321, 15.987)))
        check(len(editor.sketch.edges) == 4, 'Drag rectangle creates four editable edges')
        np.testing.assert_allclose(editor.sketch.nodes[0], [-18.123, -13.789])
        editor.finish()
        editor.select_contour(event((0, -13.789)))
        check(len(editor.selected) == 4, 'Double-click selects the entire rectangle')
        check(tuple(editor.dimension_picker['values']) == ('宽度', '高度'), 'Whole contour only offers span dimensions')
        check('水平' in editor.dimension_hint.get(), 'Width clearly describes the horizontal span')
        editor.size_kind.set('宽度')
        editor.size_value.set('32.125')
        editor.dimension()
        editor.size_kind.set('高度')
        editor.size_value.set('25')
        editor.dimension()
        check(editor.canvas.find_withtag('dimension'), 'Assigned dimensions appear on drawing')
        original = editor.signature()
        before_rotation = np.array(editor.sketch.nodes)
        center = before_rotation.mean(axis=0)
        editor.rotation_value.set('30')
        editor.rotate_button.invoke()
        np.testing.assert_allclose(editor.sketch.nodes, (before_rotation-center) @ rotation_matrix(30).T+center, atol=1e-7)
        check(abs(float(editor.size_value.get())-25) < .002, 'Rotation keeps displayed dimension value')
        check(editor.canvas.find_withtag('rotation_handle'), 'Rotation handle is visible on whole contour')
        rotated = editor.signature()
        editor.undo()
        check(editor.signature() == original, 'Rotation can be undone')
        editor.redo()
        check(editor.signature() == rotated, 'Rotation can be redone')
        editor.selected = editor.sketch.groups[0][:]
        editor.refresh()
        x, y, pivot = editor.rotation_handle
        handle_start = ((x-editor.center_x)/editor.scale, (editor.center_y-y)/editor.scale)
        handle_end = np.array(pivot)+rotation_matrix(-30) @ (np.array(handle_start)-pivot)
        editor.press(event(handle_start))
        editor.motion(event(handle_end, shift=True))
        editor.release(event(handle_end, shift=True))
        np.testing.assert_allclose(editor.sketch.nodes, before_rotation, atol=1e-7)
        before_move = np.array(editor.sketch.nodes[:4])
        first_curve = editor.sketch.curve(editor.sketch.edge(1))
        midpoint = first_curve.point(.5)
        tool('move')
        editor.press(event(midpoint))
        editor.motion(event(midpoint+np.array([1, 1])))
        editor.release(event(midpoint+np.array([1, 1])))
        np.testing.assert_allclose(np.array(editor.sketch.nodes[:4])-before_move, np.ones((4, 2)), atol=.002)
        editor.undo()
        editor.finish()
        editor.selected = [1]
        editor.refresh()
        editor.size_kind.set('长度')
        editor.size_value.set('32.125')
        editor.dimension()
        check(editor.sketch.constraints[-1]['value'] == 32.125, 'Dimension becomes persistent geometry relation')
        original = editor.signature()
        check(str(editor.relation_buttons['vertical']['state']) == 'disabled', 'Conflicting relation is disabled before clicking')
        editor.add_relation('vertical')
        check(editor.signature() == original, 'Conflicting relation is rejected without changing draft')
        tool('circle')
        click((24, 4))
        click((29, 4))
        check(len(editor.sketch.groups) == 2, 'Independent circle does not replace rectangle')
        check(editor.size_kind.get() == '直径' and '两倍半径' in editor.dimension_hint.get(), 'Circle diameter is explicit')
        check(str(editor.relation_buttons['horizontal']['state']) == 'disabled', 'Circle cannot have a horizontal relation')
        editor.size_value.set('10')
        editor.dimension()
        tool('polyline')
        for point in ((-20, -22), (20, -22)):
            click(point)
        editor.finish()
        line = editor.sketch.edges[-1]['id']
        editor.selected = [line]
        editor.press(event((0, -22)))
        editor.motion(event((0, -16)))
        editor.release(event((0, -16)))
        check(editor.sketch.edge(line)['bend'] > .2, 'Drag straight edge into circular arc')
        curved = editor.signature()
        editor.undo()
        check(abs(editor.sketch.edge(line)['bend']) < 1e-9, 'Undo bend')
        editor.redo()
        check(editor.signature() == curved, 'Redo restores bend')
        tool('bezier')
        for point in ((-20, 22), (-12, 28), (12, 8), (20, 22)):
            click(point)
        bezier = editor.sketch.edges[-1]
        check(bezier['controls'] is not None, 'Control-point curve is created')
        handle = bezier['controls'][0]
        editor.selected = [bezier['id']]
        editor.press(event(editor.sketch.nodes[handle]))
        editor.motion(event((-10, 26)))
        editor.release(event((-10, 26)))
        np.testing.assert_allclose(editor.sketch.nodes[handle], [-10, 26])
        editor.scale_value.set('1.1')
        editor.scale_all()
        check(any(c['kind'] == 'length' and abs(c['value']-32.125*1.1) < 1e-8 for c in editor.sketch.constraints), 'Scaling updates assigned dimensions')
        check(any(c['kind'] == 'diameter' and abs(c['value']-11) < 1e-8 for c in editor.sketch.constraints), 'Scaling updates circle diameter')
        original = editor.signature()
        editor.order_list.selection_set(1)
        editor.reorder(-1)
        check(editor.sketch.order[0] == 1, 'User can place second contour first')
        editor.order_auto.set(True)
        editor.auto_order()
        check(not editor.sketch.order, 'Skip ordering restores automatic order')
        tool('erase')
        circle = next(e for e in editor.sketch.edges if e['radius'] is not None)
        center = editor.sketch.nodes[circle['a']]
        click((center[0]+circle['radius'], center[1]))
        check(all(e['id'] != circle['id'] for e in editor.sketch.edges), 'Eraser deletes selected circle')
        editor.undo()
        check(any(e['id'] == circle['id'] for e in editor.sketch.edges), 'Undo restores erased geometry and order')
        editor.selected = editor.sketch.groups[0][:]
        editor.refresh()
        editor.rotation_value.set('27')
        editor.rotate_button.invoke()
        app.preview_sketch()
        root.update_idletasks()
        check(app.preset_preview.canvas.find_withtag('hand_reference'), 'Sketch preview uses hand illustration')
        check(len(app.preset_preview.canvas.find_withtag('preview_path')) >= 4, 'Preview keeps disconnected contours separate')
        capture('demo-sketch-preview.png')
        app.tabs.select(app.editor_tab)
        editor.finish()
        editor.fit()
        editor.selected = editor.sketch.groups[0][:]
        editor.refresh()
        capture('demo-sketch-dimensions.png')
        custom = app.get_config()
        # Palm guidance shares the preview coordinates, includes global offsets,
        # and offers an explicit (undoable) fit instead of changing geometry silently.
        app.vars['cx_um'].set('60')
        app.sync_editor()
        check('明显超出' in editor.palm_notice.get(), 'Large palm overhang warns while editing')
        check('明显超出' in app.preset_preview.warning.get(), 'Preview uses the same palm warning')
        editor.fit_to_palm()
        check('明显超出' not in editor.palm_notice.get(), 'Fit moves the drawing into palm even with a global offset: '+editor.message.get())
        editor.undo()
        app.vars['cx_um'].set('0')
        check(abs(editor.view_center[0]) < 10, 'Returning the offset to zero recenters the palm framing')
        app.editor.zoom(.8)
        root.geometry("1200x820+25+25")
        root.update_idletasks()
        check(app.get_config() == custom, "Zoom and resize preserve physical coordinates")
        root.geometry("1380x920+25+25")
        app.editor.fit()
        for label, size in ARRAY_PRESETS.items():
            if size is not None:
                app.demo_array_choice.set(label)
                app.select_hardware()
                check(app.get_config() == custom, "Hardware selection must not alter design")
        filename = runtime / "smoke-config.json"
        with patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(filename)):
            app.save_custom()
        with patch('desktop_app.sketch_editor.messagebox.askyesno', return_value=True):
            app.editor.clear()
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(filename)), \
                patch("desktop_app.app.messagebox.askyesnocancel", return_value=False):
            app.load_config()
        check(app.get_config() == custom, "File roundtrip")
        check(not app.debug_mode.get(), "Loading a figure stays in user mode")
        check(app.current_file == filename and not app.draft_dirty(), "Import tracks current saved file")
        # Invalid input and cancelled replacement preserve both draft and file identity.
        invalid = runtime / "invalid.json"
        invalid.write_text('{"schema":"wrong"}', encoding="utf-8")
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(invalid)), \
                patch("desktop_app.app.messagebox.showerror") as error:
            app.load_config()
            check(error.called, "Invalid import is explained")
        check(app.get_config() == custom and app.current_file == filename, "Invalid import preserves current document")
        app.vars["cx_um"].set("1")
        draft = app.get_config()
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(filename)), \
                patch("desktop_app.app.messagebox.askyesnocancel", return_value=None):
            app.load_config()
            app.close()
        check(app.get_config() == draft and not app.closing, "Cancel import or close retains edits")
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(filename)), \
                patch("desktop_app.app.messagebox.askyesnocancel", return_value=True), \
                patch("desktop_app.app.filedialog.asksaveasfilename", return_value=""):
            app.load_config()
        check(app.get_config() == draft, "Cancel saving also cancels replacement")
        original = filename.read_bytes()
        with patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(filename)), \
                patch("desktop_app.app.os.replace", side_effect=OSError("disk unavailable")), \
                patch("desktop_app.app.messagebox.showerror"):
            check(not app.save_config(), "Failed save is reported")
        check(filename.read_bytes() == original and app.draft_dirty(), "Failed save preserves original bytes and dirty state")
        backup = runtime / "edited-copy.json"
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(filename)), \
                patch("desktop_app.app.messagebox.askyesnocancel", return_value=True), \
                patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(backup)):
            app.load_config()
        check(json.loads(backup.read_text(encoding="utf-8"))["config"] == draft.wire(), "Save before import retains previous edits")
        check(app.get_config() == custom and not app.draft_dirty(), "Successful import becomes clean")
        app.vars["cx_um"].set("1")
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(backup)), \
                patch("desktop_app.app.messagebox.askyesnocancel", return_value=True), \
                patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(backup)):
            app.vars["cx_um"].set("2")
            app.load_config()
        check(app.get_config().cx_um == 2000, "Import rereads a file saved from the replacement prompt")
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(filename)):
            app.load_config()
        capture("demo-editor.png")
        app.demo_array_choice.set("8 × 8 · 64 路")
        app.select_hardware()
        with patch("desktop_app.app.Session", side_effect=deferred_session):
            app.connect()

    def apply():
        check(not app.state.output, "Connection must not enable output")
        check(app.hardware.count == 64, "Installed array handshake")
        check(str(app.start_button["state"]) == "disabled", "No playback before explicit upload")
        app.set_config(app.state.config)
        app.update_controls()
        check(str(app.start_button["state"]) == "disabled", "Matching default figure is not an upload receipt")
        app.set_config(custom)
        oversized = replace(custom, cx_um=60000)
        app.set_config(oversized)
        app.update_controls()
        check('明显超出' in app.preset_preview.warning.get(), 'Large-hand scenario displays a warning')
        check(str(app.apply_button['state']) == 'normal', 'Palm warning never disables sending within device limits')
        capture('demo-palm-warning.png')
        app.set_config(custom)
        app.start_button.invoke()
        app.send("START")
        check(not app.busy, "Guard START even when called outside the disabled button")
        hold_feedback["value"] = True
        app.editor.apply_button.invoke()
        check(str(app.playback.play_button["state"]) == "disabled", "No playback while waiting for upload confirmation")
        check(app.tabs.select() == str(app.playback_tab), "Uploading opens ordinary feedback page")

    def release_feedback():
        check(app.pending_verb is None, "Device ACK was consumed")
        check(app.await_revision is not None, "Still waiting for STATE after ACK")
        check(str(app.playback.play_button["state"]) == "disabled", "ACK alone cannot unlock playback")
        check(not app.config_confirmed(), "Do not claim upload success before matching feedback")
        hold_feedback["value"] = False

    def play():
        check(app.state.config == custom, "Applied design readback")
        check(len(app.state.phases) == 64, "Use all installed emitters")
        check(not app.debug_mode.get(), "Feedback accessible in normal mode")
        check(app.playback.plot_config == custom, "Show received figure after upload")
        check("发送成功" in app.playback.feedback.get(), "Show upload confirmation")
        check(str(app.playback.play_button["state"]) == "normal", "Enable playback after confirmed upload")
        # Even a connected, previously configured device must not play a new draft.
        app.vars["cx_um"].set("1")
        app.update_controls()
        check(str(app.start_button["state"]) == "disabled", "Editing disables playback")
        app.vars["cx_um"].set("0")
        app.update_controls()
        capture("demo-upload-confirmed.png")
        app.playback.play_button.invoke()

    def visible_running():
        nonlocal running_position, running_sample
        check(app.tabs.select() == str(app.playback_tab), "Play keeps ordinary feedback visible")
        check("正在播放" in app.playback.heading.get(), "Visible running status")
        check(app.playback.position_mm == app.state.focus_mm[:2], "Marker comes from received position")
        running_position = app.playback.position_mm
        running_sample = app.state.sample

    def visible_moving():
        check(app.playback.position_mm != running_position, "Marker visibly moves as feedback arrives")
        check(app.playback.position_mm == app.state.focus_mm[:2], "No locally invented position")
        check(app.playback.canvas.find_withtag("hand_reference"), "Hand illustration is visible")
        x, y = app.playback.screen(app.state.focus_mm[:2])
        marker = app.playback.canvas.coords(app.playback.marker)
        np.testing.assert_allclose([(marker[0]+marker[2])/2, (marker[1]+marker[3])/2], [x, y])
        capture("demo-playback.png")
        app.playback.update_state(replace(app.state, scan_on=False, output=False), True, '切换线段')
        check(app.playback.position_mm is None, 'Blank travel never shows an active tactile marker')
        check('切换' in app.playback.message.get(), 'Blank travel is explained')
        app.playback.update_state(app.state, True, '正在播放')
        app.playback.pause_button.invoke()

    def visible_paused():
        nonlocal paused_position, paused_sample
        check("已暂停" in app.playback.heading.get(), "Show pause immediately after readback")
        paused_position = app.playback.position_mm
        paused_sample = app.state.sample

    def resume():
        check(app.playback.position_mm == paused_position, "Paused marker stays fixed across snapshots")
        check(str(app.playback.play_button["state"]) == "normal", "Resume remains available")
        app.playback.play_button.invoke()

    def show_debug():
        app.debug_mode.set(True)
        app.toggle_debug()

    def inspect_running():
        payload = app.actual_plot.last_payload
        check(payload[1].count == 64, "Render installed phase dimensions")
        check(payload[0] == custom, "Render design path separately")
        np.testing.assert_allclose(trajectory_point(custom, 0)[:2], np.array(custom.strokes_um()[0][0])/1000)
        snapshot_path = runtime / "smoke-phases.csv"
        with patch("desktop_app.app.filedialog.asksaveasfilename", return_value=str(snapshot_path)):
            app.export_snapshot()
        check(len(snapshot_path.read_text(encoding="utf-8-sig").splitlines()) == 65, "Export all phase channels")
        app.tabs.select(0)
        capture("demo-running.png")
        app.tabs.select(app.editor_tab)
        capture("demo-editor-debug.png")
        app.send("PAUSE")

    def stopped():
        app.send("STOP")

    def local():
        check(app.playback.position_mm is None, "Stop removes active marker")
        saved_state = app.state
        with patch("desktop_app.app.filedialog.askopenfilename", return_value=str(runtime / "smoke-config.json")), \
                patch("desktop_app.app.messagebox.askyesnocancel", return_value=False), \
                patch.object(app.session, 'command', wraps=app.session.command) as commands:
            app.load_config()
            check(not commands.called, 'Import never sends a command')
        check(app.state.config == saved_state.config and not app.busy, "Import leaves device contents unchanged")
        check(str(app.start_button["state"]) == "disabled", "Imported document requires upload even with matching device contents")
        for code in SHAPES:
            if code == "CUSTOM":
                continue
            app.shape_buttons[code].invoke()
            check(app.tabs.select() == str(app.preset_tab), "Preset buttons open preview")
            check(app.preset_preview.config.shape == code, "Preview selected preset")
            check(str(app.start_button["state"]) == "disabled", "New preset requires upload")
            check(app.state.config == custom, "Selecting a preset does not alter the device")
        app.shape_buttons["SQUARE"].invoke()
        capture("demo-preset-square.png")
        app.shape_buttons["CUSTOM"].invoke()
        check(app.get_config() == custom, "Preset selection preserves custom drawing")
        app.send("MODE", value="LOCAL")

    def button():
        app.send("_BUTTON", action="NEXT")

    def local_play():
        check(app.state.config.shape != "CUSTOM", "Physical selection must appear in readback")
        app.send("_BUTTON", action="PLAY")

    def disconnect_local():
        app.disconnect()

    def reconnect():
        app.demo_array_choice.set("4 × 4 · 16 路")
        app.select_hardware()
        app.connect()

    def incompatible():
        app.set_config(replace(custom, cx_um=100000))
        app.update_controls()
        check(str(app.apply_button["state"]) == "disabled", "Reject trajectory outside declared workspace")
        check(str(app.start_button["state"]) == "disabled", "Reconnect resets upload confirmation")
        app.set_config(custom)
        app.update_controls()
        check(str(app.apply_button["state"]) == "normal", "Free-position drawing accepted on small array")
        app.apply_custom()

    def small_running():
        app.send("START")

    def mute():
        check(len(app.state.phases) == 16, "4x4 actual readback")
        check(app.state.config.strokes_um() == custom.strokes_um(), "4x4 preserves every contour coordinate")
        app.debug_mode.set(False)
        app.toggle_debug()
        root.geometry("1100x780+25+25")
        app.tabs.select(app.playback_tab)
        capture("demo-playback-minimum.png")
        check(app.playback.play_button.winfo_ismapped(), "Playback controls visible at minimum size")
        app.tabs.select(app.editor_tab)
        capture("demo-minimum.png")
        check(app.editor.apply_button.winfo_ismapped(), "Send button visible at minimum size")
        check(app.editor.apply_button.winfo_rooty()+app.editor.apply_button.winfo_height()
              <= root.winfo_rooty()+root.winfo_height(), "Editor actions fit minimum window")
        app.send("_MUTE", value=True)

    def finish():
        check(not app.ready, "Lost telemetry must close connection")
        check("未知" in app.source_line.get(), "Disconnected state must be visibly unknown")
        check(app.playback.position_mm is None, "Lost feedback removes live marker")
        check(str(app.start_button["state"]) == "disabled", "Lost feedback disables playback")
        check(any("超时" in record["text"] for record in app.log_records), "Timeout reported")
        with patch("desktop_app.app.messagebox.askyesnocancel", return_value=False):
            app.close()

    steps.extend([
        (lambda: True, start),
        (lambda: app.ready and app.state is not None, apply),
        (lambda: app.await_revision is not None, release_feedback),
        (lambda: app.state and app.state.revision == 1 and not app.busy, play),
        (lambda: app.state.output and not app.busy, visible_running),
        (lambda: app.state.sample > running_sample + 2 and app.state.output, visible_moving),
        (lambda: app.state.state == "PAUSED" and not app.busy, visible_paused),
        (lambda: app.state.sample > paused_sample, resume),
        (lambda: app.state.output and not app.busy, show_debug),
        (lambda: app.state and app.state.output and app.actual_plot.last_payload is not None
         and app.actual_plot.last_payload[4] is True, inspect_running),
        (lambda: app.state.state == "PAUSED" and not app.busy, stopped),
        (lambda: app.state.state == "IDLE" and not app.busy, local),
        (lambda: app.state.mode == "LOCAL" and not app.busy, button),
        (lambda: app.state.config.shape != "CUSTOM", local_play),
        (lambda: app.state.output, disconnect_local),
        (lambda: not app.session.is_alive(), reconnect),
        (lambda: app.ready and app.state is not None, incompatible),
        (lambda: app.state.revision == 1 and not app.busy, small_running),
        (lambda: app.state.output and not app.busy and app.actual_plot.last_payload is not None
         and app.actual_plot.last_payload[1].count == 16 and app.actual_plot.last_payload[4], mute),
        (lambda: not app.session.is_alive() and not app.ready, finish),
    ])

    def run_step():
        try:
            if time.monotonic() > deadline:
                raise TimeoutError("GUI scenario exceeded 40 seconds")
            if steps and steps[0][0]():
                _, action = steps.pop(0)
                print("GUI:", action.__name__, flush=True)
                action()
            if not app.closing:
                root.after(150, run_step)
        except Exception as error:
            fail(error)

    root.after(500, run_step)
    root.mainloop()
    result = {"passed": not errors and not steps, "errors": errors, "remaining_steps": len(steps)}
    (runtime / "gui-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
