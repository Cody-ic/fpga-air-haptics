import 'dart:async';
import 'dart:math';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'model.dart';
import 'protocol.dart';

abstract class DeviceTransport {
  bool get simulated;
  Stream<List<int>> get bytes;
  Stream<String> get errors;
  Future<void> open();
  Future<void> write(List<int> data);
  Future<void> close();
}

class BleProfile {
  final String service, tx, rx, mode;
  final int packetBytes;
  const BleProfile({
    this.service = '6e400001-b5a3-f393-e0a9-e50e24dcca9e',
    this.tx = '6e400002-b5a3-f393-e0a9-e50e24dcca9e',
    this.rx = '6e400003-b5a3-f393-e0a9-e50e24dcca9e',
    this.mode = 'auto',
    this.packetBytes = 0,
  });
  static String normalize(String text) {
    var value = text.trim().toLowerCase();
    if (RegExp(r'^[0-9a-f]{4}$').hasMatch(value)) value = '0000$value';
    if (RegExp(r'^[0-9a-f]{8}$').hasMatch(value)) {
      value = '$value-0000-1000-8000-00805f9b34fb';
    }
    if (!RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    ).hasMatch(value)) {
      throw const FormatException('UUID 格式无效');
    }
    return value;
  }

  BleProfile validated() {
    if (!['auto', 'response', 'without-response'].contains(mode) ||
        packetBytes < 0 ||
        packetBytes > 512) {
      throw const FormatException('写入方式或分包大小无效');
    }
    return BleProfile(
      service: normalize(service),
      tx: normalize(tx),
      rx: normalize(rx),
      mode: mode,
      packetBytes: packetBytes,
    );
  }

  Map<String, Object> get json => {
    'service': service,
    'tx': tx,
    'rx': rx,
    'mode': mode,
    'packetBytes': packetBytes,
  };
  factory BleProfile.fromJson(Map<String, dynamic> f) => BleProfile(
    service: f['service'],
    tx: f['tx'],
    rx: f['rx'],
    mode: f['mode'],
    packetBytes: integer(f['packetBytes']),
  ).validated();
}

class BleTransport implements DeviceTransport {
  final BluetoothDevice device;
  final BleProfile profile;
  final _bytes = StreamController<List<int>>();
  final _errors = StreamController<String>();
  StreamSubscription<List<int>>? _notifications;
  StreamSubscription<BluetoothConnectionState>? _connection;
  BluetoothCharacteristic? _writer;
  bool _closed = false,
      _connected = false,
      _failed = false,
      _withoutResponse = false;
  BleTransport(this.device, BleProfile profile) : profile = profile.validated();
  @override
  bool get simulated => false;
  @override
  Stream<List<int>> get bytes => _bytes.stream;
  @override
  Stream<String> get errors => _errors.stream;
  void _check() {
    if (_closed || _failed) throw StateError('蓝牙连接已结束');
  }

  @override
  Future<void> open() async {
    try {
      _connection = device.connectionState.listen((state) {
        if (state == BluetoothConnectionState.connected) _connected = true;
        if (state == BluetoothConnectionState.disconnected &&
            _connected &&
            !_closed) {
          _failed = true;
          _errors.add('蓝牙已断开，设备输出状态未知');
        }
      });
      await device.connect(
        timeout: const Duration(seconds: 15),
        autoConnect: false,
        mtu: 247,
      );
      _check();
      final services = await device.discoverServices();
      _check();
      final matching = services
          .where((s) => normalizeUuid(s.uuid) == profile.service)
          .toList();
      if (matching.length != 1) throw StateError('未找到配置的蓝牙服务，请核对模块连接设置');
      final writers = matching.single.characteristics
          .where((c) => normalizeUuid(c.uuid) == profile.tx)
          .toList();
      final readers = matching.single.characteristics
          .where((c) => normalizeUuid(c.uuid) == profile.rx)
          .toList();
      if (writers.length != 1 || readers.length != 1) {
        throw StateError('未找到唯一的蓝牙收发通道');
      }
      final writer = writers.single, reader = readers.single;
      _withoutResponse =
          profile.mode == 'without-response' ||
          (profile.mode == 'auto' && !writer.properties.write);
      if (_withoutResponse
          ? !writer.properties.writeWithoutResponse
          : !writer.properties.write) {
        throw StateError('模块不支持所选写入方式');
      }
      if (!reader.properties.notify && !reader.properties.indicate) {
        throw StateError('模块没有状态通知通道');
      }
      _notifications = reader.onValueReceived.listen(
        (value) {
          if (!_closed && !_failed) _bytes.add(List<int>.from(value));
        },
        onError: (Object e) {
          if (!_closed) _errors.add('蓝牙接收失败：$e');
        },
      );
      await reader.setNotifyValue(true);
      _check();
      _writer = writer;
    } catch (_) {
      // A cancelled open can finish connecting after close already ran.
      // Always tear down that late connection as well.
      if (_closed) {
        try {
          await device
              .disconnect(queue: false, androidDelay: 0, timeout: 2)
              .timeout(const Duration(seconds: 2));
        } catch (_) {
          // The connection may already have disappeared.
        }
      }
      await close();
      rethrow;
    }
  }

