import 'dart:async';
import 'package:flutter/foundation.dart';
import 'model.dart';
import 'protocol.dart';
import 'transport.dart';

class _Pending {
  final String verb;
  int? sentMs;
  _Pending(this.verb);
}

class DeviceSession extends ChangeNotifier {
  DeviceTransport? _transport;
  StreamSubscription<List<int>>? _bytes;
  StreamSubscription<String>? _errors;
  Timer? _timer;
  final _clock = Stopwatch()..start();
  FrameDecoder _decoder = FrameDecoder();
  Future<void> _writes = Future.value();
  final Map<int, _Pending> _pending = {};
  final List<String> logs = [];
  int _epoch = 0, _seq = 0, _receivedMs = 0, _pingMs = 0, _commandMs = 0;
  bool connecting = false, ready = false, closing = false, _disposed = false;
  String message = '选择设备，或先试试演示模式。', deviceName = '', boot = '';
  Snapshot? snapshot;
  ArraySpec? array;
  Workspace? workspace;
  Set<String> capabilities = {};
  Map<String, int> limits = {};
  String? busyVerb, _awaitVerb;
  String? _requestedMode;
  int? _awaitRevision, _afterSample, _confirmedRevision;
  FigureConfig? _submitted, _confirmed;
  bool get simulated => _transport?.simulated ?? false;
  bool get connected => _transport != null;
  bool get busy => busyVerb != null || _awaitVerb != null;
  bool get fresh =>
      ready &&
      snapshot != null &&
      _clock.elapsedMilliseconds - _receivedMs < 1600;
  bool get canConfigure =>
      fresh && !busy && snapshot!.mode == 'REMOTE' && snapshot!.state == 'IDLE';
  bool canPlay(FigureConfig draft) =>
      fresh &&
      !busy &&
      snapshot!.mode == 'REMOTE' &&
      ['IDLE', 'PAUSED'].contains(snapshot!.state) &&
      _confirmedRevision == snapshot!.revision &&
      _confirmed != null &&
      draft.same(_confirmed!) &&
      snapshot!.config.same(draft);

  void invalidateDraftReceipt() {
    _confirmed = null;
    _confirmedRevision = null;
  }

  bool get canPause =>
      fresh &&
      !busy &&
      snapshot!.mode == 'REMOTE' &&
      snapshot!.state == 'RUNNING';
  void _update() {
    if (!_disposed) notifyListeners();
  }

  void _log(String text) {
    logs.add(text);
    if (logs.length > 200) logs.removeAt(0);
  }

  Future<void> connect(DeviceTransport transport) async {
    if (connected || closing || connecting) return;
    final epoch = ++_epoch;
    _transport = transport;
    connecting = true;
    ready = false;
    snapshot = null;
    array = null;
    workspace = null;
    _confirmed = null;
    _confirmedRevision = null;
    _submitted = null;
    _pending.clear();
    busyVerb = null;
    _awaitVerb = null;
    _decoder = FrameDecoder();
    _writes = Future.value();
    message = transport.simulated ? '正在连接演示设备…' : '正在连接蓝牙设备…';
    _update();
    _bytes = transport.bytes.listen(
      (raw) {
        if (epoch == _epoch) _receive(raw);
      },
      onError: (Object e) {
        if (epoch == _epoch) _fail('$e');
      },
    );
    _errors = transport.errors.listen((e) {
      if (epoch == _epoch) _fail(e);
    });
    try {
      await transport.open().timeout(const Duration(seconds: 18));
      if (epoch != _epoch) {
        await transport.close();
        return;
      }
      connecting = false;
      message = '连接已接通，等待设备确认…';
      _pingMs = _clock.elapsedMilliseconds;
      _timer = Timer.periodic(
        const Duration(milliseconds: 100),
        (_) => _tick(),
      );
      await _request('HELLO');
      _update();
    } catch (e) {
      if (epoch == _epoch) _fail('连接失败：$e');
    }
  }

  Future<void> _request(String verb, [Map<String, String> fields = const {}]) {
    final transport = _transport;
    if (transport == null) return Future.value();
    final epoch = _epoch;
    _seq = _seq % 65535 + 1;
    while (_pending.containsKey(_seq)) {
      _seq = _seq % 65535 + 1;
    }
    final seq = _seq, request = _Pending(verb);
    final data = Frame('CMD', seq, verb, fields).encode();
    _pending[seq] = request;
    _writes = _writes
        .then((_) async {
          if (epoch != _epoch || !_pending.containsKey(seq)) return;
          request.sentMs = _clock.elapsedMilliseconds;
          _log('发送 $verb #$seq');
          await transport.write(data);
        })
        .catchError((Object e) {
          if (epoch == _epoch) _fail('发送未完成：$e');
        });
    return _writes;
  }

