import queue
import time
import unittest
from dataclasses import replace

import numpy as np

from desktop_app.controller import Session
from desktop_app.demo import DemoDevice
from desktop_app.egopressure import load_sequence, sample_path, counts_to_kpa
from desktop_app.model import ArraySpec, Config, array_coordinates
from desktop_app.pressure import (PressureMapping, compile_pressure, extract_patches,
                                  image_loss, patch_density, plane_points, propagator,
                                  refine_quantized_phases)
from desktop_app.protocol import Snapshot, decode, encode
from desktop_app.transport import DemoTransport


class PressureTests(unittest.TestCase):
    array = ArraySpec(8,8,10300)

    @classmethod
    def setUpClass(cls):
        cls.sequence = load_sequence(sample_path())
        cls.result = compile_pressure(counts_to_kpa(cls.sequence.force[324]), cls.array)

    def test_selected_frame_and_finite_quantized_field(self):
        r = self.result
        np.testing.assert_array_equal(r.raw_kpa, counts_to_kpa(self.sequence.force[324]))
        self.assertEqual(len(r.config.static_phases(self.array)), 64)
        self.assertTrue(np.isfinite(r.field).all())
        self.assertGreater(np.std(r.field), 0)
        self.assertEqual(r.metrics['patches'], 2)
        self.assertFalse(r.metrics['calibration_applied'])
        self.assertEqual(Config.from_wire(r.config.wire()), r.config)

    def test_empty_invalid_and_single_contact(self):
        for value in (np.zeros((105,185)), np.full((105,185), np.nan), np.ones((3,4))):
            with self.assertRaises(ValueError):
                compile_pressure(value, self.array)
        raw = np.zeros((105,185))
        raw[50:54,80:84] = 10
        patches,_,_ = extract_patches(raw)
        self.assertEqual(len(patches), 1)

    def test_analytic_gradient_matches_central_difference(self):
        rng = np.random.default_rng(1349)
        h = (rng.normal(size=(13, 7))+1j*rng.normal(size=(13, 7)))*.07
        background = (rng.normal(size=(19, 7))+1j*rng.normal(size=(19, 7)))*.04
        goal = rng.uniform(.05, .5, len(h))
        phase = rng.uniform(-np.pi, np.pi, 7)
        _, gradient = image_loss(phase, h, goal, background)
        epsilon = 1e-6
        numeric = np.zeros(7)
        for channel in range(7):
            direction = np.eye(7)[channel]*epsilon
            numeric[channel] = (image_loss(phase+direction, h, goal, background)[0]
                                -image_loss(phase-direction, h, goal, background)[0])/(2*epsilon)
        np.testing.assert_allclose(gradient, numeric, rtol=2e-6, atol=2e-8)

    def test_discrete_refinement_is_strict_monotone_and_deterministic(self):
        rng = np.random.default_rng(4501)
        h = (rng.normal(size=(17, 8))+1j*rng.normal(size=(17, 8)))*.06
        background = (rng.normal(size=(23, 8))+1j*rng.normal(size=(23, 8)))*.04
        goal = rng.uniform(.1, .5, len(h))
        codes = np.arange(8)*7
        original = codes.copy()
        result, info = refine_quantized_phases(codes, h, goal, background)
        np.testing.assert_array_equal(codes, original)
        self.assertGreater(info['improvements'], 0)
        self.assertTrue(np.all(np.diff(info['loss_history']) < 0))
        self.assertLess(info['refined_loss'], info['initial_quantized_loss'])
        self.assertLessEqual(info['sweeps'], 2)
        self.assertTrue(np.all((result >= 0) & (result < 64)))
        self.assertAlmostEqual(info['refined_loss'], image_loss(2*np.pi*result/64, h, goal, background)[0], places=13)
        again, again_info = refine_quantized_phases(codes, h, goal, background)
        np.testing.assert_array_equal(result, again)
        self.assertEqual(info, again_info)
        unchanged, zero = refine_quantized_phases(codes, h, goal, background, max_sweeps=0)
        np.testing.assert_array_equal(unchanged, codes)
        self.assertEqual(zero['sweeps'], 0)
        self.assertEqual(zero['improvements'], 0)
        self.assertAlmostEqual(zero['refined_loss'], zero['initial_quantized_loss'], places=13)

    def test_fixed_mapping_preserves_contact_displacement(self):
        raw = np.zeros((105, 185))
        raw[45:50, 80:85] = 10
        shifted = np.zeros_like(raw)
        shifted[45:50, 88:93] = 10
        mapping = PressureMapping((0., 0.), .4, 40.).validate()
        first, info, edges = extract_patches(raw, mapping=mapping)
        second, next_info, next_edges = extract_patches(shifted, mapping=mapping)
        delta = np.array(second[0]['centre_mm'])-first[0]['centre_mm']
        np.testing.assert_allclose(delta, [8*240/185*.4, 0], atol=1e-12)
        np.testing.assert_allclose(first[0]['covariance_mm2'], second[0]['covariance_mm2'], atol=1e-12)
        np.testing.assert_array_equal(edges[0], next_edges[0])
        self.assertEqual(info['mapping_mode'], 'fixed')
        self.assertEqual(info['origin_display_mm'], next_info['origin_display_mm'])
        self.assertEqual(info['scale'], next_info['scale'])
        # Legacy one-frame mode still centers/rescales each frame independently.
        legacy, legacy_info, _ = extract_patches(raw)
        legacy_shifted, _, _ = extract_patches(shifted)
        np.testing.assert_allclose(legacy[0]['centre_mm'], legacy_shifted[0]['centre_mm'], atol=1e-12)
        self.assertEqual(legacy_info['mapping_mode'], 'per_frame')

    def test_invalid_search_controls_and_mapping_are_rejected(self):
        for name, values in (('starts', (0, -1, 1.5, True, 33)),
                             ('maxiter', (0, -1, 1.5, True, 5001)),
                             ('refinement_sweeps', (-1, 1.5, True, 9))):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    compile_pressure(self.result.raw_kpa, self.array, **{name: value})
        for mapping in (PressureMapping([0, 0], 1, 40), PressureMapping((0,), 1, 40),
                        PressureMapping(((0, 0), (1, 1)), 1, 40),
                        PressureMapping((float('nan'), 0), 1, 40),
                        PressureMapping((0, 0), 0, 40), PressureMapping((0, 0), -1, 40),
                        PressureMapping((0, 0), float('inf'), 40), PressureMapping((0, 0), 1, 0)):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                mapping.validate()
        with self.assertRaises(ValueError):
            compile_pressure(self.result.raw_kpa, self.array, mapping={})
        h, goal, bg = np.ones((3, 2), complex), np.ones(3), np.empty((0, 2), complex)
        for codes in ([0, 64], [-1, 1], [0., 1.], [True, False]):
            with self.subTest(codes=codes), self.assertRaises(ValueError):
                refine_quantized_phases(codes, h, goal, bg)
        for options in ({'steps': True}, {'steps': 1}, {'max_sweeps': 9},
                        {'scale': True}, {'scale': float('nan')}, {'scale': 1j}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                refine_quantized_phases([0, 1], h, goal, bg, **options)

    def test_real_sample_loss_scores_the_actual_wire_codes(self):
        r = self.result
        baseline = compile_pressure(r.raw_kpa, self.array, refinement_sweeps=0)
        self.assertAlmostEqual(r.metrics['initial_quantized_loss'], baseline.metrics['loss'], places=11)
        self.assertLessEqual(r.metrics['loss'], baseline.metrics['loss'])
        patches, _, _ = extract_patches(r.raw_kpa)
        _, points = plane_points(40, 1, 50)
        _, train = plane_points(40, 2, 50)
        peak = patch_density(points[:, :2], patches).max()
        goals = .5*np.sqrt(patch_density(train[:, :2], patches)/peak)
        emitters = array_coordinates(self.array)
        reference = abs(propagator([[0, 0, 50]], emitters)).sum()
        h = propagator(train, emitters)/reference
        codes = np.asarray(r.config.static_phases(self.array))
        actual = image_loss(2*np.pi*codes/64, h[goals >= .5*.15], goals[goals >= .5*.15],
                            h[goals < .5*.08])[0]
        self.assertAlmostEqual(r.metrics['loss'], actual, places=12)
        self.assertEqual(r.metrics['loss'], r.metrics['refined_loss'])
        for key in ('continuous_loss', 'initial_quantized_loss', 'refined_loss',
                    'whole_plane_rmse', 'background_peak'):
            self.assertTrue(np.isfinite(r.metrics[key]))
        # The fine-grid field remains independently evaluated, not copied from
        # training samples or from the unquantized optimizer's objective.
        fine = abs(propagator(points, emitters) @ np.exp(2j*np.pi*codes/64))/reference
        np.testing.assert_allclose(r.field.ravel(), fine, rtol=1e-12, atol=1e-12)

    def test_compile_accepts_a_frozen_mapping_without_recentering(self):
        r = self.result
        mapping = PressureMapping(tuple(r.mapping['origin_display_mm']), r.mapping['scale'], 40.)
        fixed = compile_pressure(r.raw_kpa, self.array, mapping=mapping, starts=1, maxiter=20)
        self.assertEqual(fixed.mapping['mapping_mode'], 'fixed')
        self.assertEqual(fixed.mapping['origin_display_mm'], list(mapping.origin_display_mm))
        self.assertEqual(fixed.mapping['scale'], mapping.scale)

    def test_stronger_frame_keeps_target_shape_and_background_contrast(self):
        old = compile_pressure(counts_to_kpa(self.sequence.force[324]), self.array, target_peak=.35)
        new = self.result
        mask = new.target >= new.target.max()*.15
        self.assertGreater(np.mean(new.field[mask]**2), 1.2*np.mean(old.field[mask]**2))
        self.assertGreater(new.field.max(), 1.25*old.field.max())
        similarity = np.dot(new.field[mask], new.target[mask]) / (
            np.linalg.norm(new.field[mask])*np.linalg.norm(new.target[mask]))
        self.assertGreater(similarity, .96)
        self.assertLess(new.metrics['background_peak'], .55*new.field.max())
        self.assertEqual(new.config.level, 100)
        self.assertEqual(new.config.mod_hz, 0)
        for peak in (0, -1, 1.1, float('nan')):
            with self.assertRaises(ValueError):
                compile_pressure(new.raw_kpa, self.array, target_peak=peak)

    def test_invalid_wire_and_legacy_compatibility(self):
        self.assertNotIn('phase_table', Config().wire())
        self.assertEqual(Config.from_wire(Config().wire()), Config())
        for change in ({'phase_table':'40'*64}, {'phase_table':'0g'*64},
                       {'phase_table':'0'}, {'phase_steps':32}, {'shape':'CIRCLE'},
                       {'scan_paths':'0:0,1:1'}, {'carrier_hz':39000}):
            with self.assertRaises(ValueError):
                replace(self.result.config, **change).validate()
        with self.assertRaises(ValueError):
            self.result.config.static_phases(ArraySpec())

    def test_demo_compensation_once_and_atomic_rejection(self):
        device = DemoDevice(array=self.array)
        device.handle(encode('CMD',1,'HELLO'))
        device.saved_offsets = tuple((i*7+3)%64 for i in range(64))
        fields = dict(self.result.config.wire(), **self.array.wire())
        replies = device.handle(encode('CMD',2,'CONFIG',**fields))
        state = Snapshot.parse(decode(replies[-1]).fields)
        nominal = self.result.config.static_phases(self.array)
        self.assertEqual(state.phases, tuple((a+b)%64 for a,b in zip(nominal,device.saved_offsets)))
        self.assertEqual(state.state, 'IDLE')
        self.assertFalse(state.output)
        for bad in ('00'*63, '40'*64):
            response = device.handle(encode('CMD',3,'CONFIG',**dict(fields,phase_table=bad)))
            self.assertEqual(decode(response[0]).kind,'ERR')
            self.assertEqual(device.revision,1)
            self.assertEqual(device.config,self.result.config)
        for seq,verb in enumerate(('START','PAUSE','START','STOP'),4):
            state = Snapshot.parse(decode(device.handle(encode('CMD',seq,verb))[-1]).fields)
            self.assertEqual(state.output,verb=='START')
            self.assertEqual(state.config,self.result.config)
        fields = decode(device.snapshot()).fields
        for change in ({'static_mode':'0'}, {'phase_offsets':','.join(['0']*64)}, {'phase_table':'NONE'}):
            with self.assertRaises(ValueError):
                Snapshot.parse(dict(fields,**change))
        device.handle(encode('CMD',9,'CONFIG',**Config().wire(),**self.array.wire()))
        self.assertEqual(device.config.phase_table,'NONE')

    def test_session_rejects_unsupported_or_changed_array_before_write(self):
        class RecordingDemo(DemoTransport):
            def __init__(self):
                super().__init__(array=PressureTests.array)
                self.writes=[]
            def write(self, raw):
                self.writes.append(decode(raw))
                super().write(raw)
        transport=RecordingDemo()
        session=Session(factory=lambda:transport)
        session.start()
        def event(kind):
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                try:
                    value=session.events.get(timeout=.1)
                except queue.Empty:
                    continue
                if value['kind']=='error':
                    self.fail(value['text'])
                if value['kind']==kind:
                    return value
            self.fail('Missing '+kind)
        try:
            event('state')
            session.capabilities.discard('STATIC_PHASE')
            session.command('CONFIG',**self.result.config.wire(),**self.array.wire())
            self.assertIn('不支持',event('rejected')['text'])
            session.capabilities.add('STATIC_PHASE')
            session.command('CONFIG',**self.result.config.wire(),**ArraySpec(8,8,11000).wire())
            self.assertIn('阵列已改变',event('rejected')['text'])
            self.assertFalse(any(f.verb=='CONFIG' for f in transport.writes))
            session.command('CONFIG',**self.result.config.wire(),**self.array.wire())
            deadline=time.monotonic()+3
            while session.last_state.config.phase_table=='NONE' and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(session.last_state.config,self.result.config)
            self.assertFalse(session.last_state.output)
        finally:
            session.close();session.join(3)
        self.assertFalse(session.is_alive())


if __name__=='__main__':
    unittest.main()
