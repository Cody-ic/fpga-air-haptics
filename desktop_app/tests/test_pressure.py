import queue
import time
import unittest
from dataclasses import replace

import numpy as np

from desktop_app.controller import Session
from desktop_app.demo import DemoDevice
from desktop_app.egopressure import load_sequence, sample_path, counts_to_kpa
from desktop_app.model import ArraySpec, Config
from desktop_app.pressure import compile_pressure, extract_patches
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
