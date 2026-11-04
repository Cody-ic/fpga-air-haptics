import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from desktop_app.egopressure import load_parquet, load_sequence, sample_path, display_points, counts_to_kpa


class EgoPressureTests(unittest.TestCase):
    def test_units_and_handedness(self):
        np.testing.assert_allclose(counts_to_kpa([1736]), [640])
        points = np.array([[.12, -.06875, -.05]])
        np.testing.assert_allclose(display_points(points), [[.12, .06875, .05]])
        self.assertGreater(np.linalg.det(np.diag([1,-1,-1])), 0)

    def test_frame_join_and_missing_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            pq.write_table(pa.Table.from_pylist([
                dict(frame=3, has_annotation=True, hand_side='right', vertices=[.01]*2334, joint_position=[.01]*63),
                dict(frame=1, has_annotation=False, hand_side='right', vertices=None, joint_position=None),
            ]), path/'annotation.parquet')
            pq.write_table(pa.Table.from_pylist([
                dict(frame=1, force=[0.]*19425), dict(frame=4, force=[1.]*19425)
            ]), path/'pressure.parquet')
            seq = load_parquet(path)
            np.testing.assert_array_equal(seq.frames, [1,2,3,4])
            np.testing.assert_array_equal(seq.hand_valid, [False,False,True,False])
            np.testing.assert_array_equal(seq.pressure_valid, [True,False,False,True])
            self.assertEqual(seq.total_force_n[0],0)
            self.assertTrue(np.isnan(seq.total_force_n[1]))
            self.assertEqual(seq.index_at(2.01/30),2)
            seq.save(path/'roundtrip.npz')
            restored = load_sequence(path/'roundtrip.npz')
            np.testing.assert_array_equal(restored.vertices,seq.vertices)
            np.testing.assert_array_equal(restored.pressure_valid,seq.pressure_valid)

    def test_real_sample(self):
        seq = load_sequence(sample_path())
        self.assertEqual(len(seq.frames),366)
        self.assertEqual(seq.hand_valid.sum(),318)
        self.assertTrue(seq.pressure_valid.all())
        self.assertAlmostEqual(float(seq.total_force_n.max()),18.222145,places=4)
        self.assertFalse(seq.hand_valid[0])
        self.assertEqual(seq.index_at(999),365)
        self.assertEqual(seq.index_at(-10),0)


if __name__ == '__main__':
    unittest.main()
