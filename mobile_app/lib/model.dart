import 'dart:math';

const shapeNames = {
  'POINT': '固定点',
  'LINE_X': '水平线',
  'LINE_Y': '竖直线',
  'CIRCLE': '圆',
  'SQUARE': '正方形',
  'TRIANGLE': '三角形',
  'ARROW': '箭头',
  'CUSTOM': '手绘图形',
};

int integer(Object? value) {
  if (value is int) return value;
  if (value is String && RegExp(r'^[+-]?[0-9]+$').hasMatch(value)) {
    return int.parse(value);
  }
  throw const FormatException('数值必须是整数');
}

class ArraySpec {
  final int rows, cols, pitchUm;
  const ArraySpec({this.rows = 4, this.cols = 4, this.pitchUm = 10000});
  int get count => rows * cols;
  factory ArraySpec.parse(Map<String, String> f) {
    final a = ArraySpec(
      rows: integer(f['hw_rows']),
      cols: integer(f['hw_cols']),
      pitchUm: integer(f['hw_pitch_um']),
    );
    if (a.rows < 1 ||
        a.rows > 16 ||
        a.cols < 1 ||
        a.cols > 16 ||
        a.pitchUm < 1000 ||
        a.pitchUm > 30000 ||
        f['mapping'] != 'ROW_MAJOR_XY') {
      throw const FormatException('设备阵列信息无效');
    }
    return a;
  }
  Map<String, String> get wire => {
    'hw_rows': '$rows',
    'hw_cols': '$cols',
    'hw_pitch_um': '$pitchUm',
    'mapping': 'ROW_MAJOR_XY',
  };
  bool same(ArraySpec other) =>
      rows == other.rows && cols == other.cols && pitchUm == other.pitchUm;
}

class FigureConfig {
  static const defaults = <String, String>{
    'carrier_hz': '40000',
    'phase_steps': '64',
    'cx_um': '0',
    'cy_um': '0',
    'z_um': '150000',
    'radius_um': '20000',
    'repeat_millihz': '500',
    'mod_hz': '200',
    'level': '30',
    'shape': 'CIRCLE',
    'path_xy_um': 'NONE',
    'path_closed': '1',
    'scan_paths': 'NONE',
    'blank_us': '2000',
  };
  final Map<String, String> wire;
  FigureConfig._(Map<String, String> values) : wire = Map.unmodifiable(values);
  factory FigureConfig([Map<String, Object?>? changes]) =>
      FigureConfig.parse({...defaults, ...?changes});
  factory FigureConfig.parse(Map<String, Object?> raw) {
    final values = <String, String>{};
    for (final key in defaults.keys) {
      if (!raw.containsKey(key)) throw FormatException('缺少图形参数：$key');
      if (['shape', 'path_xy_um', 'scan_paths'].contains(key)) {
        if (raw[key] is! String) throw const FormatException('图形格式错误');
        values[key] = raw[key] as String;
      } else {
        values[key] = '${integer(raw[key])}';
      }
    }
    final config = FigureConfig._(values);
    config._validate();
    return config;
  }
  String get shape => wire['shape']!;
  int value(String key) => int.parse(wire[key]!);
  FigureConfig change(Map<String, Object?> changes) =>
      FigureConfig.parse({...wire, ...changes});
  bool same(FigureConfig other) =>
      defaults.keys.every((key) => wire[key] == other.wire[key]);

  void _validate() {
    const limits = {
      'carrier_hz': (20000, 80000),
      'phase_steps': (8, 256),
      'cx_um': (-100000, 100000),
      'cy_um': (-100000, 100000),
      'z_um': (20000, 300000),
      'radius_um': (0, 80000),
      'repeat_millihz': (10, 200000),
      'mod_hz': (0, 1000),
      'level': (0, 100),
      'path_closed': (0, 1),
      'blank_us': (100, 100000),
    };
    for (final entry in limits.entries) {
      final n = value(entry.key);
      if (n < entry.value.$1 || n > entry.value.$2) {
        throw FormatException('参数超出范围：${entry.key}');
      }
    }
    if (![8, 16, 32, 64, 128, 256].contains(value('phase_steps')) ||
        !shapeNames.containsKey(shape)) {
      throw const FormatException('图形或相位级数不支持');
    }
    final old = legacyPoints;
    final paths = strokes;
    if (old.isNotEmpty && paths.isNotEmpty) {
      throw const FormatException('两种路径不能同时使用');
    }
    if (shape == 'CUSTOM' && old.isEmpty && paths.isEmpty) {
      throw const FormatException('请先画出图形');
    }
    if (shape == 'CUSTOM' &&
        paths.isNotEmpty &&
        value('blank_us') * paths.length * value('repeat_millihz') >=
            1000000000) {
      throw const FormatException('图形切换时间不足，请降低播放频率');
    }
  }

