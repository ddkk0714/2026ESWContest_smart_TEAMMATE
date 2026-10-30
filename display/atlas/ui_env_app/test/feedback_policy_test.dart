import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/bluetooth_control_page.dart';
import 'package:deskmate_display/bluetooth_link.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/lamp_defaults.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:deskmate_display/voice_alert.dart';
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
    final manual = <(String, int?, int)>[];
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: BluetoothControlPage(
                bluetooth: bluetooth,
                feedback: feedback,
                music: music,
                links: links,
                onManualAdjust: (t, from, to) => manual.add((t, from, to))))));
    await tester.ensureVisible(find.byKey(const ValueKey('lamp-test-bright')));
    await tester.tap(find.byKey(const ValueKey('lamp-test-bright')));
    await tester.pumpAndSettle();
    expect(bluetooth.frames.last, ilinkFrame([1, 8, 1, 255]));
    // 사람이 직접 바꾼 값은 manual_adjust 로: 처음엔 이전 값을 모른다(null), 두 번째는 255 → 64
    expect(manual, [('desk_lamp', null, 255)]);
    await tester.ensureVisible(find.byKey(const ValueKey('lamp-test-dim')));
    await tester.tap(find.byKey(const ValueKey('lamp-test-dim')));
    await tester.pumpAndSettle();
    expect(manual.last, ('desk_lamp', 255, 64));
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

  group('adjust_brightness', () {
    Map<String, dynamic> adjust(String id, num percent,
            {String gate = 'auto'}) =>
        {
          ..._command(id),
          'operation': 'adjust_brightness',
          'value': {'percent': percent},
          'gate': gate,
        };

    test('computes relative brightness with floor, ceiling and a minimum step',
        () {
      expect(adjustBrightness(current: 110, percent: 60, floor: 30),
          (base: 110, actual: 176));
      expect(adjustBrightness(current: 110, percent: -50, floor: 30),
          (base: 110, actual: 55));
      // 하한
      expect(adjustBrightness(current: 110, percent: -90, floor: 30),
          (base: 110, actual: 30));
      // 상한
      expect(adjustBrightness(current: 200, percent: 60, floor: 30),
          (base: 200, actual: 255));
      // 꺼짐·모름 → 계산 기준 = 하한
      expect(adjustBrightness(current: null, percent: 60, floor: 30),
          (base: 30, actual: 48));
      // 올리는데 반올림이 기준과 같으면 최소 단계(+10)
      expect(adjustBrightness(current: 30, percent: 1, floor: 30),
          (base: 30, actual: 40));
      // 반올림이 이미 기준과 다르면 최소 단계 없이 그 값(250 × 1.01 = 252.5 → 253)
      expect(adjustBrightness(current: 250, percent: 1, floor: 30),
          (base: 250, actual: 253));
      // 254 에서 +1 % 는 256.5 → 상한 255
      expect(adjustBrightness(current: 254, percent: 1, floor: 30),
          (base: 254, actual: 255));
      // 255 는 더 못 올린다
      expect(adjustBrightness(current: 255, percent: 10, floor: 30),
          (base: 255, actual: 255));
      // 내릴 때 기준이 하한이면 그대로
      expect(adjustBrightness(current: 30, percent: -20, floor: 30),
          (base: 30, actual: 30));
      // 0 % 는 그대로
      expect(adjustBrightness(current: 90, percent: 0, floor: 30),
          (base: 90, actual: 90));
      // 기준이 하한보다 낮으면 하한으로 올려 잡는다
      expect(adjustBrightness(current: 10, percent: 50, floor: 30),
          (base: 30, actual: 45));
      // min_raw: 결과가 그보다 낮으면 거기까지(깨우기 = +60 %, 적어도 153)
      expect(adjustBrightness(current: 48, percent: 60, floor: 30, minRaw: 153),
          (base: 48, actual: 153));
      expect(
          adjustBrightness(current: 200, percent: 10, floor: 30, minRaw: 153),
          (base: 200, actual: 220));
      expect(
          adjustBrightness(current: null, percent: 60, floor: 30, minRaw: 153),
          (base: 30, actual: 153));
    });

    test('min_raw in the command lifts the result; invalid min_raw is ignored',
        () async {
      final bluetooth = _FakeBluetooth();
      final coordinator = FeedbackCoordinator(
          music: _FakeMusic(),
          lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
          bluetooth: bluetooth)
        ..enabled = true;
      await coordinator.lamp.apply('start'); // 48
      final wake = await coordinator.handleControlCommand({
        ...adjust('w1', 60),
        'value': {'percent': 60, 'min_raw': 153}
      });
      expect(wake?['previous'], 48);
      expect(wake?['actual'], 153);
      expect(bluetooth.frames.last, ilinkFrame([1, 8, 1, 153]));
      final bad = await coordinator.handleControlCommand({
        ...adjust('w2', 10),
        'value': {'percent': 10, 'min_raw': 999}
      });
      _expectLampTransport(bad);
      expect(bad?['actual'], 168);
    });

    test(
        'lamp tracks the last written brightness from phase lighting and commands',
        () async {
      final bluetooth = _FakeBluetooth();
      final lamp = IlinkLampFeedback(bluetooth)..select(firstAddress);
      expect(lamp.currentBrightness, isNull);
      expect(lamp.previousBrightness, isNull);
      await lamp.apply('focus');
      expect(lamp.currentBrightness, 110);
      expect(lamp.previousBrightness, 110);
      await lamp.apply('fatigue');
      expect(lamp.currentBrightness, 180);
      // 끄면 계산 기준은 하한(null), 실제 이전 상태는 0
      await lamp.apply('idle');
      expect(lamp.currentBrightness, isNull);
      expect(lamp.previousBrightness, 0);
      expect(lamp.poweredOn, isFalse);
      await lamp.setBrightness(70);
      expect(lamp.currentBrightness, 70);
      // 실패한 쓰기는 기준을 바꾸지 않는다
      bluetooth.writeFailuresRemaining = 2;
      expect(await lamp.setBrightness(200), isFalse);
      expect(lamp.currentBrightness, 70);
      // 다른 램프를 고르면 모름
      lamp.select('AA:BB:CC:DD:EE:09');
      expect(lamp.currentBrightness, isNull);
    });

    test(
        'hub adjust_brightness uses the tracked baseline and reports previous/actual',
        () async {
      final bluetooth = _FakeBluetooth();
      final coordinator = FeedbackCoordinator(
          music: _FakeMusic(),
          lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
          bluetooth: bluetooth)
        ..enabled = true;
      // 국면 조명으로 110 을 쓴 뒤 +60 %
      await coordinator.lamp.apply('focus');
      final up = await coordinator.handleControlCommand(adjust('a1', 60));
      _expectLampTransport(up);
      expect(up?['previous'], 110);
      expect(up?['actual'], 176);

      expect(bluetooth.frames.sublist(bluetooth.frames.length - 2), [
        ilinkFrame([1, 8, 5, 1]),
        ilinkFrame([1, 8, 1, 176]),
      ]);
      expect(coordinator.lamp.currentBrightness, 176);
      // 이어서 −50 % 는 176 기준
      final down = await coordinator.handleControlCommand(adjust('a2', -50));
      expect(down?['previous'], 176);
      expect(down?['actual'], 88);
      // hub set_brightness 뒤에는 그 값이 기준
      await coordinator.handleControlCommand({..._command('s1'), 'value': 40});
      final fromSet = await coordinator.handleControlCommand(adjust('a3', 50));
      expect(fromSet?['previous'], 40);
      expect(fromSet?['actual'], 60);
      // 되돌리기는 hub 가 set_brightness <previous> 로 보낸다(기존 경로)
      final undo = await coordinator.handleControlCommand({
        ..._command('u1'),
        'gate': 'undo',
        'undo_of_command_id': 'a3',
        'value': 40
      });
      _expectLampTransport(undo);
      expect(coordinator.lamp.currentBrightness, 40);
    });

    test(
        'unknown lamp: base is the floor and previous is omitted; off lamp: previous is 0',
        () async {
      final bluetooth = _FakeBluetooth();
      final coordinator = FeedbackCoordinator(
          music: _FakeMusic(),
          lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
          bluetooth: bluetooth)
        ..enabled = true;
      expect(coordinator.brightnessFloor, kLampBrightnessFloorDefault);
      // 모름(앱 시작 뒤 한 번도 안 씀): 계산 기준 30, previous 는 넣지 않는다(hub 가 되돌리기 생략)
      final unknown = await coordinator.handleControlCommand(adjust('f1', 60));
      _expectLampTransport(unknown);
      expect(unknown?.containsKey('previous'), isFalse);
      expect(unknown?['actual'], 48);

      // 꺼짐: 계산 기준은 하한(50), previous 는 0(되돌리기 set_brightness 0 = 끔)
      await coordinator.handleControlCommand({
        ..._command('baseline-off'),
        'operation': 'set_power',
        'value': 'off'
      });
      coordinator.brightnessFloor = 50;
      final off = await coordinator.handleControlCommand(adjust('f2', 20));
      expect(off?['previous'], 0);
      expect(off?['actual'], 60);
      // 전원 켜기 프레임이 먼저
      expect(bluetooth.frames[bluetooth.frames.length - 2],
          ilinkFrame([1, 8, 5, 1]));
    });

    test(
        'adjust_brightness is refused like set_brightness when disabled, expired, unconfigured or malformed',
        () async {
      final bluetooth = _FakeBluetooth();
      final coordinator = FeedbackCoordinator(
          music: _FakeMusic(),
          lamp: IlinkLampFeedback(bluetooth)..select(firstAddress),
          bluetooth: bluetooth);
      expect(
          (await coordinator
              .handleControlCommand(adjust('d1', 60)))?['error_code'],
          'feedback_disabled');
      coordinator.enabled = true;
      expect(
          (await coordinator.handleControlCommand(
              {...adjust('e1', 60), 'expires_ts_ms': 1}))?['error_code'],
          'expired');
      expect(
          (await coordinator.handleControlCommand(
              {...adjust('b1', 60), 'value': 60}))?['error_code'],
          'unsupported_operation_or_value');
      expect(
          (await coordinator.handleControlCommand({
            ...adjust('b2', 60),
            'value': {'percent': 'x'}
          }))?['error_code'],
          'unsupported_operation_or_value');
      expect(bluetooth.writeCount, 0);
      final none = FeedbackCoordinator(
          music: _FakeMusic(),
          lamp: IlinkLampFeedback(bluetooth),
          bluetooth: bluetooth)
        ..enabled = true;
      expect((await none.handleControlCommand(adjust('n1', 60)))?['error_code'],
          'lamp_not_configured');
    });
  });

  group('lamp rediscovery', () {
    // 10-26 Pi 5: BlueZ forgot the unpaired lamp 30 s after it disconnected, so
    // every hub command failed in 56 ms with bluetooth_write_failed.
    FeedbackCoordinator coordinator(_FakeBluetooth bluetooth) =>
        FeedbackCoordinator(
            music: _FakeMusic(),
            lamp: IlinkLampFeedback(bluetooth, settleDelay: Duration.zero)
              ..select(firstAddress),
            bluetooth: bluetooth)
          ..enabled = true;

    test('a forgotten lamp is found by a scan and the command succeeds',
        () async {
      final bluetooth = _FakeBluetooth()
        ..forgotten = true
        ..visible = [firstAddress];
      final result =
          await coordinator(bluetooth).handleControlCommand(_command('r1'));
      _expectLampTransport(result);
      expect(result?['recovery'], 'rediscovered');
      expect(bluetooth.scans, 1);
      expect(bluetooth.frames.last, ilinkFrame([1, 8, 1, 70]));
    });

    test('a lamp no scan can find reports device_not_found', () async {
      final bluetooth = _FakeBluetooth()..forgotten = true;
      final result =
          await coordinator(bluetooth).handleControlCommand(_command('r2'));
      expect(result?['status'], 'failed');
      expect(result?['error_code'], 'device_not_found');
      expect(result?['recovery'], 'not_found');
      expect(bluetooth.frames, isEmpty);
    });

    test('commands scan at most once per window', () async {
      final bluetooth = _FakeBluetooth()..forgotten = true;
      final c = coordinator(bluetooth);
      final first = await c.handleControlCommand(_command('w1'));
      final second = await c.handleControlCommand(_command('w2'));
      expect(bluetooth.scans, 1);
      expect(first?['recovery'], 'not_found');
      expect(second?['error_code'], 'bluetooth_write_failed');
      expect(second?.containsKey('recovery'), isFalse);
    });

    test(
        'phase lighting never scans and backs off a lamp that will not connect',
        () async {
      // 10-26 0.1.8: phase lighting retried every second with a scan each time;
      // the lamp link flapped, the system showed 96 popups, the app crashed.
      final bluetooth = _FakeBluetooth()
        ..forgotten = true
        ..visible = [firstAddress];
      final lamp = IlinkLampFeedback(bluetooth, settleDelay: Duration.zero)
        ..select(firstAddress);
      for (var i = 0; i < 5; i++) {
        await lamp.apply('focus');
      }
      expect(bluetooth.scans, 0);
      expect(bluetooth.connectedAddresses, hasLength(1)); // 첫 시도 한 번, 그 뒤 조용히
    });

    test(
        'background connects: first try, quiet after a failure, one retry after a drop',
        () async {
      final bluetooth = _FakeBluetooth()..connectFailuresRemaining = 1;
      final lamp = IlinkLampFeedback(bluetooth, settleDelay: Duration.zero)
        ..select(firstAddress);
      expect(await lamp.connectInBackground(), isFalse); // 첫 시도 — 실패
      expect(await lamp.connectInBackground(), isFalse); // 실패 뒤엔 BlueZ 를 안 건드림
      expect(bluetooth.connectedAddresses, hasLength(1));

      expect(await lamp.connect(), isTrue); // 사람이 누른 "다시 연결"
      bluetooth.writeFailuresRemaining = 1; // 링크가 끊겨 있었다
      await lamp.apply('focus'); // 끊긴 뒤 한 번 다시 붙어 쓴다
      expect(bluetooth.connectedAddresses, hasLength(3));
      expect(lamp.connected, isTrue);

      bluetooth
        ..writeFailuresRemaining = 1
        ..connectFailuresRemaining = 5;
      await lamp.apply('idle'); // 또 끊김 → 한 번 다시 붙어 보고 실패
      expect(bluetooth.connectedAddresses, hasLength(4));
      await lamp.apply('focus'); // 그 뒤엔 조용히
      await lamp.apply('idle');
      expect(bluetooth.connectedAddresses, hasLength(4));
      expect(lamp.connected, isFalse);
    });

    test('a lamp that connects first time is not scanned', () async {
      final bluetooth = _FakeBluetooth();
      final result =
          await coordinator(bluetooth).handleControlCommand(_command('r3'));
      _expectLampTransport(result);
      expect(result?.containsKey('recovery'), isFalse);
      expect(bluetooth.scans, 0);
    });
  });

  group('speaker commands', () {
    Map<String, dynamic> speakerCommand(
            String id, String operation, Object? value) =>
        {
          'command_id': id,
          'target_id': 'speaker',
          'operation': operation,
          'value': value,
          'origin_state': 'REST',
          'gate': 'notice',
          'requires_confirmation': false,
          'expires_ts_ms': DateTime.now().millisecondsSinceEpoch + 15000,
        };
    FeedbackCoordinator coordinator(
            _FakeAlerts alerts, SpeechOutput? speech, NoticeBoard notices) =>
        FeedbackCoordinator(
            music: _FakeMusic(),
            lamp: IlinkLampFeedback(_FakeBluetooth()),
            bluetooth: _FakeBluetooth(),
            alerts: alerts,
            speech: speech,
            notices: notices)
          ..enabled = true;

    test('speak without TTS falls back to a chime and the text on screen',
        () async {
      // 10-26 Pi 5: com.atlas.VoiceManager1 is not on this board image.
      final alerts = _FakeAlerts();
      final notices = NoticeBoard();
      final result = await coordinator(alerts, _NoSpeech(), notices)
          .handleControlCommand(speakerCommand(
              's1', 'speak', {'text': '1분만 일어나서 쉬어 가세요.', 'lang': 'ko'}));
      expect(result?['status'], 'failed');
      expect(result?['error_code'], 'tts_unavailable');
      expect(result?['fallback'], 'chime_text');
      expect(alerts.played, [kChimeSoft]);
      expect(notices.current(DateTime.now())?.text, '1분만 일어나서 쉬어 가세요.');
      expect(notices.current(DateTime.now())?.requestId, isNull);
    });

    test('speak with TTS succeeds without a chime', () async {
      final alerts = _FakeAlerts();
      final speech = _RecordingSpeech();
      final notices = NoticeBoard();
      final result = await coordinator(alerts, speech, notices)
          .handleControlCommand(
              speakerCommand('s2', 'speak', {'text': '쉬어 가세요', 'lang': 'ko'}));
      expect(result?['status'], 'succeeded');
      expect(result?['actual_value'], {'mode': 'tts'});
      expect(speech.spoken, [('쉬어 가세요', 'ko')]);
      expect(alerts.played, isEmpty);
      expect(notices.current(DateTime.now()), isNull);
    });

    test('chime plays the sound and volume the hub asked for', () async {
      // hub ad76ff0: pattern single|double|triple, volume low|mid|high (sound_pref).
      final alerts = _FakeAlerts();
      final c = coordinator(alerts, null, NoticeBoard());
      final triple = await c.handleControlCommand(speakerCommand(
          'c1', 'chime', {'pattern': 'triple', 'volume': 'low'}));
      expect(triple?['status'], 'succeeded');
      expect(triple?['actual_value'], {'pattern': 'triple', 'volume': 'low'});
      await c.handleControlCommand(speakerCommand(
          'c1b', 'chime', {'pattern': 'single', 'volume': 'mid'}));
      final plain =
          await c.handleControlCommand(speakerCommand('c2', 'chime', {}));
      expect(plain?['actual_value'], {'pattern': 'double'});
      expect(alerts.played, [kChimeAlert, kChimeSingle, kChimeSoft]);
      expect(alerts.volumes, [0.35, 0.65, 1.0]);
      alerts.fail = true;
      expect(
          (await c.handleControlCommand(
              speakerCommand('c3', 'chime', null)))?['error_code'],
          'audio_failed');
    });

    test('speaker commands follow the switch and reject bad values', () async {
      final c = coordinator(_FakeAlerts(), null, NoticeBoard());
      c.enabled = false;
      expect(
          (await c.handleControlCommand(
              speakerCommand('d1', 'speak', {'text': 'x'})))?['error_code'],
          'feedback_disabled');
      c.enabled = true;
      expect(
          (await c.handleControlCommand(
              speakerCommand('d2', 'speak', {'text': '  '})))?['error_code'],
          'unsupported_operation_or_value');
      expect(
          (await c.handleControlCommand(
              speakerCommand('d3', 'beep', null)))?['error_code'],
          'unsupported_operation_or_value');
      final noSpeaker = FeedbackCoordinator(
          music: _FakeMusic(),
          lamp: IlinkLampFeedback(_FakeBluetooth()),
          bluetooth: _FakeBluetooth())
        ..enabled = true;
      expect(
          (await noSpeaker.handleControlCommand(
              speakerCommand('d4', 'chime', null)))?['error_code'],
          'speaker_unavailable');
    });
  });
}

