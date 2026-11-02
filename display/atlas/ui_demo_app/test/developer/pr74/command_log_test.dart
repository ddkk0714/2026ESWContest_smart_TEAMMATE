import 'package:deskmate_ui_demo/developer/pr74/command_log.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _env(Map<String, Object?> data, {String node = 'hub'}) =>
    {'schema_version': '1.0', 'ts': 1769000002.1, 'node': node, 'data': data};

void main() {
  final at = DateTime(2026, 10, 24, 12, 3, 41);

  test('control/cmd becomes one CMD line with target, operation, value, gate, id',
      () {
    final e = CommandLog.entryFor(
        CommandLog.topicCmd,
        _env({
          'command_id': '9f3c1a2b7d4e',
          'target_id': 'desk_lamp',
          'operation': 'set_brightness',
          'value': 230,
          'origin_state': 'ACTION_ENV',
          'cause': 'environment',
          'gate': 'auto',
          'requires_confirmation': false,
          'expires_ts_ms': 1769000017100,
        }),
        now: at)!;
    expect(e.kind, CommandLogKind.cmd);
    expect(e.line,
        '12:03:41 CMD desk_lamp set_brightness 230 (auto, id 9f3c1a) environment');
    expect(e.ok, isNull);
  });

  test('control/result keeps the error code and node; failed is not ok', () {
    final e = CommandLog.entryFor(
        CommandLog.topicResult,
        _env({
          'command_id': '9f3c1a2b7d4e',
          'status': 'failed',
          'error_code': 'lamp_not_configured',
          'completed_ts_ms': 1,
        }, node: 'display-control'),
        now: at)!;
    expect(e.line,
        '12:03:41 RESULT 9f3c1a failed lamp_not_configured (display-control)');
    expect(e.ok, isFalse);

    final ok = CommandLog.entryFor(
        CommandLog.topicResult,
        _env({
          'command_id': 'abcdef12',
          'status': 'succeeded',
          'actual_value': 'on',
        }, node: 'ble-fan'),
        now: at)!;
    expect(ok.line, '12:03:41 RESULT abcdef succeeded on (ble-fan)');
    expect(ok.ok, isTrue);
  });

  test('interaction/request shows kind, action, explanation, arm, minutes, expiry',
      () {
    final e = CommandLog.entryFor(
        CommandLog.topicRequest,
        _env({
          'request_id': 'a8021bf0-202',
          'kind': 'posture_suggest',
          'prompt_code': 'fix_posture',
          'action': 'fix_posture',
          'options': ['accept', 'reject'],
          'expires_in_s': 180,
          'explanation': '졸음 2회 · 앞으로 기울어짐',
          'arm': 'posture_suggest',
          'arm_label': '자세 제안',
          'ventilate_minutes': null,
        }),
        now: at)!;
    expect(e.kind, CommandLogKind.request);
    expect(e.line,
        '12:03:41 REQUEST posture_suggest fix_posture "졸음 2회 · 앞으로 기울어짐" arm=자세 제안 180 s');

    // prompt_code 만 있는 예전 모양도 읽는다.
    final old = CommandLog.entryFor(
        CommandLog.topicRequest,
        _env({'kind': 'env_suggest', 'prompt_code': 'adjust_environment', 'expires_in_s': 30}),
        now: at)!;
    expect(old.line, '12:03:41 REQUEST env_suggest adjust_environment 30 s');
  });

  test('feedback/user shows verdict, correction and request id; personalization separately',
      () {
    final accept = CommandLog.entryFor(
        CommandLog.topicFeedback,
        _env({'request_id': 'a8021bf0-202', 'verdict': 'accept', 'response_ms': 4200},
            node: 'display'),
        now: at)!;
    expect(accept.kind, CommandLogKind.feedback);
    expect(accept.line, '12:03:41 FEEDBACK accept (req a8021b)');

    final correct = CommandLog.entryFor(
        CommandLog.topicFeedback,
        _env({
          'request_id': 'atlas-display',
          'verdict': 'correct',
          'corrected_state': 'FOCUS_PC',
          'note': '전화 받느라',
        }),
        now: at)!;
    expect(correct.line,
        '12:03:41 FEEDBACK correct → FOCUS_PC "전화 받느라" (req atlas-)');

    final privacy = CommandLog.entryFor(
        CommandLog.topicFeedback,
        _env({'kind': 'personalization', 'request_id': 'p-1', 'action': 'grant'}),
        now: at)!;
    expect(privacy.line, '12:03:41 FEEDBACK personalization grant');
  });

  test('adjust_brightness shows the percent, and its result shows previous→actual', () {
    final cmd = CommandLog.entryFor(
        CommandLog.topicCmd,
        _env({
          'command_id': 'adj001',
          'target_id': 'desk_lamp',
          'operation': 'adjust_brightness',
          'value': {'percent': 60},
          'gate': 'auto',
        }),
        now: at)!;
    expect(cmd.line, '12:03:41 CMD desk_lamp adjust_brightness +60% (auto, id adj001)');
    final minus = CommandLog.entryFor(
        CommandLog.topicCmd,
        _env({
          'command_id': 'adj002',
          'target_id': 'desk_lamp',
          'operation': 'adjust_brightness',
          'value': {'percent': -50},
          'gate': 'undo',
        }),
        now: at)!;
    expect(minus.text, 'desk_lamp adjust_brightness -50% (undo, id adj002)');
    final res = CommandLog.entryFor(
        CommandLog.topicResult,
        _env({
          'command_id': 'adj001',
          'status': 'succeeded',
          'actual_value': 176,
          'previous': 110,
          'actual': 176,
        }, node: 'display-control'),
        now: at)!;
    expect(res.line, '12:03:41 RESULT adj001 succeeded 110→176 (display-control)');
    expect(res.ok, isTrue);
  });

  test('vent_suggest shows minutes and why; manual_adjust feedback shows from→to', () {
    final vent = CommandLog.entryFor(
        CommandLog.topicRequest,
        _env({
          'request_id': 'v-1',
          'kind': 'vent_suggest',
          'minutes': 5,
          'why': ['co2_high', 'outdoor_ok'],
          'expires_in_s': 60,
        }),
        now: at)!;
    expect(vent.line, '12:03:41 REQUEST vent_suggest 5 분 why=co2_high,outdoor_ok 60 s');
    final checkin = CommandLog.entryFor(
        CommandLog.topicRequest,
        _env({'request_id': 'c-1', 'kind': 'checkin_suggest', 'expires_in_s': 30}),
        now: at)!;
    expect(checkin.text, 'checkin_suggest 30 s');
    final manual = CommandLog.entryFor(
        CommandLog.topicFeedback,
        _env({'kind': 'manual_adjust', 'target_id': 'desk_lamp', 'from': 110, 'to': 64, 'ts': 1},
            node: 'display'),
        now: at)!;
    expect(manual.line, '12:03:41 FEEDBACK manual_adjust desk_lamp 110→64');
    final unknownFrom = CommandLog.entryFor(
        CommandLog.topicFeedback,
        _env({'kind': 'manual_adjust', 'target_id': 'vent_fan', 'to': 3, 'ts': 1}),
        now: at)!;
    expect(unknownFrom.text, 'manual_adjust vent_fan ?→3');
  });

  test('unknown topics and messages without data are ignored', () {
    expect(CommandLog.entryFor('deskmate/state/phase', _env({'fsm_state': 'IDLE'})),
        isNull);
    expect(CommandLog.entryFor(CommandLog.topicCmd, {'schema_version': '1.0'}),
        isNull);
    final log = CommandLog();
    log.addMessage('deskmate/health/hub', _env({'status': 'online'}));
    expect(log.isEmpty, isTrue);
  });

  test('keeps only the newest 200 lines and clear empties it', () {
    final log = CommandLog();
    var notified = 0;
    log.addListener(() => notified++);
    for (var i = 0; i < 250; i++) {
      log.addMessage(
          CommandLog.topicCmd,
          _env({
            'command_id': 'cmd$i',
            'target_id': 'vent_fan',
            'operation': 'set_level',
            'value': i % 4,
            'gate': 'auto',
          }));
    }
    expect(log.length, 200);
    expect(log.entries.first.text, startsWith('vent_fan set_level 2 (auto, id cmd50)'));
    expect(log.entries.last.text, startsWith('vent_fan set_level 1 (auto, id cmd249'));
    expect(notified, 250);
    log.clear();
    expect(log.isEmpty, isTrue);
    expect(notified, 251);
    // 비어 있을 때 clear 는 알리지 않는다.
    log.clear();
    expect(notified, 251);
  });
}
