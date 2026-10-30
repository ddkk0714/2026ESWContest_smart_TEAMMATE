import 'dart:io';

import 'package:dbus/dbus.dart';
import 'package:deskmate_display/bluetooth_link.dart';
import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/fan_control.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/link_status.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:flutter_test/flutter_test.dart';

const _fanAddress = 'AA:BB:CC:00:00:FA';
const _fanInfo = BluetoothDeviceInfo(
    address: 'aa:bb:cc:00:00:fa',
    name: 'DESKMATE-FAN',
    paired: false,
    connected: false);

/// Firmware state bytes: [version, power, speed, level, mode, dir, timer lo, hi].
List<int> _state(
        {bool power = true, int speed = 60, int level = 2, int timer = 0}) =>
    [1, power ? 1 : 0, speed, level, 0, 0, timer & 0xff, timer >> 8];

void main() {
  group('fanFrame matches the firmware opcodes', () {
    test('accepted operations', () {
      expect(fanFrame('set_power', 'on'), [1, 1]);
      expect(fanFrame('set_power', 'off'), [1, 0]);
      expect(fanFrame('set_level', 3), [3, 3]);
      expect(fanFrame('set_level', 0), [3, 0]);
      expect(fanFrame('set_speed', 45), [2, 45]);
      expect(fanFrame('set_mode', 'natural'), [4, 1]);
      expect(fanFrame('set_mode', 'normal'), [4, 0]);
      expect(fanFrame('set_timer', 1800), [6, 0x08, 0x07]);
    });

    test('anything else is refused', () {
      expect(fanFrame('set_power', true), isNull);
      expect(fanFrame('set_level', 4), isNull);
      expect(fanFrame('set_level', '2'), isNull);
      expect(fanFrame('set_speed', 101), isNull);
      expect(fanFrame('set_timer', 70000), isNull);
      expect(fanFrame('set_mode', 'turbo'), isNull);
      expect(fanFrame('power_cycle', null), isNull);
    });
  });

  test('FanState decodes the firmware record', () {
    final state = FanState.decode([1, 1, 55, 2, 1, 1, 0x58, 0x02])!;
    expect(state.power, isTrue);
    expect(state.level, 2);
    expect(state.natural, isTrue);
    expect(state.timerSeconds, 600);
    expect(state.actualFor('set_power'), 'on');
    expect(state.actualFor('set_mode'), 'natural');
    expect(state.matches('set_timer', 601), isTrue);
    expect(state.matches('set_level', 3), isFalse);
    expect(state.summary, '2단 자연풍 · 10분 뒤 꺼짐');
    expect(FanState.decode([2, 0, 0, 0, 0, 0, 0, 0]), isNull);
    expect(FanState.decode([1, 0]), isNull);
    expect(FanState.decode(null), isNull);
  });

  test('gattReadBytes finds the characteristic bytes in a D-Bus reply', () {
    DBusDict entry(String uuid, DBusValue bytes) =>
        DBusDict(DBusSignature.string, DBusSignature.variant, {
          const DBusString('characteristic'): DBusVariant(DBusString(uuid)),
          const DBusString('value'): DBusVariant(DBusDict(
              DBusSignature.string,
              DBusSignature.variant,
              {const DBusString('bytes'): DBusVariant(bytes)})),
        });
    final reply = [
      DBusArray(DBusSignature('a{sv}'), [
        entry('other', DBusArray.byte([9])),
        entry(fanStateUuid.toUpperCase(), DBusArray.int32([1, 1, 300, 2])),
      ])
    ];
    expect(gattReadBytes(reply, fanStateUuid), [1, 1, 300 & 0xff, 2]);
    expect(gattReadBytes(const [], fanStateUuid), isNull);
  });

  group('vent_fan commands from the hub', () {
    late _FakeBluetooth bluetooth;
    late BleFanControl fan;
    late FeedbackCoordinator coordinator;

    setUp(() {
      bluetooth = _FakeBluetooth();
      fan = BleFanControl(bluetooth, retryDelay: Duration.zero);
      coordinator = FeedbackCoordinator(
          music: _NoMusic(),
          lamp: IlinkLampFeedback(bluetooth),
          bluetooth: bluetooth,
          fan: fan)
        ..enabled = true;
    });

    test('are left to another adapter while no fan is selected', () async {
      expect(await coordinator.handleControlCommand(_command('c0')), isNull);
      expect(bluetooth.frames, isEmpty);
    });

    test('power on is written and confirmed from the fan state', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      final result = await coordinator.handleControlCommand(_command('c1'));
      expect(bluetooth.frames, [
        [1, 1]
      ]);
      expect(bluetooth.serviceIds.single, fanServiceUuid);
      expect(result?['status'], 'succeeded');
      expect(result?['actual_value'], 'on');
      expect(fan.lastState?.level, 2);
    });

    test('a fan found again by a scan reports recovery rediscovered', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      bluetooth.connectFailuresRemaining = 2; // 두 번 실패 → 검색 → 세 번째에 붙음
      final result = await coordinator.handleControlCommand(_command('c1r'));
      expect(bluetooth.scans, 1);
      expect(result?['status'], 'succeeded');
      expect(result?['recovery'], 'rediscovered');
      expect(fan.lastRediscovered, isTrue);
      // 이미 붙어 있으면 다음 명령엔 recovery 가 없다.
      final next = await coordinator.handleControlCommand(_command('c1s'));
      expect(next?.containsKey('recovery'), isFalse);
    });

    test('level change reports the level the fan is at', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state(speed: 100, level: 3);
      final result = await coordinator.handleControlCommand(
          {..._command('c2'), 'operation': 'set_level', 'value': 3});
      expect(bluetooth.frames.single, [3, 3]);
      expect(result?['status'], 'succeeded');
      expect(result?['actual_value'], 3);
    });

    test('a state that disagrees is a failure', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state(power: false, level: 0);
      final result = await coordinator.handleControlCommand(_command('c3'));
      expect(result?['status'], 'failed');
      expect(result?['error_code'], 'state_mismatch');
      expect(result?['actual_value'], 'off');
    });

    test('unreadable state is transport only and never fabricates actual state',
        () async {
      fan.select(_fanAddress);
      bluetooth.state = null; // read throws
      final result = await coordinator.handleControlCommand(_command('c4'));
      expect(result?['status'], 'executing');
      expect(result?['confirmation'], 'transport_only');
      expect(result?['error_code'], 'device_state_unconfirmed');
      expect(result?['completed_ts_ms'], isNull);
      expect(result?.containsKey('actual_value'), isFalse);
      expect(bluetooth.frames, [
        [1, 1]
      ]);
    });

    test('valid readback explicitly identifies reported state confirmation',
        () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      final result =
          await coordinator.handleControlCommand(_command('readback'));
      expect(result?['confirmation'], 'state_readback');
      expect(result?['status'], 'succeeded');
      expect(result?['actual_value'], 'on');
    });

    test('malformed state cannot become a confirmed power-off', () async {
      fan.select(_fanAddress);
      bluetooth.state = [1, 2, 0, 0, 0, 0, 0, 0];
      final result = await coordinator
          .handleControlCommand({..._command('malformed'), 'value': 'off'});
      expect(result?['status'], 'executing');
      expect(result?.containsKey('actual_value'), isFalse);
    });

    test('Mock and stale undo never reach the selected fan', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      final mock = await coordinator.handleControlCommand(
          {..._command('mock-fan'), 'execution_mode': 'mock'});
      final undo = await coordinator.handleControlCommand({
        ..._command('undo-old'),
        'gate': 'undo',
        'undo_of_command_id': 'unknown'
      });
      expect(mock?['error_code'], 'mock_command_blocked');
      expect(undo?['error_code'], 'superseded_command');
      expect(bluetooth.frames, isEmpty);
    });

    test('matching fan undo is bound to its own latest command', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      await coordinator.handleControlCommand(_command('fan-parent'));
      bluetooth.state = _state(power: false, level: 0);
      final undo = await coordinator.handleControlCommand({
        ..._command('fan-undo'),
        'gate': 'undo',
        'undo_of_command_id': 'fan-parent',
        'value': 'off'
      });
      expect(undo?['status'], 'succeeded');
      expect(bluetooth.frames.length, 2);
    });

    test('countdown reports the read value rather than requested seconds',
        () async {
      fan.select(_fanAddress);
      bluetooth.state = _state(timer: 599);
      final result = await coordinator.handleControlCommand(
          {..._command('timer'), 'operation': 'set_timer', 'value': 600});
      expect(result?['status'], 'succeeded');
      expect(result?['actual_value'], 599);
      expect(result?['confirmation'], 'state_readback');
    });

    test('disabled feedback during connect cancels the pending fan write',
        () async {
      fan.select(_fanAddress);
      bluetooth.connectDelay = const Duration(milliseconds: 40);
      final pending =
          coordinator.handleControlCommand(_command('disable-race'));
      await Future<void>.delayed(const Duration(milliseconds: 5));
      coordinator.enabled = false;
      expect((await pending)?['error_code'], 'feedback_disabled');
      expect(bluetooth.frames, isEmpty);
    });

    test('expiry while connecting is rechecked immediately before writing',
        () async {
      fan.select(_fanAddress);
      bluetooth.connectDelay = const Duration(milliseconds: 40);
      final result = await coordinator.handleControlCommand({
        ..._command('expiry-race'),
        'expires_ts_ms': DateTime.now()
            .add(const Duration(milliseconds: 10))
            .millisecondsSinceEpoch
      });
      expect(result?['error_code'], 'expired');
      expect(bluetooth.frames, isEmpty);
    });

    test('selecting another fan invalidates the pending write', () async {
      fan.select(_fanAddress);
      bluetooth.connectDelay = const Duration(milliseconds: 40);
      final pending =
          coordinator.handleControlCommand(_command('selection-race'));
      await Future<void>.delayed(const Duration(milliseconds: 5));
      fan.select('AA:BB:CC:00:00:FB');
      await pending;
      expect(bluetooth.frames, isEmpty);
    });

    test('a dropped connection is retried once on a new session', () async {
      fan.select(_fanAddress);
      bluetooth
        ..state = _state()
        ..writeFailuresRemaining = 1;
      expect(await fan.connect(), isTrue);
      final result = await coordinator.handleControlCommand(_command('c5'));
      expect(result?['status'], 'succeeded');
      expect(bluetooth.connects, 2);
    });

    test('a link that fails to come up is retried, rescanning every other try',
        () async {
      // 10-24 Pi 5: about half the GATT connects to the Pico 2 W fail at once.
      fan.select(_fanAddress);
      bluetooth
        ..state = _state()
        ..connectFailuresRemaining = 3;
      final result = await coordinator.handleControlCommand(_command('c6'));
      expect(result?['status'], 'succeeded');
      expect(bluetooth.connects, 4);
      // A scan right after a failed Connect aborts the link BlueZ is still making: every other try only.
      expect(bluetooth.scans, 1);
    });

    test('gives up after the configured attempts', () async {
      final limited = BleFanControl(bluetooth,
          connectAttempts: 2, retryDelay: Duration.zero)
        ..select(_fanAddress);
      bluetooth.connectFailuresRemaining = 5;
      expect(await limited.connect(), isFalse);
      expect(bluetooth.connects, 2);
      expect(limited.lastError, isA<StateError>());
    });

    test('refusals never reach the fan', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      final confirm = await coordinator.handleControlCommand(
          {..._command('r1'), 'requires_confirmation': true});
      final unsupported = await coordinator.handleControlCommand(
          {..._command('r2'), 'operation': 'set_level', 'value': 9});
      final expired = await coordinator
          .handleControlCommand({..._command('r3'), 'expires_ts_ms': 1});
      coordinator.enabled = false;
      final disabled = await coordinator.handleControlCommand(_command('r4'));
      expect(confirm?['error_code'], 'invalid_command');
      expect(unsupported?['error_code'], 'unsupported_operation_or_value');
      expect(expired?['error_code'], 'expired');
      expect(disabled?['error_code'], 'feedback_disabled');
      expect(bluetooth.frames, isEmpty);
    });

    test('a QoS 1 duplicate does not write twice', () async {
      fan.select(_fanAddress);
      bluetooth.state = _state();
      final results = await Future.wait([
        coordinator.handleControlCommand(_command('d1')),
        coordinator.handleControlCommand(_command('d1')),
      ]);
      expect(results[1], results[0]);
      expect(bluetooth.frames.length, 1);
    });
  });

  group('fan link', () {
    late Directory temp;
    late SettingsStore store;
    late _FakeBluetooth bluetooth;
    late FeedbackCoordinator feedback;

    BluetoothLinkManager manager() => BluetoothLinkManager(
        bluetooth: bluetooth, feedback: feedback, settings: store);

    setUp(() {
      temp = Directory.systemTemp.createTempSync('fan-link-test-');
      store = SettingsStore(
          candidates: ['${temp.path}/settings.json'],
          legacyPostureCandidates: const []);
      bluetooth = _FakeBluetooth();
      feedback = FeedbackCoordinator(
          music: _NoMusic(),
          lamp: IlinkLampFeedback(bluetooth),
          bluetooth: bluetooth,
          fan: BleFanControl(bluetooth));
    });

    tearDown(() => temp.deleteSync(recursive: true));

    test('choosing the fan saves it and restore brings it back', () async {
      final links = manager();
      addTearDown(links.dispose);
      expect(links.fanStatus.health, LinkHealth.unconfigured);
      expect(await links.selectFan(_fanInfo), isTrue);
      expect(store.load().fan?.address, _fanAddress);
      expect(feedback.fan!.configured, isTrue);
      expect(links.fanStatus.health, LinkHealth.ok);

      final again = FeedbackCoordinator(
          music: _NoMusic(),
          lamp: IlinkLampFeedback(bluetooth),
          bluetooth: bluetooth,
          fan: BleFanControl(bluetooth));
      final restored = BluetoothLinkManager(
          bluetooth: bluetooth, feedback: again, settings: store);
      addTearDown(restored.dispose);
      await restored.restore();
      expect(again.fan!.address, _fanAddress);
      expect(restored.savedFan?.name, 'DESKMATE-FAN');
    });

    test('restore connects the lamp and the fan one after the other', () async {
      // 10-24 Pi 5: connecting speaker, lamp and fan at once left only the speaker up.
      store.save(const DeskmateSettings(
        lamp: SavedBluetoothDevice(
            address: 'AA:BB:CC:00:00:02', name: 'KLZS-L1 app'),
        fan: SavedBluetoothDevice(address: _fanAddress, name: 'DESKMATE-FAN'),
      ));
      bluetooth.connectDelay = const Duration(milliseconds: 20);
      final links = manager();
      addTearDown(links.dispose);
      await links.restore();
      expect(bluetooth.connects, 2);
      expect(bluetooth.maxInFlight, 1);
      expect(links.lampStatus.health, LinkHealth.ok);
      expect(links.fanStatus.health, LinkHealth.ok);
    });

    test('forgetting the fan hands vent_fan back to other adapters', () async {
      final links = manager();
      addTearDown(links.dispose);
      await links.selectFan(_fanInfo);
      links.forget(LinkId.fan);
      expect(feedback.fan!.configured, isFalse);
      expect(store.load().fan, isNull);
      feedback.enabled = true;
      expect(await feedback.handleControlCommand(_command('f1')), isNull);
    });
  });
}

