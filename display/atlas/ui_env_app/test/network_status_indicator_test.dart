import 'dart:async';
import 'package:dbus/dbus.dart';
import 'package:deskmate_display/network_status_indicator.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('ConnMan variants decode active Wi-Fi and wired links separately', () {
    final value = DBusArray(DBusSignature('(oa{sv})'), [
      DBusStruct([
        DBusObjectPath('/saved'),
        DBusDict.stringVariant({
          'Type': const DBusString('wifi'),
          'State': const DBusString('idle'),
          'Name': const DBusString('Saved network'),
          'Strength': const DBusByte(100),
        })
      ]),
      DBusStruct([
        DBusObjectPath('/wifi'),
        DBusDict.stringVariant({
          'Type': const DBusString('wifi'),
          'State': const DBusString('ready'),
          'Name': const DBusString('DESKMATE'),
          'Strength': const DBusByte(72),
        })
      ]),
      DBusStruct([
        DBusObjectPath('/ethernet'),
        DBusDict.stringVariant({
          'Type': const DBusString('ethernet'),
          'State': const DBusString('ready'),
          'Error': const DBusString('online-check-failed'),
        })
      ]),
    ]);
    final status = AtlasNetworkStatusSource.decode(value);
    expect(status.wifiConnected, isTrue);
    expect(status.ethernetConnected, isTrue);
    expect(status.ssid, 'DESKMATE');
    expect(status.strength, 72);
    expect(status.label, contains('신호 72%'));
    expect(status.label, contains('유선 LAN 연결됨'));
    expect(status.label, isNot(contains('인터넷 연결됨')));
  });

  test(
      'disconnected, connecting and weak Wi-Fi do not invent signal or connectivity',
      () {
    final saved = DeskNetworkStatus.fromServices([
      {'Type': 'wifi', 'State': 'idle', 'Name': 'Saved', 'Strength': 100},
      {'Type': 'ethernet', 'State': 'ready'},
    ]);
    expect(saved.wifiConnected, isFalse);
    expect(saved.ethernetConnected, isTrue);
    expect(saved.label, startsWith('Wi-Fi 미연결'));
    final joining = DeskNetworkStatus.fromServices([
      {'Type': 'wifi', 'State': 'association', 'Strength': 40}
    ]);
    expect(joining.wifiConnecting, isTrue);
    expect(joining.wifiConnected, isFalse);
    final weak = DeskNetworkStatus.fromServices([
      {'Type': 'wifi', 'State': 'online', 'Strength': 0}
    ]);
    expect(weak.strength, 0);
    expect(weak.wifiConnected, isTrue);
    expect(
        DeskNetworkStatus.fromServices([
          {'Type': 'wifi', 'State': 'online', 'Strength': 999}
        ]).strength,
        isNull);
  });

  testWidgets(
      'indicator refreshes, explains status on tap, and recovers from failure',
      (tester) async {
    final source = _Source()
      ..value = const DeskNetworkStatus(ethernetConnected: true);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Center(child: NetworkStatusIndicator(source: source)))));
    await tester.pump();
    expect(find.byKey(const ValueKey('network-ethernet')), findsOneWidget);
    expect(tester.widget<Tooltip>(find.byType(Tooltip)).message,
        contains('Wi-Fi 미연결'));
    source.value =
        const DeskNetworkStatus(wifiConnected: true, ssid: '작업실', strength: 82);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(find.byKey(const ValueKey('network-ethernet')), findsNothing);
    await tester.tap(find.byType(NetworkStatusIndicator));
    await tester.pump();
    expect(find.textContaining('신호 82%'), findsOneWidget);
    source.fail = true;
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(tester.widget<Tooltip>(find.byType(Tooltip)).message,
        contains('확인할 수 없어요'));
    source.fail = false;
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(
        tester.widget<Tooltip>(find.byType(Tooltip)).message, contains('작업실'));
    await tester.pumpWidget(const SizedBox());
    final reads = source.reads;
    await tester.pump(const Duration(seconds: 10));
    expect(source.reads, reads);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'slow status replies time out visually and cannot update disposed UI',
      (tester) async {
    final pending = Completer<DeskNetworkStatus>();
    final source = _Source()..pending = pending;
    await tester
        .pumpWidget(MaterialApp(home: NetworkStatusIndicator(source: source)));
    await tester.pump(const Duration(seconds: 3));
    expect(tester.widget<Tooltip>(find.byType(Tooltip)).message,
        contains('확인할 수 없어요'));
    await tester.pumpWidget(const SizedBox());
    pending.complete(const DeskNetworkStatus(wifiConnected: true));
    await tester.pump();
    expect(tester.takeException(), isNull);
  });
}

class _Source implements NetworkStatusSource {
  DeskNetworkStatus value = const DeskNetworkStatus();
  bool fail = false;
  int reads = 0;
  Completer<DeskNetworkStatus>? pending;
  @override
  Future<DeskNetworkStatus> read() async {
    reads++;
    if (pending != null) return pending!.future;
    if (fail) throw StateError('unavailable');
    return value;
  }

  @override
  Future<void> close() async {}
}
