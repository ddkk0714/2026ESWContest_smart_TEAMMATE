import 'package:dbus/dbus.dart';
import 'package:deskmate_display/bluetooth_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
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
