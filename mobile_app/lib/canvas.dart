import 'dart:math';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'hand_data.dart';
import 'model.dart';
import 'sketch.dart';

// A touch that begins on the drawing surface belongs to the canvas, not the
// surrounding page's vertical scroll recognizer. Both fingers stay in one arena.
class _CanvasScaleRecognizer extends ScaleGestureRecognizer {
  VoidCallback? onFirstPointer;
  @override
  void addAllowedPointer(PointerDownEvent event) {
    if (pointerCount == 0) onFirstPointer?.call();
    super.addAllowedPointer(event);
    resolve(GestureDisposition.accepted);
  }
}

class PalmCanvas extends StatefulWidget {
  final FigureConfig? config;
  final MobileSketch? sketch;
  final DrawTool tool;
  final VoidCallback? onChanged;
  final VoidCallback? onSelectionChanged;
  final Point<double>? focus;
  final bool playing, blank;
  final double height;
  const PalmCanvas({
    super.key,
    this.config,
    this.sketch,
    this.tool = DrawTool.select,
    this.onChanged,
    this.onSelectionChanged,
    this.focus,
    this.playing = false,
    this.blank = false,
    this.height = 350,
  });
  @override
  State<PalmCanvas> createState() => _PalmCanvasState();
}

class _PalmCanvasState extends State<PalmCanvas> {
  double zoom = 1, _baseZoom = 1;
  Offset pan = Offset.zero, _basePan = Offset.zero, _gestureStart = Offset.zero;
  Point<double>? start, last;
  SketchElement? temporary;
  int? handle;
  bool multi = false;
  List<SketchElement>? before;
  double scale(Size size) => min(size.width / 120, size.height / 115) * zoom;
  Offset origin(Size size) =>
      Offset(size.width / 2, size.height / 2 - 10 * scale(size)) + pan;
  Point<double> world(Offset p, Size size) {
    final o = origin(size), s = scale(size);
    return Point(
      (p.dx - o.dx) / s - (widget.config?.value('cx_um') ?? 0) / 1000,
      -(p.dy - o.dy) / s - (widget.config?.value('cy_um') ?? 0) / 1000,
    );
  }

  void begin(ScaleStartDetails details, Size size) {
    _baseZoom = zoom;
    _basePan = pan;
    _gestureStart = details.localFocalPoint;
    start = world(details.localFocalPoint, size);
    last = start;
    multi = (widget.sketch != null && multi) || details.pointerCount > 1;
    handle = null;
    final sketch = widget.sketch;
    if (sketch == null || multi) return;
    before = sketch.elements.map((e) => e.copy()).toList();
    if (widget.tool == DrawTool.select) {
      final e = sketch.selection;
      if (e != null && e.kind != 'rectangle') {
        for (var i = 0; i < e.points.length; i++) {
          if (e.points[i].distanceTo(start!) < 18 / scale(size)) {
            handle = i;
            break;
          }
        }
      }
      final centerHandle =
          e != null &&
          e.kind == 'rectangle' &&
          e.center.distanceTo(start!) < 18 / scale(size);
      if (handle == null && !centerHandle) {
        sketch.selected = sketch.hit(start!, 15 / scale(size));
      }
      if (sketch.selection != null) sketch.checkpoint();
      widget.onSelectionChanged?.call();
    } else {
      temporary = SketchElement('pen', [start!]);
    }
  }

  void update(ScaleUpdateDetails details, Size size) {
    if (details.pointerCount > 1 || multi) {
      if (!multi && before != null && widget.sketch != null) {
        widget.sketch!.elements
          ..clear()
          ..addAll(before!);
        temporary = null;
        widget.onChanged?.call();
      }
      multi = true;
      setState(() {
        zoom = (_baseZoom * details.scale).clamp(.35, 3.5);
        pan = _basePan + details.localFocalPoint - _gestureStart;
      });
      return;
    }
    if (widget.sketch == null || start == null) return;
    final point = world(details.localFocalPoint, size), sketch = widget.sketch!;
    if (widget.tool == DrawTool.select) {
      final e = sketch.selection;
      if (e != null) {
        if (handle != null) {
          if (e.kind == 'circle' && handle == 0) {
            e.move(Point(point.x - e.points[0].x, point.y - e.points[0].y));
          } else {
            e.points[handle!] = point;
          }
        } else {
          e.move(Point(point.x - last!.x, point.y - last!.y));
        }
        widget.onChanged?.call();
      }
    } else {
      switch (widget.tool) {
        case DrawTool.line:
          temporary = SketchElement('line', [start!, point]);
        case DrawTool.rectangle:
          temporary = SketchElement('rectangle', [
            start!,
            Point(point.x, start!.y),
            point,
            Point(start!.x, point.y),
          ]);
        case DrawTool.circle:
          temporary = SketchElement('circle', [start!, point]);
        case DrawTool.curve:
          final mid = mix(start!, point, .5),
              dx = point.x - start!.x,
              dy = point.y - start!.y;
          temporary = SketchElement('curve', [
            start!,
            Point(mid.x - dy * .25, mid.y + dx * .25),
            point,
          ]);
        case DrawTool.pen:
          temporary ??= SketchElement('pen', [start!]);
          if (temporary!.points.last.distanceTo(point) > .3 &&
              temporary!.points.length < 1000) {
            temporary!.points.add(point);
          }
        case DrawTool.select:
          break;
      }
    }
    last = point;
    setState(() {});
  }