Map<String, dynamic> _command(String id) => {
      'command_id': id,
      'target_id': 'vent_fan',
      'operation': 'set_power',
      'value': 'on',
      'gate': 'auto',
      'requires_confirmation': false,
      'expires_ts_ms': DateTime.now()
          .add(const Duration(seconds: 15))
          .millisecondsSinceEpoch,
    };

// implements는 실제 시스템 D-Bus 클라이언트를 생성하지 않는다.
class _FakeBluetooth implements AtlasBluetoothService {
  int connects = 0;
  int connectFailuresRemaining = 0;
  int scans = 0;
  Duration connectDelay = Duration.zero;
  int _inFlight = 0;
  int maxInFlight = 0;
  int writeFailuresRemaining = 0;
  List<int>? state;
  final List<List<int>> frames = [];
  final List<String> serviceIds = [];

  @override
  Future<GattConnection> connectLamp(String address) async {
    connects++;
    if (connectDelay != Duration.zero) await Future<void>.delayed(connectDelay);
    _inFlight++;
    if (_inFlight > maxInFlight) maxInFlight = _inFlight;
    try {
      if (connectDelay > Duration.zero)
        await Future<void>.delayed(connectDelay);
    } finally {
      _inFlight--;
    }
    if (connectFailuresRemaining > 0) {
      connectFailuresRemaining--;
      throw StateError('Failed to connect with remote device');
    }
    return GattConnection(
        adapter: 'test-adapter', clientId: 'client-$connects');
  }

  @override
  Future<void> writeCharacteristic(
    GattConnection connection, {
    required String serviceId,
    required String characteristicId,
    required List<int> value,
  }) async {
    if (writeFailuresRemaining > 0) {
      writeFailuresRemaining--;
      throw StateError('GATT write failed');
    }
    frames.add(List.of(value));
    serviceIds.add(serviceId);
  }

  @override
  Stream<List<BluetoothDeviceInfo>> scanProgressive(
      {Duration duration = const Duration(seconds: 8),
      Duration interval = const Duration(seconds: 1)}) async* {
    scans++;
    yield const [_fanInfo];
  }

  @override
  Future<List<int>?> readCharacteristic(
    GattConnection connection, {
    required String serviceId,
    required String characteristicId,
  }) async {
    final value = state;
    if (value == null) throw StateError('read not supported');
    return value;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}

class _NoMusic implements MusicPlayback {
  @override
  bool get isPlaying => false;

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}
