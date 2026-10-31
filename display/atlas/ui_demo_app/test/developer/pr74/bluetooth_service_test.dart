import 'package:dbus/dbus.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_service.dart';
import 'package:flutter_test/flutter_test.dart';

// ui_env_app/test/bluetooth_service_test.dart 의 GATT 링크 대기 시험을 옮긴 것.
void main() {
  DBusArray gattStatus(String? state) => DBusArray(DBusSignature('a{sv}'), [
        if (state != null)
          DBusDict(DBusSignature.string, DBusSignature.variant, {
            const DBusString('adapterAddress'):
                const DBusVariant(DBusString('88:a2:9e:3c:cc:b5')),
            const DBusString('address'):
                const DBusVariant(DBusString('c0:18:d5:08:e4:6c')),
            const DBusString('status'): DBusVariant(DBusString(state)),
          }),
      ]);

  test('GATT Connect asks for a direct (manual) connection', () {
    // ATLAS Gatt.Connect auto_connect: 0 = connect whenever the device shows
    // up (background), 1 = connect now.
    expect(gattManualConnect, 1);
  });

  test('link wait returns the adapter once GATT status is connected', () async {
    final replies = [null, 'connecting', 'connecting', 'connected'];
    final seen = <String>[];
    var calls = 0;

    final link = await waitForGattLink(
        () async => gattStatus(replies[calls++]), 'C0:18:D5:08:E4:6C',
        interval: Duration.zero, onChange: (state, _) => seen.add(state));

    expect(link.adapter, '88:a2:9e:3c:cc:b5');
    expect(link.state, 'connected');
    expect(calls, 4);
    expect(seen, ['', 'connecting', 'connected']);
  });

  test('link wait accepts a link that is already discovering', () async {
    final link = await waitForGattLink(
        () async => gattStatus('Discovering'), 'c0:18:d5:08:e4:6c',
        interval: Duration.zero);

    expect(link.state, 'discovering');
  });

  test('link wait gives up when the link never comes up', () async {
    // 10-26 Pi 5: the lamp never reached "connected"; discovering anyway
    // failed with "Device with supplied address is not available".
    var calls = 0;
    await expectLater(
      waitForGattLink(() async {
        calls++;
        return gattStatus('connecting');
      }, 'C0:18:D5:08:E4:6C',
          timeout: const Duration(milliseconds: 30),
          interval: const Duration(milliseconds: 5)),
      throwsA(isA<StateError>().having(
          (e) => e.message, 'message', contains('"connecting"'))),
    );
    expect(calls, greaterThan(1));
  });
}
