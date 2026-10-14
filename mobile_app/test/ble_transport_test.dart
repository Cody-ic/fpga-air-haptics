import 'dart:async';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:touchsee_mobile/transport.dart';

const profile = BleProfile();

class FakeCharacteristic extends BluetoothCharacteristic {
  final notifications = StreamController<List<int>>.broadcast();
  final writes = <List<int>>[];
  final modes = <bool>[];
  final events = <String>[];
  bool fail = false, hang = false;
  @override
  final CharacteristicProperties properties;
  FakeCharacteristic(String uuid, this.properties)
    : super(
        remoteId: const DeviceIdentifier('test-device'),
        serviceUuid: Guid(profile.service),
        characteristicUuid: Guid(uuid),
      );
  @override
  Stream<List<int>> get onValueReceived => notifications.stream;
  @override
  Future<bool> setNotifyValue(
    bool notify, {
    int timeout = 15,
    bool forceIndications = false,
  }) async {
    expect(notifications.hasListener, isTrue);
    events.add('notify');
    notifications.add([1, 2]);
    return true;
  }

  @override
  Future<void> write(
    List<int> value, {
    bool withoutResponse = false,
    bool allowLongWrite = false,
    int timeout = 15,
  }) async {
    writes.add(List.of(value));
    modes.add(withoutResponse);
    if (hang) await Completer<void>().future;
    if (fail && writes.length == 2) throw StateError('link lost mid-frame');
  }
}

class FakeService implements BluetoothService {
  @override
  Guid get uuid => Guid(profile.service);
  @override
  final List<BluetoothCharacteristic> characteristics;
  FakeService(this.characteristics);
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeDevice extends BluetoothDevice {
  final states = StreamController<BluetoothConnectionState>.broadcast();
  final writer = FakeCharacteristic(
    profile.tx,
    const CharacteristicProperties(write: true, writeWithoutResponse: true),
  );
  final reader = FakeCharacteristic(
    profile.rx,
    const CharacteristicProperties(notify: true),
  );
  Completer<void>? pendingConnect;
  int disconnects = 0;
  FakeDevice() : super.fromId('test-device');
  @override
  int get mtuNow => 23;
  @override
  Stream<BluetoothConnectionState> get connectionState => states.stream;
  @override
  Future<void> connect({
    Duration timeout = const Duration(seconds: 35),
    int? mtu = 512,
    bool autoConnect = false,
  }) async {
    await pendingConnect?.future;
    states.add(BluetoothConnectionState.connected);
  }

  @override
  Future<List<BluetoothService>> discoverServices({
    bool subscribeToServicesChanged = true,
    int timeout = 15,
  }) async => [
    FakeService([writer, reader]),
  ];
  @override
  Future<void> disconnect({
    int timeout = 35,
    bool queue = true,
    int androidDelay = 2000,
  }) async {
    disconnects++;
    states.add(BluetoothConnectionState.disconnected);
  }

  Future<void> disposeFake() async {
    await states.close();
    await writer.notifications.close();
    await reader.notifications.close();
  }
}

void main() {
  late FakeDevice device;
  late BleTransport transport;
  final received = <int>[], errors = <String>[];
  void create([BleProfile value = profile]) {
    device = FakeDevice();
    transport = BleTransport(device, value);
    received.clear();
    errors.clear();
    transport.bytes.listen(received.addAll);
    transport.errors.listen(errors.add);
    addTearDown(() async {
      await transport.close();
      await device.disposeFake();
    });
  }

  test(
    'subscribes before data arrives; MTU fragments preserve bytes and mode',
    () async {
      create();
      await transport.open();
      final frame = List.generate(53, (i) => i);
      await transport.write(frame);
      await Future<void>.delayed(Duration.zero);
      expect(received, [1, 2]);
      expect(device.reader.events, ['notify']);
      expect(device.writer.writes.map((p) => p.length), [20, 20, 13]);
      expect(device.writer.writes.expand((p) => p), frame);
      expect(device.writer.modes, everyElement(false));
    },
  );
  test('user cap and no-response mode are respected', () async {
    create(const BleProfile(packetBytes: 7, mode: 'without-response'));
    await transport.open();
    await transport.write(List.filled(20, 10));
    expect(device.writer.writes.map((p) => p.length), [7, 7, 6]);
    expect(device.writer.modes, everyElement(true));
  });
  test('partial write failure forbids retries and further frames', () async {
    create();
    await transport.open();
    device.writer.fail = true;
    await expectLater(transport.write(List.filled(61, 65)), throwsStateError);
    await expectLater(transport.write([10]), throwsStateError);
    expect(device.writer.writes.length, 2);
  });
  test(
    'hung write terminates the adapter and rejects the next frame',
    () async {
      create();
      await transport.open();
      device.writer.hang = true;
      await expectLater(transport.write([1]), throwsA(isA<TimeoutException>()));
      await expectLater(transport.write([2]), throwsStateError);
      expect(device.writer.writes.length, 1);
    },
  );
  test('cancelled open tears down a connection that arrives late', () async {
    create();
    device.pendingConnect = Completer<void>();
    final opening = transport.open();
    final rejected = expectLater(opening, throwsStateError);
    await transport.close();
    expect(device.disconnects, 1);
    device.pendingConnect!.complete();
    await rejected;
    expect(device.disconnects, 2);
  });
  test('unexpected disconnection reports loss and blocks writing', () async {
    create();
    await transport.open();
    device.states.add(BluetoothConnectionState.disconnected);
    await Future<void>.delayed(Duration.zero);
    expect(errors.single, contains('未知'));
    await expectLater(transport.write([1]), throwsStateError);
  });
  test('profiles normalize short UUIDs and reject invalid settings', () {
    expect(
      BleProfile.normalize('FFE1'),
      '0000ffe1-0000-1000-8000-00805f9b34fb',
    );
    expect(
      () => const BleProfile(service: 'xyz').validated(),
      throwsFormatException,
    );
    expect(
      () => const BleProfile(packetBytes: 513).validated(),
      throwsFormatException,
    );
  });
}
