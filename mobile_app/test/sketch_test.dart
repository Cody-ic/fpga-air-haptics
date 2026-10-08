import 'dart:math';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:touchsee_mobile/model.dart';
import 'package:touchsee_mobile/sketch.dart';
import 'package:touchsee_mobile/library.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('partial common edges are removed without adding transfer lines', () {
    final sketch = MobileSketch();
    sketch.add(
      SketchElement('line', [const Point(0.0, 0.0), const Point(10.0, 0.0)]),
    );
    sketch.add(
      SketchElement('line', [const Point(-5.0, 0.0), const Point(15.0, 0.0)]),
    );
    final paths = sketch.compile();
    expect(paths.length, 3);
    expect(paths[1], [const Point(-5.0, 0.0), const Point(0.0, 0.0)]);
    expect(paths[2], [const Point(10.0, 0.0), const Point(15.0, 0.0)]);
    final config = FigureConfig({
      'shape': 'CUSTOM',
      'scan_paths': FigureConfig.encodePaths(paths),
    });
    expect(config.strokes.length, 3);
  });
  test('rotation, scaling, selection and undo preserve geometry', () {
    final sketch = MobileSketch();
    sketch.add(
      SketchElement('rectangle', [
        const Point(-10.0, -5.0),
        const Point(10.0, -5.0),
        const Point(10.0, 5.0),
        const Point(-10.0, 5.0),
      ]),
    );
    sketch.checkpoint();
    sketch.selection!.rotate(90);
    expect(
      sketch.selection!.bounds.$3 - sketch.selection!.bounds.$1,
      closeTo(10, 1e-9),
    );
    sketch.undo();
    expect(sketch.elements.single.bounds.$3, 10);
    sketch.scaleAll(2);
    expect(sketch.elements.single.bounds.$3, 20);
    expect(sketch.hit(const Point(20.0, 0.0), 1), 0);
  });
  test('curve handles and saved source geometry remain editable', () {
    final sketch = MobileSketch()
      ..add(
        SketchElement('curve', [
          const Point(-10.0, 0.0),
          const Point(0.0, 20.0),
          const Point(10.0, 0.0),
        ]),
      );
    final config = FigureConfig({
      'shape': 'CUSTOM',
      'scan_paths': FigureConfig.encodePaths(sketch.compile()),
    });
    final document = FigureLibrary.readDocument(
      FigureLibrary.document(config, geometry: sketch.elements),
    );
    expect(document.geometry!.single.kind, 'curve');
    expect(document.geometry!.single.points.length, 3);
    expect(document.config.same(config), true);
  });
  test(
    'freehand simplification keeps endpoints and capacity failure is explicit',
    () {
      final path = List.generate(100, (i) => Point(i * .1, 0.0));
      expect(simplify(path), [path.first, path.last]);
      final sketch = MobileSketch();
      for (var i = 0; i < 6; i++) {
        sketch.add(
          SketchElement('circle', [
            Point(i * 20.0, 0.0),
            Point(i * 20.0 + 4, 0.0),
          ]),
        );
      }
      expect(sketch.compile, throwsFormatException);
    },
  );
  test(
    'local library survives restart and stores curve controls separately',
    () async {
      SharedPreferences.setMockInitialValues({});
      final library = FigureLibrary();
      await library.load();
      final sketch = MobileSketch()
        ..add(
          SketchElement('circle', [
            const Point(0.0, 0.0),
            const Point(10.0, 0.0),
          ]),
        );
      final config = FigureConfig({
        'shape': 'CUSTOM',
        'scan_paths': FigureConfig.encodePaths(sketch.compile()),
      });
      await library.save('我的圆', config, geometry: sketch.elements);
      final restored = FigureLibrary();
      await restored.load();
      expect(restored.figures.single.name, '我的圆');
      expect(restored.figures.single.geometry!.single.kind, 'circle');
      await restored.remove(restored.figures.single.id);
      expect(restored.figures, isEmpty);
    },
  );
  test(
    'resaving an unedited legacy import keeps its path and retrace semantics',
    () async {
      SharedPreferences.setMockInitialValues({});
      final config = FigureConfig({
        'shape': 'CUSTOM',
        'path_xy_um': '-15123:12000,23456:-9000',
        'path_closed': 0,
      });
      final sketch = MobileSketch()..fromConfig(config);
      final exported = FigureLibrary.readDocument(
        FigureLibrary.document(config, geometry: sketch.elements),
      );
      expect(exported.config.same(config), isTrue);
      expect(exported.geometry, isNull);
      final library = FigureLibrary();
      await library.load();
      await library.save('旧图形', config, geometry: sketch.elements);
      final restored = FigureLibrary();
      await restored.load();
      expect(restored.figures.single.config.same(config), isTrue);
      expect(restored.figures.single.geometry, isNull);
    },
  );
}
