import 'package:deskmate_display/command_log.dart';
import 'package:deskmate_display/device_state_board.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _cmd(String id, String target, String op, Object? value,
        {String gate = 'auto'}) =>
    {
      'schema_version': '1.0',
      'node': 'hub',
      'data': {
        'command_id': id,
        'target_id': target,
        'operation': op,
        'value': value,
        'gate': gate,
        'requires_confirmation': false,
        'expires_ts_ms': 1,
      }
    };

Map<String, dynamic> _result(String id, String status, {String? error}) => {
      'schema_version': '1.0',
      'node': 'display-control',
      'data': {
        'command_id': id,
        'status': status,
        if (error != null) 'error_code': error,
      }
    };

void main() {
  test('starts with the three known devices showing "-"', () {
    final board = DeviceStateBoard();
    expect(board.summary(), '현재: 선풍기 - / 램프 - / 공기청정기 -');
  });

  test('commands accumulate per device and a result marks the last command', () {
    final board = DeviceStateBoard();
    var notified = 0;
    board.addListener(() => notified++);
    board.addMessage(CommandLog.topicCmd, _cmd('c1', 'vent_fan', 'set_power', 'on'));
    board.addMessage(CommandLog.topicCmd, _cmd('c2', 'vent_fan', 'set_level', 2));
    expect(board['vent_fan']!.valueText, 'ON · 풍량 2');
    // 결과가 아직 없다
    expect(board['vent_fan']!.line(), '선풍기 ON · 풍량 2 … 결과 없음');
    board.addMessage(CommandLog.topicResult, _result('c2', 'succeeded'));
    expect(board['vent_fan']!.line(), '선풍기 ON · 풍량 2 ✓');
    expect(notified, 3);
  });

  test('a failed result says 제어 못함 with the error code; the commanded value stays',
      () {
    final board = DeviceStateBoard();
    board.addMessage(
        CommandLog.topicCmd, _cmd('l1', 'desk_lamp', 'set_brightness', 230));
    board.addMessage(CommandLog.topicResult,
        _result('l1', 'failed', error: 'lamp_not_configured'));
    expect(board['desk_lamp']!.line(),
        '램프 밝기 230 ✗ 제어 못함(lamp_not_configured)');
    expect(board.summary(available: {'desk_lamp': false, 'vent_fan': false}),
        '현재: 선풍기 - (기기 없음 · 제어 못함) / 램프 밝기 230 ✗ 제어 못함(lamp_not_configured) (기기 없음 · 제어 못함) / 공기청정기 -');
  });

  test('a device missing in the app is marked even without any result', () {
    final board = DeviceStateBoard();
    board.addMessage(CommandLog.topicCmd, _cmd('f1', 'vent_fan', 'set_power', 'on'));
    expect(board['vent_fan']!.line(available: false),
        '선풍기 ON … 결과 없음 (기기 없음 · 제어 못함)');
    expect(board['vent_fan']!.line(available: true), '선풍기 ON … 결과 없음');
  });

  test('undo overwrites the value; a late result for an older command is ignored',
      () {
    final board = DeviceStateBoard();
    board.addMessage(CommandLog.topicCmd, _cmd('a1', 'vent_fan', 'set_power', 'on'));
    board.addMessage(CommandLog.topicCmd,
        _cmd('a2', 'vent_fan', 'set_power', 'off', gate: 'undo'));
    expect(board['vent_fan']!.valueText, 'OFF');
    expect(board['vent_fan']!.lastGate, 'undo');
    board.addMessage(CommandLog.topicResult, _result('a1', 'succeeded'));
    expect(board['vent_fan']!.resultStatus, isNull);
    board.addMessage(CommandLog.topicResult, _result('a2', 'timeout'));
    expect(board['vent_fan']!.line(), '선풍기 OFF ✗ 제어 못함');
  });

  test('adjust_brightness shows the percent until the result, then 기준→새 값', () {
    final board = DeviceStateBoard();
    board.addMessage(CommandLog.topicCmd, _cmd('b0', 'desk_lamp', 'set_brightness', 110));
    board.addMessage(CommandLog.topicResult, _result('b0', 'succeeded'));
    board.addMessage(
        CommandLog.topicCmd, _cmd('b1', 'desk_lamp', 'adjust_brightness', {'percent': 60}));
    expect(board['desk_lamp']!.line(), '램프 밝기 +60% … 결과 없음');
    board.addMessage(CommandLog.topicResult, {
      'schema_version': '1.0',
      'node': 'display-control',
      'data': {
        'command_id': 'b1',
        'status': 'succeeded',
        'actual_value': 176,
        'previous': 110,
        'actual': 176,
      }
    });
    expect(board['desk_lamp']!.line(), '램프 밝기 110→176 ✓');
    // 다음 명령이 오면 기준 표시는 사라진다
    board.addMessage(CommandLog.topicCmd, _cmd('b2', 'desk_lamp', 'set_brightness', 90));
    expect(board['desk_lamp']!.line(), '램프 밝기 90 … 결과 없음');
    // 실패하면 % 그대로 남고 제어 못함
    board.addMessage(
        CommandLog.topicCmd, _cmd('b3', 'desk_lamp', 'adjust_brightness', {'percent': -30}));
    board.addMessage(CommandLog.topicResult,
        _result('b3', 'failed', error: 'lamp_not_configured'));
    expect(board['desk_lamp']!.line(), '램프 밝기 -30% ✗ 제어 못함(lamp_not_configured)');
  });

  test('unknown targets are appended; other topics and bad data are ignored', () {
    final board = DeviceStateBoard();
    board.addMessage(CommandLog.topicCmd, _cmd('p1', 'plug_1', 'set_power', 'on'));
    expect(board.devices.map((d) => d.target).toList(),
        ['vent_fan', 'desk_lamp', 'air_purifier', 'plug_1']);
    expect(board['plug_1']!.line(), '플러그 ON … 결과 없음');
    board.addMessage(CommandLog.topicRequest, {'data': {'kind': 'x'}});
    board.addMessage(CommandLog.topicCmd, {'data': {'target_id': 'vent_fan'}});
    expect(board['vent_fan']!.valueText, '-');
    board.clear();
    expect(board.summary(), '현재: 선풍기 - / 램프 - / 공기청정기 - / 플러그 -');
  });
}
