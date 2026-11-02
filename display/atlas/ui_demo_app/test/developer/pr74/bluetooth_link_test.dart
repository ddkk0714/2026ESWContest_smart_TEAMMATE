import 'dart:io';

import 'package:deskmate_ui_demo/developer/pr74/bluetooth_control_page.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_link.dart';
import 'package:deskmate_ui_demo/developer/pr74/bluetooth_service.dart';
import 'package:deskmate_ui_demo/developer/pr74/fan_control.dart';
import 'package:deskmate_ui_demo/developer/pr74/feedback_policy.dart';
import 'package:deskmate_ui_demo/developer/pr74/link_status.dart';
import 'package:deskmate_ui_demo/developer/pr74/music_playback.dart';
import 'package:deskmate_ui_demo/developer/pr74/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _speakerInfo = BluetoothDeviceInfo(
    address: 'aa:bb:cc:00:00:01',
    name: 'KLZS-L1',
    paired: true,
    connected: false,
    deviceClass: 0x0400); // major class Audio/Video
const _lampInfo = BluetoothDeviceInfo(
    address: 'AA:BB:CC:00:00:02',
    name: 'KLZS-L1 app',
    paired: false,
    connected: false);

/// D-Bus 없이 호출만 기록하는 가짜. 필요한 메서드만 구현하고 나머지는 부르면 실패한다.
class _FakeBluetooth implements AtlasBluetoothService {
  final calls = <String>[];
  bool speakerFails = false;

  @override
  Future<void> connectSpeaker(BluetoothDeviceInfo device) async {
    calls.add('connectSpeaker ${device.address}');
    if (speakerFails) throw StateError('A2DP 연결이 완료되지 않았습니다');
  }

  /// 이만큼은 A2dp.Connect 가 실패한다(V38 재시도). [speakerFails] 면 늘 실패.
  int speakerFailures = 0;

  @override
  Future<void> connectSpeakerAddress(String address) async {
    calls.add('connectSpeakerAddress $address');
    if (speakerFails) throw StateError('A2DP 연결이 완료되지 않았습니다');
    if (speakerFailures > 0) {
      speakerFailures--;
      throw StateError('A2DP 연결이 완료되지 않았습니다');
    }
  }

  @override
  Future<bool> rediscoverSpeaker(String address,
      {Duration limit = const Duration(seconds: 6),
      Duration atLeast = const Duration(seconds: 3)}) async {
    calls.add('rediscoverSpeaker $address');
    return true;
  }

  /// 선풍기 GATT 연결(BleFanControl → connectLamp role '선풍기'). [fanFails] 면 [fanError] 로 실패.
  bool fanFails = false;
  String fanError = 'GATT connect failed';

  @override
  Future<GattConnection> connectLamp(String address,
      {String role = '램프'}) async {
    calls.add('connectLamp $address $role');
    if (fanFails) throw StateError(fanError);
    return GattConnection(adapter: 'a', clientId: 'c-${calls.length}');
  }

  @override
  Future<void> releaseStaleGatt(String adapter, String clientId) async =>
      calls.add('releaseStaleGatt $adapter $clientId');

  bool scanDenied = false;

