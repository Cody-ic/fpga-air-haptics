import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'package:flutter_test/flutter_test.dart';
import 'package:touchsee_mobile/model.dart';
import 'package:touchsee_mobile/protocol.dart';
import 'package:touchsee_mobile/library.dart';

void main() {
  final reference =
      jsonDecode(File('test/fixtures/python_reference.json').readAsStringSync())
          as Map;
  test('CRC and HELLO match the Python wire contract', () {
    expect(crc16(ascii.encode('123456789')), 0x29b1);
    expect(
      ascii.decode(const Frame('CMD', 1, 'HELLO').encode()),
      'HAP3 CMD 1 HELLO*F6AD\n',
    );
  });
  test(
    '3D geometry frames roundtrip but unsupported geometry fails clearly',
    () {
      final hello = Frame.decode(ascii.encode(reference['geometry_hello']));
      expect(hello.fields['mapping'], 'EXPLICIT_XYZ');
      expect(() => ArraySpec.parse(hello.fields), throwsFormatException);
      final raw = reference['geometry_chunk'] as String;
      final frame = Frame.decode(ascii.encode(raw));
      expect(frame.fields['elements']!.split('|').length, 16);
      expect(frame.fields['geometry_id'], hello.fields['geometry_id']);
      expect(ascii.decode(frame.encode()), raw);
      final state = Frame.decode(
        ascii.encode((reference['frames'] as List).last),
      );
      expect(state.fields['phase_offsets']!.split(',').length, 64);
      expect(Snapshot(state.fields).array.count, 64);
    },
  );
  test(
    'Python ACK and STATE survive arbitrary fragments and combined frames',
    () {
      final bytes = ascii.encode((reference['frames'] as List).join());
      final decoder = FrameDecoder(), frames = <Frame>[];
      for (var i = 0; i < bytes.length; i += 7) {
        frames.addAll(decoder.feed(bytes.sublist(i, min(i + 7, bytes.length))));
      }
      expect(decoder.rejected, 0);
      expect(frames.length, 4);
      expect(frames.first.fields['adc_capture'], '1');
      final snapshot = Snapshot(frames.last.fields);
      expect(snapshot.array.count, 64);
      expect(snapshot.revision, 1);
      expect(snapshot.simulated, true);
      expect(
        snapshot.config.wire['scan_paths'],
        '0:0,10000:0,10000:10000,0:0|30000:0,40000:0',
      );
    },
  );
  test('optional receiver extension preserves the shared frame encoding', () {
    final raw = reference['receiver_capture'] as String;
    final frame = Frame.decode(ascii.encode(raw));
    expect(frame.kind, 'ACK');
    expect(frame.verb, 'CAPTURE');
    expect(frame.fields['simulated'], '1');
    expect(frame.fields['fs_hz'], '400000');
    expect(frame.fields['n'], '200');
    expect(frame.fields['raw']!.split(',').length, 200);
    expect(ascii.decode(frame.encode()), raw);
  });
  test('corrupt and oversized lines are rejected then recover at newline', () {
    final raw = const Frame('CMD', 2, 'STOP').encode();
    final corrupted = List<int>.from(raw)..[8] = 88;
    final decoder = FrameDecoder();
    expect(decoder.feed(corrupted), isEmpty);
    expect(decoder.feed(List.filled(8193, 65)), isEmpty);
    expect(
      decoder.feed([...ascii.encode('partial-tail\n'), ...raw]).single.verb,
      'STOP',
    );
    expect(decoder.rejected, 2);
    final body = 'HAP3 ACK 1 PING rev=1 rev=2';
    final duplicate = ascii.encode(
      '$body*${crc16(ascii.encode(body)).toRadixString(16).padLeft(4, '0')}\n',
    );
    expect(() => Frame.decode(duplicate), throwsFormatException);
  });
  test('all shapes, multistroke blanking and 64 phases match Python', () {
    final array = ArraySpec.parse(
      (reference['array'] as Map).map(
        (key, value) => MapEntry('$key', '$value'),
      ),
    );
    for (final vector in reference['vectors']) {
      final config = FigureConfig.parse(
        Map<String, Object?>.from(vector['config']),
      );
      final sample = config.sample((vector['seconds'] as num).toDouble());
      expect(sample.focus.x, closeTo(vector['focus_mm'][0], 1e-9));
      expect(sample.focus.y, closeTo(vector['focus_mm'][1], 1e-9));
      expect(sample.on, vector['scan_on']);
      expect(sample.stroke, vector['stroke_index']);
      expect(config.phases(sample.focus, array), vector['phases']);
    }
  });
  test(
    'invalid types, missing fields, capacity and conflicting paths reject',
    () {
      expect(() => FigureConfig({'radius_um': true}), throwsFormatException);
      expect(() => FigureConfig({'radius_um': 1.2}), throwsFormatException);
      final missing = Map<String, Object?>.from(FigureConfig.defaults)
        ..remove('level');
      expect(() => FigureConfig.parse(missing), throwsFormatException);
      expect(
        () => FigureConfig({'shape': 'CUSTOM', 'scan_paths': '0:0,0:0'}),
        throwsFormatException,
      );
      expect(
        () => FigureConfig({
          'shape': 'CUSTOM',
          'scan_paths': '0:0,1:1',
          'path_xy_um': '1:1',
        }),
        throwsFormatException,
      );
      expect(
        () => FigureConfig({
          'shape': 'CUSTOM',
          'scan_paths': List.filled(33, '0:0,1:1').join('|'),
        }),
        throwsFormatException,
      );
    },
  );
  test('atomic snapshot rejects contradictory output and phase count', () {
    final f = Frame.decode(ascii.encode(reference['frames'].last)).fields;
    expect(() => Snapshot({...f, 'output': '1'}), throwsFormatException);
    expect(() => Snapshot({...f, 'phases': '1,2'}), throwsFormatException);
    expect(() => Snapshot({...f, 'scan_on': '2'}), throwsFormatException);
  });
  test('desktop import and export preserve arbitrary physical coordinates', () {
    final config = FigureLibrary.parseDocument(
      jsonEncode(reference['document']),
    );
    Directory('.runtime').createSync(recursive: true);
    File(
      '.runtime/phone-export.json',
    ).writeAsStringSync(FigureLibrary.document(config));
    expect(
      FigureLibrary.parseDocument(FigureLibrary.document(config)).same(config),
      true,
    );
    final points = config.paths;
    expect(points.first.first, const Point(1.0, -1.0));
    expect(Workspace(Workspace.defaults).accepts(config), true);
    expect(
      Workspace(Workspace.defaults).accepts(config.change({'cx_um': 100000})),
      false,
    );
  });
}
