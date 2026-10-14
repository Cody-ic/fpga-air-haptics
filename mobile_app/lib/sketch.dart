import 'dart:math';
import 'model.dart';

enum DrawTool { select, line, rectangle, circle, curve, pen }

const toolNames = {
  DrawTool.select: '选择',
  DrawTool.line: '直线',
  DrawTool.rectangle: '矩形',
  DrawTool.circle: '圆',
  DrawTool.curve: '曲线',
  DrawTool.pen: '手绘',
};

class SketchElement {
  final String kind;
  final List<Point<double>> points;
  SketchElement(this.kind, Iterable<Point<double>> points)
    : points = List.of(points);
  SketchElement copy() => SketchElement(kind, points);
  List<Point<double>> get path {
    if (kind == 'circle') {
      final c = points[0], r = c.distanceTo(points[1]);
      if (r < .001) return [c];
      return List.generate(
        49,
        (i) => Point(
          c.x + r * cos(i * 2 * pi / 48),
          c.y + r * sin(i * 2 * pi / 48),
        ),
      );
    }
    if (kind == 'curve') {
      final a = points[0], control = points[1], b = points[2];
      return List.generate(25, (i) {
        final t = i / 24, s = 1 - t;
        return Point(
          s * s * a.x + 2 * s * t * control.x + t * t * b.x,
          s * s * a.y + 2 * s * t * control.y + t * t * b.y,
        );
      });
    }
    return [...points, if (kind == 'rectangle') points.first];
  }

  Point<double> get center {
    final b = bounds;
    return Point((b.$1 + b.$3) / 2, (b.$2 + b.$4) / 2);
  }

  (double, double, double, double) get bounds {
    final p = path;
    return (
      p.map((p) => p.x).reduce(min),
      p.map((p) => p.y).reduce(min),
      p.map((p) => p.x).reduce(max),
      p.map((p) => p.y).reduce(max),
    );
  }

  void move(Point<double> delta) {
    for (var i = 0; i < points.length; i++) {
      points[i] = Point(points[i].x + delta.x, points[i].y + delta.y);
    }
  }

  void rotate(double degrees) {
    final c = center, t = degrees * pi / 180;
    for (var i = 0; i < points.length; i++) {
      final x = points[i].x - c.x, y = points[i].y - c.y;
      points[i] = Point(
        c.x + x * cos(t) - y * sin(t),
        c.y + x * sin(t) + y * cos(t),
      );
    }
  }

  void scale(double factor) {
    if (!factor.isFinite || factor <= 0 || factor > 20) {
      throw const FormatException('缩放倍数应大于 0 且不超过 20');
    }
    final c = center;
    for (var i = 0; i < points.length; i++) {
      points[i] = Point(
        c.x + (points[i].x - c.x) * factor,
        c.y + (points[i].y - c.y) * factor,
      );
    }
  }

  void setSize(double width, double height) {
    if (width <= 0 || height <= 0 || width > 600 || height > 600) {
      throw const FormatException('尺寸应在 0～600 mm 之间');
    }
    final b = bounds, c = center;
    if (kind == 'circle') {
      points[1] = Point(points[0].x + width / 2, points[0].y);
      return;
    }
    // Width/height are explicitly the axis-aligned envelope, including rotation.
    for (var i = 0; i < points.length; i++) {
      points[i] = Point(
        c.x + (points[i].x - c.x) * width / max(.001, b.$3 - b.$1),
        c.y + (points[i].y - c.y) * height / max(.001, b.$4 - b.$2),
      );
    }
  }

  Map<String, Object> get json => {
    'kind': kind,
    'points': points.map((p) => [p.x, p.y]).toList(),
  };
  factory SketchElement.fromJson(Map<String, dynamic> data) {
    final kind = data['kind'];
    if (!['line', 'rectangle', 'circle', 'curve', 'pen'].contains(kind)) {
      throw const FormatException('草图类型无效');
    }
    final points = (data['points'] as List).map((p) {
      if (p is! List ||
          p.length != 2 ||
          p.any((v) => v is! num || !v.isFinite || v.abs() > 300)) {
        throw const FormatException('草图坐标无效');
      }
      return Point((p[0] as num).toDouble(), (p[1] as num).toDouble());
    }).toList();
    final expected = {'line': 2, 'rectangle': 4, 'circle': 2, 'curve': 3}[kind];
    if (points.isEmpty ||
        points.length > 256 ||
        expected != null && points.length != expected) {
      throw const FormatException('草图控制点数量无效');
    }
    return SketchElement(kind, points);
  }
}

class MobileSketch {
  final List<SketchElement> elements = [];
  final List<List<SketchElement>> _history = [];
  int? selected;
  SketchElement? get selection =>
      selected == null || selected! >= elements.length
      ? null
      : elements[selected!];
  bool get canUndo => _history.isNotEmpty;
  void checkpoint() {
    _history.add(elements.map((e) => e.copy()).toList());
    if (_history.length > 30) _history.removeAt(0);
  }

