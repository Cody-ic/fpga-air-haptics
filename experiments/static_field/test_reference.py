"""Independent numerical invariants for the offline reference."""
import unittest

import numpy as np
from scipy.optimize import check_grad

from desktop_app.model import ArraySpec, Config, array_coordinates, focus_phases
from experiments.static_field.reference import (
    loss_gradient, propagator, quantize_phase, solve,
)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.emitters = array_coordinates(ArraySpec(8, 8, 10300))

    def test_single_focus_matches_existing_phase_codes_and_coherent_bound(self):
        for point in [(0, 0, 50), (13, -7, 50), (-20, 10, 70)]:
            h = propagator([point], self.emitters)[0]
            phase = -np.angle(h)
            np.testing.assert_array_equal(quantize_phase(phase), focus_phases(Config(), point, ArraySpec(8, 8, 10300)))
            self.assertAlmostEqual(abs(h @ np.exp(1j*phase)), np.abs(h).sum(), places=12)
            quantized = quantize_phase(phase)*2*np.pi/64
            self.assertGreaterEqual(abs(h @ np.exp(1j*quantized))/np.abs(h).sum(), np.cos(np.pi/64)-1e-12)

    def test_gradient_against_finite_difference(self):
        rng = np.random.default_rng(13)
        a = (rng.normal(size=(5, 8))+1j*rng.normal(size=(5, 8)))/10
        b = (rng.normal(size=(17, 8))+1j*rng.normal(size=(17, 8)))/10
        phi = rng.uniform(-np.pi, np.pi, 8)
        error = check_grad(lambda p: loss_gradient(p, a, b, .4)[0],
                           lambda p: loss_gradient(p, a, b, .4)[1], phi)
        self.assertLess(error, 1e-5)

    def test_piston_on_axis_and_isotropic_limit(self):
        source = np.array([[0., 0., 0.]])
        points = np.array([[0., 0., 50.], [20., 0., 50.]])
        iso = propagator(points, source)
        piston = propagator(points, source, piston_radius_mm=5)
        self.assertAlmostEqual(abs(iso[0,0]-piston[0,0]), 0., places=14)
        self.assertLess(abs(piston[1,0]), abs(iso[1,0]))
        np.testing.assert_allclose(propagator(points, source, piston_radius_mm=1e-8), iso)

    def test_global_phase_invariance_and_coherent_cancellation(self):
        a = propagator([[0.,0.,50.]], [[-5.,0.,0.], [5.,0.,0.]])
        self.assertLess(abs((a @ np.array([1, -1]))[0]), 1e-14)
        w = np.exp(1j*np.array([.4, 1.7]))
        np.testing.assert_allclose(abs(a@w), abs(a@(w*np.exp(1j*2.1))))

    def test_two_points_improve_objective_and_stay_phase_only(self):
        a = propagator([[-10.,0.,50.], [10.,0.,50.]], self.emitters)
        a /= np.abs(propagator([[0,0,50]], self.emitters)).sum()
        phase, trials = solve(a, np.empty((0,64), complex), .6, starts=2, maxiter=120)
        self.assertTrue(np.isfinite(phase).all())
        np.testing.assert_allclose(abs(np.exp(1j*phase)), 1.)
        self.assertLess(min(t['loss'] for t in trials), min(t['initial_loss'] for t in trials))
        self.assertGreater(np.min(abs(a @ np.exp(1j*phase))), .55)

    def test_invalid_geometry_rejected(self):
        with self.assertRaises(ValueError):
            propagator([[0,0,0]], self.emitters)


if __name__ == '__main__':
    unittest.main()