  static List<Point<double>> parsePath(String text) {
    final result = <Point<double>>[];
    for (final token in text.split(',')) {
      if (!RegExp(r'^-?[0-9]{1,6}:-?[0-9]{1,6}$').hasMatch(token)) {
        throw const FormatException('路径坐标格式无效');
      }
      final xy = token.split(':').map(int.parse).toList();
      if (xy.any((n) => n.abs() > 300000)) {
        throw const FormatException('路径坐标超出范围');
      }
      final point = Point(xy[0] / 1000, xy[1] / 1000);
      if (result.isNotEmpty && result.last == point) {
        throw const FormatException('相邻坐标不能重复');
      }
      result.add(point);
    }
    return result;
  }

  List<Point<double>> get legacyPoints {
    final text = wire['path_xy_um']!;
    if (text == 'NONE') return [];
    if (text.length > 64 * 16) throw const FormatException('旧路径过长');
    final points = parsePath(text);
    if (points.length > 64 ||
        (value('path_closed') == 1 &&
            points.length > 1 &&
            points.first == points.last)) {
      throw const FormatException('旧路径节点无效');
    }
    return points;
  }

  List<List<Point<double>>> get strokes {
    final text = wire['scan_paths']!;
    if (text == 'NONE') return [];
    if (text.length > 256 * 16) throw const FormatException('路径过长');
    final result = text.split('|').map(parsePath).toList();
    if (result.length > 32 || result.fold(0, (n, p) => n + p.length) > 256) {
      throw const FormatException('最多 32 段、256 个路径坐标，请简化图形');
    }
    return result;
  }

  static String encodePaths(List<List<Point<double>>> paths) => paths.isEmpty
      ? 'NONE'
      : paths
            .map(
              (p) => p
                  .map((v) => '${(v.x * 1000).round()}:${(v.y * 1000).round()}')
                  .join(','),
            )
            .join('|');

  List<List<Point<double>>> get localPaths {
    final r = value('radius_um') / 1000;
    Point<double> p(double x, double y) => Point(x * r, y * r);
    switch (shape) {
      case 'CUSTOM':
        if (strokes.isNotEmpty) return strokes;
        final old = legacyPoints;
        return [
          [...old, if (value('path_closed') == 1 && old.length > 1) old.first],
        ];
      case 'POINT':
        return [
          [p(0, 0)],
        ];
      case 'CIRCLE':
        return [
          List.generate(
            97,
            (i) => p(cos(2 * pi * i / 96), sin(2 * pi * i / 96)),
          ),
        ];
      case 'SQUARE':
        return [
          [p(-1, -1), p(1, -1), p(1, 1), p(-1, 1), p(-1, -1)],
        ];
      case 'TRIANGLE':
        return [
          [p(0, 1), p(-.866, -.5), p(.866, -.5), p(0, 1)],
        ];
      case 'LINE_X':
        return [
          [p(-1, 0), p(1, 0)],
        ];
      case 'LINE_Y':
        return [
          [p(0, -1), p(0, 1)],
        ];
      default:
        return [
          [
            p(-1, 0),
            p(1, 0),
            p(.3, .7),
            p(1, 0),
            p(.3, -.7),
            p(1, 0),
            p(-1, 0),
          ],
        ];
    }
  }

  List<List<Point<double>>> get paths => localPaths
      .map(
        (path) => path
            .map(
              (p) => Point(
                p.x + value('cx_um') / 1000,
                p.y + value('cy_um') / 1000,
              ),
            )
            .toList(),
      )
      .toList();

