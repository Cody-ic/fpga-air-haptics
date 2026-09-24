"""Editable geometry, modest geometric constraints and gated path compilation.

Geometry is in millimetres; the compiler alone quantizes it to wire micrometres.
Authored contours retain their identity and order, even when edges overlap.
"""

from copy import deepcopy
from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import least_squares

TOL = .001  # Geometry comparison tolerance, not physical positioning accuracy.
MAX_EDGES = 256
MAX_SCAN_POINTS = 256
MAX_STROKES = 32


def cross(a, b):
    return float(a[0]*b[1]-a[1]*b[0])


@dataclass
class Curve:
    start: np.ndarray
    end: np.ndarray
    center: object = None
    sweep: float = 0.0

    @property
    def radius(self):
        return float(np.linalg.norm(self.start-self.center)) if self.center is not None else 0.0

    def point(self, t):
        if self.center is None:
            return self.start+(self.end-self.start)*t
        angle = math.atan2(*(self.start-self.center)[::-1])+self.sweep*t
        return self.center+self.radius*np.array([math.cos(angle), math.sin(angle)])

    def parameter(self, point):
        if self.center is None:
            d = self.end-self.start
            if np.dot(d, d) < 1e-14:
                return 0.0 if np.linalg.norm(point-self.start) < TOL else float('inf')
            return float(np.dot(point-self.start, d)/np.dot(d, d))
        a = math.atan2(*(point-self.center)[::-1])-math.atan2(*(self.start-self.center)[::-1])
        angle = (a % (2*math.pi)) if self.sweep > 0 else -((-a) % (2*math.pi))
        return angle/self.sweep

    def same_support(self, other):
        if isinstance(other, Bezier):
            return False
        if self.center is None and other.center is None:
            d = self.end-self.start
            if np.linalg.norm(d) < TOL or np.linalg.norm(other.end-other.start) < TOL:
                return np.linalg.norm(d) < TOL and np.linalg.norm(other.end-self.start) < TOL and np.linalg.norm(other.start-self.start) < TOL
            return all(abs(cross(d, p-self.start))/np.linalg.norm(d) < TOL
                       for p in (other.start, other.end))
        return (self.center is not None and other.center is not None
                and np.linalg.norm(self.center-other.center) < TOL
                and abs(self.radius-other.radius) < TOL)

    def samples(self, begin=0.0, end=1.0, tolerance=.1):
        count = 1
        if self.center is not None:
            step = 2*math.acos(max(-1, 1-tolerance/self.radius))
            count = max(2, math.ceil(abs(self.sweep*(end-begin))/max(step, 1e-6)))
        if count > MAX_SCAN_POINTS:
            raise ValueError("曲线过大或过于复杂，请缩小尺寸或简化图形。")
        return [self.point(t) for t in np.linspace(begin, end, count+1)]


