import queue
import time
import unittest
from dataclasses import replace

import numpy as np

from desktop_app.controller import Session
from desktop_app.demo import DemoDevice
from desktop_app.egopressure import load_sequence, sample_path, counts_to_kpa
from desktop_app.model import ArraySpec, Config
from desktop_app.pressure import compile_pressure, extract_patches, propagator, plane_points
from desktop_app.model import array_coordinates
from threadpoolctl import threadpool_limits
from desktop_app.protocol import Snapshot, decode, encode
from desktop_app.transport import DemoTransport


class PressureTests(unittest.TestCase):
    array = ArraySpec(8,8,10300)

    @classmethod
    def setUpClass(cls):
        cls.sequence = load_sequence(sample_path())
        cls.result = compile_pressure(counts_to_kpa(cls.sequence.force[324]), cls.array)
        cls.focused = compile_pressure(cls.result.raw_kpa, cls.array, centre_spacing_mm=35, mode='focus')

    def test_focused_centres_gain_without_a_central_or_distant_hotspot(self):
        old = compile_pressure(self.result.raw_kpa, self.array, centre_spacing_mm=35)
        new = self.focused
        self.assertEqual(new.metrics['mode'], 'focus')
        np.testing.assert_array_equal(new.raw_kpa, old.raw_kpa)
        self.assertEqual(new.mapping, old.mapping)
        self.assertEqual(replace(new.config, phase_table=old.config.phase_table), old.config)
        emitters = array_coordinates(self.array)
        centres = np.array(new.mapping['centres_mm'])
        # Independent evaluation: dense, wider plane than the preview, and
        # modest height/directivity perturbations, not just training loss.
        with threadpool_limits(limits=1):
            for z, radius in ((50,0),(45,0),(55,0),(50,5)):
                sample = np.column_stack((centres, np.full(2,z)))
                h = propagator(sample,emitters,piston_radius_mm=radius)
                phasors = [np.exp(2j*np.pi*np.array(r.config.static_phases(self.array))/64) for r in (old,new)]
                before, after = [abs(h@w) for w in phasors]
                # Exact design plane must gain substantially; perturbations
                # must retain a gain, not promise the same improvement off-plane.
                gain = 1.4 if (z,radius)==(50,0) else 1.1
                self.assertTrue(np.all(after > gain*before), (z,radius,before,after))
            _, plane = plane_points(80,1,50)
            outside = np.min(np.linalg.norm(plane[:,None,:2]-centres[None,:,:],axis=-1),axis=1)>=8
            h = propagator(plane[outside],emitters)
            before, after = [abs(h@w).max() for w in phasors]
            self.assertLess(after,before)
        self.assertLess(new.metrics['bridge_peak'], .1*min(new.metrics['centre_amplitudes']))
        self.assertGreater(min(new.metrics['centre_amplitudes']), .6)

    def test_focus_single_unequal_contacts_and_invalid_mode(self):
        raw = np.zeros((105,185)); raw[45:50,60:65] = 8
        one = compile_pressure(raw, self.array, mode='focus')
        self.assertEqual(one.metrics['patches'], 1)
        self.assertIsNone(one.metrics['bridge_peak'])
        self.assertTrue(np.isfinite(one.field).all())
        raw[60:65,110:115] = 2
        two = compile_pressure(raw, self.array, mode='focus', centre_spacing_mm=35)
        self.assertGreater(two.metrics['centre_amplitudes'][0], 1.5*two.metrics['centre_amplitudes'][1])
        self.assertGreater(two.metrics['centre_amplitudes'][1], .2)
        with self.assertRaises(ValueError):
            compile_pressure(raw, self.array, mode='unknown')

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

    def test_spacing_moves_centres_without_stretching_contacts_or_raw_frame(self):
        raw = self.result.raw_kpa
        original, before, edges = extract_patches(raw)
        adjusted, after, adjusted_edges = extract_patches(raw, centre_spacing_mm=35)
        a = np.array([p['centre_mm'] for p in original])
        b = np.array([p['centre_mm'] for p in adjusted])
        self.assertAlmostEqual(np.linalg.norm(b[1]-b[0]), 35)
        np.testing.assert_allclose(a.mean(0), b.mean(0))
        np.testing.assert_allclose((b[1]-b[0])/35, (a[1]-a[0])/before['centre_spacing_mm'])
        for p, q in zip(original, adjusted):
            self.assertEqual({k:v for k,v in p.items() if k!='centre_mm'},
                             {k:v for k,v in q.items() if k!='centre_mm'})
        for p, q in zip(edges, adjusted_edges):
            np.testing.assert_array_equal(p,q)
        self.assertEqual(after['original_centres_mm'], before['centres_mm'])
        new = compile_pressure(raw, self.array, centre_spacing_mm=35)
        np.testing.assert_array_equal(new.raw_kpa, raw)
        self.assertEqual(new.config.z_um, 50000)
        self.assertNotEqual(new.config.phase_table, self.result.config.phase_table)
        self.assertEqual(len(new.config.static_phases(self.array)), 64)
        # A useful two-region result must retain two separate peaks, with a valley.
        from scipy.ndimage import map_coordinates
        line = b[0]+(b[1]-b[0])*np.linspace(0,1,201)[:,None]
        curve = map_coordinates(new.field, [line[:,1]-new.axis_mm[0],line[:,0]-new.axis_mm[0]], order=1)
        self.assertGreater(min(curve[:80].max(),curve[121:].max()), .3)
        self.assertLess(curve[80:121].min(), .1*min(curve[:80].max(),curve[121:].max()))
        single = np.zeros((105,185)); single[50:54,80:84] = 10
        self.assertEqual(extract_patches(single)[0], extract_patches(single,centre_spacing_mm=35)[0])
        for spacing in (-1, 4, 81, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                extract_patches(raw, centre_spacing_mm=spacing)

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
        result = self.focused
        device = DemoDevice(array=self.array)
        device.handle(encode('CMD',1,'HELLO'))
        device.saved_offsets = tuple((i*7+3)%64 for i in range(64))
        fields = dict(result.config.wire(), **self.array.wire())
        replies = device.handle(encode('CMD',2,'CONFIG',**fields))
        state = Snapshot.parse(decode(replies[-1]).fields)
        nominal = result.config.static_phases(self.array)
        self.assertEqual(state.phases, tuple((a+b)%64 for a,b in zip(nominal,device.saved_offsets)))
        self.assertEqual(state.state, 'IDLE')
        self.assertFalse(state.output)
        for bad in ('00'*63, '40'*64):
            response = device.handle(encode('CMD',3,'CONFIG',**dict(fields,phase_table=bad)))
            self.assertEqual(decode(response[0]).kind,'ERR')
            self.assertEqual(device.revision,1)
            self.assertEqual(device.config,result.config)
        for seq,verb in enumerate(('START','PAUSE','START','STOP'),4):
            state = Snapshot.parse(decode(device.handle(encode('CMD',seq,verb))[-1]).fields)
            self.assertEqual(state.output,verb=='START')
            self.assertEqual(state.config,result.config)
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
