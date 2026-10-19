import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/bluetooth_control_page.dart';
import 'package:deskmate_display/bluetooth_link.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';

void main() {
  const firstAddress = 'AA:BB:CC:DD:EE:01';
  const secondAddress = 'AA:BB:CC:DD:EE:02';

  test(
      'manual override wins against in-flight phase and delayed environment commands until explicit re-enable',
      () async {
    final bluetooth = _FakeBluetooth()
      ..connectDelay = const Duration(milliseconds: 40);
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(), lamp: lamp, bluetooth: bluetooth)
      ..enabled = true;
    final phase = coordinator.apply(_state('FOCUS_PC', phase: 'focus'));
    await Future<void>.delayed(const Duration(milliseconds: 5));
    final manual = lamp.setBrightness(99);
    await phase;
    expect(await manual, isTrue);
    expect(bluetooth.frames, [
      ilinkFrame([1, 8, 5, 1]),
      ilinkFrame([1, 8, 1, 99])
    ]);
    final count = bluetooth.writeCount;
    await coordinator.apply(_state('RECOVERY', phase: 'recovery'));
    final blocked = await coordinator.handleControlCommand(_command('late'));
    expect(blocked?['status'], 'cancelled');
    expect(blocked?['error_code'], 'manual_override');
    expect(bluetooth.writeCount, count);
    coordinator.enabled = false;
    coordinator.enabled = true;
    expect((await coordinator.handleControlCommand(_command('new')))?['status'],
        'executing');
  });
  test(
      'manual action during environment connect cancels queued automatic write',
      () async {
    final bluetooth = _FakeBluetooth()
      ..connectDelay = const Duration(milliseconds: 40);
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(), lamp: lamp, bluetooth: bluetooth)
      ..enabled = true;
    final automatic = coordinator.handleControlCommand(_command('race'));
    await Future<void>.delayed(const Duration(milliseconds: 5));
    final manual = lamp.setBrightness(33);
    expect((await automatic)?['error_code'], 'manual_override');
    await manual;
    expect(bluetooth.frames.last, ilinkFrame([1, 8, 1, 33]));
    expect(bluetooth.frames.length, 2);
  });
  test(
      'environment owns brightness through MONITOR/recovery; stale undo and Mock never actuate',
      () async {
    final bluetooth = _FakeBluetooth();
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(),
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth)
      ..enabled = true;
    await coordinator.handleControlCommand(_command('first'));
    final count = bluetooth.writeCount;
    await coordinator.apply(_state('MONITOR', phase: 'focus'));
    await coordinator.apply(_state('RECOVERY', phase: 'recovery'));
    expect(bluetooth.writeCount, count);
    await coordinator
        .handleControlCommand({..._command('second'), 'value': 80});
    expect(
        (await coordinator.handleControlCommand({
          ..._command('old-undo'),
          'gate': 'undo',
          'undo_of_command_id': 'first'
        }))?['error_code'],
        'superseded_command');
    final after = bluetooth.writeCount;
    expect(
        (await coordinator.handleControlCommand(
            {..._command('mock'), 'execution_mode': 'mock'}))?['error_code'],
        'mock_command_blocked');
    expect(bluetooth.writeCount, after);
  });
  testWidgets(
      'manual brightness and music tests work with automatic feedback off',
      (tester) async {
    final bluetooth = _FakeBluetooth();
    final music = _FakeMusic();
    final feedback = FeedbackCoordinator(
        music: music,
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth);
    final links = BluetoothLinkManager(
        bluetooth: bluetooth,
        feedback: feedback,
        settings: SettingsStore(
            candidates: const [], legacyPostureCandidates: const []));
    addTearDown(links.dispose);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: BluetoothControlPage(
                bluetooth: bluetooth,
                feedback: feedback,
                music: music,
                links: links))));
    await tester.ensureVisible(find.byKey(const ValueKey('lamp-test-bright')));
    await tester.tap(find.byKey(const ValueKey('lamp-test-bright')));
    await tester.pumpAndSettle();
    expect(bluetooth.frames.last, ilinkFrame([1, 8, 1, 255]));
    await tester.ensureVisible(find.byKey(const ValueKey('music-test')));
    await tester.tap(find.byKey(const ValueKey('music-test')));
    await tester.pump();
    expect(music.isPlaying, isTrue);
    expect(feedback.enabled, isFalse);
    expect(tester.takeException(), isNull);
  });

  test('a connection that finishes after expiry sends no light frames',
      () async {
    final bluetooth = _FakeBluetooth()
      ..connectDelay = const Duration(milliseconds: 120);
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(),
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth)
      ..enabled = true;
    final result = await coordinator.handleControlCommand({
      ..._command('slow-connect'),
      'expires_ts_ms': DateTime.now()
          .add(const Duration(milliseconds: 50))
          .millisecondsSinceEpoch
    });
    expect(result?['error_code'], 'expired');
    expect(bluetooth.frames, isEmpty);
  });

  test(
      'music failure leaves successful light feedback intact and retries sound',
      () async {
    final bluetooth = _FakeBluetooth();
    final music = _FakeMusic()..toggleFails = true;
    final coordinator = FeedbackCoordinator(
        music: music,
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth)
      ..enabled = true;
    final state = _state('ACTION_POSTURE', gate: 'auto');
    await coordinator.apply(state);
    expect(coordinator.lastAudioError, isA<StateError>());
    expect(bluetooth.frames, hasLength(3));
    music.toggleFails = false;
    await coordinator.apply(state);
    expect(music.isPlaying, isTrue);
    expect(coordinator.lastAudioError, isNull);
    expect(bluetooth.frames, hasLength(3));
  });

  for (final fsm in ['FATIGUE_SUSPECT', 'ACTION_ENV']) {
    test('$fsm must not actuate before a suggestion is accepted', () async {
      final bluetooth = _FakeBluetooth();
      final music = _FakeMusic();
      final coordinator = FeedbackCoordinator(
        music: music,
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth,
      )..enabled = true;
      await coordinator.apply(_state(fsm, gate: 'suggest'));
      expect(bluetooth.writeCount, 0);
      expect(music.isPlaying, isFalse);
    });
  }

  test('an unchanged phase retries a failed lamp write', () async {
    final bluetooth = _FakeBluetooth()..writeFailuresRemaining = 2;
    final coordinator = FeedbackCoordinator(
      music: _FakeMusic(),
      lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
      bluetooth: bluetooth,
    )..enabled = true;
    final state = _state('FOCUS_PC', phase: 'focus');
    await coordinator.apply(state);
    expect(coordinator.lamp.connected, isFalse);
    await coordinator.apply(state);
    expect(coordinator.lamp.connected, isTrue);
    expect(bluetooth.writeCount, 2 + ilinkFramesForPhase('focus').length);
  });

  test(
      'accepted local suggestion retries failed writes until it leaves the action',
      () async {
    final bluetooth = _FakeBluetooth()..writeFailuresRemaining = 2;
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(),
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth)
      ..enabled = true;
    final suggestion = _state('ACTION_POSTURE', gate: 'suggest');
    await coordinator.apply(suggestion);
    await coordinator.accept(suggestion);
    expect(coordinator.lamp.connected, isFalse);
    await coordinator.apply(suggestion);
    expect(coordinator.lamp.connected, isTrue);
    final count = bluetooth.writeCount;
    await coordinator.apply(_state('MONITOR'));
    await coordinator.apply(suggestion);
    expect(bluetooth.writeCount, count,
        reason: 'A later suggestion requires a new acceptance');
  });

  test('acceptance and auto actions run once even within the fatigue phase',
      () async {
    final bluetooth = _FakeBluetooth();
    final music = _FakeMusic();
    final coordinator = FeedbackCoordinator(
        music: music,
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth)
      ..enabled = true;
    final suggestion = _state('ACTION_BREAK', gate: 'suggest');
    await coordinator.apply(_state('FATIGUE_SUSPECT'));
    await coordinator.apply(suggestion);
    expect(bluetooth.writeCount, 0);
    await coordinator.accept(suggestion);
    expect(bluetooth.writeCount, 3);
    expect(music.isPlaying, isTrue);
    await coordinator.apply(_state('ACTION_BREAK', gate: 'auto'));
    expect(bluetooth.writeCount, 3);
    await coordinator.apply(_state('RECOVERY', phase: 'recovery'));
    expect(music.isPlaying, isFalse);
  });

  test(
      'hub command writes requested brightness and deduplicates concurrent delivery',
      () async {
    final bluetooth = _FakeBluetooth();
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(),
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth)
      ..enabled = true;
    final command = _command('c1');
    final results = await Future.wait([
      coordinator.handleControlCommand(command),
      coordinator.handleControlCommand(command),
    ]);
    expect(results[0]?['status'], 'executing');
    expect(results[0]?.containsKey('actual_value'), isFalse);
    expect(results[0]?['confirmation'], 'transport_only');
    expect(results[0]?['completed_ts_ms'], isNull);
    expect(results[1], results[0]);
    expect(bluetooth.frames, [
      ilinkFrame([1, 8, 5, 1]),
      ilinkFrame([1, 8, 1, 70])
    ]);
    expect(
        await coordinator.handleControlCommand(
            {..._command('other'), 'target_id': 'vent_fan'}),
        isNull);
    expect(bluetooth.writeCount, 2);
    final undo = await coordinator.handleControlCommand({
      ..._command('undo'),
      'gate': 'undo',
      'value': 40,
      'undo_of_command_id': 'c1'
    });
    expect(undo?['status'], 'executing');
    expect(bluetooth.frames.last, ilinkFrame([1, 8, 1, 40]));
  });

  test(
      'disabled, expired, malformed and disconnected commands report failure without success',
      () async {
    final bluetooth = _FakeBluetooth();
    final coordinator = FeedbackCoordinator(
        music: _FakeMusic(),
        lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
        bluetooth: bluetooth);
    expect(
        (await coordinator
            .handleControlCommand(_command('disabled')))?['error_code'],
        'feedback_disabled');
    coordinator.enabled = true;
    expect(
        (await coordinator.handleControlCommand(
            {..._command('expired'), 'expires_ts_ms': 1}))?['error_code'],
        'expired');
    expect(
        (await coordinator.handleControlCommand({
          ..._command('bad'),
          'requires_confirmation': true
        }))?['error_code'],
        'invalid_command');
    expect(
        (await coordinator.handleControlCommand(
            {..._command('bad-value'), 'value': 256}))?['status'],
        'failed');
    expect(bluetooth.writeCount, 0);
    bluetooth.writeFailuresRemaining = 2;
    expect(
        (await coordinator
            .handleControlCommand(_command('disconnected')))?['error_code'],
        'bluetooth_write_failed');
    expect(
        (await coordinator.handleControlCommand(_command('retry')))?['status'],
        'executing');
  });

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

  test('concurrent lamp connects share one GATT Connect', () async {
    // 10-12 Pi 5: restore, selection and phase retries raced and the losers
    // got "Already Connected", leaving the app without a usable session.
    final bluetooth = _FakeBluetooth()
      ..connectDelay = const Duration(milliseconds: 20);
    final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);

    final results = await Future.wait([lamp.connect(), lamp.connect()]);

    expect(results, [true, true]);
    expect(bluetooth.connectedAddresses, [firstAddress]);
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
  Duration connectDelay = Duration.zero;
  final List<String> connectedAddresses = [];
  final List<List<int>> frames = [];

  @override
  Future<GattConnection> connectLamp(String address) async {
    if (connectDelay > Duration.zero) await Future<void>.delayed(connectDelay);
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
    frames.add(List.of(value));
    if (writeFailuresRemaining > 0) {
      writeFailuresRemaining--;
      throw StateError('GATT write failed');
    }
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}

Map<String, dynamic> _command(String id) => {
      'command_id': id,
      'target_id': 'desk_lamp',
      'operation': 'set_brightness',
      'value': 70,
      'gate': 'suggest',
      'requires_confirmation': false,
      'expires_ts_ms': DateTime.now()
          .add(const Duration(seconds: 15))
          .millisecondsSinceEpoch,
    };

class _FakeMusic implements MusicPlayback {
  bool _playing = false;
  bool toggleFails = false;
  @override
  double get volume => 1;
  @override
  bool get isPlaying => _playing;
  @override
  Future<bool> toggle() async {
    if (toggleFails) throw StateError('audio playback failed');
    return _playing = !_playing;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} was not expected');
}

DisplayState _state(String fsm,
        {String gate = 'none', String phase = 'fatigue'}) =>
    DisplayState(
        fsmState: fsm,
        phase: phase,
        context: 'pc',
        focus: .2,
        fatigue: .72,
        confidence: .72,
        gate: gate,
        reasons: const [],
        sequence: 1,
        timestamp: DateTime(2026, 10, 12));
