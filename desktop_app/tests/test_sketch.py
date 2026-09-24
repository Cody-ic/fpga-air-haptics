import json
import unittest
from dataclasses import replace

import numpy as np

from desktop_app.demo import DemoDevice
from desktop_app.model import Config, ArraySpec, encode_strokes, trajectory_paths, trajectory_sample, config_from_document
from desktop_app.protocol import Snapshot, decode, encode, MAX_LINE
from desktop_app.sketch import Bezier, Sketch, cross, rotation_matrix


def length(paths):
    return sum(np.linalg.norm(np.diff(np.array(p)/1000, axis=0), axis=1).sum() for p in paths)


class SketchTests(unittest.TestCase):
    def test_shared_edges_are_subtracted_in_user_order(self):
        s = Sketch()
        s.add_rectangle((0, 0), (10, 10))
        s.add_rectangle((10, 0), (20, 10))
        paths, shared = s.compile()
        self.assertEqual(shared, 1)
        self.assertAlmostEqual(length(paths), 70)
        self.assertEqual(paths[0][0], (0, 0))
        s.order = [1, 0]
        paths, shared = s.compile()
        self.assertEqual(paths[0][0], (10000, 0))
        self.assertAlmostEqual(length(paths), 70)
        self.assertEqual(shared, 1)

    def test_partial_and_reversed_line_overlap(self):
        s = Sketch()
        s.add_polyline([(0, 0), (20, 0)])
        s.add_polyline([(30, 0), (10, 0)])
        paths, shared = s.compile()
        self.assertEqual(shared, 1)
        self.assertAlmostEqual(length(paths), 30)
        self.assertEqual(paths[1], [(30000, 0), (20000, 0)])

    def test_circle_and_reversed_arc_overlap(self):
        s = Sketch()
        s.add_circle((0, 0), 10)
        s.add_circle((0, 0), 10)
        paths, shared = s.compile()
        self.assertEqual(len(paths), 1)
        self.assertGreater(shared, 0)
        s = Sketch()
        a = s.add_polyline([(0, 0), (10, 0)])[0]
        b = s.add_polyline([(10, 0), (0, 0)])[0]
        s.edge(a)['bend'] = .5
        s.edge(b)['bend'] = -.5
        paths, shared = s.compile()
        self.assertEqual(len(paths), 1)
        self.assertEqual(shared, 1)

    def test_erasure_does_not_add_a_closing_edge(self):
        s = Sketch()
        edges = s.add_rectangle((0, 0), (10, 10))
        s.remove([edges[1]])
        paths, shared = s.compile()
        self.assertFalse(s.is_closed(s.groups[0]))
        self.assertEqual(len(paths), 2)
        self.assertAlmostEqual(length(paths), 30)
        self.assertFalse(shared)

    def test_geometry_dimensions_scale_and_roundtrip(self):
        s = Sketch()
        edges = s.add_rectangle((-10, -10), (10, 10))
        s.constrain('length', [edges[0]], 30)
        s.scale(1.5)
        loaded = Sketch.from_document(json.loads(json.dumps(s.document())))
        self.assertEqual(s.document(), loaded.document())
        self.assertEqual(s.compile(), loaded.compile())
        self.assertAlmostEqual(np.linalg.norm(s.curve(s.edge(edges[0])).end-s.curve(s.edge(edges[0])).start), 45, places=5)
        self.assertEqual(s.constraints[-1]['value'], 45)

    def test_parallel_perpendicular_and_conflicts(self):
        s = Sketch()
        a = s.add_polyline([(0, 0), (10, 2)])[0]
        b = s.add_polyline([(20, 0), (25, 10)])[0]
        s.constrain('horizontal', [a])
        s.constrain('perpendicular', [a, b])
        self.assertLess(max(abs(s.residuals())), .002)
        old = s.document()
        candidate = s.copy()
        with self.assertRaises(ValueError):
            candidate.constrain('parallel', [a, b])
        self.assertEqual(s.document(), old)

    def test_circle_radius_and_control_curve_dimensions(self):
        s = Sketch()
        circle = s.add_circle((0, 0), 10)
        s.constrain('radius', [circle], 12)
        self.assertAlmostEqual(s.edge(circle)['radius'], 12, places=5)
        s.constrain('diameter', [circle], 24)
        self.assertFalse(any(c['kind'] == 'radius' for c in s.constraints))
        curve = s.add_bezier([(-20, 0), (-20, 10), (0, 10), (0, 0)])
        s.constrain('width', [curve], 25)
        s.constrain('height', [curve], 12)
        low, high = s.bounds(s.edge(curve))
        np.testing.assert_allclose(high-low, [25, 12], atol=.002)
        s.scale(2)
        self.assertAlmostEqual(s.edge(circle)['radius'], 24, places=5)
        s.check_constraints()

    def test_whole_rectangle_width_and_height(self):
        s = Sketch()
        edges = s.add_rectangle((0, 0), (10, 20))
        s.constrain('width', edges, 25)
        s.constrain('height', edges, 15)
        low, high = s.selection_bounds(edges)
        np.testing.assert_allclose(high-low, [25, 15], atol=.002)
        s.scale(2)
        low, high = s.selection_bounds(edges)
        np.testing.assert_allclose(high-low, [50, 30], atol=.002)
        self.assertEqual([c['value'] for c in s.constraints if 'value' in c], [50, 30])

    def test_dimensioned_rectangle_rotates_rigidly_and_roundtrips(self):
        s = Sketch()
        edges = s.add_rectangle((-10, -5), (10, 5))
        s.constrain('width', edges, 24)
        s.constrain('height', edges, 12)
        before = np.array(s.nodes)
        center = before.mean(axis=0)
        s.rotate(edges, 37)
        np.testing.assert_allclose(s.nodes, (before-center) @ rotation_matrix(37).T+center, atol=1e-8)
        s.check_constraints()
        self.assertEqual([c['value'] for c in s.constraints if 'value' in c], [24, 12])
        loaded = Sketch.from_document(json.loads(json.dumps(s.document())))
        self.assertEqual(loaded.compile(), s.compile())
        loaded.constrain('width', list(reversed(edges)), 30)
        self.assertEqual(sum(c['kind'] == 'width' for c in loaded.constraints), 1)
        self.assertAlmostEqual(loaded.dimension_angle(edges, 'width'), 37)
        loaded.check_constraints()

    def test_rigid_rotation_respects_external_and_direction_relations(self):
        s = Sketch()
        a = s.add_polyline([(0, 0), (10, 0)])[0]
        s.constrain('horizontal', [a])
        original = s.document()
        with self.assertRaises(ValueError):
            s.copy().rotate([a], 20)
        self.assertEqual(s.document(), original)
        s.rotate([a], 180)
        s.remove_constraint(0)
        s.rotate([a], 25)
        b = s.add_polyline([(20, 0), (30, 10)])[0]
        s.constrain('parallel', [a, b])
        with self.assertRaises(ValueError):
            s.copy().rotate([a], 30)
        s.rotate([a, b], 30)
        s.check_constraints()

    def test_coincidence_persists_can_be_removed_and_remaps_on_erasure(self):
        s = Sketch()
        unwanted = s.add_circle((-30, 0), 5)
        a = s.add_polyline([(0, 0), (10, 0)])[0]
        b = s.add_polyline([(12, 3), (20, 10)])[0]
        nodes = [s.edge(a)['b'], s.edge(b)['a']]
        s.constrain('coincident', [], nodes=nodes)
        np.testing.assert_allclose(s.nodes[nodes[0]], s.nodes[nodes[1]], atol=.002)
        for node in s.coincident_nodes([nodes[0]]):
            s.nodes[node][1] += 4
        s.solve()
        np.testing.assert_allclose(s.nodes[nodes[0]], s.nodes[nodes[1]], atol=.002)
        s.remove([unwanted])
        s = Sketch.from_document(json.loads(json.dumps(s.document())))
        nodes = s.constraints[0]['nodes']
        np.testing.assert_allclose(s.nodes[nodes[0]], s.nodes[nodes[1]], atol=.002)
        s.remove_constraint(0)
        s.nodes[nodes[0]][0] += 3
        s.check_constraints()
        self.assertGreater(np.linalg.norm(np.subtract(s.nodes[nodes[0]], s.nodes[nodes[1]])), 2)

    def test_coincidence_preflight_rejects_collapse_and_checks_conflicts(self):
        s = Sketch()
        a = s.add_polyline([(0, 0), (10, 0)])[0]
        nodes = [s.edge(a)['a'], s.edge(a)['b']]
        self.assertFalse(s.relation_status('coincident', [], nodes)[0])
        b = s.add_polyline([(20, 0), (30, 0)])[0]
        s.constrain('horizontal', [a])
        s.constrain('vertical', [b])
        original = s.document()
        self.assertFalse(s.relation_status('parallel', [a, b])[0])
        self.assertFalse(s.relation_status('horizontal', [a])[0])
        self.assertFalse(s.relation_status('tangent', [a, b])[0])
        self.assertTrue(s.relation_status('perpendicular', [a, b])[0])
        self.assertEqual(s.document(), original)

    def test_coincident_endpoints_support_tangency_and_cancel_dependency(self):
        s = Sketch()
        line = s.add_polyline([(-10, 0), (0, 0)])[0]
        curve = s.add_bezier([(1, 1), (5, 2), (5, 10), (10, 10)])
        s.constrain('coincident', [], nodes=[s.edge(line)['b'], s.edge(curve)['a']])
        s.constrain('tangent', [line, curve])
        s.check_constraints()
        s.remove_constraint(0)
        self.assertFalse(s.constraints)
        s.validate()

    def test_rotated_arc_and_bezier_keep_true_dimension_bounds(self):
        s = Sketch()
        arc = s.add_polyline([(-10, 0), (10, 0)])[0]
        s.edge(arc)['bend'] = .5
        curve = s.add_bezier([(-20, 15), (-30, 25), (15, 45), (20, 15)])
        s.constrain('width', [arc], 20)
        s.constrain('height', [curve], 20)
        s.rotate([arc, curve], -63)
        s.check_constraints()
        for c in s.constraints:
            low, high = s.selection_bounds(c['edges'], c['angle'])
            self.assertAlmostEqual((high-low)[0 if c['kind'] == 'width' else 1], c['value'], places=4)

    def test_malformed_coincidence_and_dimension_axis_rejected(self):
        s = Sketch()
        edges = s.add_rectangle((0, 0), (10, 10))
        for constraint in [dict(kind='coincident', edges=[], nodes=[0, 999]),
                           dict(kind='coincident', edges=[], nodes=[False, 1]),
                           dict(kind='width', edges=edges, value=10, angle=float('nan'))]:
            data = s.document()
            data['constraints'].append(constraint)
            with self.assertRaises(ValueError):
                Sketch.from_document(data)

    def test_circle_external_and_internal_tangency(self):
        for center, radius in (((17, 0), 5), ((6, 0), 5)):
            s = Sketch()
            a = s.add_circle((0, 0), 10)
            b = s.add_circle(center, radius)
            s.constrain('tangent', [a, b])
            s.check_constraints()
            self.assertLess(max(abs(s.residuals())), .002)

    def test_line_circle_and_arc_tangency(self):
        s = Sketch()
        line = s.add_polyline([(-10, 0), (10, 0)])[0]
        circle = s.add_circle((0, 4), 5)
        s.constrain('horizontal', [line])
        s.constrain('tangent', [line, circle])
        s.check_constraints()
        s = Sketch()
        line = s.add_polyline([(-10, 0), (0, 0)])[0]
        arc = s.add_polyline([(0, 0), (10, 10)])[0]
        s.edge(arc)['bend'] = .4
        s.constrain('tangent', [line, arc])
        s.check_constraints()

    def test_bezier_handles_tangency_and_subcurve_overlap(self):
        s = Sketch()
        line = s.add_polyline([(-10, 0), (0, 0)])[0]
        curve = s.add_bezier([(0, 0), (5, 2), (5, 10), (10, 10)])
        s.constrain('tangent', [line, curve])
        self.assertEqual(len(s.groups), 1)
        s.check_constraints()
        points = np.array([(0, 0), (0, 10), (10, 10), (10, 0)], dtype=float)
        a, b, c, d = points
        middle = Bezier(points).point(.5)
        right = [middle, (b+2*c+d)/4, (c+d)/2, d]
        s = Sketch()
        s.add_bezier(points)
        s.add_bezier(right)
        paths, shared = s.compile()
        self.assertGreater(shared, 0)
        self.assertEqual(len(paths), 1)
        self.assertGreater(max(p[1] for p in paths[0]), 7000)

    def test_disconnected_paths_and_explicit_blank_intervals(self):
        config = Config(shape='CUSTOM', repeat_millihz=1000, blank_us=10000,
                        scan_paths=encode_strokes([[(0, 0), (10000, 0)], [(30000, 0), (40000, 0)]]))
        config.validate()
        self.assertEqual(len(trajectory_paths(config)), 2)
        point, on, index = trajectory_sample(config, .495)
        self.assertFalse(on)
        self.assertEqual(index, 0)
        np.testing.assert_allclose(point, [20, 0, 150])
        self.assertTrue(trajectory_sample(config, .51)[1])
        clock = [0.0]
        device = DemoDevice(clock=lambda: clock[0])
        device.config = config
        device.state = 'RUNNING'
        clock[0] = .495
        state = Snapshot.parse(decode(device.snapshot()).fields)
        self.assertFalse(state.output)
        self.assertFalse(state.scan_on)
        bad = decode(device.snapshot()).fields
        bad['output'] = '1'
        with self.assertRaises(ValueError):
            Snapshot.parse(bad)

    def test_full_capacity_frame_and_atomic_rejection(self):
        paths = [[(-100000+j*100-i, -100000+i) for j in range(8)] for i in range(32)]
        c = Config(shape='CUSTOM', scan_paths=encode_strokes(paths), phase_steps=256)
        device = DemoDevice(array=ArraySpec(16, 16))
        device.config = c
        raw = device.snapshot()
        self.assertLessEqual(len(raw), MAX_LINE)
        self.assertEqual(Snapshot.parse(decode(raw).fields).config, c)
        device.handle(encode('CMD', 1, 'HELLO'))
        bad = dict(c.wire(), blank_us=100000, repeat_millihz=200000)
        response = device.handle(encode('CMD', 2, 'CONFIG', **bad, **device.array.wire()))
        self.assertEqual(decode(response[0]).kind, 'ERR')
        self.assertEqual(device.config, c)
        self.assertEqual(device.revision, 0)

    def test_old_files_migrate_and_malformed_sketches_fail(self):
        c = Config(shape='CUSTOM', path_xy_um='0:0,10000:0')
        old = c.wire()
        old.pop('scan_paths')
        old.pop('blank_us')
        self.assertEqual(config_from_document(dict(schema='haptics-config-2', config=old)), c)
        s = Sketch()
        s.add_circle((0, 0), 10)
        bad = s.document()
        bad['edges'][0]['radius'] = float('nan')
        with self.assertRaises(ValueError):
            Sketch.from_document(bad)
        with self.assertRaises(ValueError):
            replace(c, scan_paths='0:0|1:1').validate()
