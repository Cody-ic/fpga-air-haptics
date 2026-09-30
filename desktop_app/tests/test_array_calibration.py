from dataclasses import replace
import json
import math
from pathlib import Path
import queue
import time
import unittest

import numpy as np

from desktop_app.array_geometry import Geometry, grid_geometry
from desktop_app.calibration import CalibrationRunner, solve_phase, validate_document
from desktop_app.controller import Session
from desktop_app.demo import DemoDevice
from desktop_app.model import ArraySpec, Config, array_coordinates, field_slice, focus_phases
from desktop_app.protocol import decode, encode
from desktop_app.transport import DemoTransport
from hardware.mechanical.export_array import geometry_from_parameters
from firmware.nucleo_f411re.generate_geometry import generate


class GeometryTests(unittest.TestCase):
    def test_cad_coordinates_export_matches_original_fixture(self):
        root = Path(__file__).resolve().parents[2]
        p = json.loads((root/'hardware/mechanical/parameters.json').read_text(encoding='utf-8'))
        g = geometry_from_parameters(p)
        self.assertEqual(g, Geometry.load(root/'hardware/arrays/p1_flat_4x4.json'))
        np.testing.assert_allclose(g.positions_mm, [(11*(c-1.5), 11*(r-1.5), 0) for r in range(4) for c in range(4)])
        self.assertEqual(g, Geometry.from_document(g.document()))

    def test_sphere_faces_its_centre_and_equal_radius_same_phase(self):
        g = grid_geometry(4, 4, 11, radius_mm=80)
        positions = np.array(g.positions_mm)
        distances = np.linalg.norm(positions-[0, 0, 80], axis=1)
        np.testing.assert_allclose(distances, 80, atol=1e-6)
        np.testing.assert_allclose(positions+80*np.array(g.normals), np.tile([0, 0, 80], (16, 1)), atol=.00005)
        phases = focus_phases(Config(), (0, 0, 80), ArraySpec.from_geometry(g))
        self.assertEqual(len(set(phases)), 1)
        config = Config(shape='POINT', z_um=80000, level=100)
        _, _, field = field_slice(config, phases, (0, 0, 80), array=ArraySpec.from_geometry(g), resolution=11)
        self.assertAlmostEqual(field[5, 5], 1, places=6)

    def test_arbitrary_nonzero_z_and_phase_formula(self):
        g = grid_geometry(1, 2, 11)
        elements = list(g.elements)
        elements[1] = (7500000, -3000000, 15000000, 0, 1000000, 0)
        g = replace(g, elements=tuple(elements), sound_speed_mm_s=350000).validate()
        a = ArraySpec.from_geometry(g)
        r = np.linalg.norm(array_coordinates(a)-[2, 1, 100], axis=1)
        expected = np.floor(np.remainder(-r*40000/350000, 1)*64+.5).astype(int) % 64
        np.testing.assert_array_equal(focus_phases(Config(), (2, 1, 100), a), expected)
        with self.assertRaisesRegex(ValueError, '未读取完整'):
            array_coordinates(ArraySpec.from_wire(a.wire()))

    def test_invalid_profile_identity_normal_duplicate_and_capacity(self):
        g = grid_geometry()
        for mutate in (lambda d: d['elements'][0].update(channel=1),
                       lambda d: d['elements'][0].update(normal=[0, 0, 2]),
                       lambda d: d['elements'][0].update(position_mm=[math.nan, 0, 0]),
                       lambda d: d['elements'][0].update(position_mm=d['elements'][1]['position_mm']),
                       lambda d: d.update(geometry_id='0'*24)):
            d = g.document()
            mutate(d)
            with self.assertRaises((ValueError, TypeError)):
                Geometry.from_document(d)
        with self.assertRaises(ValueError):
            grid_geometry(4, 4, 11, radius_mm=10)
        with self.assertRaises(ValueError):
            generate(grid_geometry(8, 8), 'must-not-be-created.h')

    def test_four_step_fit_and_bad_feedback(self):
        for phase in (0, 1, 17, 31, 48, 63):
            powers = [400+225+600*math.cos(2*math.pi*(phase+step)/64)+.01 for step in (0, 16, 32, 48)]
            trim, _ = solve_phase(powers, 400.01, 225.01, .01)
            self.assertEqual(trim, (-phase) % 64)
        for values in (([100]*4, 50, 50, 0), ([1, 2, 3, 4], 1, 1, 1), ([100, 200, 400, 200], 400, 400, 0)):
            with self.assertRaises(ValueError):
                solve_phase(*values)


