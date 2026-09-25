import 'dart:async';
import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'canvas.dart';
import 'library.dart';
import 'model.dart';
import 'session.dart';
import 'sketch.dart';
import 'transport.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const TouchSeeApp());
}

class TouchSeeApp extends StatelessWidget {
  const TouchSeeApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: '触见',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: const Color(0xfff3f6fa),
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xff2463c5),
        brightness: Brightness.light,
      ),
      cardTheme: const CardThemeData(
        elevation: 0,
        color: Colors.white,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(24)),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 50),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
          ),
        ),
      ),
      inputDecorationTheme: const InputDecorationTheme(
        border: OutlineInputBorder(),
        isDense: true,
      ),
      navigationBarTheme: const NavigationBarThemeData(
        backgroundColor: Colors.white,
      ),
    ),
    home: const Workbench(),
  );
}

class Workbench extends StatefulWidget {
  const Workbench({super.key});
  @override
  State<Workbench> createState() => _WorkbenchState();
}

class _WorkbenchState extends State<Workbench> with WidgetsBindingObserver {
  final session = DeviceSession();
  final library = FigureLibrary();
  final sketch = MobileSketch();
  FigureConfig draft = FigureConfig();
  DrawTool tool = DrawTool.line;
  bool drawing = false, storeReady = false, scanning = false, debug = false;
  int tab = 0, demoSize = 4, _scanEpoch = 0;
  String? drawingError, selectedPeer;
  String scanMessage = '扫描附近设备，选择你们的蓝牙模块。';
  final Map<String, ScanResult> peers = {};
  StreamSubscription<List<ScanResult>>? _scanSubscription;
  FigureConfig? get validDraft =>
      drawing && (sketch.elements.isEmpty || drawingError != null)
      ? null
      : draft;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    library
        .load()
        .then((_) {
          if (mounted) setState(() => storeReady = true);
        })
        .catchError((Object e) {
          if (mounted) notice('图形库读取失败：$e');
        });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if ([
      AppLifecycleState.paused,
      AppLifecycleState.detached,
      AppLifecycleState.hidden,
    ].contains(state)) {
      unawaited(cancelScan());
      unawaited(session.disconnect(reason: '应用已离开前台，请重新连接后确认设备状态。'));
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    unawaited(cancelScan());
    session.dispose();
    super.dispose();
  }

  void notice(String message) {
    if (mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
    }
  }

  Future<void> guarded(Future<void> Function() action) async {
    try {
      await action();
    } catch (e) {
      notice('$e');
    }
  }

