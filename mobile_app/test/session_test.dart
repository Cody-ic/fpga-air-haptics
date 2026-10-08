import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:touchsee_mobile/model.dart';
import 'package:touchsee_mobile/protocol.dart';
import 'package:touchsee_mobile/session.dart';
import 'package:touchsee_mobile/transport.dart';

Future<void> until(bool Function() condition) async {
  final limit = DateTime.now().add(const Duration(seconds: 5));
  while (!condition()) {
    if (DateTime.now().isAfter(limit)) fail('Expected state did not arrive');
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
}

class ManualTransport implements DeviceTransport {
  final data = StreamController<List<int>>(),
      faults = StreamController<String>();
  final List<Frame> sent = [];
  bool closed = false;
  @override
  bool get simulated => false;
  @override
  Stream<List<int>> get bytes => data.stream;
  @override
  Stream<String> get errors => faults.stream;
  @override
  Future<void> open() async {}
  @override
  Future<void> write(List<int> raw) async {
    sent.add(Frame.decode(raw));
  }

  @override
  Future<void> close() async {
    closed = true;
    await data.close();
    await faults.close();
  }

  void receive(String text, {bool real = true}) {
    final frame = Frame.decode(ascii.encode(text));
    data.add(
      Frame(frame.kind, frame.seq, frame.verb, {
        ...frame.fields,
        if (real && frame.fields.containsKey('simulated')) 'simulated': '0',
      }).encode(),
    );
  }
}

void main() {
  test(
    'Demo requires CONFIG ACK and a later matching STATE, then plays',
    () async {
      final session = DeviceSession(), demo = DemoTransport();
      addTearDown(() async {
        await session.disconnect();
        session.dispose();
      });
      await session.connect(demo);
      await until(() => session.fresh);
      final config = FigureConfig({'shape': 'TRIANGLE'});
      expect(session.canPlay(config), false);
      demo.holdState = true;
      await session.apply(config);
      await until(() => !session.busyVerb.toString().contains('CONFIG'));
      expect(session.canPlay(config), false);
      expect(session.snapshot!.revision, 0);
      demo.holdState = false;
      await until(() => session.canPlay(config));
      await session.play(config);
      await until(() => session.snapshot!.output && !session.busy);
      await session.command('PAUSE');
      await until(() => session.snapshot!.state == 'PAUSED' && !session.busy);
      expect(session.snapshot!.output, false);
      await session.command('STOP');
      await until(() => session.snapshot!.state == 'IDLE' && !session.busy);
      await session.disconnect();
      expect(session.canPlay(config), false);
      await session.connect(DemoTransport());
      await until(() => session.fresh);
      expect(session.canPlay(FigureConfig()), false);
    },
  );
  test('lost return stream times out and clears permission to play', () async {
    final session = DeviceSession(), demo = DemoTransport();
    addTearDown(() async {
      await session.disconnect();
      session.dispose();
    });
    await session.connect(demo);
    await until(() => session.fresh);
    await session.apply(FigureConfig());
    await until(() => session.canPlay(FigureConfig()));
    demo.mute = true;
    await until(() => !session.connected);
    expect(session.ready, false);
    expect(session.canPlay(FigureConfig()), false);
    expect(session.message, contains('未知'));
  });
  test(
    'real source fixture accepts HELLO and rejects a changed boot',
    () async {
      final reference = jsonDecode(
        File('test/fixtures/python_reference.json').readAsStringSync(),
      );
      final transport = ManualTransport(), session = DeviceSession();
      addTearDown(() async {
        await session.disconnect();
        session.dispose();
      });
      await session.connect(transport);
      transport.receive(reference['frames'][0]);
      transport.receive(reference['frames'][1]);
      await until(() => session.fresh);
      expect(session.simulated, false);
      expect(session.array!.count, 64);
      transport.receive((reference['frames'][1] as String));
      final state = Frame.decode(ascii.encode(reference['frames'][1]));
      transport.data.add(
        Frame('TEL', 0, 'STATE', {
          ...state.fields,
          'simulated': '0',
          'boot': 'newboot',
        }).encode(),
      );
      await until(() => !session.connected);
      expect(session.message, contains('重新连接'));
    },
  );
  test(
    'BLE must reject simulated HELLO instead of silently entering Demo',
    () async {
      final reference = jsonDecode(
        File('test/fixtures/python_reference.json').readAsStringSync(),
      );
      final transport = ManualTransport(), session = DeviceSession();
      addTearDown(() async {
        await session.disconnect();
        session.dispose();
      });
      await session.connect(transport);
      transport.receive(reference['frames'][0], real: false);
      await until(() => !session.connected);
      expect(session.ready, false);
      expect(session.message, contains('来源'));
    },
  );
  test(
    'control ACK waits for the matching state, and import invalidates receipt',
    () async {
      final reference = jsonDecode(
        File('test/fixtures/python_reference.json').readAsStringSync(),
      );
      final transport = ManualTransport(), session = DeviceSession();
      addTearDown(() async {
        await session.disconnect();
        session.dispose();
      });
      await session.connect(transport);
      transport.receive(reference['frames'][0]);
      transport.receive(reference['frames'][1]);
      await until(() => session.fresh);
      final config = FigureConfig.parse(
        Map<String, Object?>.from(reference['document']['config']),
      );
      await session.apply(config);
      transport.receive(reference['frames'][2]);
      transport.receive(reference['frames'][3]);
      await until(() => session.canPlay(config));
      session.invalidateDraftReceipt();
      expect(session.canPlay(config), isFalse);
      await session.command('START');
      final command = transport.sent.last;
      transport.data.add(
        Frame('ACK', command.seq, 'START', {
          'applied': '1',
          'rev': '1',
        }).encode(),
      );
      final fields = Frame.decode(ascii.encode(reference['frames'][3])).fields;
      transport.data.add(
        Frame('TEL', 0, 'STATE', {
          ...fields,
          'simulated': '0',
          'sample': '100',
        }).encode(),
      );
      await until(() => session.snapshot!.sample == 100);
      expect(session.busy, isTrue);
      transport.data.add(
        Frame('TEL', 0, 'STATE', {
          ...fields,
          'simulated': '0',
          'sample': '101',
          'state': 'RUNNING',
          'output': '1',
        }).encode(),
      );
      await until(() => !session.busy);
      expect(session.canPause, isTrue);
    },
  );
}
