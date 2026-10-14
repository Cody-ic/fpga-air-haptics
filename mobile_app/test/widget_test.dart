import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:touchsee_mobile/main.dart';
import 'package:touchsee_mobile/canvas.dart';
import 'package:touchsee_mobile/session.dart';
import 'package:touchsee_mobile/sketch.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });
  Future<void> start(
    WidgetTester tester, {
    Size size = const Size(390, 844),
  }) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = size;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.runAsync(() async {
      final font = File('C:/Windows/Fonts/msyh.ttc');
      if (font.existsSync()) {
        final loader = FontLoader('Roboto')
          ..addFont(
            Future.value(ByteData.sublistView(await font.readAsBytes())),
          );
        await loader.load();
      }
      final icons = File(
        'build/unit_test_assets/fonts/MaterialIcons-Regular.otf',
      );
      if (icons.existsSync()) {
        await (FontLoader('MaterialIcons')..addFont(
              Future.value(ByteData.sublistView(await icons.readAsBytes())),
            ))
            .load();
      }
    });
    await tester.pumpWidget(
      const RepaintBoundary(key: ValueKey('screen'), child: TouchSeeApp()),
    );
    await tester.pumpAndSettle();
  }

  Future<void> show(WidgetTester tester, Finder finder) async {
    await tester.ensureVisible(finder);
    await tester.pump(const Duration(milliseconds: 250));
  }

  Future<void> capture(WidgetTester tester, String name) async {
    await tester.pump(const Duration(milliseconds: 250));
    final boundary = tester.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('screen')),
    );
    await tester.runAsync(() async {
      final image = await boundary.toImage(pixelRatio: 2);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final folder = Directory('.runtime/screenshots');
      await folder.create(recursive: true);
      await File(
        '${folder.path}/$name.png',
      ).writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  testWidgets(
    'phone connects Demo, sends, receives and plays; pre-send stays disabled',
    (tester) async {
      await start(tester);
      expect(find.text('让图形触手可及'), findsOneWidget);
      await capture(tester, '01-figure');
      await tester.tap(find.text('播放').last);
      await tester.pumpAndSettle();
      await show(tester, find.byKey(const ValueKey('play-button')));
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('play-button')))
            .onPressed,
        isNull,
      );
      await tester.tap(find.text('设备').last);
      await tester.pumpAndSettle();
      await capture(tester, '02-device');
      await tester.tap(find.byKey(const ValueKey('connect-demo')));
      await tester.pump(const Duration(milliseconds: 600));
      await tester.tap(find.text('三角形'));
      await tester.pump();
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('send-figure')),
        350,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.byKey(const ValueKey('send-figure')));
      await tester.pump(const Duration(milliseconds: 600));
      await show(tester, find.byKey(const ValueKey('play-button')));
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('play-button')))
            .onPressed,
        isNotNull,
      );
      await tester.tap(find.byKey(const ValueKey('play-button')));
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.text('正在播放'), findsWidgets);
      final canvas = tester.widget<PalmCanvas>(
        find.byKey(const ValueKey('playback-canvas')),
      );
      expect(canvas.focus, isNotNull);
      expect(canvas.playing, isTrue);
      await tester.drag(find.byType(ListView).first, const Offset(0, 450));
      await tester.pump();
      await capture(tester, '03-playback');
      await tester.tap(find.byTooltip('停止输出'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('设备').last);
      await tester.pump();
      await tester.tap(find.text('断开连接'));
      // Font loading and image capture use the real event loop. Drain any
      // StreamSubscription cancellation future created in that zone too.
      await tester.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 30)),
      );
      for (var i = 0; i < 10; i++) {
        await tester.pump(const Duration(milliseconds: 100));
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 1)),
        );
      }
      final session = tester
          .widgetList<AnimatedBuilder>(find.byType(AnimatedBuilder))
          .map((w) => w.animation)
          .whereType<DeviceSession>()
          .first;
      expect(
        session.connected,
        isFalse,
        reason: '${session.message}\n${session.logs.join('\n')}',
      );
      expect(find.text('未连接'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.pump();
    },
  );
  testWidgets('touch tools draw, select, rotate, size and remove a rectangle', (
    tester,
  ) async {
    await start(tester, size: const Size(360, 800));
    await tester.tap(find.text('自己绘制'));
    await tester.pump();
    await tester.tap(find.text('矩形'));
    await tester.pump();
    final canvas = find.byKey(const ValueKey('draft-canvas'));
    await show(tester, canvas);
    final center = tester.getCenter(canvas);
    await tester.dragFrom(center - const Offset(40, 30), const Offset(80, 60));
    await tester.pump();
    var drawing = tester.widget<PalmCanvas>(canvas).sketch!;
    expect(drawing.elements.length, 1);
    expect(drawing.elements.single.kind, 'rectangle');
    await show(tester, find.text('旋转'));
    await tester.tap(find.text('旋转'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '30');
    await tester.tap(find.text('应用'));
    await tester.pumpAndSettle();
    expect(
      drawing.elements.single.points[0].y,
      isNot(equals(drawing.elements.single.points[1].y)),
    );
    await tester.tap(find.text('尺寸'));
    await tester.pumpAndSettle();
    expect(find.text('外接尺寸（沿画布坐标轴）'), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, '35');
    await tester.enterText(find.byType(TextField).last, '30');
    await tester.tap(find.text('应用'));
    await tester.pumpAndSettle();
    expect(
      drawing.elements.single.bounds.$3 - drawing.elements.single.bounds.$1,
      closeTo(35, 1e-6),
    );
    await show(tester, canvas);
    await capture(tester, '04-sketch');
    await show(tester, find.byTooltip('删除选中线条'));
    await tester.tap(find.byTooltip('删除选中线条'));
    await tester.pump();
    expect(drawing.elements, isEmpty);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
  });
  testWidgets('tablet and large text layouts fit and BLE settings validate', (
    tester,
  ) async {
    await start(tester, size: const Size(800, 1000));
    tester.platformDispatcher.textScaleFactorTestValue = 1.5;
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpAndSettle();
    await tester.tap(find.text('设备').last);
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('连接设置'),
      250,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('连接设置'));
    await tester.pumpAndSettle();
    expect(find.text('蓝牙连接设置'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
  });
  testWidgets(
    'pinching does not add geometry; selected rectangle center drags',
    (tester) async {
      await start(tester);
      await tester.tap(find.text('自己绘制'));
      await tester.pump();
      await tester.tap(find.text('矩形'));
      await tester.pump();
      final finder = find.byKey(const ValueKey('draft-canvas'));
      await show(tester, finder);
      final center = tester.getCenter(finder);
      final first = await tester.startGesture(
        center - const Offset(25, 0),
        pointer: 1,
      );
      final second = await tester.startGesture(
        center + const Offset(25, 0),
        pointer: 2,
      );
      await first.moveBy(const Offset(-20, 0));
      await second.moveBy(const Offset(20, 0));
      await first.up();
      await second.up();
      await tester.pump();
      final sketch = tester.widget<PalmCanvas>(finder).sketch!;
      expect(sketch.elements, isEmpty);
      await tester.tap(find.byTooltip('恢复掌面视图'));
      await tester.pump();
      await tester.dragFrom(
        center - const Offset(40, 30),
        const Offset(80, 60),
      );
      await tester.pump();
      await show(tester, find.text('选择'));
      await tester.tap(find.text('选择'));
      await tester.pump();
      await show(tester, finder);
      final painter = tester
          .widgetList<CustomPaint>(
            find.descendant(of: finder, matching: find.byType(CustomPaint)),
          )
          .map((w) => w.painter)
          .whereType<PalmPainter>()
          .first;
      expect(tester.widget<PalmCanvas>(finder).tool, DrawTool.select);
      final old = sketch.selection!.center;
      final size = tester.getSize(finder);
      final s = size.width / 120 * painter.zoom;
      final handle =
          tester.getTopLeft(finder) +
          Offset(
            size.width / 2 + old.x * s,
            size.height / 2 - 10 * s - old.y * s,
          ) +
          painter.pan;
      await tester.dragFrom(handle, const Offset(20, 0));
      await tester.pump();
      expect(sketch.selection!.center.x, greaterThan(old.x));
      expect(sketch.elements.length, 1);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.pump();
    },
  );
}
