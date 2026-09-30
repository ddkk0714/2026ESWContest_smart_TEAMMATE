import 'dart:io';

import 'package:deskmate_display/bluetooth_link.dart';
import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/link_status.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:deskmate_display/settings_store.dart';
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

  @override
  Future<void> connectSpeakerAddress(String address) async {
    calls.add('connectSpeakerAddress $address');
    if (speakerFails) throw StateError('A2DP 연결이 완료되지 않았습니다');
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// 램프 재시도 규칙은 Codex 쪽(IlinkLampFeedback) 테스트가 지킨다. 여기서는 결과만 흉내 낸다.
class _FakeLamp extends IlinkLampFeedback {
  _FakeLamp(super.bluetooth);
  bool connectOk = true;
  String? selected;
  int connects = 0;

  @override
  void select(String address) => selected = address;

  @override
  Future<bool> connect() async {
    connects++;
    return connectOk;
  }

  @override
  bool get connected => connectOk && connects > 0;

  @override
  Object? get lastError => connectOk ? null : 'GATT 연결 실패';
}

class _NoMusic implements MusicPlayback {
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
      bluetooth: bluetooth, feedback: feedback, settings: store);

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
}