class GeometrySessionTests(unittest.TestCase):
    def start(self, array, factory=None):
        s = Session(demo_array=array, factory=factory)
        self.addCleanup(lambda: (s.close(), s.join(4)))
        s.start()
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            event = s.events.get(timeout=3)
            if event['kind'] == 'error':
                self.fail(event['text'])
            if event['kind'] == 'state':
                return s
        self.fail('No complete handshake')

    def test_256_element_chunk_handshake(self):
        g = grid_geometry(16, 16, 11, radius_mm=200)
        s = self.start(ArraySpec.from_geometry(g))
        self.assertEqual(s.array.geometry, g)
        self.assertIs(s.last_state.array, s.array)
        self.assertEqual(len(s.last_state.phases), 256)

    def test_corrupt_chunk_never_announces_ready(self):
        class Corrupt(DemoTransport):
            def write(self, raw):
                f = decode(raw)
                if f.verb == 'GEOMETRY':
                    self.buffer.extend(encode('ACK', f.seq, f.verb, geometry_id='0'*24, start=0, count=16,
                                              sound_speed_mm_s=343000, elements='0,0,0,0,0,1000000'))
                else:
                    super().write(raw)
        a = ArraySpec.from_geometry(grid_geometry(radius_mm=80))
        s = Session(factory=lambda: Corrupt(array=a))
        s.start()
        s.join(5)
        self.assertFalse(s.is_alive())
        events = list(s.events.queue)
        self.assertFalse(any(e['kind'] == 'ready' for e in events))
        self.assertTrue(any(e['kind'] == 'error' for e in events))

    def test_calibration_demo_complete_restores_stopped_figure_and_source_guard(self):
        s = self.start(ArraySpec.from_geometry(grid_geometry(1, 2, 11, radius_mm=80)))
        original = s.last_state.config
        runner = CalibrationRunner(s, (0, 0, 100), repeats=2)
        runner.start()
        runner.join(30)
        if runner.is_alive():
            runner.cancel()
            runner.join(8)
            self.fail('Calibration timed out')
        self.assertIsNone(runner.error, runner.error)
        self.assertEqual(runner.result['phase_offsets'], [0, 57])
        self.assertEqual(s.last_state.phase_offsets, (0, 57))
        self.assertEqual(s.last_state.config, original)
        self.assertFalse(s.last_state.output)
        self.assertEqual(s.last_state.channel_mask, 3)
        with self.assertRaises(ValueError):
            validate_document(runner.result, s.array, 64, False)

    def test_cancel_preserves_previous_correction_and_forces_off(self):
        s = self.start(ArraySpec(1, 2, 11000))
        original = s.last_state.config
        runner = CalibrationRunner(s, (0, 0, 100), repeats=2)
        runner.start()
        deadline = time.monotonic()+4
        while time.monotonic() < deadline and not s.last_state.output:
            time.sleep(.01)
        runner.cancel()
        runner.join(10)
        self.assertFalse(runner.is_alive())
        self.assertIsNone(runner.result)
        self.assertIn('取消', runner.error)
        self.assertFalse(s.last_state.output)
        self.assertEqual(s.last_state.phase_offsets, (0, 0))
        self.assertEqual(s.last_state.config, original)


if __name__ == '__main__':
    unittest.main()