  void _receive(List<int> bytes) {
    try {
      for (final frame in _decoder.feed(bytes)) {
        final f = frame.fields;
        if (frame.kind == 'ACK' || frame.kind == 'ERR') {
          final request = _pending[frame.seq];
          if (request == null || request.verb != frame.verb) continue;
          _pending.remove(frame.seq);
          _log('收到 ${frame.kind} ${frame.verb} #${frame.seq}');
          if (frame.kind == 'ERR') {
            if (frame.verb == 'HELLO') throw StateError('设备拒绝协议握手');
            if (frame.verb == busyVerb) {
              busyVerb = null;
              _submitted = null;
            }
            const friendly = {
              'BUSY': '请先停止播放',
              'LOCAL_CONTROL': '当前由设备按键控制',
              'OUT_OF_WORKSPACE': '图形超出设备范围',
              'HARDWARE_MISMATCH': '设备阵列信息不一致，请重连',
              'BAD_CONFIG': '设备无法使用这些图形参数',
            };
            message = friendly[f['code']] ?? '设备拒绝操作：${f['code']}';
            continue;
          }
          if (frame.verb == 'HELLO') {
            if (f['proto'] != '3' ||
                (f['boot'] ?? '').isEmpty ||
                !['0', '1'].contains(f['simulated']) ||
                (f['simulated'] == '1') != simulated) {
              throw StateError('设备协议或数据来源不匹配');
            }
            if (integer(f['hb_ms']) < 2000) throw StateError('设备心跳期限不兼容');
            capabilities = (f['caps'] ?? '').split(',').toSet();
            if (!capabilities.containsAll([
              'CONFIG',
              'MODE',
              'START',
              'PAUSE',
              'STOP',
              'STATE',
              'PHASE',
            ])) {
              throw StateError('设备缺少必要控制能力');
            }
            limits = {
              for (final key in [
                'max_rows',
                'max_cols',
                'max_channels',
                'max_nodes',
                'max_scan_points',
                'max_strokes',
              ])
                key: integer(f[key]),
            };
            if (limits.values.any((n) => n <= 0)) throw StateError('设备容量声明无效');
            array = ArraySpec.parse(f);
            workspace = Workspace(f);
            if (array!.rows > limits['max_rows']! ||
                array!.cols > limits['max_cols']! ||
                array!.count > limits['max_channels']!) {
              throw StateError('实际阵列超过设备容量');
            }
            boot = f['boot']!;
            deviceName = f['device'] ?? '触见设备';
            ready = true;
            _receivedMs = _clock.elapsedMilliseconds;
            message = '设备已连接，发送图形后即可播放。';
          } else if ([
            'CONFIG',
            'MODE',
            'START',
            'PAUSE',
            'STOP',
          ].contains(frame.verb)) {
            final rev = integer(f['rev']);
            if (f['applied'] != '1' || rev < 0) throw StateError('设备未确认命令生效');
            if (frame.verb == busyVerb) {
              _awaitVerb = busyVerb;
              busyVerb = null;
              _awaitRevision = rev;
              _afterSample = snapshot?.sample ?? -1;
              message = '设备已接收，正在确认状态…';
            }
          }
        } else if (frame.kind == 'TEL' &&
            frame.seq == 0 &&
            frame.verb == 'STATE' &&
            ready) {
          Snapshot state;
          try {
            state = Snapshot(f);
          } on FormatException catch (e) {
            _log('忽略无效状态：$e');
            continue;
          }
          if (state.boot != boot ||
              state.simulated != simulated ||
              !state.array.same(array!) ||
              !workspace!.accepts(state.config)) {
            throw StateError('设备状态发生不兼容变化，请重新连接');
          }
          final old = snapshot;
          if (old != null &&
              (state.sample <= old.sample ||
                  state.uptimeMs < old.uptimeMs ||
                  state.revision < old.revision)) {
            continue;
          }
          snapshot = state;
          _receivedMs = _clock.elapsedMilliseconds;
          if (_awaitVerb != null &&
              state.sample > _afterSample! &&
              state.revision >= _awaitRevision!) {
            final verb = _awaitVerb;
            if (verb == 'CONFIG') {
              if (_submitted != null &&
                  state.config.same(_submitted!) &&
                  state.revision == _awaitRevision) {
                _confirmed = state.config;
                _confirmedRevision = state.revision;
                message = '图形已确认，可以播放。';
              } else {
                message = '设备回传的图形不一致，请重新发送。';
              }
              _submitted = null;
            } else {
              final expectedState = {
                'START': 'RUNNING',
                'PAUSE': 'PAUSED',
                'STOP': 'IDLE',
              }[verb];
              if (expectedState != null && state.state != expectedState ||
                  verb == 'MODE' && state.mode != _requestedMode) {
                continue; // ACK alone must never confirm a control transition.
              }
              message = {
                'START': '正在播放',
                'PAUSE': '已暂停',
                'STOP': '已停止输出',
                'MODE': '控制方式已切换',
              }[verb]!;
            }
            _awaitVerb = null;
          }
        }
      }
      _update();
    } catch (e) {
      _fail('设备确认失败：$e');
    }
  }