  Widget card(List<Widget> children) => Card(
    child: Padding(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: children,
      ),
    ),
  );
  Widget gap([double size = 14]) => SizedBox(height: size);
  Widget heading(String text, {String? subtitle}) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        text,
        style: Theme.of(
          context,
        ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
      ),
      if (subtitle != null) ...[
        gap(5),
        Text(
          subtitle,
          style: TextStyle(
            color: Colors.blueGrey.shade600,
            fontSize: 13,
            height: 1.5,
          ),
        ),
      ],
    ],
  );

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: session,
    builder: (context, _) {
      final connected = session.ready;
      return Scaffold(
        appBar: AppBar(
          backgroundColor: const Color(0xfff3f6fa),
          title: Row(
            children: [
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: const Color(0xff2463c5),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Icon(
                  Icons.touch_app_rounded,
                  color: Colors.white,
                  size: 23,
                ),
              ),
              const SizedBox(width: 10),
              const Text('触见', style: TextStyle(fontWeight: FontWeight.w800)),
            ],
          ),
          actions: [
            TextButton.icon(
              onPressed: () => setState(() => tab = 2),
              icon: Icon(connected ? Icons.link : Icons.link_off, size: 17),
              label: Text(
                connected ? (session.simulated ? '演示中' : '已连接') : '未连接',
              ),
            ),
            IconButton(
              tooltip: '停止输出',
              onPressed: session.ready ? () => session.command('STOP') : null,
              icon: const Icon(Icons.stop_circle_outlined),
              color: const Color(0xffb02d40),
            ),
          ],
        ),
        body: SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 680),
              child: ListView(
                key: ValueKey('tab-$tab'),
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
                children: switch (tab) {
                  0 => figurePage(),
                  1 => playPage(),
                  2 => devicePage(),
                  _ => libraryPage(),
                },
              ),
            ),
          ),
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: tab,
          onDestinationSelected: (value) => setState(() => tab = value),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.gesture), label: '图形'),
            NavigationDestination(
              icon: Icon(Icons.play_circle_outline),
              label: '播放',
            ),
            NavigationDestination(icon: Icon(Icons.bluetooth), label: '设备'),
            NavigationDestination(
              icon: Icon(Icons.bookmarks_outlined),
              label: '图形库',
            ),
          ],
        ),
      );
    },
  );

  List<Widget> figurePage() => [
    heading('让图形触手可及', subtitle: '选择基本图案，或在掌面上画出你的想法。'),
    gap(),
    card([
      Wrap(
        spacing: 8,
        runSpacing: 4,
        children: [
          for (final shape in shapeNames.entries.where(
            (e) => e.key != 'CUSTOM',
          ))
            ChoiceChip(
              label: Text(shape.value),
              selected: !drawing && draft.shape == shape.key,
              onSelected: (_) => setState(() {
                drawing = false;
                drawingError = null;
                draft = draft.change({'shape': shape.key});
              }),
            ),
          ChoiceChip(
            label: const Text('自己绘制'),
            selected: drawing,
            onSelected: (_) => setState(() {
              drawing = true;
              syncSketch();
            }),
          ),
        ],
      ),
      gap(),
      if (drawing) ...[
        Wrap(
          spacing: 5,
          runSpacing: 4,
          children: [
            for (final entry in toolNames.entries)
              ChoiceChip(
                label: Text(entry.value),
                selected: tool == entry.key,
                onSelected: (_) => setState(() => tool = entry.key),
              ),
          ],
        ),
        gap(8),
        Text(
          tool == DrawTool.select
              ? '点选线条后拖动，圆点手柄可以调整端点或弧度。'
              : '在画布上拖动绘制。双指缩放和移动视图。',
          style: const TextStyle(fontSize: 12, color: Colors.blueGrey),
        ),
        gap(8),
      ],
      PalmCanvas(
        key: const ValueKey('draft-canvas'),
        config: draft,
        sketch: drawing ? sketch : null,
        tool: tool,
        onChanged: syncSketch,
        onSelectionChanged: () => setState(() {}),
        height: MediaQuery.sizeOf(context).height < 700 ? 280 : 340,
      ),
      if (drawing) ...[
        gap(8),
        Wrap(
          spacing: 4,
          children: [
            IconButton(
              tooltip: '撤销',
              onPressed: sketch.canUndo
                  ? () {
                      sketch.undo();
                      syncSketch();
                    }
                  : null,
              icon: const Icon(Icons.undo),
            ),
            IconButton(
              tooltip: '删除选中线条',
              onPressed: sketch.selection != null
                  ? () {
                      sketch.remove();
                      syncSketch();
                    }
                  : null,
              icon: const Icon(Icons.delete_outline),
            ),
            TextButton(
              onPressed: sketch.selection != null ? editSize : null,
              child: const Text('尺寸'),
            ),
            TextButton(
              onPressed: sketch.selection != null ? rotateSelection : null,
              child: const Text('旋转'),
            ),
            TextButton(
              onPressed: sketch.elements.isNotEmpty ? scaleDrawing : null,
              child: const Text('整体缩放'),
            ),
            TextButton(
              onPressed: sketch.elements.length > 1 ? orderDrawing : null,
              child: const Text('顺序'),
            ),
            TextButton(
              onPressed: sketch.elements.isNotEmpty ? clearDrawing : null,
              child: const Text('清空'),
            ),
          ],
        ),
        Text(
          drawingError ?? '公共线按顺序只呈现一次；轮廓间关闭输出后切换。',
          style: TextStyle(
            fontSize: 12,
            color: drawingError != null ? Colors.red.shade700 : Colors.blueGrey,
          ),
        ),
      ],
      if (validDraft != null && outsidePalm(validDraft!))
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: Text(
            '图形明显超出手掌参考范围，可缩小或移动。不同人的手掌大小不同，仍可发送。',
            style: TextStyle(fontSize: 12, color: Colors.orange.shade900),
          ),
        ),
    ]),
    gap(),
    card([
      if (!drawing)
        numberSlider(
          '图形半径 / 半边长',
          draft.value('radius_um') / 1000,
          1,
          60,
          'mm',
          (v) => change({'radius_um': (v * 1000).round()}),
        ),
      numberSlider(
        '显示高度',
        draft.value('z_um') / 1000,
        20,
        300,
        'mm',
        (v) => change({'z_um': (v * 1000).round()}),
      ),
      numberSlider(
        '每秒播放次数',
        draft.value('repeat_millihz') / 1000,
        .1,
        5,
        '次',
        (v) => change({'repeat_millihz': (v * 1000).round()}),
      ),
      TextButton.icon(
        onPressed: editPosition,
        icon: const Icon(Icons.open_with, size: 18),
        label: Text(
          '位置：x ${draft.value('cx_um') / 1000} · y ${draft.value('cy_um') / 1000} mm',
        ),
      ),
    ]),
    gap(),
    FilledButton.icon(
      key: const ValueKey('send-figure'),
      onPressed: session.canConfigure && validDraft != null
          ? () async {
              await session.apply(validDraft!);
              if (mounted) setState(() => tab = 1);
            }
          : null,
      icon: const Icon(Icons.send_outlined),
      label: Text(session.connected ? '发送到设备' : '请先连接设备'),
    ),
    gap(8),
    Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            onPressed: storeReady && validDraft != null ? saveFigure : null,
            icon: const Icon(Icons.bookmark_add_outlined),
            label: const Text('保存图形'),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: OutlinedButton.icon(
            onPressed: validDraft != null ? preview : null,
            icon: const Icon(Icons.pan_tool_outlined),
            label: const Text('手掌预览'),
          ),
        ),
      ],
    ),
  ];

  Widget numberSlider(
    String name,
    double value,
    double minValue,
    double maxValue,
    String unit,
    ValueChanged<double> changed,
  ) => Column(
    children: [
      Row(
        children: [
          Expanded(child: Text(name)),
          Text(
            '${value.toStringAsFixed(value % 1 == 0 ? 0 : 1)} $unit',
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        ],
      ),
      Slider(
        value: value.clamp(minValue, maxValue),
        min: minValue,
        max: maxValue,
        divisions: 50,
        label: '${value.toStringAsFixed(1)} $unit',
        onChanged: changed,
      ),
    ],
  );
  void change(Map<String, Object?> values) {
    try {
      setState(() => draft = draft.change(values));
    } catch (e) {
      notice('$e');
    }
  }

  void syncSketch() {
    try {
      drawingError = null;
      if (sketch.elements.isNotEmpty) {
        draft = draft.change({
          'shape': 'CUSTOM',
          'path_xy_um': 'NONE',
          'scan_paths': FigureConfig.encodePaths(sketch.compile()),
        });
      }
    } catch (e) {
      drawingError = e.toString().replaceFirst('FormatException: ', '');
    }
    if (mounted) setState(() {});
  }

  Future<T?> inputDialog<T>(WidgetBuilder builder) async {
    final route = DialogRoute<T>(context: context, builder: builder);
    final value = await Navigator.of(context, rootNavigator: true).push(route);
    // Controllers remain alive until the reverse animation removes the route.
    await route.completed;
    return value;
  }

  Future<List<double>?> numbers(
    String title,
    List<String> labels,
    List<double> values,
  ) async {
    final controls = values
        .map((v) => TextEditingController(text: v.toStringAsFixed(2)))
        .toList();
    final result = await inputDialog<List<double>>(
      (ctx) => AlertDialog(
        title: Text(title),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (var i = 0; i < labels.length; i++)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: TextField(
                    controller: controls[i],
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                      signed: true,
                    ),
                    decoration: InputDecoration(labelText: labels[i]),
                  ),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () {
              final numbers = controls
                  .map((c) => double.tryParse(c.text.trim()))
                  .toList();
              if (numbers.any((n) => n == null || !n.isFinite)) {
                notice('请输入有效的数值');
                return;
              }
              Navigator.pop(ctx, numbers.cast<double>());
            },
            child: const Text('应用'),
          ),
        ],
      ),
    );
    for (final c in controls) {
      c.dispose();
    }
    return result;
  }

  Future<void> editPosition() async {
    final value = await numbers(
      '图形位置',
      ['水平偏移 / mm', '垂直偏移 / mm'],
      [draft.value('cx_um') / 1000, draft.value('cy_um') / 1000],
    );
    if (value != null) {
      change({
        'cx_um': (value[0] * 1000).round(),
        'cy_um': (value[1] * 1000).round(),
      });
    }
  }

  Future<void> editSize() async {
    final e = sketch.selection!;
    final b = e.bounds;
    final value = await numbers(
      e.kind == 'circle' ? '圆的直径' : '外接尺寸（沿画布坐标轴）',
      e.kind == 'circle' ? ['直径 / mm'] : ['宽 / mm', '高 / mm'],
      e.kind == 'circle'
          ? [b.$3 - b.$1]
          : [max(.1, b.$3 - b.$1), max(.1, b.$4 - b.$2)],
    );
    if (value != null) {
      try {
        sketch.checkpoint();
        e.setSize(value[0], value.length > 1 ? value[1] : value[0]);
        syncSketch();
      } catch (error) {
        notice('$error');
      }
    }
  }

  Future<void> rotateSelection() async {
    final value = await numbers('旋转选中图形', ['逆时针角度 / °'], [15]);
    if (value != null) {
      sketch.checkpoint();
      sketch.selection?.rotate(value[0] % 360);
      syncSketch();
    }
  }

  Future<void> scaleDrawing() async {
    final value = await numbers('围绕图形原点整体缩放', ['尺寸倍数'], [1]);
    if (value != null) {
      try {
        sketch.scaleAll(value[0]);
        syncSketch();
      } catch (error) {
        notice('$error');
      }
    }
  }

  Future<void> clearDrawing() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清空当前草图？'),
        content: const Text('清空后仍可以撤销。已保存的图形不受影响。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清空'),
          ),
        ],
      ),
    );
    if (yes == true) {
      sketch.clear();
      syncSketch();
    }
  }

  Future<void> orderDrawing() async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, refresh) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              '呈现顺序',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const Text('也可以保留绘制顺序；公共线由先出现的轮廓呈现。'),
            for (var i = 0; i < sketch.elements.length; i++)
              ListTile(
                title: Text(
                  '${i + 1}. ${elementName(sketch.elements[i].kind)}',
                ),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      tooltip: '上移',
                      onPressed: i > 0
                          ? () {
                              sketch.reorder(i, i - 1);
                              refresh(() {});
                              syncSketch();
                            }
                          : null,
                      icon: const Icon(Icons.arrow_upward),
                    ),
                    IconButton(
                      tooltip: '下移',
                      onPressed: i < sketch.elements.length - 1
                          ? () {
                              sketch.reorder(i, i + 1);
                              refresh(() {});
                              syncSketch();
                            }
                          : null,
                      icon: const Icon(Icons.arrow_downward),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  String elementName(String kind) =>
      {
        'line': '直线',
        'rectangle': '矩形',
        'circle': '圆',
        'curve': '曲线',
        'pen': '路径',
      }[kind] ??
      kind;
  Future<void> preview() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => Padding(
        padding: const EdgeInsets.fromLTRB(18, 0, 18, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              '待发送图形',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            gap(10),
            PalmCanvas(
              config: validDraft,
              height: MediaQuery.sizeOf(context).height * .5,
            ),
            gap(10),
            const Text(
              '这里是图形预览。设备返回的位置会显示在“播放”页面。',
              style: TextStyle(color: Colors.blueGrey),
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> playPage() {
    final state = session.snapshot, fresh = session.fresh;
    final simulated = state?.simulated ?? session.simulated;
    final status = state == null
        ? '等待发送图形'
        : !fresh
        ? '设备状态未知'
        : {
            'IDLE': '已停止',
            'RUNNING': '正在播放',
            'PAUSED': '已暂停',
            'FAULT': '设备故障',
          }[state.state]!;
    final position =
        fresh && state != null && ['RUNNING', 'PAUSED'].contains(state.state)
        ? state.focus
        : null;
    return [
      heading('感受图形的轨迹', subtitle: '画面根据设备回传更新。'),
      gap(),
      card([
        Row(
          children: [
            Icon(
              simulated ? Icons.science_outlined : Icons.sensors,
              color: const Color(0xff087e8b),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                state == null && !session.connected
                    ? '尚未连接设备'
                    : simulated
                    ? 'Demo · 模拟设备反馈'
                    : 'BLE · 设备回传',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
          ],
        ),
        gap(),
        Text(
          status,
          style: const TextStyle(fontSize: 25, fontWeight: FontWeight.w700),
        ),
        if (session.message != status) ...[
          gap(6),
          Semantics(
            liveRegion: true,
            child: Text(
              session.message,
              style: const TextStyle(color: Colors.blueGrey, height: 1.5),
            ),
          ),
        ],
        gap(),
        PalmCanvas(
          key: const ValueKey('playback-canvas'),
          config: state?.config,
          focus: position,
          playing: state?.output ?? false,
          blank:
              state != null &&
              (!state.scanOn || state.state == 'RUNNING' && !state.output),
          height: 350,
        ),
        gap(),
        if (state != null && fresh)
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: [
              Chip(label: Text(shapeNames[state.config.shape]!)),
              Chip(label: Text('${state.array.rows} × ${state.array.cols} 阵列')),
              Chip(label: Text(state.mode == 'LOCAL' ? '设备按键控制' : '手机控制')),
            ],
          ),
        gap(8),
        Row(
          children: [
            Expanded(
              child: FilledButton.icon(
                key: const ValueKey('play-button'),
                onPressed: validDraft != null && session.canPlay(validDraft!)
                    ? () => session.play(validDraft!)
                    : null,
                icon: const Icon(Icons.play_arrow),
                label: Text(state?.state == 'PAUSED' ? '继续' : '播放'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: session.canPause
                    ? () => session.command('PAUSE')
                    : null,
                icon: const Icon(Icons.pause),
                label: const Text('暂停'),
              ),
            ),
          ],
        ),
        gap(10),
        OutlinedButton.icon(
          onPressed: session.ready ? () => session.command('STOP') : null,
          icon: const Icon(Icons.stop),
          label: const Text('停止输出'),
        ),
      ]),
      gap(),
      card([
        const Text('播放前先发送图形', style: TextStyle(fontWeight: FontWeight.w700)),
        gap(6),
        const Text(
          '修改图形或重新连接后，需要重新发送并等待设备确认。手掌是放置参考，未进行手部位置检测。',
          style: TextStyle(color: Colors.blueGrey, height: 1.6),
        ),
        if (simulated) ...[
          gap(8),
          const Text(
            'Demo 以慢速演示路径，不代表真实触觉效果。',
            style: TextStyle(color: Color(0xff966414)),
          ),
        ],
        TextButton(
          onPressed: () => setState(() => tab = 0),
          child: const Text('返回图形编辑'),
        ),
      ]),
    ];
  }

  List<Widget> devicePage() => [
    heading('连接你的设备', subtitle: '没有硬件也可以完整体验发送、确认与播放。'),
    gap(),
    if (session.connected || session.closing)
      card([
        Text(
          session.ready
              ? (session.simulated ? '演示设备已连接' : session.deviceName)
              : '正在连接／断开…',
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
        gap(8),
        Semantics(liveRegion: true, child: Text(session.message)),
        if (session.array != null) ...[
          gap(8),
          Text(
            '实际阵列：${session.array!.rows} × ${session.array!.cols} · ${session.array!.count} 路',
          ),
        ],
        gap(),
        OutlinedButton(
          onPressed: session.closing ? null : () => session.disconnect(),
          child: const Text('断开连接'),
        ),
        if (session.fresh) ...[
          gap(8),
          Wrap(
            spacing: 8,
            children: [
              OutlinedButton(
                onPressed: !session.busy && session.snapshot!.state == 'IDLE'
                    ? () => session.command('MODE', {'value': 'REMOTE'})
                    : null,
                child: const Text('手机控制'),
              ),
              OutlinedButton(
                onPressed: !session.busy && session.snapshot!.state == 'IDLE'
                    ? () => session.command('MODE', {'value': 'LOCAL'})
                    : null,
                child: const Text('设备按键控制'),
              ),
            ],
          ),
        ],
      ])
    else ...[
      card([
        const Row(
          children: [
            Icon(Icons.science_outlined, color: Color(0xff087e8b)),
            SizedBox(width: 10),
            Text(
              '先体验一下',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
            ),
          ],
        ),
        gap(8),
        const Text(
          '演示设备会接收图形并返回模拟状态，无需蓝牙。',
          style: TextStyle(color: Colors.blueGrey),
        ),
        gap(8),
        Wrap(
          spacing: 8,
          children: [
            for (final size in [4, 8, 12, 16])
              ChoiceChip(
                label: Text('$size × $size'),
                selected: demoSize == size,
                onSelected: scanning
                    ? null
                    : (_) => setState(() => demoSize = size),
              ),
          ],
        ),
        gap(8),
        FilledButton.tonalIcon(
          key: const ValueKey('connect-demo'),
          onPressed: scanning
              ? null
              : () async {
                  await session.connect(
                    DemoTransport(
                      array: ArraySpec(rows: demoSize, cols: demoSize),
                    ),
                  );
                  if (mounted) setState(() => tab = 0);
                },
          icon: const Icon(Icons.play_circle_outline),
          label: const Text('连接演示设备'),
        ),
      ]),
      gap(),
      card([
        const Row(
          children: [
            Icon(Icons.bluetooth, color: Color(0xff2463c5)),
            SizedBox(width: 10),
            Text(
              'BLE 蓝牙',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
            ),
          ],
        ),
        gap(8),
        Text(
          scanMessage,
          style: const TextStyle(color: Colors.blueGrey, height: 1.5),
        ),
        gap(),
        FilledButton.icon(
          onPressed: scanning ? cancelScan : scan,
          icon: Icon(scanning ? Icons.close : Icons.search),
          label: Text(scanning ? '取消扫描' : '扫描附近设备'),
        ),
        if (scanning) ...[gap(), const LinearProgressIndicator()],
        RadioGroup<String>(
          groupValue: selectedPeer,
          onChanged: (value) {
            if (!scanning) setState(() => selectedPeer = value);
          },
          child: Column(
            children: [
              for (final entry in peers.entries)
                RadioListTile<String>(
                  title: Text(
                    entry.value.advertisementData.advName.isEmpty
                        ? '未命名设备'
                        : entry.value.advertisementData.advName,
                  ),
                  subtitle: Text('${entry.key} · ${entry.value.rssi} dBm'),
                  value: entry.key,
                  enabled: !scanning,
                ),
            ],
          ),
        ),
        if (peers.isNotEmpty) ...[
          gap(8),
          FilledButton(
            onPressed: selectedPeer != null && !scanning
                ? () async {
                    final device = peers[selectedPeer!]!.device;
                    await session.connect(
                      BleTransport(device, library.profile),
                    );
                  }
                : null,
            child: const Text('连接所选设备'),
          ),
        ],
        TextButton(
          onPressed: storeReady && !scanning ? bleSettings : null,
          child: const Text('连接设置'),
        ),
        TextButton(
          onPressed: () => guarded(() => FlutterBluePlus.turnOn()),
          child: const Text('打开手机蓝牙'),
        ),
      ]),
    ],
    gap(),
    if (!session.connected && session.message != '选择设备，或先试试演示模式。')
      Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Text(
          session.message,
          style: const TextStyle(color: Colors.blueGrey),
        ),
      ),
    card([
      const Text('使用说明', style: TextStyle(fontWeight: FontWeight.w700)),
      gap(8),
      const Text(
        '连接 → 发送图形 → 等待确认 → 播放。\n请保持应用在前台；切到后台会断开连接，重新进入后需再次确认图形。',
        style: TextStyle(color: Colors.blueGrey, height: 1.7),
      ),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('调试信息'),
        value: debug,
        onChanged: (value) => setState(() => debug = value),
      ),
      if (debug) ...[
        const Text(
          'HAP3 为待定协议草案。模块与固件需配套；数字回传不等于超声或触觉实测。',
          style: TextStyle(fontSize: 12, color: Colors.blueGrey),
        ),
        gap(8),
        if (session.snapshot != null)
          SelectableText(
            '版本 ${session.snapshot!.revision} · 快照 ${session.snapshot!.sample}\n'
            '相位 ${session.snapshot!.phases.join(', ')}',
            style: const TextStyle(fontSize: 12),
          ),
        TextButton(onPressed: communicationLog, child: const Text('查看通信日志')),
      ],
      TextButton(
        onPressed: () => showLicensePage(
          context: context,
          applicationName: '触见',
          applicationVersion: '0.1.0',
        ),
        child: const Text('开源许可'),
      ),
    ]),
  ];

  Future<void> scan() async {
    final epoch = ++_scanEpoch;
    setState(() {
      scanning = true;
      selectedPeer = null;
      peers.clear();
      scanMessage = '正在查找设备，请允许附近设备权限。';
    });
    try {
      _scanSubscription = FlutterBluePlus.onScanResults.listen(
        (results) {
          if (!mounted || epoch != _scanEpoch) return;
          setState(() {
            for (final r in results) {
              peers[r.device.remoteId.str] = r;
            }
          });
        },
        onError: (Object error) {
          if (mounted && epoch == _scanEpoch) {
            setState(() => scanMessage = '扫描失败：$error');
          }
        },
      );
      await FlutterBluePlus.startScan(
        timeout: const Duration(seconds: 5),
        androidCheckLocationServices: false,
      );
      if (epoch != _scanEpoch) {
        await FlutterBluePlus.stopScan();
        return;
      }
      await Future<void>.delayed(const Duration(seconds: 5));
      if (mounted && epoch == _scanEpoch) {
        setState(
          () => scanMessage = peers.isEmpty
              ? '未发现设备。检查蓝牙和模块供电；Android 11 及更早版本还需开启定位服务。'
              : '选择你们的模块，再点击连接。',
        );
      }
    } catch (e) {
      if (mounted && epoch == _scanEpoch) {
        setState(() => scanMessage = '无法扫描：请检查蓝牙及附近设备权限。详情：$e');
      }
    } finally {
      if (epoch == _scanEpoch) {
        await _scanSubscription?.cancel();
        if (mounted) setState(() => scanning = false);
      }
    }
  }

  Future<void> cancelScan() async {
    ++_scanEpoch;
    if (scanning) {
      try {
        await FlutterBluePlus.stopScan();
      } catch (_) {}
    }
    await _scanSubscription?.cancel();
    _scanSubscription = null;
    if (mounted) setState(() => scanning = false);
  }

  Future<void> bleSettings() async {
    final p = library.profile;
    final controllers = [
      p.service,
      p.tx,
      p.rx,
      '${p.packetBytes}',
    ].map((s) => TextEditingController(text: s)).toList();
    var mode = p.mode;
    final next = await inputDialog<BleProfile>(
      (ctx) => StatefulBuilder(
        builder: (ctx, refresh) => AlertDialog(
          title: const Text('蓝牙连接设置'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('默认 Nordic UART 服务。其他模块请按资料修改。设备指令暂用 HAP3 草案。'),
                gap(),
                for (var i = 0; i < 4; i++)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 7),
                    child: TextField(
                      controller: controllers[i],
                      decoration: InputDecoration(
                        labelText: [
                          '服务 UUID',
                          '手机发送通道 UUID',
                          '手机接收通道 UUID',
                          '每包字节上限（0 为自动）',
                        ][i],
                      ),
                    ),
                  ),
                DropdownButtonFormField<String>(
                  initialValue: mode,
                  items: const [
                    DropdownMenuItem(value: 'auto', child: Text('自动选择写入方式')),
                    DropdownMenuItem(value: 'response', child: Text('有应答写入')),
                    DropdownMenuItem(
                      value: 'without-response',
                      child: Text('无应答写入'),
                    ),
                  ],
                  onChanged: (value) => refresh(() => mode = value!),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () {
                try {
                  Navigator.pop(
                    ctx,
                    BleProfile(
                      service: controllers[0].text,
                      tx: controllers[1].text,
                      rx: controllers[2].text,
                      packetBytes: integer(controllers[3].text.trim()),
                      mode: mode,
                    ).validated(),
                  );
                } catch (e) {
                  notice('$e');
                }
              },
              child: const Text('保存'),
            ),
          ],
        ),
      ),
    );
    for (final c in controllers) {
      c.dispose();
    }
    if (next != null) await guarded(() => library.setProfile(next));
  }

  Future<void> communicationLog() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (ctx) => SizedBox(
        height: MediaQuery.sizeOf(context).height * .65,
        child: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            const Text(
              '最近通信记录',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            gap(),
            SelectableText(session.logs.join('\n')),
          ],
        ),
      ),
    );
  }

  List<Widget> libraryPage() => [
    heading('我的图形', subtitle: '保存在本机，也可与电脑交换图形路径文件。'),
    gap(),
    Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            onPressed: storeReady ? importFigure : null,
            icon: const Icon(Icons.file_open_outlined),
            label: const Text('导入文件'),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: OutlinedButton.icon(
            onPressed: validDraft != null
                ? () => guarded(() async {
                    if (await library.exportFile(
                      validDraft!,
                      geometry: drawing ? sketch.elements : null,
                    )) {
                      notice('已导出当前图形');
                    }
                  })
                : null,
            icon: const Icon(Icons.ios_share),
            label: const Text('导出当前图形'),
          ),
        ),
      ],
    ),
    gap(),
    if (library.figures.isEmpty)
      card([
        const Icon(Icons.bookmarks_outlined, size: 42, color: Colors.blueGrey),
        gap(),
        const Text(
          '还没有保存的图形',
          textAlign: TextAlign.center,
          style: TextStyle(fontWeight: FontWeight.w600),
        ),
        gap(8),
        const Text(
          '编辑完成后，点击“保存图形”。',
          textAlign: TextAlign.center,
          style: TextStyle(color: Colors.blueGrey),
        ),
      ]),
    for (final figure in library.figures) ...[
      card([
        Row(
          children: [
            Expanded(
              child: Text(
                figure.name,
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            IconButton(
              tooltip: '删除保存的图形',
              onPressed: () => deleteFigure(figure),
              icon: const Icon(Icons.delete_outline),
            ),
          ],
        ),
        Text(
          shapeNames[figure.config.shape]!,
          style: const TextStyle(color: Colors.blueGrey),
        ),
        gap(),
        FilledButton.tonal(
          onPressed: () async {
            if (await confirmReplace()) {
              loadFigure(figure.config, geometry: figure.geometry);
            }
          },
          child: const Text('打开图形'),
        ),
      ]),
      gap(),
    ],
  ];
  Future<bool> confirmReplace() async =>
      await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('替换当前编辑的图形？'),
          content: const Text('尚未保存的编辑会被替换。已保存的图形不受影响。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('打开'),
            ),
          ],
        ),
      ) ??
      false;
  void loadFigure(FigureConfig config, {List<SketchElement>? geometry}) {
    setState(() {
      draft = config;
      drawing = config.shape == 'CUSTOM';
      drawingError = null;
      sketch.fromConfig(config);
      if (geometry != null) {
        sketch.elements
          ..clear()
          ..addAll(geometry.map((e) => e.copy()));
      }
      session.invalidateDraftReceipt();
      tool = DrawTool.select;
      tab = 0;
    });
    notice('图形已打开，检查后重新发送到设备。');
  }

  Future<void> importFigure() async {
    await guarded(() async {
      final document = await library.importFile();
      if (document != null && mounted && await confirmReplace()) {
        loadFigure(document.config, geometry: document.geometry);
      }
    });
  }

  Future<void> saveFigure() async {
    final control = TextEditingController(
      text: '${shapeNames[draft.shape]} ${library.figures.length + 1}',
    );
    final name = await inputDialog<String>(
      (ctx) => AlertDialog(
        title: const Text('保存图形'),
        content: TextField(
          controller: control,
          maxLength: 40,
          decoration: const InputDecoration(labelText: '图形名称'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () {
              if (control.text.trim().isNotEmpty) {
                Navigator.pop(ctx, control.text.trim());
              }
            },
            child: const Text('保存'),
          ),
        ],
      ),
    );
    control.dispose();
    if (name != null && validDraft != null) {
      await guarded(() async {
        await library.save(
          name,
          validDraft!,
          geometry: drawing ? sketch.elements : null,
        );
        if (mounted) setState(() {});
        notice('已保存到图形库');
      });
    }
  }

  Future<void> deleteFigure(SavedFigure figure) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('删除“${figure.name}”？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (yes == true) {
      await guarded(() async {
        await library.remove(figure.id);
        if (mounted) setState(() {});
      });
    }
  }
}