  void undo() {
    if (_history.isEmpty) return;
    elements
      ..clear()
      ..addAll(_history.removeLast());
    selected = null;
  }

  void add(SketchElement element) {
    checkpoint();
    elements.add(element);
    selected = elements.length - 1;
  }

  void remove() {
    if (selection == null) return;
    checkpoint();
    elements.removeAt(selected!);
    selected = null;
  }

  void clear() {
    checkpoint();
    elements.clear();
    selected = null;
  }

  void reorder(int from, int to) {
    checkpoint();
    final item = elements.removeAt(from);
    elements.insert(to, item);
    selected = to;
  }

  void fromConfig(FigureConfig config) {
    checkpoint();
    elements
      ..clear()
      ..addAll(config.localPaths.map((p) => SketchElement('pen', p)));
    selected = null;
  }

  void scaleAll(double factor) {
    if (!factor.isFinite || factor <= 0 || factor > 20) {
      throw const FormatException('缩放倍数应大于 0 且不超过 20');
    }
    checkpoint();
    for (final e in elements) {
      for (var i = 0; i < e.points.length; i++) {
        e.points[i] = Point(e.points[i].x * factor, e.points[i].y * factor);
      }
    }
  }

  int? hit(Point<double> point, double tolerance) {
    for (var i = elements.length - 1; i >= 0; i--) {
      final path = elements[i].path;
      if (path.first.distanceTo(point) <= tolerance) return i;
      for (var j = 1; j < path.length; j++) {
        if (distanceToSegment(point, path[j - 1], path[j]) <= tolerance) {
          return i;
        }
      }
    }
    return null;
  }

  List<List<Point<double>>> compile() {
    final result = <List<Point<double>>>[];
    final previous = <(Point<double>, Point<double>)>[];
    for (final element in elements) {
      final path = element.path
          .map(
            (p) =>
                Point((p.x * 1000).round() / 1000, (p.y * 1000).round() / 1000),
          )
          .toList();
      List<Point<double>>? current;
      if (path.length == 1) {
        result.add(path);
        continue;
      }
      final seen = <(Point<double>, Point<double>)>[];
      for (var i = 1; i < path.length; i++) {
        final a = path[i - 1], b = path[i];
        if (a == b) continue;
        final kept = subtractShared(a, b, previous);
        for (final segment in kept) {
          if (current != null && current.last.distanceTo(segment.$1) < .001) {
            current.add(segment.$2);
          } else {
            current = [segment.$1, segment.$2];
            result.add(current);
          }
        }
        seen.add((a, b));
      }
      previous.addAll(seen);
    }
    if (result.length > 32 || result.fold(0, (n, p) => n + p.length) > 256) {
      throw const FormatException('图形较复杂，请减少线条或简化曲线（最多 256 个路径坐标）');
    }
    return result;
  }
}

double distanceToSegment(Point<double> p, Point<double> a, Point<double> b) {
  final dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
  final t = length == 0 ? 0.0 : ((p.x - a.x) * dx + (p.y - a.y) * dy) / length;
  return p.distanceTo(mix(a, b, t.clamp(0.0, 1.0)));
}

List<(Point<double>, Point<double>)> subtractShared(
  Point<double> a,
  Point<double> b,
  List<(Point<double>, Point<double>)> earlier,
) {
  final dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
  var ranges = <(double, double)>[(0, 1)];
  for (final segment in earlier) {
    final c = segment.$1, d = segment.$2;
    if (((c.x - a.x) * dy - (c.y - a.y) * dx).abs() > sqrt(length) * .001 ||
        ((d.x - a.x) * dy - (d.y - a.y) * dx).abs() > sqrt(length) * .001) {
      continue;
    }
    final u = ((c.x - a.x) * dx + (c.y - a.y) * dy) / length,
        v = ((d.x - a.x) * dx + (d.y - a.y) * dy) / length;
    final lo = min(u, v), hi = max(u, v), next = <(double, double)>[];
    for (final range in ranges) {
      if (hi <= range.$1 || lo >= range.$2) {
        next.add(range);
        continue;
      }
      if (lo > range.$1) next.add((range.$1, min(lo, range.$2)));
      if (hi < range.$2) next.add((max(hi, range.$1), range.$2));
    }
    ranges = next;
  }
  return ranges
      .where((r) => r.$2 - r.$1 > 1e-9)
      .map((r) => (mix(a, b, r.$1), mix(a, b, r.$2)))
      .toList();
}

List<Point<double>> simplify(
  List<Point<double>> path, {
  double tolerance = .5,
}) {
  if (path.length < 3) return List.of(path);
  var distance = 0.0, index = 0;
  for (var i = 1; i < path.length - 1; i++) {
    final d = distanceToSegment(path[i], path.first, path.last);
    if (d > distance) {
      distance = d;
      index = i;
    }
  }
  if (distance <= tolerance) return [path.first, path.last];
  final a = simplify(path.sublist(0, index + 1), tolerance: tolerance),
      b = simplify(path.sublist(index), tolerance: tolerance);
  return [...a.take(a.length - 1), ...b];
}
