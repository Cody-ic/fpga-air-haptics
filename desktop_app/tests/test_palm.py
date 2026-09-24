import unittest

from desktop_app.palm import HAND_OUTLINE, PALM_OUTLINE, outside_distances, palm_warning
from desktop_app.sketch import Sketch


class PalmTests(unittest.TestCase):
    def test_palm_framing_excludes_fingers_but_shared_hand_keeps_them(self):
        self.assertGreater(HAND_OUTLINE[:, 1].max(), 100)
        self.assertLessEqual(PALM_OUTLINE[:, 1].max(), 35)
        self.assertEqual(outside_distances([(0, 0)])[0], 0)
        self.assertTrue(palm_warning([[(0, 90)]]))
        self.assertTrue(palm_warning([[(-63, 22)]]))

    def test_inside_and_small_overhang_are_quiet(self):
        self.assertEqual(palm_warning([[(-15, -15), (15, 15)]]), '')
        self.assertEqual(palm_warning([[(50, 0)]]), '')
        self.assertGreater(outside_distances([(50, 0)])[0], 0)

    def test_large_overhang_and_offsets_warn_without_changing_paths(self):
        paths = [[(-60, -35), (60, -35), (60, -45), (-60, -45)]]
        before = repr(paths)
        self.assertTrue(palm_warning(paths))
        self.assertEqual(repr(paths), before)
        self.assertTrue(palm_warning([[(70, 0), (75, 5)]]))
        self.assertEqual(palm_warning([]), '')

    def test_curve_middle_outside_is_detected(self):
        s = Sketch()
        curve = s.add_bezier([(-10, 0), (-110, 0), (-110, 15), (10, 15)])
        self.assertEqual(palm_warning([[(-10, 0), (10, 15)]]), '')
        self.assertTrue(palm_warning([s.curve(s.edge(curve)).samples()]))
