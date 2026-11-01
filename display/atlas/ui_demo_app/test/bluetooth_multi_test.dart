import 'dart:async';
import 'package:dbus/dbus.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_service.dart';
import 'package:deskmate_ui_demo/developer/pr74/feedback_policy.dart';
import 'package:deskmate_ui_demo/developer/pr74/fan_control.dart';

class LeaseService extends Fake implements AtlasBluetoothService {
  int next = 0;
  final released = <String>[];
  Completer<GattConnection>? waiting;
  bool failWrite = false;
  final written = <String>[];
  @override
  Future<void> writeCharacteristic(GattConnection connection,
      {required String serviceId,
      required String characteristicId,
      required List<int> value}) async {
    written.add(connection.clientId);
    if (failWrite) {
      failWrite = false;
      throw StateError('link lost');
    }
  }

  @override
  Future<List<int>?> readCharacteristic(GattConnection connection,
          {required String serviceId,
          required String characteristicId}) async =>
      null;
  @override
  Future<GattConnection> connectLamp(String address) async => waiting == null
      ? GattConnection(adapter: 'adapter', clientId: 'lease-${++next}')
      : waiting!.future;
  @override
  Future<void> disconnectGatt(GattConnection connection) async {
    released.add(connection.clientId);
  }
}

class GattClient extends Fake implements DBusClient {
  int active = 0, peak = 0, next = 0;
  final disconnected = <String>[];
  final linked = <String>[];
  @override
  Future<DBusMethodSuccessResponse> callMethod(
      {String? destination,
      required DBusObjectPath path,
      String? interface,
      required String name,
      Iterable<DBusValue> values = const [],
      DBusSignature? replySignature,
      bool noReplyExpected = false,
      bool noAutoStart = false,
      bool allowInteractiveAuthorization = false}) async {
    if (name == 'Connect') {
      active++;
      if (active > peak) peak = active;
      final id = 'client-${++next}';
      await Future<void>.delayed(const Duration(milliseconds: 15));
      linked.add(values.elementAt(1).asString());
      return DBusMethodSuccessResponse([DBusString(id)]);
    }
    if (name == 'GetStatus') {
      return DBusMethodSuccessResponse([
        DBusArray(DBusSignature('a{sv}'), [
          for (final address in linked)
            DBusDict(DBusSignature.string, DBusSignature.variant, {
              const DBusString('adapterAddress'):
                  const DBusVariant(DBusString('11:22:33:44:55:66')),
              const DBusString('address'): DBusVariant(DBusString(address)),
              const DBusString('status'):
                  const DBusVariant(DBusString('connected')),
            }),
        ]),
      ]);
    }
    if (name == 'DiscoverServices') {
      await Future<void>.delayed(const Duration(milliseconds: 15));
      active--;
      return DBusMethodSuccessResponse([]);
    }
    if (name == 'Disconnect') {
      disconnected.add(values.last.asString());
      return DBusMethodSuccessResponse([]);
    }
    throw StateError('unexpected $name');
  }

  @override
  Future<void> close() async {}
}

class Service extends AtlasBluetoothService {
  Service(superClient) : super(client: superClient);
  @override
  Future<bool> ensureBluetoothPermission(
          {Duration waitForConsent = const Duration(seconds: 20)}) async =>
      true;
  @override
  Future<String> adapterAddress() async => '11:22:33:44:55:66';
}

void main() {
  test('fan write retry releases failed lease before reconnecting', () async {
    final service = LeaseService()..failWrite = true;
    final fan = BleFanControl(service)..select('AA:BB:CC:DD:EE:02');
    final result = await fan.send([1]);
    expect(result.written, isTrue);
    expect(service.released, ['lease-1']);
    expect(service.written, ['lease-1', 'lease-2']);
  });
  test('late fan connection is released after forgetting the device', () async {
    final service = LeaseService()..waiting = Completer<GattConnection>();
    final fan = BleFanControl(service)..select('AA:BB:CC:DD:EE:02');
    final pending = fan.connect();
    fan.forget();
    service.waiting!
        .complete(GattConnection(adapter: 'adapter', clientId: 'stale'));
    expect(await pending, isFalse);
    expect(fan.connected, isFalse);
    expect(service.released, ['stale']);
  });
  test('changing lamp and forgetting fan release only their own GATT leases',
      () async {
    final service = LeaseService();
    final lamp = IlinkLampFeedback(service)..select('AA:BB:CC:DD:EE:01');
    final fan = BleFanControl(service)..select('AA:BB:CC:DD:EE:02');
    await lamp.connect();
    await fan.connect();
    lamp.select('AA:BB:CC:DD:EE:03');
    await Future<void>.delayed(Duration.zero);
    expect(service.released, ['lease-1']);
    expect(fan.connected, isTrue);
    fan.forget();
    await Future<void>.delayed(Duration.zero);
    expect(service.released, ['lease-1', 'lease-2']);
  });
  test('late lamp connection cannot replace a newly selected device', () async {
    final service = LeaseService()..waiting = Completer<GattConnection>();
    final lamp = IlinkLampFeedback(service)..select('AA:BB:CC:DD:EE:01');
    final pending = lamp.connect();
    lamp.select('AA:BB:CC:DD:EE:02');
    service.waiting!
        .complete(GattConnection(adapter: 'adapter', clientId: 'stale'));
    expect(await pending, isFalse);
    expect(lamp.connected, isFalse);
    expect(service.released, ['stale']);
  });
  test(
      'three GATT devices keep distinct clients while connecting one at a time',
      () async {
    final client = GattClient();
    final liveService = Service(client);
    final connections = await Future.wait([
      for (final tail in ['01', '02', '03'])
        liveService.connectLamp('AA:BB:CC:DD:EE:$tail')
    ]);
    expect(client.peak, 1);
    expect(connections.map((c) => c.clientId).toSet().length, 3);
    await liveService.close();
    expect(client.disconnected.toSet().length, 3);
  });
  test('connecting the same address twice reuses its existing GATT client',
      () async {
    final client = GattClient();
    final liveService = Service(client);
    final a = await liveService.connectLamp('AA:BB:CC:DD:EE:01');
    final b = await liveService.connectLamp('AA:BB:CC:DD:EE:01');
    expect(a.clientId, b.clientId);
    expect(client.next, 1);
    await liveService.close();
  });
}
