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

  group('discoverUntilServices (V36)', () {
    List<DBusValue> reply(String? uuid) => [
          DBusArray(DBusSignature('a{sv}'), [
            if (uuid != null)
              DBusDict(DBusSignature.string, DBusSignature.variant, {
                const DBusString('uuid'): DBusVariant(DBusString(uuid)),
              }),
          ])
        ];
    const fan = 'f1970001-51e1-4b0e-a7f1-8d27d8e34683';

    test('an empty service list is retried until the services show up',
        () async {
      final replies = [reply(null), reply(null), reply(fan)];
      final empties = <int>[];
      var calls = 0;
      final got = await discoverUntilServices(() async => replies[calls++],
          emptyDelay: Duration.zero, onEmpty: empties.add);
      expect(gattServiceUuids(got), {fan});
      expect(calls, 3);
      expect(empties, [1, 2]);
    });

    test('services on the first answer are taken at once', () async {
      var calls = 0;
      final got = await discoverUntilServices(() async {
        calls++;
        return reply(fan);
      }, emptyDelay: Duration.zero);
      expect(gattServiceUuids(got), {fan});
      expect(calls, 1);
    });

    test('still empty after the retries returns the empty list as before',
        () async {
      var calls = 0;
      final got = await discoverUntilServices(() async {
        calls++;
        return reply(null);
      }, emptyRetries: 3, emptyDelay: Duration.zero);
      expect(gattServiceUuids(got), isEmpty);
      expect(calls, 4); // 처음 1 + 다시 3
    });

    test('errors keep the existing error retry and still rethrow at the end',
        () async {
      var calls = 0;
      final errors = <int>[];
      final got = await discoverUntilServices(() async {
        if (++calls == 1) throw StateError('GATT service discovery failed');
        return reply(fan);
      },
          delay: Duration.zero,
          emptyDelay: Duration.zero,
          onError: (_, attempt) => errors.add(attempt));
      expect(gattServiceUuids(got), {fan});
      expect(errors, [1]);
      await expectLater(
          discoverUntilServices(() async => throw StateError('x'),
              attempts: 2, delay: Duration.zero),
          throwsStateError);
    });
  });
}
