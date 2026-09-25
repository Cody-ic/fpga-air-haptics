import 'dart:convert';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'model.dart';
import 'sketch.dart';
import 'transport.dart';

class SavedFigure {
  final String id, name;
  final FigureConfig config;
  final List<SketchElement>? geometry;
  SavedFigure(this.id, this.name, this.config, [this.geometry]);
  Map<String, Object> get json => {
    'id': id,
    'name': name,
    'config': config.wire,
    if (geometry != null)
      'mobile_sketch': geometry!.map((e) => e.json).toList(),
  };
  factory SavedFigure.fromJson(Map<String, dynamic> f) => SavedFigure(
    f['id'] as String,
    f['name'] as String,
    FigureConfig.parse(Map<String, Object?>.from(f['config'] as Map)),
    readGeometry(f['mobile_sketch']),
  );
}

List<SketchElement>? readGeometry(Object? data) {
  if (data == null) return null;
  if (data is! List || data.length > 100) throw const FormatException('草图数据无效');
  return data
      .map((e) => SketchElement.fromJson(Map<String, dynamic>.from(e)))
      .toList();
}

class FigureDocument {
  final FigureConfig config;
  final List<SketchElement>? geometry;
  FigureDocument(this.config, this.geometry) {
    if (geometry != null && config.shape == 'CUSTOM') {
      final sketch = MobileSketch()..elements.addAll(geometry!);
      if (FigureConfig.encodePaths(sketch.compile()) !=
          config.wire['scan_paths']) {
        throw const FormatException('原始草图与图形路径不一致');
      }
    }
  }
}

class FigureLibrary {
  final List<SavedFigure> figures = [];
  SharedPreferences? _prefs;
  BleProfile profile = const BleProfile();
  static const files = MethodChannel('io.codyic.touchsee/files');
  Future<void> load() async {
    _prefs = await SharedPreferences.getInstance();
    final text = _prefs!.getString('figures');
    if (text != null) {
      final entries = (jsonDecode(text) as List)
          .map((e) => SavedFigure.fromJson(Map<String, dynamic>.from(e)))
          .toList();
      figures.addAll(entries);
    }
    final settings = _prefs!.getString('bleProfile');
    if (settings != null) profile = BleProfile.fromJson(jsonDecode(settings));
  }

  Future<void> save(
    String name,
    FigureConfig config, {
    List<SketchElement>? geometry,
  }) async {
    if (figures.length >= 100) throw StateError('图形库已满，请先删除不需要的图形');
    geometry = compatibleGeometry(config, geometry);
    final item = SavedFigure(
      DateTime.now().microsecondsSinceEpoch.toString(),
      name.trim(),
      config,
      geometry?.map((e) => e.copy()).toList(),
    );
    final next = [...figures, item];
    if (!await _prefs!.setString(
      'figures',
      jsonEncode(next.map((f) => f.json).toList()),
    )) {
      throw StateError('保存失败');
    }
    figures.add(item);
  }

  Future<void> remove(String id) async {
    final next = figures.where((f) => f.id != id).toList();
    if (!await _prefs!.setString(
      'figures',
      jsonEncode(next.map((f) => f.json).toList()),
    )) {
      throw StateError('删除失败');
    }
    figures
      ..clear()
      ..addAll(next);
  }

  Future<void> setProfile(BleProfile value) async {
    value = value.validated();
    if (!await _prefs!.setString('bleProfile', jsonEncode(value.json))) {
      throw StateError('设置保存失败');
    }
    profile = value;
  }

  static FigureConfig parseDocument(String text) {
    return readDocument(text).config;
  }

  static FigureDocument readDocument(String text) {
    if (utf8.encode(text).length > 65536) throw const FormatException('图形文件过大');
    final doc = jsonDecode(text.replaceFirst('\ufeff', ''));
    if (doc is! Map ||
        !['haptics-config-2', 'haptics-config-3'].contains(doc['schema']) ||
        doc['config'] is! Map) {
      throw const FormatException('请选择触见图形文件（版本 2 或 3）');
    }
    final values = Map<String, Object?>.from(doc['config']);
    if (doc['schema'] == 'haptics-config-2') {
      values.putIfAbsent('scan_paths', () => 'NONE');
      values.putIfAbsent('blank_us', () => 2000);
    }
    return FigureDocument(
      FigureConfig.parse(values),
      readGeometry(doc['mobile_sketch']),
    );
  }

  static List<SketchElement>? compatibleGeometry(
    FigureConfig config,
    List<SketchElement>? geometry,
  ) {
    if (geometry == null || config.shape != 'CUSTOM') return null;
    // Imported legacy/desktop paths remain authoritative until edited. Derived
    // handles need not compile identically (e.g. legacy retracing or shared edges).
    final sketch = MobileSketch()..elements.addAll(geometry);
    return FigureConfig.encodePaths(sketch.compile()) ==
            config.wire['scan_paths']
        ? geometry
        : null;
  }

  static String document(FigureConfig config, {List<SketchElement>? geometry}) {
    geometry = compatibleGeometry(config, geometry);
    return const JsonEncoder.withIndent('  ').convert({
      'schema': 'haptics-config-3',
      'config': config.wire,
      if (geometry != null)
        'mobile_sketch': geometry.map((e) => e.json).toList(),
    });
  }

  Future<FigureDocument?> importFile() async {
    final text = await files.invokeMethod<String>('import');
    return text == null ? null : readDocument(text);
  }

  Future<bool> exportFile(
    FigureConfig config, {
    List<SketchElement>? geometry,
  }) async =>
      await files.invokeMethod<bool>('export', {
        'name': '触见图形.json',
        'content': document(config, geometry: geometry),
      }) ??
      false;
}