// implements는 실제 시스템 D-Bus 클라이언트를 생성하지 않는다.
class _FakeBluetooth implements AtlasBluetoothService {
  /// BlueZ 가 램프를 잊은 상태: 검색에 보이기 전까지 Connect 가 바로 실패한다.
  bool forgotten = false;

  /// 검색에 보이는 주소들.
  List<String> visible = const [];
  int scans = 0;
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
    if (forgotten) {
      throw StateError('Device with supplied address is not available');
    }
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
  Stream<List<BluetoothDeviceInfo>> scanProgressive(
      {Duration duration = const Duration(seconds: 8),
      Duration interval = const Duration(seconds: 1)}) async* {
    scans++;
    if (visible.isNotEmpty) forgotten = false;
    yield [
      for (final address in visible)
        BluetoothDeviceInfo(
            address: address,
            name: 'KLZS-L1 app',
            paired: false,
            connected: false),
    ];
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

class _FakeAlerts implements AlertSound {
  final played = <String>[];
  final volumes = <double>[];
  bool fail = false;

  @override
  Future<void> play(String asset, {double volume = 1}) async {
    if (fail) throw StateError('no audio');
    played.add(asset);
    volumes.add(volume);
  }
}

class _NoSpeech implements SpeechOutput {
  @override
  Future<void> speak(String text, {required String lang}) async =>
      throw StateError('org.freedesktop.DBus.Error.ServiceUnknown');
}

class _RecordingSpeech implements SpeechOutput {
  final spoken = <(String, String)>[];

  @override
  Future<void> speak(String text, {required String lang}) async =>
      spoken.add((text, lang));
}

void _expectLampTransport(Map<String, dynamic>? result) {
  expect(result?['status'], 'executing');
  expect(result?['confirmation'], 'transport_only');
  expect(result?['completed_ts_ms'], isNull);
  expect(result?.containsKey('actual_value'), isFalse);
}
