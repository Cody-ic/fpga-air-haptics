import 'dart:convert';
import 'dart:math';
import 'model.dart';

const maxFrameBytes = 8192;
final _token = RegExp(r'^[A-Za-z0-9_,.?:+|\-]+$');
final _key = RegExp(r'^[a-z][a-z0-9_]*$');

int crc16(List<int> bytes) {
  var crc = 0xffff;
  for (final byte in bytes) {
    crc ^= byte << 8;
    for (var i = 0; i < 8; i++) {
      crc = ((crc & 0x8000) != 0 ? (crc << 1) ^ 0x1021 : crc << 1) & 0xffff;
    }
  }
  return crc;
}

class Frame {
  final String kind, verb;
  final int seq;
  final Map<String, String> fields;
  const Frame(this.kind, this.seq, this.verb, [this.fields = const {}]);
  List<int> encode() {
    if (!['CMD', 'ACK', 'ERR', 'TEL'].contains(kind) ||
        seq < 0 ||
        seq > 65535 ||
        !_token.hasMatch(verb)) {
      throw const FormatException('无效帧头');
    }
    final parts = ['HAP3', kind, '$seq', verb];
    for (final e in fields.entries) {
      if (!_key.hasMatch(e.key) || !_token.hasMatch(e.value)) {
        throw const FormatException('非法协议字段');
      }
      parts.add('${e.key}=${e.value}');
    }
    final body = ascii.encode(parts.join(' '));
    final bytes = [
      ...body,
      ...ascii.encode(
        '*${crc16(body).toRadixString(16).padLeft(4, '0').toUpperCase()}\n',
      ),
    ];
    if (bytes.length > maxFrameBytes) throw const FormatException('报文过长');
    return bytes;
  }

  factory Frame.decode(List<int> bytes) {
    if (bytes.length > maxFrameBytes) throw const FormatException('报文过长');
    final line = ascii.decode(bytes).replaceFirst(RegExp(r'[\r\n]+$'), '');
    final cut = line.lastIndexOf('*');
    if (cut < 0 ||
        !RegExp(r'^[0-9a-fA-F]{4}$').hasMatch(line.substring(cut + 1))) {
      throw const FormatException('缺少校验');
    }
    if (crc16(ascii.encode(line.substring(0, cut))) !=
        int.parse(line.substring(cut + 1), radix: 16)) {
      throw const FormatException('校验失败');
    }
    final parts = line.substring(0, cut).split(' ');
    if (parts.length < 4 || parts[0] != 'HAP3') {
      throw const FormatException('协议版本不匹配');
    }
    final fields = <String, String>{};
    for (final item in parts.skip(4)) {
      final pair = item.split('=');
      if (pair.length != 2 ||
          fields.containsKey(pair[0]) ||
          !_key.hasMatch(pair[0]) ||
          !_token.hasMatch(pair[1])) {
        throw const FormatException('重复或非法字段');
      }
      fields[pair[0]] = pair[1];
    }
    final frame = Frame(
      parts[1],
      integer(parts[2]),
      parts[3],
      Map.unmodifiable(fields),
    );
    frame.encode();
    return frame;
  }
}

class FrameDecoder {
  final List<int> _buffer = [];
  bool _dropping = false;
  int rejected = 0;
  List<Frame> feed(List<int> bytes) {
    final frames = <Frame>[];
    for (final byte in bytes) {
      if (_dropping) {
        if (byte == 10) _dropping = false;
        continue;
      }
      _buffer.add(byte);
      if (_buffer.length > maxFrameBytes) {
        _buffer.clear();
        _dropping = byte != 10;
        rejected++;
      } else if (byte == 10) {
        try {
          frames.add(Frame.decode(_buffer));
        } on FormatException {
          rejected++;
        }
        _buffer.clear();
      }
    }
    return frames;
  }
}

class Snapshot {
  final FigureConfig config;
  final ArraySpec array;
  final String boot, mode, state, reason;
  final int sample, uptimeMs, revision, stroke;
  final bool output, simulated, scanOn;
  final Point<double> focus;
  final int zUm;
  final List<int> phases;
  Snapshot(Map<String, String> f)
    : config = FigureConfig.parse(f),
      array = ArraySpec.parse(f),
      boot = f['boot'] ?? '',
      mode = f['mode'] ?? '',
      state = f['state'] ?? '',
      reason = f['reason'] ?? 'NONE',
      sample = integer(f['sample']),
      uptimeMs = integer(f['uptime_ms']),
      revision = integer(f['rev']),
      stroke = integer(f['stroke_index']),
      output = f['output'] == '1',
      simulated = f['simulated'] == '1',
      scanOn = f['scan_on'] == '1',
      focus = Point(integer(f['fx_um']) / 1000, integer(f['fy_um']) / 1000),
      zUm = integer(f['fz_um']),
      phases = (f['phases'] ?? '').split(',').map(integer).toList() {
    if (boot.isEmpty ||
        min(sample, min(uptimeMs, revision)) < 0 ||
        !['LOCAL', 'REMOTE'].contains(mode) ||
        !['IDLE', 'RUNNING', 'PAUSED', 'FAULT'].contains(state) ||
        [
          'output',
          'simulated',
          'scan_on',
        ].any((key) => !['0', '1'].contains(f[key]))) {
      throw const FormatException('设备状态无效');
    }
    final strokes = config.shape == 'CUSTOM' && config.strokes.isNotEmpty
        ? config.strokes.length
        : 1;
    if (stroke < 0 ||
        stroke >= strokes ||
        (output &&
            (state != 'RUNNING' || config.value('level') == 0 || !scanOn)) ||
        focus.x.abs() > 400 ||
        focus.y.abs() > 400 ||
        zUm < 20000 ||
        zUm > 300000 ||
        phases.length != array.count ||
        phases.any((n) => n < 0 || n >= config.value('phase_steps'))) {
      throw const FormatException('设备回传不完整或相互矛盾');
    }
  }
}