class Bezier:
    """Cubic style curve; controls are editable handles, not interpolation points."""
    center = None
    radius = 0.0

    def __init__(self, points):
        self.controls = np.asarray(points, dtype=float)
        self.start, self.end = self.controls[0], self.controls[-1]

    def point(self, t):
        a, b, c, d = self.controls
        return (1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*d

    def tangent(self, at_start):
        return self.controls[1]-self.start if at_start else self.end-self.controls[2]

    def same_support(self, other):
        if not isinstance(other, Bezier):
            return False
        if (np.max(np.abs(self.controls-other.controls)) < TOL
                or np.max(np.abs(self.controls-other.controls[::-1])) < TOL):
            return True
        start, end = other.parameter(self.start), other.parameter(self.end)
        return math.isfinite(start) and math.isfinite(end) and all(
            np.linalg.norm(self.point(t)-other.point(start+(end-start)*t)) < TOL for t in (.25, .5, .75))

    def parameter(self, point):
        if np.linalg.norm(point-self.start) < TOL:
            return 0.0
        if np.linalg.norm(point-self.end) < TOL:
            return 1.0
        a, b, c, d = self.controls
        coefficients = np.array([-a+3*b-3*c+d, 3*a-6*b+3*c, -3*a+3*b, a-point])
        axis = int(np.argmax(np.ptp(self.controls, axis=0)))
        roots = np.roots(np.trim_zeros(coefficients[:, axis], 'f'))
        candidates = [float(t.real) for t in roots if abs(t.imag) < 1e-7
                      and np.linalg.norm(self.point(float(t.real))-point) < TOL]
        return min(candidates, key=lambda t: abs(t-.5)) if candidates else float('inf')

    def samples(self, begin=0.0, end=1.0, tolerance=.1):
        result = [self.point(begin)]

        def split(points):
            a, b, c, d = points
            ab, bc, cd = (a+b)/2, (b+c)/2, (c+d)/2
            abc, bcd = (ab+bc)/2, (bc+cd)/2
            middle = (abc+bcd)/2
            return np.array([a, ab, abc, middle]), np.array([middle, bcd, cd, d])

        def walk(points, lo, hi, depth=0):
            chord = points[-1]-points[0]
            length = np.linalg.norm(chord)
            error = max(np.linalg.norm(p-points[0]) if length < 1e-9 else abs(cross(chord, p-points[0]))/length for p in points[1:3])
            # Also split collinear reversals; distance to the infinite line alone misses them.
            polygon = sum(np.linalg.norm(b-a) for a, b in zip(points, points[1:]))
            if max(error, polygon-length) <= tolerance or depth >= 16:
                if begin < hi <= end+1e-10:
                    result.append(self.point(hi))
                return
            left, right = split(points)
            middle = (lo+hi)/2
            if middle > begin:
                walk(left, lo, middle, depth+1)
            if middle < end:
                walk(right, middle, hi, depth+1)
            if len(result) > MAX_SCAN_POINTS:
                raise ValueError('控制点曲线过于复杂，请简化。')
        walk(self.controls, 0, 1)
        if np.linalg.norm(result[-1]-self.point(end)) > 1e-8:
            result.append(self.point(end))
        return result


class Sketch:
    def __init__(self):
        self.nodes = []
        self.edges = []
        self.groups = []
        self.constraints = []
        self.order = []  # Empty means stable creation order, never random per frame.
        self.next_id = 1

    def document(self):
        return deepcopy(dict(nodes=self.nodes, edges=self.edges, groups=self.groups,
                             constraints=self.constraints, order=self.order, next_id=self.next_id))

    @classmethod
    def from_document(cls, data):
        obj = cls()
        if not isinstance(data, dict) or set(data) != set(obj.document()):
            raise ValueError("草图文件结构无效")
        for key, value in deepcopy(data).items():
            setattr(obj, key, value)
        obj.validate()
        obj.check_constraints()
        return obj

    def copy(self):
        obj = Sketch()
        for key, value in self.document().items():
            setattr(obj, key, value)
        return obj

    def edge(self, identifier):
        return next(e for e in self.edges if e['id'] == identifier)

    def node(self, point, snap=True):
        p = list(map(float, point))
        if snap:
            for i, old in enumerate(self.nodes):
                if np.linalg.norm(np.subtract(old, p)) < TOL:
                    return i
        self.nodes.append(p)
        return len(self.nodes)-1

    def add_edge(self, a, b=None, radius=None):
        if len(self.edges) >= MAX_EDGES:
            raise ValueError("草图过于复杂，请先简化。")
        item = dict(id=self.next_id, a=a, b=b, bend=0.0, radius=radius, controls=None)
        self.next_id += 1
        self.edges.append(item)
        return item['id']

    def add_polyline(self, points, closed=False, join=True):
        ids = [self.node(p) for p in points]
        if len(ids) < 2 or any(a == b for a, b in zip(ids, ids[1:])):
            raise ValueError("请在不同位置绘制一条线。")
        if closed and ids[-1] != ids[0]:
            ids.append(ids[0])
        group = [self.add_edge(a, b) for a, b in zip(ids, ids[1:])]
        if join:
            self.add_group(group)
        else:
            self.groups.append(group)
        return group

    def add_group(self, group):
        if not self.is_closed(group):
            start = self.curve(self.edge(group[0])).start
            candidates = [g for g in self.groups if not self.is_closed(g)
                          and np.linalg.norm(self.curve(self.edge(g[-1])).end-start) < TOL]
            if len(candidates) == 1:
                candidates[0].extend(group)
                group = candidates[0]
            else:
                self.groups.append(group)
            if not self.is_closed(group):
                end = self.curve(self.edge(group[-1])).end
                candidates = [g for g in self.groups if g is not group and not self.is_closed(g)
                              and np.linalg.norm(self.curve(self.edge(g[0])).start-end) < TOL]
                if len(candidates) == 1:
                    other = candidates[0]
                    group.extend(other)
                    self.groups.remove(other)
        else:
            self.groups.append(group)
        self.order = []

    def add_point(self, point):
        node = self.node(point)
        self.groups.append([self.add_edge(node, node)])
        self.order = []

    def add_rectangle(self, a, b):
        x, y = a
        u, v = b
        if abs(x-u) < .01 or abs(y-v) < .01:
            raise ValueError("矩形的宽和高须大于 0.01 mm。")
        group = self.add_polyline([(x, y), (u, y), (u, v), (x, v)], True)
        for i, edge in enumerate(group):
            self.constraints.append(dict(kind='horizontal' if i % 2 == 0 else 'vertical', edges=[edge]))
        return group

    def add_circle(self, center, radius):
        if not math.isfinite(radius) or radius < .01:
            raise ValueError("圆的半径须大于 0.01 mm。")
        identifier = self.add_edge(self.node(center, snap=False), radius=float(radius))
        self.add_group([identifier])
        return identifier

    def add_bezier(self, points):
        if len(points) != 4:
            raise ValueError('请指定起点、两个控制点和终点。')
        ids = [self.node(p, snap=i in (0, 3)) for i, p in enumerate(points)]
        identifier = self.add_edge(ids[0], ids[3])
        self.edge(identifier)['controls'] = ids[1:3]
        self.add_group([identifier])
        return identifier

    def curve(self, edge):
        a = np.array(self.nodes[edge['a']], dtype=float)
        if edge['controls'] is not None:
            return Bezier([self.nodes[i] for i in [edge['a'], *edge['controls'], edge['b']]])
        if edge['radius'] is not None:
            p = a+np.array([edge['radius'], 0.0])
            return Curve(p, p, a, 2*math.pi)
        b = np.array(self.nodes[edge['b']], dtype=float)
        bend = edge['bend']
        if abs(bend) < 1e-7:
            return Curve(a, b)
        d = b-a
        n = np.array([-d[1], d[0]])
        center = (a+b)/2+n*(bend*bend-1)/(4*bend)
        return Curve(a, b, center, -4*math.atan(bend))

    def bounds(self, edge):
        curve = self.curve(edge)
        parameters = [0.0, 1.0]
        if isinstance(curve, Bezier):
            a, b, c, d = curve.controls
            for axis in (0, 1):
                coefficients = [3*(-a+3*b-3*c+d)[axis], 2*(3*a-6*b+3*c)[axis], (-3*a+3*b)[axis]]
                for root in np.roots(np.trim_zeros(coefficients, 'f')):
                    if abs(root.imag) < 1e-8 and 0 < root.real < 1:
                        parameters.append(float(root.real))
        elif curve.center is not None:
            for direction in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                t = curve.parameter(curve.center+curve.radius*np.array(direction))
                if 0 <= t <= 1:
                    parameters.append(t)
        points = np.array([curve.point(t) for t in parameters])
        return points.min(axis=0), points.max(axis=0)

    def selection_bounds(self, identifiers):
        bounds = [self.bounds(self.edge(i)) for i in identifiers]
        return np.min([b[0] for b in bounds], axis=0), np.max([b[1] for b in bounds], axis=0)

    def remove(self, identifiers):
        identifiers = set(identifiers)
        self.edges = [e for e in self.edges if e['id'] not in identifiers]
        self.groups = [[i for i in group if i not in identifiers] for group in self.groups]
        self.groups = [g for g in self.groups if g]
        self.constraints = [c for c in self.constraints if not identifiers.intersection(c['edges'])]
        self.order = []
        used = sorted({i for e in self.edges for i in [e['a'], e['b'], *(e['controls'] or [])] if i is not None})
        mapping = {old: new for new, old in enumerate(used)}
        self.nodes = [self.nodes[i] for i in used]
        for e in self.edges:
            e['a'] = mapping[e['a']]
            e['b'] = mapping[e['b']] if e['b'] is not None else None
            if e['controls'] is not None:
                e['controls'] = [mapping[i] for i in e['controls']]

    def ordered_groups(self):
        return [(i, self.groups[i]) for i in (self.order or list(range(len(self.groups))))]

    def is_closed(self, group):
        curves = [self.curve(self.edge(i)) for i in group]
        return bool(curves and all(np.linalg.norm(a.end-b.start) < TOL
                                   for a, b in zip(curves, curves[1:]+curves[:1])))

    def compile(self):
        """Subtract earlier analytic coverage before sampling; never add bridges."""
        self.validate()
        seen, paths, current = [], [], []
        shared = 0
        for _, group in self.ordered_groups():
            current = []
            for identifier in group:
                curve = self.curve(self.edge(identifier))
                if not isinstance(curve, Bezier) and curve.center is None and np.linalg.norm(curve.end-curve.start) < TOL:
                    if current:
                        paths.append(current)
                        current = []
                    paths.append([tuple(round(float(v)*1000) for v in curve.start)])
                    continue
                covers = [old for old in seen if curve.same_support(old)]
                cuts = [0.0, 1.0]
                for old in covers:
                    for point in (old.start, old.end):
                        t = curve.parameter(point)
                        if 1e-8 < t < 1-1e-8:
                            cuts.append(t)
                cuts = sorted(set(round(t, 10) for t in cuts))
                for begin, end in zip(cuts, cuts[1:]):
                    mid = curve.point((begin+end)/2)
                    if any(-1e-8 <= old.parameter(mid) <= 1+1e-8 for old in covers):
                        shared += 1
                        if current:
                            paths.append(current)
                            current = []
                        continue
                    sampled = [tuple(round(float(v)*1000) for v in p) for p in curve.samples(begin, end)]
                    sampled = [p for j, p in enumerate(sampled) if j == 0 or p != sampled[j-1]]
                    if len(sampled) < 2:
                        continue
                    if current and current[-1] == sampled[0]:
                        current.extend(sampled[1:])
                    else:
                        if current:
                            paths.append(current)
                        current = sampled
                seen.append(curve)
            if current:
                paths.append(current)
        if len(paths) > MAX_STROKES or sum(map(len, paths)) > MAX_SCAN_POINTS:
            raise ValueError("图形过于复杂，请减少轮廓或简化曲线后再预览、发送。")
        return paths, shared

    def validate(self):
        if not isinstance(self.nodes, list) or len(self.nodes) > MAX_EDGES*4:
            raise ValueError("草图控制点过多")
        for point in self.nodes:
            if (not isinstance(point, list) or len(point) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 300 for v in point)):
                raise ValueError("草图坐标无效或超出 ±300 mm")
        if not isinstance(self.edges, list) or len(self.edges) > MAX_EDGES:
            raise ValueError("草图线条过多")
        identifiers = []
        for e in self.edges:
            if not isinstance(e, dict) or set(e) != {'id', 'a', 'b', 'bend', 'radius', 'controls'}:
                raise ValueError("草图线条无效")
            if type(e['id']) is not int or e['id'] < 1:
                raise ValueError("草图线条编号无效")
            identifiers.append(e['id'])
            if e['controls'] is not None:
                if (not isinstance(e['controls'], list) or len(e['controls']) != 2 or e['radius'] is not None
                        or any(type(i) is not int or not 0 <= i < len(self.nodes) for i in e['controls'])):
                    raise ValueError('曲线控制点无效')
            for node in [e['a']] + ([e['b']] if e['radius'] is None else []):
                if type(node) is not int or not 0 <= node < len(self.nodes):
                    raise ValueError("草图端点无效")
            if type(e['bend']) not in (int, float) or not math.isfinite(e['bend']) or abs(e['bend']) > 1:
                raise ValueError("圆弧最多支持半圆，请分段绘制。")
            if e['radius'] is not None:
                if (type(e['radius']) not in (int, float) or not math.isfinite(e['radius'])
                        or not .01 <= e['radius'] <= 300 or e['b'] is not None):
                    raise ValueError("圆的半径无效")
                if max(abs(v)+e['radius'] for v in self.nodes[e['a']]) > 300:
                    raise ValueError("圆超出草图坐标范围")
            elif e['a'] != e['b'] and np.linalg.norm(np.subtract(self.nodes[e['a']], self.nodes[e['b']])) < .001:
                raise ValueError("线条长度过小")
            if e['controls'] is not None and any(np.linalg.norm(np.subtract(self.nodes[x], self.nodes[y])) < .001
                                                  for x, y in ((e['a'], e['controls'][0]), (e['b'], e['controls'][1]))):
                raise ValueError('曲线手柄不能与对应端点重合。')
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("草图线条编号重复")
        if not isinstance(self.groups, list) or len(self.groups) > MAX_EDGES:
            raise ValueError("草图轮廓无效")
        flat = [i for g in self.groups for i in g if type(i) is int] if all(isinstance(g, list) and g for g in self.groups) else []
        if sorted(flat) != sorted(identifiers) or sum(map(len, self.groups)) != len(flat):
            raise ValueError("草图轮廓引用无效")
        if (not isinstance(self.order, list) or any(type(i) is not int for i in self.order)
                or self.order and sorted(self.order) != list(range(len(self.groups)))):
            raise ValueError("轮廓顺序无效")
        if type(self.next_id) is not int or self.next_id <= max(identifiers, default=0):
            raise ValueError("草图编号无效")
        if not isinstance(self.constraints, list) or len(self.constraints) > MAX_EDGES*3:
            raise ValueError("几何关系过多")
        for c in self.constraints:
            self.validate_constraint(c)
        return self

    def validate_constraint(self, c):
        single = {'horizontal', 'vertical', 'length', 'radius', 'diameter'}
        span = {'width', 'height'}
        pair = {'parallel', 'perpendicular', 'tangent'}
        if not isinstance(c, dict) or c.get('kind') not in single | pair | span:
            raise ValueError("不支持此几何关系")
        ids = c.get('edges')
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_EDGES
                or c['kind'] not in span and len(ids) != (1 if c['kind'] in single else 2)
                or any(type(i) is not int or i not in {e['id'] for e in self.edges} for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("请选择所需的一条或两条线。")
        edges = [self.edge(i) for i in ids]
        straight = lambda e: e['radius'] is None and e['controls'] is None and e['a'] != e['b'] and abs(e['bend']) < 1e-7
        if c['kind'] in {'horizontal', 'vertical', 'parallel', 'perpendicular', 'length'} and not all(map(straight, edges)):
            raise ValueError("该关系适用于直线；弯曲前请先移除直线关系。")
        if c['kind'] in {'length', 'radius', 'diameter', 'width', 'height'}:
            value = c.get('value')
            if type(value) not in (float, int) or not math.isfinite(value) or not .01 <= value <= 600:
                raise ValueError("尺寸应为 0.01～600 mm。")
            if c['kind'] in {'radius', 'diameter'} and self.curve(edges[0]).center is None:
                raise ValueError("半径适用于圆或圆弧。")
        if c['kind'] == 'tangent':
            a, b = edges
            if any(e['controls'] is not None and e['a'] == e['b'] for e in edges):
                raise ValueError('请将闭合控制点曲线拆成多段后，再设置端点相切。')
            if a['radius'] is None and b['radius'] is None:
                if not ({a['a'], a['b']} & {b['a'], b['b']}) or (straight(a) and straight(b)):
                    raise ValueError("线与圆弧需有共同端点；两条直线请使用平行。")
            elif a['radius'] is not None and b['radius'] is not None:
                if c.get('side') not in ('external', 'internal'):
                    raise ValueError("圆相切类型无效")
            else:
                line = b if a['radius'] is not None else a
                if not straight(line):
                    raise ValueError("支持直线与圆相切，或在共同端点连接圆弧。")

    def residuals(self):
        result = []
        for c in self.constraints:
            edges = [self.edge(i) for i in c['edges']]
            curves = [self.curve(e) for e in edges]
            kind = c['kind']
            a = curves[0]
            d = a.end-a.start
            if kind == 'horizontal':
                result.append(d[1])
            elif kind == 'vertical':
                result.append(d[0])
            elif kind == 'length':
                result.append(np.linalg.norm(d)-c['value'])
            elif kind == 'radius':
                result.append(a.radius-c['value'])
            elif kind == 'diameter':
                result.append(2*a.radius-c['value'])
            elif kind in ('width', 'height'):
                low, high = self.selection_bounds(c['edges'])
                result.append((high-low)[0 if kind == 'width' else 1]-c['value'])
            elif kind in ('parallel', 'perpendicular'):
                v = curves[1].end-curves[1].start
                denominator = max(.001, np.linalg.norm(d)*np.linalg.norm(v))
                result.append(20*(cross(d, v) if kind == 'parallel' else np.dot(d, v))/denominator)
            elif kind == 'tangent':
                b = curves[1]
                if all(e['radius'] is not None for e in edges):
                    target = a.radius+b.radius if c['side'] == 'external' else abs(a.radius-b.radius)
                    result.append(np.linalg.norm(a.center-b.center)-target)
                elif any(e['radius'] is not None for e in edges):
                    circle, line = (a, b) if edges[0]['radius'] is not None else (b, a)
                    v = line.end-line.start
                    result.append(abs(cross(v, circle.center-line.start))/max(.001, np.linalg.norm(v))-circle.radius)
                else:
                    common = next(iter({edges[0]['a'], edges[0]['b']} & {edges[1]['a'], edges[1]['b']}))
                    p = np.array(self.nodes[common])
                    vectors = []
                    for curve in curves:
                        if isinstance(curve, Bezier):
                            v = curve.tangent(np.linalg.norm(p-curve.start) < TOL)
                        else:
                            v = curve.end-curve.start if curve.center is None else np.array([-(p-curve.center)[1], (p-curve.center)[0]])
                        vectors.append(v/max(.001, np.linalg.norm(v)))
                    result.append(20*cross(*vectors))
        return np.array(result)

    def check_constraints(self):
        if self.constraints and np.max(np.abs(self.residuals())) > .002:
            raise ValueError("几何关系或尺寸存在冲突，本次修改未应用。")
        for c in self.constraints:
            if c['kind'] != 'tangent':
                continue
            edges = [self.edge(i) for i in c['edges']]
            if all(e['radius'] is not None for e in edges):
                a, b = [self.curve(e) for e in edges]
                if np.linalg.norm(a.center-b.center) < TOL and abs(a.radius-b.radius) < TOL:
                    raise ValueError('重合的圆不属于相切，请先移动其中一个圆。')
            if sum(e['radius'] is not None for e in edges) == 1:
                circle, line = sorted(edges, key=lambda e: e['radius'] is None)
                t = self.curve(line).parameter(np.array(self.nodes[circle['a']]))
                if not -1e-5 <= t <= 1+1e-5:
                    raise ValueError("相切位置落在线段延长线上，请先调整线段位置或长度。")

    def solve(self):
        """Keep the drawing near the requested edit; reject unsatisfied relations."""
        self.validate()
        if not self.constraints:
            return
        involved = {i for c in self.constraints for i in c['edges']}
        active = sorted({i for e in self.edges if e['id'] in involved
                         for i in [e['a'], e['b'], *(e['controls'] or [])] if i is not None})
        parameters = []
        for i in active:
            parameters.extend((('node', i, 0), ('node', i, 1)))
        parameters += [('radius' if e['radius'] is not None else 'bend', j, None)
                       for j, e in enumerate(self.edges) if e['id'] in involved]

        def read(key):
            kind, i, axis = key
            return self.nodes[i][axis] if kind == 'node' else self.edges[i][kind]

        original = np.array([read(key) for key in parameters])
        lower = [-300 if kind == 'node' else .01 if kind == 'radius' else -1 for kind, _, _ in parameters]
        upper = [300 if kind in ('node', 'radius') else 1 for kind, _, _ in parameters]
        # Straight-line relations lock bend at zero without exposing CAD fixed constraints.
        straight_ids = {i for c in self.constraints if c['kind'] in ('horizontal', 'vertical', 'parallel', 'perpendicular', 'length') for i in c['edges']}
        straight_ids.update(e['id'] for e in self.edges if e['id'] in involved and e['radius'] is None
                            and e['controls'] is None and abs(e['bend']) < 1e-7)
        for c in self.constraints:
            if c['kind'] == 'tangent':
                es = [self.edge(i) for i in c['edges']]
                if sum(e['radius'] is not None for e in es) == 1:
                    straight_ids.update(e['id'] for e in es if e['radius'] is None)

        def assign(values):
            for (kind, i, axis), v in zip(parameters, values):
                if kind == 'node':
                    self.nodes[i][axis] = float(v)
                else:
                    self.edges[i][kind] = float(v)

        def residual(values):
            assign(values)
            lock = [20*self.edge(i)['bend'] for i in straight_ids]
            return np.r_[self.residuals(), lock, (values-original)*1e-5]

        try:
            result = least_squares(residual, original, bounds=(lower, upper), max_nfev=120,
                                   ftol=1e-10, xtol=1e-10, gtol=1e-10)
            assign(result.x)
            for i in straight_ids:
                self.edge(i)['bend'] = 0.0
            self.validate()
            self.check_constraints()
        except Exception:
            assign(original)
            raise

    def constrain(self, kind, identifiers, value=None):
        c = dict(kind=kind, edges=list(identifiers))
        if value is not None:
            c['value'] = float(value)
        if kind == 'tangent' and len(identifiers) == 2:
            a, b = [self.curve(self.edge(i)) for i in identifiers]
            if all(self.edge(i)['radius'] is not None for i in identifiers):
                distance = np.linalg.norm(a.center-b.center)
                c['side'] = 'external' if abs(distance-a.radius-b.radius) <= abs(distance-abs(a.radius-b.radius)) else 'internal'
        self.validate_constraint(c)
        # Editing a dimension replaces that dimension, but does not erase other relations.
        if kind in ('length', 'radius', 'diameter', 'width', 'height'):
            same_kinds = {'radius', 'diameter'} if kind in ('radius', 'diameter') else {kind}
            self.constraints = [old for old in self.constraints if not (old['kind'] in same_kinds and old['edges'] == c['edges'])]
        if c not in self.constraints:
            self.constraints.append(c)
        self.solve()

    def scale(self, factor):
        if not math.isfinite(factor) or not .01 <= factor <= 100:
            raise ValueError("缩放倍数应为 0.01～100。")
        self.nodes = [[v*factor for v in p] for p in self.nodes]
        for e in self.edges:
            if e['radius'] is not None:
                e['radius'] *= factor
        for c in self.constraints:
            if 'value' in c:
                c['value'] *= factor
        self.validate()
        self.check_constraints()
