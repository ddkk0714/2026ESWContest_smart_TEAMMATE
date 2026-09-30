import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const firstAddress = 'AA:BB:CC:DD:EE:01';
  const secondAddress = 'AA:BB:CC:DD:EE:02';

  test('a failed write discards GATT and succeeds after one reconnection',
      () async {
    final bluetooth = _FakeBluetooth()..writeFailuresRemaining = 1;
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);

    await lamp.apply('focus');

    expect(bluetooth.connectedAddresses, [firstAddress, firstAddress]);
    expect(bluetooth.writeCount, 1 + ilinkFramesForPhase('focus').length);
    expect(lamp.connected, isTrue);
    expect(lamp.lastError, isNull);
    expect(lamp.lastOkAt, isNotNull);
  });

  test('two failed writes leave no cached connection and next phase retries',
      () async {
    final bluetooth = _FakeBluetooth()..writeFailuresRemaining = 2;
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);

    await lamp.apply('focus');
    expect(bluetooth.connectedAddresses, [firstAddress, firstAddress]);
    expect(lamp.connected, isFalse);
    expect(lamp.lastError, isA<StateError>());

    await lamp.apply('recovery');
    expect(bluetooth.connectedAddresses,
        [firstAddress, firstAddress, firstAddress]);
    expect(lamp.connected, isTrue);
    expect(lamp.lastError, isNull);
  });

  test('connect reports success and failure without throwing', () async {
    final bluetooth = _FakeBluetooth()..connectFailuresRemaining = 1;
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);

    expect(await lamp.connect(), isFalse);
    expect(lamp.connected, isFalse);
    expect(lamp.lastError, isA<StateError>());
    expect(await lamp.connect(), isTrue);
    expect(lamp.connected, isTrue);
    expect(lamp.lastError, isNull);
  });

  test('selecting another address clears old connection and records', () async {
    final bluetooth = _FakeBluetooth();
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);
    await lamp.apply('focus');
    expect(lamp.lastOkAt, isNotNull);

    lamp.select(secondAddress);
    expect(lamp.address, secondAddress);
    expect(lamp.connected, isFalse);
    expect(lamp.lastOkAt, isNull);
    expect(lamp.lastError, isNull);
    await lamp.apply('focus');
    expect(bluetooth.connectedAddresses, [firstAddress, secondAddress]);
  });

  test('an unselected lamp does not connect or write', () async {
    final bluetooth = _FakeBluetooth();
    final lamp = IlinkLampFeedback(bluetooth);

    expect(await lamp.connect(), isFalse);
    await lamp.apply('focus');

    expect(lamp.address, isNull);
    expect(bluetooth.connectedAddresses, isEmpty);
    expect(bluetooth.writeCount, 0);
  });

  test('coordinator exposes the selected speaker address', () {
    final bluetooth = _FakeBluetooth();
    final coordinator = FeedbackCoordinator(
      music: _FakeMusic(),
      lamp: IlinkLampFeedback(bluetooth),
      bluetooth: bluetooth,
    );

    expect(coordinator.speakerAddress, isNull);
    coordinator.selectSpeaker(firstAddress);
    expect(coordinator.speakerAddress, firstAddress);
  });
}

// implements는 실제 시스템 D-Bus 클라이언트를 생성하지 않는다.
class _FakeBluetooth implements AtlasBluetoothService {
  int connectFailuresRemaining = 0;
  int writeFailuresRemaining = 0;
  int writeCount = 0;
  final List<String> connectedAddresses = [];

  @override
  Future<GattConnection> connectLamp(String address) async {
    connectedAddresses.add(address);
    if (connectFailuresRemaining > 0) {
      connectFailuresRemaining--;
      throw StateError('GATT connect failed');
    }
    return GattConnection(
      adapter: 'test-adapter',
      clientId: 'client-${connectedAddresses.length}',
    );
  }

  @override
  Future<void> writeCharacteristic(
    GattConnection connection, {
    required String serviceId,
    required String characteristicId,
    required List<int> value,
  }) async {
    writeCount++;
    if (writeFailuresRemaining > 0) {
      writeFailuresRemaining--;
      throw StateError('GATT write failed');
    }
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}

class _FakeMusic implements MusicPlayback {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}