  Future<void> apply(FigureConfig config) async {
    if (!canConfigure) return;
    if (!workspace!.accepts(config)) {
      message = '图形超出设备声明的范围，请调整尺寸或位置。';
      _update();
      return;
    }
    if (config.legacyPoints.isNotEmpty &&
            (!capabilities.contains('CUSTOM_XY') ||
                config.legacyPoints.length > limits['max_nodes']!) ||
        config.strokes.isNotEmpty &&
            (!capabilities.contains('SCAN_PATHS') ||
                config.strokes.length > limits['max_strokes']! ||
                config.strokes.fold(0, (n, p) => n + p.length) >
                    limits['max_scan_points']!)) {
      message = '图形超过当前设备支持的容量。';
      _update();
      return;
    }
    _submitted = config;
    _confirmed = null;
    _confirmedRevision = null;
    await command('CONFIG', {...config.wire, ...array!.wire});
  }

  Future<void> play(FigureConfig draft) async {
    if (canPlay(draft)) await command('START');
  }

  Future<void> command(
    String verb, [
    Map<String, String> fields = const {},
  ]) async {
    if (!ready || closing || (busy && verb != 'STOP')) return;
    if (verb == 'STOP') {
      _pending.removeWhere(
        (_, p) =>
            p.sentMs == null &&
            ['CONFIG', 'START', 'PAUSE', 'MODE'].contains(p.verb),
      );
      _submitted = null;
    }
    busyVerb = verb;
    if (verb == 'MODE') _requestedMode = fields['value'];
    _awaitVerb = null;
    _commandMs = _clock.elapsedMilliseconds;
    message = '正在等待设备确认…';
    _update();
    await _request(verb, fields);
  }

  void _tick() {
    if (closing || !connected) return;
    final now = _clock.elapsedMilliseconds;
    if (_pending.values.any(
          (p) => p.sentMs != null && now - p.sentMs! > 2000,
        ) ||
        ready && now - _receivedMs > 2500 ||
        busy && now - _commandMs > 3500) {
      _fail('设备未及时回传确认，当前输出状态未知。');
      return;
    }
    if (ready &&
        now - _pingMs >= 800 &&
        !_pending.values.any((p) => p.verb == 'PING')) {
      _pingMs = now;
      unawaited(_request('PING'));
    }
    _update();
  }

  void _fail(String error) {
    if (closing || !connected) return;
    _log(error);
    unawaited(disconnect(reason: error));
  }

  Future<void> disconnect({String? reason}) async {
    if (closing) return;
    final transport = _transport;
    if (transport == null) return;
    final attemptStop =
        ready && (snapshot == null || snapshot!.mode != 'LOCAL');
    closing = true;
    connecting = false;
    ready = false;
    _confirmed = null;
    _confirmedRevision = null;
    busyVerb = null;
    _awaitVerb = null;
    _timer?.cancel();
    message = reason ?? '连接已断开，请重新连接后发送图形。';
    _update();
    ++_epoch;
    _pending.clear();
    await _bytes?.cancel();
    await _errors?.cancel();
    _log('已取消接收，正在收尾发送');
    try {
      // Finish a pending frame before STOP; a failed BLE write rejects STOP.
      await _writes.timeout(const Duration(seconds: 2));
      _log('发送队列已结束');
      if (attemptStop) {
        await transport
            .write(Frame('CMD', (_seq % 65535) + 1, 'STOP').encode())
            .timeout(const Duration(milliseconds: 1600));
      }
    } catch (_) {
      /* no claim that lost hardware has stopped */
    }
    _log('正在关闭传输连接');
    try {
      await transport.close();
    } catch (e) {
      _log('连接关闭异常：$e');
    } finally {
      _transport = null;
      closing = false;
      _update();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(disconnect());
    super.dispose();
  }
}