  void end(ScaleEndDetails details) {
    final sketch = widget.sketch;
    // A change in finger count ends one scale phase, not the user's drawing.
    if (details.pointerCount > 0) {
      if (!multi && before != null && sketch != null) {
        sketch.elements
          ..clear()
          ..addAll(before!);
        widget.onChanged?.call();
      }
      multi = true;
    }
    if (!multi && sketch != null && temporary != null) {
      var e = temporary!;
      if (e.kind == 'pen') e = SketchElement('pen', simplify(e.points));
      if (e.kind == 'pen' || pathLength(e.path) > .2) sketch.add(e);
      widget.onChanged?.call();
    }
    setState(() {
      temporary = null;
      start = null;
      before = null;
    });
  }

  @override
  Widget build(BuildContext context) => SizedBox(
    height: widget.height,
    child: LayoutBuilder(
      builder: (context, c) {
        final size = Size(c.maxWidth, c.maxHeight);
        return Stack(
          children: [
            Positioned.fill(
              child: Semantics(
                label: widget.sketch == null
                    ? '手掌图形预览'
                    : '草图画布，${toolNames[widget.tool]}工具，双指缩放和移动',
                child: RawGestureDetector(
                  behavior: HitTestBehavior.opaque,
                  gestures: {
                    if (widget.sketch != null)
                      _CanvasScaleRecognizer:
                          GestureRecognizerFactoryWithHandlers<
                            _CanvasScaleRecognizer
                          >(_CanvasScaleRecognizer.new, (recognizer) {
                            recognizer.onFirstPointer = () => multi = false;
                            recognizer.onStart = (d) => begin(d, size);
                            recognizer.onUpdate = (d) => update(d, size);
                            recognizer.onEnd = end;
                          }),
                    if (widget.sketch == null)
                      ScaleGestureRecognizer:
                          GestureRecognizerFactoryWithHandlers<
                            ScaleGestureRecognizer
                          >(ScaleGestureRecognizer.new, (recognizer) {
                            recognizer.onStart = (d) => begin(d, size);
                            recognizer.onUpdate = (d) => update(d, size);
                            recognizer.onEnd = end;
                          }),
                  },
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(20),
                    child: CustomPaint(
                      painter: PalmPainter(
                        config: widget.config,
                        sketch: widget.sketch,
                        temporary: temporary,
                        focus: widget.focus,
                        playing: widget.playing,
                        blank: widget.blank,
                        zoom: zoom,
                        pan: pan,
                      ),
                    ),
                  ),
                ),
              ),
            ),
            Positioned(
              right: 8,
              bottom: 8,
              child: IconButton.filledTonal(
                tooltip: '恢复掌面视图',
                icon: const Icon(Icons.fit_screen),
                onPressed: () => setState(() {
                  zoom = 1;
                  pan = Offset.zero;
                }),
              ),
            ),
            Positioned(
              left: 14,
              bottom: 14,
              child: IgnorePointer(
                child: Text(
                  '手掌示意 · 非位置检测',
                  style: TextStyle(
                    fontSize: 11,
                    color: Colors.blueGrey.shade500,
                  ),
                ),
              ),
            ),
          ],
        );
      },
    ),
  );
}