  ({Point<double> focus, bool on, int stroke}) sample(double seconds) {
    final fraction = (seconds * value('repeat_millihz') / 1000) % 1;
    final r = value('radius_um') / 1000;
    Point<double> translated(Point<double> p) =>
        Point(p.x + value('cx_um') / 1000, p.y + value('cy_um') / 1000);
    if (shape == 'CUSTOM' && strokes.isNotEmpty) {
      final paths = this.paths;
      final period = 1000 / value('repeat_millihz');
      final blank = value('blank_us') / 1000000;
      final active = period - paths.length * blank;
      final weights = paths.map((path) => max(.1, pathLength(path))).toList();
      final total = weights.reduce((a, b) => a + b);
      var offset = seconds % period;
      for (var i = 0; i < paths.length; i++) {
        final duration = active * weights[i] / total;
        if (offset < duration) {
          return (
            focus: along(paths[i], offset / duration),
            on: true,
            stroke: i,
          );
        }
        offset -= duration;
        if (offset < blank) {
          return (
            focus: mix(
              paths[i].last,
              paths[(i + 1) % paths.length].first,
              offset / blank,
            ),
            on: false,
            stroke: i,
          );
        }
        offset -= blank;
      }
      return (focus: paths.first.first, on: true, stroke: 0);
    }
    Point<double> focus;
    if (shape == 'CIRCLE') {
      focus = Point(r * cos(2 * pi * fraction), r * sin(2 * pi * fraction));
    } else if (shape == 'LINE_X' || shape == 'LINE_Y') {
      final t = r * (1 - 4 * (fraction - .5).abs());
      focus = Point(shape == 'LINE_X' ? t : 0.0, shape == 'LINE_Y' ? t : 0.0);
    } else {
      var path = localPaths.first;
      if (shape == 'CUSTOM' && value('path_closed') == 0) {
        path = [...path, ...path.reversed.skip(1)];
      }
      focus = along(path, fraction);
    }
    return (focus: translated(focus), on: true, stroke: 0);
  }

  List<int> phases(Point<double> focus, ArraySpec array) => List.generate(
    array.count,
    (i) {
      final x = (i % array.cols - (array.cols - 1) / 2) * array.pitchUm / 1000;
      final y = (i ~/ array.cols - (array.rows - 1) / 2) * array.pitchUm / 1000;
      final distance =
          sqrt(
            pow(focus.x - x, 2) +
                pow(focus.y - y, 2) +
                pow(value('z_um') / 1000, 2),
          ) /
          1000;
      return (((-distance * value('carrier_hz') / 343) % 1) *
                      value('phase_steps') +
                  .5)
              .floor() %
          value('phase_steps');
    },
  );
}

Point<double> mix(Point<double> a, Point<double> b, double t) =>
    Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
double pathLength(List<Point<double>> path) {
  var length = 0.0;
  for (var i = 1; i < path.length; i++) {
    length += path[i].distanceTo(path[i - 1]);
  }
  return length;
}

Point<double> along(List<Point<double>> path, double fraction) {
  var distance = (fraction % 1) * pathLength(path);
  for (var i = 1; i < path.length; i++) {
    final length = path[i].distanceTo(path[i - 1]);
    if (length > 0 && distance <= length) {
      return mix(path[i - 1], path[i], distance / length);
    }
    distance -= length;
  }
  return path.first;
}

class Workspace {
  static const defaults = {
    'x_min_um': '-100000',
    'x_max_um': '100000',
    'y_min_um': '-100000',
    'y_max_um': '100000',
    'z_min_um': '20000',
    'z_max_um': '300000',
  };
  final Map<String, int> bounds;
  Workspace(Map<String, String> f)
    : bounds = {for (final key in defaults.keys) key: integer(f[key])} {
    for (final axis in ['x', 'y', 'z']) {
      final lo = bounds['${axis}_min_um']!, hi = bounds['${axis}_max_um']!;
      if (lo >= hi ||
          lo < (axis == 'z' ? 20000 : -400000) ||
          hi > (axis == 'z' ? 300000 : 400000)) {
        throw const FormatException('设备工作范围无效');
      }
    }
  }
  bool accepts(FigureConfig config) {
    final z = config.value('z_um');
    if (z < bounds['z_min_um']! || z > bounds['z_max_um']!) return false;
    return config.paths
        .expand((p) => p)
        .every(
          (p) =>
              p.x * 1000 >= bounds['x_min_um']! &&
              p.x * 1000 <= bounds['x_max_um']! &&
              p.y * 1000 >= bounds['y_min_um']! &&
              p.y * 1000 <= bounds['y_max_um']!,
        );
  }
}
