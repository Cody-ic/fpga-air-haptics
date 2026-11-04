import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from desktop_app.egopressure import ReplaySequence, load_sequence, sample_path
from desktop_app.hand_pose import palm_frame_from_joints, geometric_bends_deg
from desktop_app.model import ArraySpec
from desktop_app.pressure import extract_patches
from desktop_app.pressure_sequence import (fit_sequence_mapping, hand_features,
                                          sequence_summary, compile_sequence_plan, save_analysis)


def example_joints():
    joints = np.zeros((21, 3))
    for first, x in zip((1, 5, 9, 13, 17), (.045, .03, .01, -.01, -.03)):
        joints[first:first+4] = [[x, .04+.02*k, .001*k] for k in range(4)]
    return joints


class HandSequenceTests(unittest.TestCase):
    def test_joint_only_contactpose_analysis_has_no_pressure_mapping(self):
        sequence = ReplaySequence('grasp', np.array([1]), np.zeros((1, 778, 3)),
                                  example_joints()[None], np.zeros((1, 105, 185)),
                                  np.ones(1, bool), np.zeros(1, bool),
                                  source_dataset='ContactPose', vertices_available=False)
        summary = sequence_summary(sequence)
        self.assertEqual(summary['dataset'], 'ContactPose')
        self.assertIsNone(summary['mapping'])
        self.assertEqual(summary['pressure_kind'], 'absent')
        self.assertEqual(summary['field_kind'], 'not_generated')
        self.assertTrue(summary['rows'][0]['hand']['valid'])
        self.assertNotIn('total_contact_force_n', summary['rows'][0])
        self.assertNotIn('mapped_pressure_centre_mm', summary['rows'][0])
        self.assertFalse(summary['rows'][0]['pressure_valid'])
        self.assertFalse(summary['live_tracking'])

    def test_zero_contact_sequence_analysis_does_not_require_a_mapping(self):
        sequence = ReplaySequence('zero', np.array([1]), np.zeros((1, 778, 3)),
                                  np.zeros((1, 21, 3)), np.zeros((1, 105, 185)),
                                  np.zeros(1, bool), np.ones(1, bool))
        summary = sequence_summary(sequence)
        self.assertIsNone(summary['mapping'])
        self.assertEqual(summary['rows'][0]['total_contact_force_n'], 0.)
        self.assertEqual(summary['rows'][0]['peak_contact_kpa'], 0.)

    def test_geometric_frame_is_rigid_transform_invariant(self):
        joints = example_joints()
        frame = palm_frame_from_joints(joints)
        theta = .73
        rotation = np.array([[np.cos(theta), -np.sin(theta), 0],
                             [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])
        moved = joints @ rotation.T + [.31, -.07, .12]
        transformed = palm_frame_from_joints(moved)
        np.testing.assert_allclose(frame.local_mm(joints), transformed.local_mm(moved), atol=1e-11)
        np.testing.assert_allclose(frame.axes.T @ frame.axes, np.eye(3), atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(frame.axes), 1)
        self.assertAlmostEqual(frame.width_mm, 60)
        self.assertTrue(all(abs(v) < 1e-5 for v in geometric_bends_deg(joints).values()))

    def test_left_hand_and_degenerate_annotation(self):
        joints = example_joints()*[-1, 1, 1]
        frame = palm_frame_from_joints(joints, 'left')
        self.assertEqual(frame.hand_side, 'left')
        self.assertAlmostEqual(np.linalg.det(frame.axes), 1)
        for invalid in (np.zeros((21, 3)), np.full((21, 3), np.nan), np.zeros((20, 3))):
            with self.assertRaises(ValueError):
                palm_frame_from_joints(invalid)

    def test_frozen_map_preserves_contact_translation(self):
        force = np.zeros((2, 105, 185), dtype=np.float32)
        force[0, 50:54, 70:74] = 100
        force[1, 50:54, 90:94] = 100
        sequence = ReplaySequence('motion', np.array([1, 2]), np.zeros((2, 778, 3)),
                                  np.zeros((2, 21, 3)), force, np.zeros(2, bool), np.ones(2, bool))
        mapping = fit_sequence_mapping(sequence)
        rows = sequence_summary(sequence, mapping)['rows']
        self.assertGreater(rows[1]['mapped_pressure_centre_mm'][0]-rows[0]['mapped_pressure_centre_mm'][0], 20)
        centres = [extract_patches(force[i], mapping=mapping)[0][0]['centre_mm'] for i in range(2)]
        self.assertGreater(centres[1][0]-centres[0][0], 20)
        self.assertEqual(hand_features(sequence, 0)['reason'], 'missing_annotation')

    def test_sparse_outlier_does_not_reset_sequence_scale(self):
        seq = load_sequence(sample_path())
        mapping = fit_sequence_mapping(seq, indices=[320, 324, 328])
        old = float(seq.force[324, 0, 0])
        try:
            seq.force[324, 0, 0] = 3
            changed = fit_sequence_mapping(seq, indices=[320, 324, 328])
        finally:
            seq.force[324, 0, 0] = old
        self.assertEqual(mapping, changed)

    def test_real_sequence_analysis_and_static_phase_export(self):
        seq = load_sequence(sample_path())
        analysis = sequence_summary(seq)
        self.assertEqual(len(analysis['rows']), 366)
        self.assertEqual(sum(r['hand']['valid'] for r in analysis['rows']), 318)
        self.assertFalse(analysis['live_tracking'])
        plan = compile_sequence_plan(seq, [324], ArraySpec(8, 8, 10300), starts=1, maxiter=15)
        frame = plan['frames'][0]
        self.assertEqual(frame['status'], 'compiled')
        self.assertEqual(len(frame['config']['phase_table']), 128)
        self.assertFalse(plan['automatic_device_playback'])
        self.assertLessEqual(frame['metrics']['refined_loss'], frame['metrics']['initial_quantized_loss']+1e-12)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'analysis.json'
            save_analysis(analysis, path)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['source_name'], seq.name)
        for indices in ([1, 1], [-1], [10000], [1.5], [324, 320]):
            with self.assertRaises(ValueError):
                compile_sequence_plan(seq, indices, ArraySpec(8, 8, 10300))


if __name__ == '__main__':
    unittest.main()
