"""ContactPose joint-only import, including an official numeric excerpt.

WINE_JOINTS is the right-hand joints array from participant 39, use/wine_glass:
official README sample https://github.com/facebookresearch/ContactPose#licensing
https://drive.google.com/file/d/1paUAxXgHp6wDFBFw9MI1mxGElEl2KPew/view
ContactPose sample data/grasps.zip!grasps/full39_use/wine_glass.zip!
wine_glass/annotations.json, SHA256 of the original annotation:
66b1495c9f4639189661c46c0fba29f5409e54dbac6266841657975cd5284dfb.
Copyright (c) 2020 Facebook, Inc. and its affiliates; MIT data license:
https://github.com/facebookresearch/ContactPose/blob/main/LICENSE.txt
Other transforms below are deliberately synthetic test cases, not observations.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from desktop_app.hand_datasets import load_contactpose
from desktop_app.hand_pose import palm_frame_from_joints
from desktop_app.egopressure import load_sequence


WINE_JOINTS = [
    [-.07834962025789718, -.06304591179495682, .07098317096104083],
    [-.06869782616813491, -.04247485086309729, .09664926802983653],
    [-.0527229830737824, -.010217617730857638, .11466239220922583],
    [-.03969288627120886, .020638795179328123, .11847233501066753],
    [-.02112614104853623, .04115675758819569, .10677363737434689],
    [.0014997126869967304, -.051718599076239186, .11449039700647375],
    [.038102083264337866, -.029261166897093745, .12733787733530702],
    [.04449615677406986, -.004501245235805528, .12678826239389943],
    [.03575340257459151, .017060739967073548, .12475382785625673],
    [.007167920841423349, -.049021752379234756, .09016956578405422],
    [.039754482281341846, -.015039204238255712, .09304537039211076],
    [.03390939902665424, .016455401395290954, .09635422843818567],
    [.01630121654718017, .03459927110011858, .10193546747729273],
    [.0024260242623765443, -.041460720221885544, .07112150435380105],
    [.0329554024347637, -.008931277721407263, .07172290235154449],
    [.024355157925556637, .017157319086911073, .0793392165075758],
    [.006445626888304144, .03147423239450728, .08829137645328458],
    [-.007581682044468546, -.030862470088768894, .05583491370333113],
    [.01574232941526242, -.006175420141126938, .05760371789875804],
    [.012923813055968309, .013373230710375812, .06418876268466014],
    [-.0007373595583942642, .0253959326595345, .07300077290168386],
]


def wine_annotation():
    # Minimal excerpt of the official schema; cameras and object poses are unused.
    return dict(participant=39, instruction="use", object="wine_glass",
                hands=[dict(valid=False, moving=False, joints=[]),
                       dict(valid=True, moving=False, joints=deepcopy(WINE_JOINTS))],
                frames=[dict(object_pose_optimized=False)])


class ContactPoseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.path = self.directory / "annotations.json"

    def tearDown(self):
        self.temp.cleanup()

    def load(self, annotation, **kwargs):
        self.path.write_text(json.dumps(annotation), encoding="utf-8")
        return load_contactpose(self.path, **kwargs)

    def test_official_grasp_numeric_excerpt_preserves_metres_and_order(self):
        sequence = self.load(wine_annotation())
        expected = np.asarray(WINE_JOINTS, dtype=np.float32)
        np.testing.assert_array_equal(sequence.joints[0], expected)
        self.assertEqual(sequence.hand_side, "right")
        self.assertEqual(sequence.source_dataset, "ContactPose")
        self.assertFalse(sequence.vertices_available)
        self.assertEqual(sequence.frames.tolist(), [1])
        self.assertEqual(sequence.hand_valid.tolist(), [True])
        self.assertEqual(sequence.pressure_valid.tolist(), [False])
        self.assertFalse(sequence.vertices.any())
        self.assertFalse(sequence.force.any())
        self.assertTrue(np.isnan(sequence.total_force_n).all())
        self.assertAlmostEqual(float(np.linalg.norm(sequence.joints[0, 9]-sequence.joints[0, 0])),
                               .08875834963143533, places=7)
        frame = palm_frame_from_joints(sequence.joints[0], sequence.hand_side)
        self.assertGreater(frame.width_mm, 50)
        self.assertLess(frame.width_mm, 100)

    def test_directory_and_frame_selection_are_explicit(self):
        annotation = wine_annotation()
        annotation["frames"] *= 4
        # An unrelated world transform cannot change these object-frame joints.
        annotation["frames"][3]["oTw"] = dict(translation=[500, 600, 700], rotation=[0, 0, 0, 1])
        first = self.load(annotation, frame_index=3)
        second = load_contactpose(self.directory, frame_index=3)
        self.assertEqual(first.frames.tolist(), [4])
        self.assertIn("frame003", first.name)
        np.testing.assert_array_equal(first.joints, second.joints)
        np.testing.assert_array_equal(first.joints[0], np.asarray(WINE_JOINTS, np.float32))

    def test_bimanual_grasp_requires_side_without_mirroring(self):
        annotation = wine_annotation()
        left = np.asarray(WINE_JOINTS)*[-1, 1, 1]
        annotation["hands"][0] = dict(valid=True, moving=False, joints=left.tolist())
        with self.assertRaisesRegex(ValueError, "side"):
            self.load(annotation)
        sequence = self.load(annotation, side="left")
        self.assertEqual(sequence.hand_side, "left")
        np.testing.assert_array_equal(sequence.joints[0], left.astype(np.float32))
        np.testing.assert_array_equal(self.load(annotation, side="right").joints[0],
                                      np.asarray(WINE_JOINTS, np.float32))

    def test_moving_hand_inverse_wxyz_transform_matches_object_pose(self):
        annotation = wine_annotation()
        expected = np.asarray(WINE_JOINTS)
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        translation = [.03, -.02, .1]
        annotation["hands"][1].update(moving=True,
                                     joints=(expected @ rotation.T+translation).tolist())
        q = np.sqrt(.5)
        annotation["frames"] = [dict(hTo=[None, dict(rotation=[q, 0, 0, q],
                                                     translation=translation)])]
        actual = self.load(annotation).joints[0]
        np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=0)
        # quat2mat also normalizes a non-unit quaternion; no scalar-first swap.
        annotation["frames"][0]["hTo"][1]["rotation"] = [2*q, 0, 0, 2*q]
        np.testing.assert_array_equal(self.load(annotation).joints[0], actual)

    def test_contact_values_never_create_pressure(self):
        annotation = wine_annotation()
        annotation["contactmap"] = [0, .5, 1]
        annotation["pressure"] = [[20000]]
        sequence = self.load(annotation)
        self.assertFalse(sequence.pressure_valid.any())
        self.assertFalse(sequence.force.any())

    def test_generic_json_import_and_npz_roundtrip_preserve_missing_data(self):
        sequence = self.load(wine_annotation())
        imported = load_sequence(self.path)
        np.testing.assert_array_equal(imported.joints, sequence.joints)
        np.testing.assert_array_equal(load_sequence(self.directory).joints, sequence.joints)
        target = self.directory / "grasp.npz"
        imported.save(target)
        restored = load_sequence(target)
        self.assertEqual(restored.source_dataset, "ContactPose")
        self.assertFalse(restored.vertices_available)
        self.assertFalse(restored.pressure_valid.any())
        np.testing.assert_array_equal(restored.joints, sequence.joints)
        np.testing.assert_array_equal(restored.hand_valid, sequence.hand_valid)

    def test_joint_only_view_fits_bounds_and_never_draws_pressure_pad(self):
        try:
            from desktop_app.replay_scene import ReplayScene
        except ImportError as exc:
            self.skipTest(f"可选OpenGL显示依赖未安装：{exc}")
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        # Exercise camera geometry and render dispatch without a Tk window or
        # an OpenGL context. The parent runs the actual visible-interface check.
        scene = object.__new__(ReplayScene)
        scene.show_pad, scene.show_joints = True, True
        scene.winfo_width = lambda: 800
        scene.winfo_height = lambda: 600
        scene.render = Mock()
        scene.tk = SimpleNamespace(call=lambda *_: 96/72)
        scene._draw_pad = Mock()
        scene.set_frame(None, np.asarray(WINE_JOINTS), None, show_pad=False)
        np.testing.assert_allclose(scene.target, (scene.joints.min(0)+scene.joints.max(0))/2)
        radius = np.linalg.norm(scene.joints-scene.target, axis=1).max()
        self.assertGreater(scene.distance*np.sin(np.radians(17.5)), radius)
        with patch('desktop_app.replay_scene.GL'), patch('desktop_app.replay_scene.gluPerspective'), \
                patch('desktop_app.replay_scene.gluLookAt'):
            scene.redraw()
        scene._draw_pad.assert_not_called()
        scene.set_frame(None, np.asarray(WINE_JOINTS), None, show_pad=True)
        np.testing.assert_allclose(scene.target, [0, .025, .02])

    def test_invalid_hand_side_frame_and_missing_annotations_rejected(self):
        for kwargs in (dict(side="Left"), dict(side=0), dict(frame_index=True),
                       dict(frame_index=-1), dict(frame_index=1.0), dict(frame_index=1)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.load(wine_annotation(), **kwargs)
        with self.assertRaises(ValueError):
            self.load(wine_annotation(), side="left")
        annotation = wine_annotation()
        annotation["hands"][1]["valid"] = False
        with self.assertRaises(ValueError):
            self.load(annotation)
        for annotation in ({}, [], {"hands": [], "frames": []},
                           {"hands": [{"valid": "false"}, {"valid": True}], "frames": [{}]}):
            with self.subTest(annotation=annotation), self.assertRaises(ValueError):
                self.load(annotation)

    def test_invalid_joints_and_moving_transforms_rejected(self):
        for joints in ([], np.zeros((20, 3)).tolist(), np.full((21, 3), np.nan).tolist(),
                       np.full((21, 3), True).tolist(), np.full((21, 3), "0").tolist(),
                       np.full((21, 3), 1e100).tolist()):
            annotation = wine_annotation()
            annotation["hands"][1]["joints"] = joints
            with self.subTest(joints=str(joints)[:40]), self.assertRaises(ValueError):
                self.load(annotation)
        for pose in (None, dict(rotation=[0, 0, 0, 0], translation=[0, 0, 0]),
                     dict(rotation=[1, 0, 0, 0], translation=[0, 0]),
                     dict(rotation=[1, 0, 0, float("inf")], translation=[0, 0, 0])):
            annotation = wine_annotation()
            annotation["hands"][1]["moving"] = True
            annotation["frames"][0]["hTo"] = [None, pose]
            with self.subTest(pose=pose), self.assertRaises(ValueError):
                self.load(annotation)
        annotation = wine_annotation()
        annotation["hands"][1]["moving"] = "false"
        with self.assertRaises(ValueError):
            self.load(annotation)


if __name__ == "__main__":
    unittest.main()
