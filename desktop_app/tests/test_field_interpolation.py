"""Interpolation estimates stay within measured geometry and value bounds."""
import unittest
import numpy as np
from desktop_app.acoustic_panel import interpolate_scan


class FieldInterpolationTests(unittest.TestCase):
    def test_insufficient_or_collinear_points(self):
        for points in ([], [(0,0,1)], [(0,0,1),(1,0,2),(2,0,3)]):
            self.assertIsNone(interpolate_scan(points))

    def test_linear_field_inside_triangle_and_mask_outside(self):
        xx, yy, values = interpolate_scan([(0,0,2),(10,0,12),(0,10,22)], resolution=11)
        inside = ~np.ma.getmaskarray(values)
        np.testing.assert_allclose(values[inside], (2+xx+2*yy)[inside])
        self.assertTrue(np.ma.getmaskarray(values)[-1,-1])
        self.assertGreaterEqual(values.min(),2)
        self.assertLessEqual(values.max(),22)

    def test_nonfinite_is_not_rendered(self):
        self.assertIsNone(interpolate_scan([(0,0,1),(1,0,float('nan')),(0,1,3)]))


if __name__=='__main__':
    unittest.main()