  static String normalizeUuid(Guid uuid) =>
      BleProfile.normalize(uuid.toString());
  @override
  Future<void> write(List<int> data) async {
    _check();
    final writer = _writer;
    if (writer == null) throw StateError('蓝牙尚未就绪');
    final chunk = min(
      512,
      min(
        max(1, device.mtuNow - 3),
        profile.packetBytes == 0 ? 512 : profile.packetBytes,
      ),
    );
    final timer = Stopwatch()..start();
    try {
      for (var offset = 0; offset < data.length; offset += chunk) {
        _check();
        final remaining = 1500 - timer.elapsedMilliseconds;
        if (remaining <= 0) throw TimeoutException('发送超时');
        await writer
            .write(
              data.sublist(offset, min(offset + chunk, data.length)),
              withoutResponse: _withoutResponse,
              timeout: 2,
            )
            .timeout(Duration(milliseconds: remaining));
      }
    } catch (_) {
      _failed = true; // Never send a second frame after a partial write.
      rethrow;
    }
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _notifications?.cancel();
    await _connection?.cancel();
    try {
      await device
          .disconnect(queue: false, androidDelay: 0, timeout: 2)
          .timeout(const Duration(seconds: 2));
    } catch (_) {
      /* watchdog owns lost output */
    }
    await _bytes.close();
    await _errors.close();
  }
}

class DemoTransport implements DeviceTransport {
  final ArraySpec array;
  final _bytes = StreamController<List<int>>();
  final _errors = StreamController<String>();
  final _decoder = FrameDecoder();
  final _clock = Stopwatch();
  final String boot = DateTime.now().microsecondsSinceEpoch.toRadixString(16);
  Timer? _timer;
  FigureConfig config = FigureConfig();
  String mode = 'REMOTE', state = 'IDLE', reason = 'NONE';
  int revision = 0, sample = 0, startedMs = 0, elapsedMs = 0, lastPingMs = 0;
  bool _closed = false, _hello = false;
  bool mute = false, holdState = false;
  DemoTransport({this.array = const ArraySpec()});
  @override
  bool get simulated => true;
  @override
  Stream<List<int>> get bytes => _bytes.stream;
  @override
  Stream<String> get errors => _errors.stream;
  @override
  Future<void> open() async {
    _clock.start();
    _timer = Timer.periodic(const Duration(milliseconds: 80), (_) {
      if (mode == 'REMOTE' &&
          state != 'IDLE' &&
          _clock.elapsedMilliseconds - lastPingMs > 3000) {
        _stop();
        reason = 'HEARTBEAT_TIMEOUT';
      }
      if (_hello) _snapshot();
    });
  }

  void _stop() {
    state = 'IDLE';
    elapsedMs = 0;
    reason = 'NONE';
  }

  int get playMs =>
      elapsedMs +
      (state == 'RUNNING' ? _clock.elapsedMilliseconds - startedMs : 0);
  void _emit(Frame frame) {
    if (_closed || mute) return;
    final data = frame.encode();
    for (var i = 0; i < data.length; i += 173) {
      _bytes.add(data.sublist(i, min(i + 173, data.length)));
    }
  }

