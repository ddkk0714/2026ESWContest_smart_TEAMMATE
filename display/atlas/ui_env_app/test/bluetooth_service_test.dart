import 'package:dbus/dbus.dart';
import 'package:deskmate_display/bluetooth_service.dart';
import 'package:flutter_test/flutter_test.dart';

class _SpeakerClient extends Fake implements DBusClient {
  bool connected = true;
  int connections = 0;

  @override
  Future<DBusMethodSuccessResponse> callMethod({
    String? destination,
    required DBusObjectPath path,
    String? interface,
    required String name,
    Iterable<DBusValue> values = const [],
    DBusSignature? replySignature,
    bool noReplyExpected = false,
    bool noAutoStart = false,
    bool allowInteractiveAuthorization = false,
  }) async {
    if (name == 'GetStatus') {
      return DBusMethodSuccessResponse([
        DBusDict(DBusSignature.string, DBusSignature.variant, {
          const DBusString('connected'): DBusVariant(DBusBoolean(connected)),
        }),
      ]);
    }
    if (name == 'Connect') {
      connections++;
      if (connected) throw StateError('Already Connected');
      connected = true;
      return DBusMethodSuccessResponse([]);
    }
    throw StateError('Unexpected Bluetooth call: $name');
  }
}

/// AudioManager with a Bluetooth and an HDMI output; refuses the route change
/// like the Pi 5 does for an app without perm_audioroute.
class _AudioClient extends Fake implements DBusClient {
  _AudioClient(this.active);

  static final bt = DBusObjectPath('/com/atlas/AudioManager1/Device/bt_speaker');
  static final hdmi =
      DBusObjectPath('/com/atlas/AudioManager1/Device/hdmi_speaker');

  DBusObjectPath active;
  int routeChanges = 0;

  @override
  Future<DBusMethodSuccessResponse> callMethod({
    String? destination,
    required DBusObjectPath path,
    String? interface,
    required String name,
    Iterable<DBusValue> values = const [],
    DBusSignature? replySignature,
    bool noReplyExpected = false,
    bool noAutoStart = false,
    bool allowInteractiveAuthorization = false,
  }) async {
    if (name == 'Get') {
      final property = (values.last as DBusString).value;
      if (property == 'ActiveOutputDevice') {
        return DBusMethodSuccessResponse([DBusVariant(active)]);
      }
      return DBusMethodSuccessResponse([
        DBusVariant(DBusArray(DBusSignature('(oss)'), [
          for (final (device, id) in [(bt, 'bt_speaker'), (hdmi, 'hdmi_speaker')])
            DBusStruct([device, DBusString(id), DBusString(id)]),
        ])),
      ]);
    }
    if (name == 'SetActiveOutputDevice') {
      routeChanges++;
      throw StateError('org.freedesktop.DBus.Error.AccessDenied');
    }
    throw StateError('Unexpected AudioManager call: $name');
  }
}

class _SpeakerService extends AtlasBluetoothService {
  _SpeakerService(_SpeakerClient client) : super(client: client);

  int outputSelections = 0;

  @override
  Future<bool> ensureBluetoothPermission({
    Duration waitForConsent = const Duration(seconds: 20),
  }) async =>
      true;

  @override
  Future<String> adapterAddress() async => '11:22:33:44:55:66';

  @override
  Future<bool> selectBluetoothOutput() async {
    outputSelections++;
    return true;
  }
}

void main() {
  for (final savedAddress in [false, true]) {
    Future<void> connect(_SpeakerService service) => savedAddress
        ? service.connectSpeakerAddress('AA:BB:CC:DD:EE:FF')
        : service.connectSpeaker(const BluetoothDeviceInfo(
            address: 'AA:BB:CC:DD:EE:FF',
            name: 'Speaker',
            paired: true,
            connected: false,
            deviceClass: 0x0400,
          ));

    test('already connected speaker skips Connect (saved=$savedAddress)',
        () async {
      final client = _SpeakerClient();
      final service = _SpeakerService(client);

      await connect(service);

      expect(client.connections, 0);
      expect(service.outputSelections, 1);
    });

    test(
        'disconnected speaker connects and selects output (saved=$savedAddress)',
        () async {
      final client = _SpeakerClient()..connected = false;
      final service = _SpeakerService(client);

      await connect(service);

      expect(client.connections, 1);
      expect(service.outputSelections, 1);
    });
  }

  test('speaker already on the Bluetooth output needs no route change',
      () async {
    // 10-24 Pi 5: dbus refused SetActiveOutputDevice from the app, so every
    // reconnect of the working speaker ended as "연결 끊김".
    final client = _AudioClient(_AudioClient.bt);

    expect(await AtlasBluetoothService(client: client).selectBluetoothOutput(),
        isTrue);
    expect(client.routeChanges, 0);
  });

  test('speaker on another output asks AudioManager to switch', () async {
    final client = _AudioClient(_AudioClient.hdmi);

    await expectLater(
        AtlasBluetoothService(client: client).selectBluetoothOutput(),
        throwsStateError);
    expect(client.routeChanges, 1);
  });

  test('GATT write payload carries the frame under "bytes"', () {
    // 10-12 Pi 5: Atlas rejected {"value": ...} with "Invalid value input for
    // GATT characteristic" and accepted {"bytes": ...}; the lamp then changed.
    final payload = gattWritePayload([0x55, 0xaa, 0x01]);

    expect(payload.children.keys, [const DBusString('bytes')]);
    final value = (payload.children[const DBusString('bytes')]! as DBusVariant)
        .value as DBusArray;
    expect(value.children.map((b) => (b as DBusByte).value), [0x55, 0xaa, 0x01]);
  });

  test('adapter address is read from GATT status for the connected lamp', () {
    // 10-12 Pi 5: Adapter.GetInfo gave the app an empty adapter, so
    // DiscoverServices failed with "Profile backend is not available".
    DBusDict entry(String adapter, String address) =>
        DBusDict(DBusSignature.string, DBusSignature.variant, {
          const DBusString('adapterAddress'): DBusVariant(DBusString(adapter)),
          const DBusString('address'): DBusVariant(DBusString(address)),
          const DBusString('status'): const DBusVariant(DBusString('connected')),
        });
    final status = DBusArray(DBusSignature('a{sv}'), [
      entry('11:11:11:11:11:11', 'aa:bb:cc:dd:ee:ff'),
      entry('88:a2:9e:3c:cc:b5', 'c0:18:d5:08:e4:6c'),
    ]);

    expect(gattAdapterFor(status, 'C0:18:D5:08:E4:6C'), '88:a2:9e:3c:cc:b5');
    expect(gattAdapterFor(status, '00:00:00:00:00:00'), '');
  });

  test('service discovery is retried after an early failure', () async {
    // Right after Connect, Atlas answered "GATT service discovery failed";
    // the same call succeeded a few seconds later.
    var calls = 0;
    await discoverWithRetry(() async {
      calls++;
      if (calls < 3) throw StateError('GATT service discovery failed');
    }, delay: Duration.zero);

    expect(calls, 3);
  });

  test('service discovery gives up with the last error', () async {
    var calls = 0;
    await expectLater(
      discoverWithRetry(() async {
        calls++;
        throw StateError('GATT service discovery failed');
      }, attempts: 2, delay: Duration.zero),
      throwsStateError,
    );
    expect(calls, 2);
  });
}
