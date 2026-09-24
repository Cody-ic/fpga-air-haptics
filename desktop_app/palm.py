"""Shared illustrative palm reference, not a measurement or device limit."""

import numpy as np


HAND_START = (-24, -61)
HAND_SEGMENTS = [
    ((-25, -42), (-37, -28), (-43, -15)),
    ((-48, -3), (-56, 3), (-63, 12)),
    ((-71, 23), (-62, 31), (-55, 26)),
    ((-47, 20), (-43, 11), (-37, 8)),
    ((-32, 8), (-32, 21), (-33, 33)),
    ((-35, 53), (-36, 72), (-35, 84)),
    ((-35, 99), (-19, 101), (-18, 85)),
    ((-17, 72), (-17, 51), (-15, 42)),
    ((-12, 41), (-12, 50), (-12, 65)),
    ((-12, 79), (-13, 94), (-11, 104)),
    ((-9, 118), (8, 115), (8, 102)),
    ((8, 86), (6, 60), (8, 47)),
    ((10, 41), (13, 45), (13, 54)),
    ((14, 68), (14, 84), (16, 94)),
    ((18, 106), (33, 104), (34, 91)),
    ((34, 75), (30, 54), (31, 40)),
    ((31, 34), (35, 34), (36, 43)),
    ((38, 55), (38, 65), (40, 72)),
    ((44, 82), (55, 77), (52, 65)),
    ((50, 49), (47, 24), (46, 9)),
    ((46, -14), (39, -35), (26, -47)),
    ((23, -52), (24, -58), (24, -61)),
    ((9, -63), (-9, -63), (-24, -61)),
]


def _outline():
    start = np.array(HAND_START, dtype=float)
    points = [start]
    for a, b, end in HAND_SEGMENTS:
        a, b, end = map(lambda p: np.asarray(p, dtype=float), (a, b, end))
        for t in np.linspace(0, 1, 13)[1:]:
            points.append((1-t)**3*start+3*(1-t)**2*t*a+3*(1-t)*t*t*b+t**3*end)
        start = end
    return np.asarray(points)


HAND_OUTLINE = _outline()


def _clip(points, normal, limit):
    """Clip the shared hand polygon for palm-only guidance, never for drawing."""
    result = []
    for a, b in zip(points, np.roll(points, -1, axis=0)):
        da, db = np.dot(a, normal)-limit, np.dot(b, normal)-limit
        if da <= 0:
            result.append(a)
        if (da <= 0) != (db <= 0):
            result.append(a+(b-a)*da/(da-db))
    return np.asarray(result)


# The reference ends near the finger roots and excludes the projecting thumb.
# These cuts serve framing and warnings only; the visible outline always uses
# the complete hand, with fingers continuing naturally beyond the viewport.
_palm = _clip(HAND_OUTLINE[:-1], (0, 1), 35)
_palm = _clip(_palm, (-48, 10), 1914)
PALM_OUTLINE = np.vstack([_palm, _palm[0]])
PALM_MARGIN_MM = 10.0  # UI warning allowance, never a tactile safety claim.


def outside_distances(points):
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    inside = np.zeros(len(points), dtype=bool)
    distance = np.full(len(points), np.inf)
    for a, b in zip(PALM_OUTLINE, PALM_OUTLINE[1:]):
        delta = b-a
        t = np.clip((points-a) @ delta/max(1e-12, delta @ delta), 0, 1)
        distance = np.minimum(distance, np.linalg.norm(points-(a+t[:, None]*delta), axis=1))
        if abs(delta[1]) > 1e-12:
            inside ^= ((a[1] > points[:, 1]) != (b[1] > points[:, 1])) & (
                points[:, 0] < a[0]+(points[:, 1]-a[1])*delta[0]/delta[1])
    return np.where(inside, 0.0, distance)


def palm_warning(paths):
    # Sample segments as well as their vertices: a curve may leave the palm
    # between endpoints. Values use physical mm, independent of view zoom.
    points = []
    for path in paths:
        path = np.asarray(path, dtype=float).reshape(-1, 2)
        if len(path):
            points.extend(path)
        for a, b in zip(path, path[1:]):
            count = max(2, int(np.ceil(np.linalg.norm(b-a)/2))+1)
            points.extend(np.linspace(a, b, count)[1:-1])
    if points and np.max(outside_distances(points)) > PALM_MARGIN_MM:
        return '图形明显超出手掌参考范围，可缩小或移动后再预览。'
    return ''