  void _snapshot() {
    if (holdState) return;
    final point = config.sample(playMs / 1000);
    _emit(
      Frame('TEL', 0, 'STATE', {
        ...config.wire,
        ...array.wire,
        'boot': boot,
        'sample': '${++sample}',
        'uptime_ms': '${_clock.elapsedMilliseconds}',
        'rev': '$revision',
        'mode': mode,
        'state': state,
        'reason': reason,
        'simulated': '1',
        'output': state == 'RUNNING' && config.value('level') > 0 && point.on
            ? '1'
            : '0',
        'scan_on': point.on ? '1' : '0',
        'stroke_index': '${point.stroke}',
        'fx_um': '${(point.focus.x * 1000).round()}',
        'fy_um': '${(point.focus.y * 1000).round()}',
        'fz_um': config.wire['z_um']!,
        'phases': config.phases(point.focus, array).join(','),
      }),
    );
  }

  @override
  Future<void> write(List<int> data) async {
    if (_closed) throw StateError('演示已关闭');
    for (final frame in _decoder.feed(data)) {
      if (frame.kind != 'CMD' || frame.seq == 0) continue;
      try {
        final verb = frame.verb;
        if (verb == 'HELLO') {
          _hello = true;
          lastPingMs = _clock.elapsedMilliseconds;
          _emit(
            Frame('ACK', frame.seq, verb, {
              'proto': '3',
              'device': 'DEMO-MOBILE',
              'boot': boot,
              'simulated': '1',
              'hb_ms': '3000',
              'caps':
                  'CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,CUSTOM_XY,SCAN_PATHS',
              'max_rows': '16',
              'max_cols': '16',
              'max_channels': '256',
              'max_nodes': '64',
              'max_scan_points': '256',
              'max_strokes': '32',
              ...array.wire,
              ...Workspace.defaults,
            }),
          );
          _snapshot();
          continue;
        }
        if (!_hello) throw StateError('HANDSHAKE_REQUIRED');
        switch (verb) {
          case 'PING':
            lastPingMs = _clock.elapsedMilliseconds;
          case 'CONFIG':
            if (mode != 'REMOTE') throw StateError('LOCAL_CONTROL');
            if (state != 'IDLE') throw StateError('BUSY');
            final candidate = FigureConfig.parse(frame.fields);
            if (!ArraySpec.parse(frame.fields).same(array)) {
              throw StateError('HARDWARE_MISMATCH');
            }
            if (!Workspace(Workspace.defaults).accepts(candidate)) {
              throw StateError('OUT_OF_WORKSPACE');
            }
            config = candidate;
            revision++;
            elapsedMs = 0;
          case 'START':
            if (mode != 'REMOTE') throw StateError('LOCAL_CONTROL');
            if (!['IDLE', 'PAUSED'].contains(state)) throw StateError('BUSY');
            startedMs = _clock.elapsedMilliseconds;
            lastPingMs = startedMs;
            state = 'RUNNING';
            reason = 'NONE';
          case 'PAUSE':
            if (mode != 'REMOTE') throw StateError('LOCAL_CONTROL');
            if (state != 'RUNNING') throw StateError('NOT_RUNNING');
            elapsedMs = playMs;
            state = 'PAUSED';
          case 'STOP':
            _stop();
          case 'MODE':
            if (state != 'IDLE') throw StateError('BUSY');
            if (!['LOCAL', 'REMOTE'].contains(frame.fields['value'])) {
              throw StateError('BAD_MODE');
            }
            mode = frame.fields['value']!;
            lastPingMs = _clock.elapsedMilliseconds;
          case 'SNAP':
            break;
          default:
            throw StateError('UNKNOWN_COMMAND');
        }
        _emit(
          Frame('ACK', frame.seq, verb, {'applied': '1', 'rev': '$revision'}),
        );
        if (verb != 'PING') _snapshot();
      } catch (e) {
        final code = e is StateError ? e.message : 'BAD_CONFIG';
        _emit(Frame('ERR', frame.seq, frame.verb, {'code': code}));
      }
    }
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    _timer?.cancel();
    _clock.stop();
    await _bytes.close();
    await _errors.close();
  }
}