class PalmPainter extends CustomPainter {
  final FigureConfig? config;
  final MobileSketch? sketch;
  final SketchElement? temporary;
  final Point<double>? focus;
  final bool playing, blank;
  final double zoom;
  final Offset pan;
  PalmPainter({
    this.config,
    this.sketch,
    this.temporary,
    this.focus,
    this.playing = false,
    this.blank = false,
    this.zoom = 1,
    this.pan = Offset.zero,
  });
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawColor(const Color(0xfff5f8fb), BlendMode.src);
    final s = min(size.width / 120, size.height / 115) * zoom,
        o = Offset(size.width / 2, size.height / 2 - 10 * s) + pan;
    Offset at(Point<double> p) => Offset(o.dx + p.x * s, o.dy - p.y * s);
    final hand = Path()
      ..moveTo(o.dx + handStart[0] * s, o.dy - handStart[1] * s);
    for (final c in handCurves) {
      hand.cubicTo(
        o.dx + c[0] * s,
        o.dy - c[1] * s,
        o.dx + c[2] * s,
        o.dy - c[3] * s,
        o.dx + c[4] * s,
        o.dy - c[5] * s,
      );
    }
    hand.close();
    canvas.drawPath(
      hand,
      Paint()..color = const Color(0xffeee8df).withValues(alpha: .52),
    );
    canvas.drawPath(
      hand,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2
        ..color = const Color(0xffcfc5b8),
    );
    final center = Point(
      (config?.value('cx_um') ?? 0) / 1000,
      (config?.value('cy_um') ?? 0) / 1000,
    );
    void draw(
      List<Point<double>> points,
      Color color,
      double width, {
      bool local = false,
    }) {
      if (points.isEmpty) return;
      Offset project(Point<double> p) =>
          at(local ? Point(p.x + center.x, p.y + center.y) : p);
      if (points.length == 1) {
        canvas.drawCircle(project(points.first), 4, Paint()..color = color);
        return;
      }
      final path = Path()
        ..moveTo(project(points.first).dx, project(points.first).dy);
      for (final p in points.skip(1)) {
        path.lineTo(project(p).dx, project(p).dy);
      }
      canvas.drawPath(
        path,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = width
          ..strokeCap = StrokeCap.round
          ..strokeJoin = StrokeJoin.round
          ..color = color,
      );
    }

    if (sketch != null) {
      for (var i = 0; i < sketch!.elements.length; i++) {
        draw(
          sketch!.elements[i].path,
          i == sketch!.selected
              ? const Color(0xff087e8b)
              : const Color(0xff2463c5),
          i == sketch!.selected ? 3.5 : 2.6,
          local: true,
        );
      }
      final e = sketch!.selection;
      if (e != null) {
        final points = e.kind == 'rectangle' ? [e.center] : e.points;
        for (final p in points) {
          final point = at(Point(p.x + center.x, p.y + center.y));
          canvas.drawCircle(point, 7, Paint()..color = Colors.white);
          canvas.drawCircle(
            point,
            7,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 2
              ..color = const Color(0xff087e8b),
          );
        }
        if (e.kind == 'curve') {
          draw(
            e.points,
            const Color(0xff087e8b).withValues(alpha: .3),
            1,
            local: true,
          );
        }
      }
    } else if (config != null) {
      for (final path in config!.paths) {
        draw(path, const Color(0xff2463c5), 3);
      }
    }
    if (temporary != null) {
      draw(temporary!.path, const Color(0xff087e8b), 2.8, local: true);
    }
    if (focus != null && !blank) {
      final point = at(focus!),
          color = playing ? const Color(0xff087e8b) : const Color(0xffb17a25);
      canvas.drawCircle(
        point,
        18,
        Paint()..color = color.withValues(alpha: .16),
      );
      canvas.drawCircle(point, 7, Paint()..color = color);
      canvas.drawCircle(
        point,
        8,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2
          ..color = Colors.white,
      );
    }
  }

  @override
  bool shouldRepaint(covariant PalmPainter oldDelegate) => true;
}

bool outsidePalm(FigureConfig config) {
  // Same reference polygon and 10 mm allowance as desktop; warning only.
  bool outside(Point<double> point) {
    var inside = false, distance = double.infinity;
    for (var i = 1; i < palmOutline.length; i++) {
      final a = Point(palmOutline[i - 1][0], palmOutline[i - 1][1]),
          b = Point(palmOutline[i][0], palmOutline[i][1]);
      if ((a.y > point.y) != (b.y > point.y) &&
          point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) {
        inside = !inside;
      }
      distance = min(distance, distanceToSegment(point, a, b));
    }
    return !inside && distance > 10;
  }

  for (final path in config.paths) {
    if (path.any(outside)) return true;
    for (var i = 1; i < path.length; i++) {
      final count = max(1, (path[i].distanceTo(path[i - 1]) / 2).ceil());
      for (var n = 1; n < count; n++) {
        if (outside(mix(path[i - 1], path[i], n / count))) return true;
      }
    }
  }
  return false;
}