  @override
  Stream<List<BluetoothDeviceInfo>> scanProgressive(
      {Duration duration = const Duration(seconds: 8),
      Duration interval = const Duration(seconds: 1)}) async* {
    if (scanDenied) throw StateError('Atlas Bluetooth 권한이 허용되지 않았습니다.');
    yield const [_speakerInfo, _lampInfo];
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// 램프 재시도 규칙은 Codex 쪽(IlinkLampFeedback) 테스트가 지킨다. 여기서는 결과만 흉내 낸다.
class _FakeLamp extends IlinkLampFeedback {
  _FakeLamp(super.bluetooth, {this.log})
      : super(settleDelay: Duration.zero, rescanAtLeast: Duration.zero);
  bool connectOk = true;
  String? selected;
  int connects = 0;

  /// 시작 복원 순서를 보려고 연결 시도를 같은 기록에 남긴다.
  final List<String>? log;

  /// 붙어 있다가 끊겨, 백그라운드로 한 번 다시 붙어도 되는 상태(실제 규칙은 feedback_policy_test).
  bool backgroundRetry = false;
  bool _linked = false;

  @override
  void select(String address) => selected = address;

  @override
  Future<bool> connect() async {
    connects++;
    log?.add('lamp connect');
    _linked = connectOk;
    return connectOk;
  }

  @override
  Future<bool> connectInBackground() async {
    if (connects > 0 && !backgroundRetry) return false;
    backgroundRetry = false;
    return connect();
  }

  @override
  bool get connected => _linked;

  @override
  Object? get lastError => connectOk ? null : 'GATT 연결 실패';
}

class _NoMusic implements MusicPlayback {
  @override
  double get volume => 1;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  late Directory temp;
  late SettingsStore store;
  late _FakeBluetooth bluetooth;
  late _FakeLamp lamp;
  late FeedbackCoordinator feedback;

  BluetoothLinkManager manager() => BluetoothLinkManager(
      bluetooth: bluetooth,
      feedback: feedback,
      settings: store,
      speakerRetryDelay: Duration.zero);

  setUp(() {
    temp = Directory.systemTemp.createTempSync('bt-link-test-');
    store = SettingsStore(
        candidates: ['${temp.path}/settings.json'],
        legacyPostureCandidates: const []);
    bluetooth = _FakeBluetooth();
    lamp = _FakeLamp(bluetooth);
    feedback = FeedbackCoordinator(
        music: _NoMusic(), lamp: lamp, bluetooth: bluetooth);
  });

  tearDown(() => temp.deleteSync(recursive: true));

  group('findSpeakerLampPair', () {
    test('pairs an audio sink with the BLE record that extends its name', () {
      final pair = findSpeakerLampPair([_lampInfo, _speakerInfo]);
      expect(pair?.speaker.name, 'KLZS-L1');
      expect(pair?.lamp.name, 'KLZS-L1 app');
    });

    test('unrelated devices do not pair', () {
      const other = BluetoothDeviceInfo(
          address: 'AA:BB:CC:00:00:03',
          name: 'Other lamp',
          paired: false,
          connected: false);
      expect(findSpeakerLampPair([_speakerInfo, other]), isNull);
      expect(findSpeakerLampPair([_lampInfo]), isNull);
    });

    test('normalizes addresses for storage', () {
      expect(normalizeBluetoothAddress(' aa:bb:cc:dd:ee:ff '),
          'AA:BB:CC:DD:EE:FF');
      expect(normalizeBluetoothAddress('AA-BB-CC-DD-EE-FF'), isNull);
    });
  });

  test('nothing saved means restore touches no device', () async {
    final links = manager();
    await links.restore();
    expect(bluetooth.calls, isEmpty);
    expect(lamp.connects, 0);
    expect(links.speakerStatus.health, LinkHealth.unconfigured);
    expect(links.lampStatus.needsAttention, isFalse);
  });

  test('a lamp that failed at start is left alone in the background',
      () async {
    // 10-26 Pi 5: every failed lamp link raised a system popup; they piled up
    // and the screen froze on the ATLAS logo (ui_env_app 0.1.12).
    store.save(const DeskmateSettings(
      lamp: SavedBluetoothDevice(
          address: 'AA:BB:CC:00:00:02', name: 'KLZS-L1 app'),
    ));
    lamp.connectOk = false;
    final links = manager();
    addTearDown(links.dispose);
    await links.restore();
    expect(lamp.connects, 3); // V38: 시작 복원도 선풍기처럼 최대 3번 — 그 이상은 없다
    links.keepLampConnected(every: const Duration(milliseconds: 10));
    await Future<void>.delayed(const Duration(milliseconds: 80));
    expect(lamp.connects, 3);
    expect(links.lampStatus.health, LinkHealth.down);
  });

  test('a lamp that dropped gets one background retry, only while feedback is on',
      () async {
    store.save(const DeskmateSettings(
      lamp: SavedBluetoothDevice(
          address: 'AA:BB:CC:00:00:02', name: 'KLZS-L1 app'),
    ));
    lamp.connectOk = false;
    final links = manager();
    addTearDown(links.dispose);
    await links.restore();
    links.setFeedbackEnabled(true);
    lamp
      ..backgroundRetry = true // 붙어 있다가 끊긴 상태
      ..connectOk = true;
    links.keepLampConnected(every: const Duration(milliseconds: 10));
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(lamp.connects, 4); // 시작 복원 3번 + 끊긴 뒤 한 번
    expect(links.lampStatus.health, LinkHealth.ok);

    lamp
      ..backgroundRetry = true
      ..connectOk = false;
    links.setFeedbackEnabled(false);
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(lamp.connects, 4);
  });

  test('restore reconnects saved speaker and lamp without scanning', () async {
    store.save(const DeskmateSettings(
      speaker:
          SavedBluetoothDevice(address: 'AA:BB:CC:00:00:01', name: 'KLZS-L1'),
      lamp: SavedBluetoothDevice(
          address: 'AA:BB:CC:00:00:02', name: 'KLZS-L1 app'),
    ));
    final links = manager();
    await links.restore();

    expect(bluetooth.calls, ['connectSpeakerAddress AA:BB:CC:00:00:01']);
    expect(feedback.speakerAddress, 'AA:BB:CC:00:00:01');
    expect(lamp.selected, 'AA:BB:CC:00:00:02');
    expect(lamp.connects, 1);
    expect(links.speakerStatus.health, LinkHealth.ok);
    expect(links.lampStatus.health, LinkHealth.ok);
  });

  test('a failed speaker restore is reported and leaves the lamp alone',
      () async {
    store.save(const DeskmateSettings(
      speaker:
          SavedBluetoothDevice(address: 'AA:BB:CC:00:00:01', name: 'KLZS-L1'),
      lamp: SavedBluetoothDevice(
          address: 'AA:BB:CC:00:00:02', name: 'KLZS-L1 app'),
    ));
    bluetooth.speakerFails = true;
    final links = manager();
    await links.restore();

    expect(links.speakerStatus.health, LinkHealth.down);
    expect(links.speakerStatus.cause, LinkCause.disconnected);
    expect(links.speakerStatus.detail, contains('A2DP'));
    expect(links.lampStatus.health, LinkHealth.ok);
    // V38: 선풍기와 같은 틀 — 3번, 두 번째 실패 뒤 한 번 다시 찾기(페어링 포함)
    expect(bluetooth.calls, [
      'connectSpeakerAddress AA:BB:CC:00:00:01',
      'connectSpeakerAddress AA:BB:CC:00:00:01',
      'rediscoverSpeaker AA:BB:CC:00:00:01',
      'connectSpeakerAddress AA:BB:CC:00:00:01',
    ]);
  });

  group('V38 same reconnect for speaker, lamp and fan', () {
    test('an unpaired speaker is found and paired before the last try',
        () async {
      store.save(const DeskmateSettings(
        speaker:
            SavedBluetoothDevice(address: 'AA:BB:CC:00:00:01', name: 'KLZS-L1'),
      ));
      bluetooth.speakerFailures = 2; // paired=false 라 A2dp.Connect 만으로는 실패
      final links = manager();
      await links.restore();
      expect(bluetooth.calls, [
        'connectSpeakerAddress AA:BB:CC:00:00:01',
        'connectSpeakerAddress AA:BB:CC:00:00:01',
        'rediscoverSpeaker AA:BB:CC:00:00:01',
        'connectSpeakerAddress AA:BB:CC:00:00:01',
      ]);
      expect(links.speakerStatus.health, LinkHealth.ok);
      expect(feedback.speakerAddress, 'AA:BB:CC:00:00:01');
    });

    test('a speaker that connects first time is not scanned or paired',
        () async {
      store.save(const DeskmateSettings(
        speaker:
            SavedBluetoothDevice(address: 'AA:BB:CC:00:00:01', name: 'KLZS-L1'),
      ));
      final links = manager();
      await links.restore();
      expect(bluetooth.calls, ['connectSpeakerAddress AA:BB:CC:00:00:01']);
    });

    test('start restore goes speaker, lamp, fan one after another, 3 tries each',
        () async {
      final log = bluetooth.calls;
      final orderedLamp = _FakeLamp(bluetooth, log: log)..connectOk = false;
      final fan = BleFanControl(bluetooth,
          retryDelay: Duration.zero, rescanAtLeast: Duration.zero);
      feedback = FeedbackCoordinator(
          music: _NoMusic(), lamp: orderedLamp, bluetooth: bluetooth, fan: fan);
      store.save(const DeskmateSettings(
        speaker:
            SavedBluetoothDevice(address: 'AA:BB:CC:00:00:01', name: 'KLZS-L1'),
        lamp: SavedBluetoothDevice(
            address: 'AA:BB:CC:00:00:02', name: 'KLZS-L1 app'),
        fan: SavedBluetoothDevice(
            address: 'AA:BB:CC:00:00:03', name: 'DESKMATE-FAN'),
      ));
      bluetooth
        ..speakerFails = true
        ..fanFails = true;
      final links = manager();
      await links.restore();
      final speaker = log.indexWhere((c) => c.startsWith('connectSpeaker'));
      final lampAt = log.indexOf('lamp connect');
      final fanAt = log.indexWhere((c) => c.startsWith('connectLamp'));
      expect(speaker < lampAt && lampAt < fanAt, isTrue, reason: '$log');
      expect(log.where((c) => c.startsWith('connectSpeakerAddress')), hasLength(3));
      expect(log.where((c) => c == 'lamp connect'), hasLength(3));
      expect(log.where((c) => c.startsWith('connectLamp')), hasLength(3));
      // 선풍기 GATT 연결은 진단 로그에 '선풍기' 로 찍힌다
      expect(log.where((c) => c.startsWith('connectLamp')).first,
          'connectLamp AA:BB:CC:00:00:03 선풍기');
      expect(links.speakerStatus.health, LinkHealth.down);
      expect(links.lampStatus.health, LinkHealth.down);
      expect(links.fanStatus.health, LinkHealth.down);
    });

    test('links left by the last run are released before anything connects',
        () async {
      final fan = BleFanControl(bluetooth,
          retryDelay: Duration.zero, rescanAtLeast: Duration.zero);
      feedback = FeedbackCoordinator(
          music: _NoMusic(), lamp: lamp, bluetooth: bluetooth, fan: fan);
      store.save(const DeskmateSettings(
        lamp: SavedBluetoothDevice(
            address: 'AA:BB:CC:00:00:02',
            name: 'KLZS-L1 app',
            gattAdapter: '88:A2:9E:3C:CC:B5',
            gattClient: 'old-lamp'),
        fan: SavedBluetoothDevice(
            address: 'AA:BB:CC:00:00:03',
            name: 'DESKMATE-FAN',
            gattAdapter: '88:A2:9E:3C:CC:B5',
            gattClient: 'old-fan'),
      ));
      final links = manager();
      await links.restore();
      expect(bluetooth.calls.take(3), [
        'releaseStaleGatt 88:A2:9E:3C:CC:B5 old-lamp',
        'releaseStaleGatt 88:A2:9E:3C:CC:B5 old-fan',
        'connectLamp AA:BB:CC:00:00:03 선풍기',
      ]);
      // 새로 맺은 선풍기 링크의 clientId 가 저장된다(다음 실행이 정리하게). 램프는 가짜라 링크가 없다
      final saved = store.load();
      expect(saved.lamp!.gattClient, isNull);
      expect(saved.fan!.gattClient, isNotNull);
      expect(saved.fan!.gattClient, isNot('old-fan'));
      expect(saved.fan!.gattAdapter, 'a');
      // 다음 실행은 그 id 를 정리한다
      final calls = bluetooth.calls.length;
      final next = manager();
      await next.restore();
      expect(bluetooth.calls[calls],
          'releaseStaleGatt a ${saved.fan!.gattClient}');
    });

    test('a link refused as already connected stops early and says why',
        () async {
      final fan = BleFanControl(bluetooth,
          retryDelay: Duration.zero, rescanAtLeast: Duration.zero);
      feedback = FeedbackCoordinator(
          music: _NoMusic(), lamp: lamp, bluetooth: bluetooth, fan: fan);
      store.save(const DeskmateSettings(
        fan: SavedBluetoothDevice(
            address: 'AA:BB:CC:00:00:03', name: 'DESKMATE-FAN'),
      ));
      bluetooth
        ..fanFails = true
        ..fanError = 'org.freedesktop.DBus.Error.Failed: Already connected';
      final links = manager();
      await links.restore();
      expect(bluetooth.calls.where((c) => c.startsWith('connectLamp')),
          hasLength(1)); // 검색·재시도로는 안 풀린다 — 팝업만 늘리지 않는다
      expect(links.fanStatus.health, LinkHealth.down);
      expect(links.fanStatus.cause, LinkCause.staleLink);
      expect(linkGuidance(LinkId.fan, LinkCause.staleLink), contains('껐다 켜'));
    });
  });

  test('connecting saves the device and keeps other settings', () async {
    store.save(const DeskmateSettings(mqttHost: '192.168.0.20'));
    final links = manager();
    await links.connectSpeaker(_speakerInfo);
    await links.selectLamp(_lampInfo);

    final saved = store.load();
    expect(saved.mqttHost, '192.168.0.20');
    expect(saved.speaker?.address, 'AA:BB:CC:00:00:01'); // 대문자로 저장
    expect(saved.speaker?.name, 'KLZS-L1');
    expect(saved.lamp?.address, 'AA:BB:CC:00:00:02');
  });

  test('pair connect stops before the lamp if the speaker fails', () async {
    bluetooth.speakerFails = true;
    final links = manager();
    await expectLater(
        links.connectPair(_speakerInfo, _lampInfo), throwsStateError);
    expect(lamp.selected, isNull);
    expect(store.load().speaker, isNull);
  });

  test('forget removes the device from settings and status', () async {
    final links = manager();
    await links.connectSpeaker(_speakerInfo);
    links.forget(LinkId.speaker);

    expect(store.load().speaker, isNull);
    expect(links.savedSpeaker, isNull);
    expect(links.speakerStatus.health, LinkHealth.unconfigured);
  });

  test('a lamp that cannot connect shows as disconnected', () async {
    lamp.connectOk = false;
    final links = manager();
    final ok = await links.selectLamp(_lampInfo);
    expect(ok, isFalse);
    expect(links.lampStatus.health, LinkHealth.down);
    expect(store.load().lamp, isNotNull); // 저장은 한다 — 다음 전환 때 다시 시도
  });

  test('successful lamp recovery clears the old failed status', () async {
    lamp.connectOk = false;
    final links = manager();
    await links.selectLamp(_lampInfo);
    expect(links.lampStatus.health, LinkHealth.down);
    lamp.connectOk = true;
    await feedback.lamp.connect();
    expect(links.lampStatus.health, LinkHealth.ok);
  });

  test('pair connection reports lamp failure instead of complete success',
      () async {
    lamp.connectOk = false;
    final links = manager();
    expect(await links.connectPair(_speakerInfo, _lampInfo), isFalse);
    expect(links.speakerStatus.health, LinkHealth.ok);
    expect(links.lampStatus.health, LinkHealth.down);
  });

  group('BluetoothControlPage', () {
    Future<void> pumpPage(WidgetTester tester) async {
      tester.view.physicalSize = const Size(1024, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: BluetoothControlPage(
            bluetooth: bluetooth,
            feedback: feedback,
            music: _NoMusic(),
            links: manager(),
          ),
        ),
      ));
    }

    testWidgets('a denied scan re-enables the scan button', (tester) async {
      bluetooth.scanDenied = true;
      await pumpPage(tester);
      await tester.tap(find.byKey(const ValueKey('bluetooth-scan')));
      await tester.pump();
      await tester.pump();

      expect(find.text('기기 검색'), findsOneWidget);
      expect(find.textContaining('권한이 허용되지 않았습니다'), findsOneWidget);
    });

    testWidgets('a found speaker+lamp pair offers one-tap connect',
        (tester) async {
      await pumpPage(tester);
      await tester.tap(find.byKey(const ValueKey('bluetooth-scan')));
      await tester.pump();
      await tester.pump();

      expect(
          find.byKey(const ValueKey('bluetooth-connect-pair')), findsOneWidget);
      expect(find.text('KLZS-L1 한 번에 연결'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
