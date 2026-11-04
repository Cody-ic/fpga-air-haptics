import unittest

import numpy as np
from scipy.optimize import check_grad

from experiments.static_field.dataset_frame import extract_patches, image_loss, patch_density


class DatasetFrameTests(unittest.TestCase):
    def test_nonuniform_target_gradient(self):
        rng=np.random.default_rng(12)
        a=(rng.normal(size=(11,8))+1j*rng.normal(size=(11,8)))/20
        b=(rng.normal(size=(19,8))+1j*rng.normal(size=(19,8)))/20
        goal=np.linspace(.05,.35,11)
        phase=rng.uniform(-np.pi,np.pi,8)
        error=check_grad(lambda p:image_loss(p,a,goal,b)[0],lambda p:image_loss(p,a,goal,b)[1],phase)
        self.assertLess(error,1e-5)

    def test_orientation_relative_mass_and_isotropic_scale(self):
        data=np.zeros((105,185))
        data[10:15,20:25]=2
        data[70:75,140:145]=4
        patches,mapping,_=extract_patches(data)
        self.assertAlmostEqual(patches[0]['weight']/patches[1]['weight'],2.)
        self.assertAlmostEqual(mapping['retained_pressure_sum_fraction'],1.)
        # Strong lower-right sensor patch -> right, negative display Y.
        self.assertGreater(patches[0]['centre_mm'][0],patches[1]['centre_mm'][0])
        self.assertLess(patches[0]['centre_mm'][1],patches[1]['centre_mm'][1])
        dx=120*240/185*mapping['scale']
        dy=-60*137.5/105*mapping['scale']
        np.testing.assert_allclose(np.subtract(patches[0]['centre_mm'],patches[1]['centre_mm']),[dx,dy])
        self.assertFalse(mapping['raw_active_touches_sensor_edge'])

    def test_gaussian_mass_integral(self):
        axis=np.arange(-25,25,.1)
        x,y=np.meshgrid(axis,axis)
        patch=dict(centre_mm=[0,0],covariance_mm2=[[4,1],[1,9]],weight=17)
        values=patch_density(np.column_stack([x.ravel(),y.ravel()]),[patch])
        self.assertAlmostEqual(values.sum()*.1**2,17,places=6)

    def test_no_contact_is_not_a_target(self):
        with self.assertRaises(ValueError):
            extract_patches(np.zeros((105,185)))


if __name__=='__main__':
    unittest.main()
