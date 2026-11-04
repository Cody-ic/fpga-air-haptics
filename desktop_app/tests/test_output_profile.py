"""Offline checks for the button experiment's output profile; no Tk window or port."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from desktop_app.app import App
from desktop_app.model import BUTTON_OUTPUT_SETTINGS, Config, SHAPES


class Variable:
    def __init__(self, value):
        self.value = str(value)

    def get(self):
        return self.value

    def set(self, value):
        self.value = str(value)


class OutputProfileTests(unittest.TestCase):
    def test_same_experimental_settings_for_all_shapes_preserving_geometry(self):
        for shape in SHAPES:
            original = Config(shape=shape, cx_um=3000, cy_um=-4000, radius_um=12000,
                              path_xy_um='-5000:0,5000:0' if shape == 'CUSTOM' else 'NONE')
            scales = {name: (1000 if name in ('cx_um','cy_um','z_um','radius_um','repeat_millihz') else 1)
                      for name in original.__dataclass_fields__ if name not in
                      ('shape','path_xy_um','path_closed','scan_paths','blank_us','phase_table')}
            variables = {name: Variable(getattr(original, name)/scale) for name, scale in scales.items()}
            variables['shape'] = Variable(SHAPES[shape])
            editor = SimpleNamespace(blank_us=original.blank_us, config_fields=lambda: dict(
                path_xy_um=original.path_xy_um, path_closed=original.path_closed, scan_paths=original.scan_paths))
            app = SimpleNamespace(vars=variables, scales=scales, editor=editor,
                                  refresh_pattern_preview=Mock(), update_controls=Mock(),
                                  status_line=Variable(''), send=Mock())
            App.use_button_profile(app)
            actual = App.get_config(app)
            for name, value in BUTTON_OUTPUT_SETTINGS.items():
                self.assertEqual(getattr(actual,name),value)
            for name in ('shape','cx_um','cy_um','radius_um','path_xy_um','path_closed','scan_paths','blank_us'):
                self.assertEqual(getattr(actual,name),getattr(original,name))
            app.send.assert_not_called()

    def test_explicit_imports_are_not_forced_to_new_defaults(self):
        old = Config(z_um=150000, level=20, mod_hz=200, repeat_millihz=500)
        self.assertEqual(Config.from_wire(old.wire()),old)
