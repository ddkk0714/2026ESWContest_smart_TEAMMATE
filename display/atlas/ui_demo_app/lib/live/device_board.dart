// `deskmate/control/cmd` + `deskmate/control/result` → 기기 위젯에 보일 지금 상태 한 줄.
//
// 표시만 한다. 블루투스 실행·결과 발행은 com.atlas.app.deskmate_display(ui_env_app)가 맡고,
// V18 은 그 결과(succeeded)를 받아 "무드등 밝기 176" 처럼 보여 줄 뿐 명령을 보내지 않는다.
library;

class DeviceBoard {
  final _commands = <String, ({String target, String operation, Object? value})>{};

  /// target_id → 상태 문구(기기 이름 제외), 예: desk_lamp → '밝기 176'.
  final states = <String, String>{};

  static const names = {
    'desk_lamp': '무드등',
    'vent_fan': '선풍기',
    'air_purifier': '공기청정기',
    'plug_1': '플러그',
  };

  /// control/cmd. 결과가 올 때 무엇을 바꿨는지 알기 위해 기억만 한다(최근 64 개).
  void onCommand(Map<String, dynamic> data) {
    final id = data['command_id'];
    final target = data['target_id'];
    final operation = data['operation'];
    if (id is! String || target is! String || operation is! String) return;
    _commands[id] = (target: target, operation: operation, value: data['value']);
    while (_commands.length > 64) {
      _commands.remove(_commands.keys.first);
    }
  }

  /// control/result. succeeded 일 때만 상태를 바꾼다. 바뀌었으면 true.
  bool onResult(Map<String, dynamic> data) {
    if (data['status'] != 'succeeded') return false;
    final command = _commands[data['command_id']];
    if (command == null) return false;
    final actual = data['actual_value'] ?? data['actual'] ?? command.value;
    final text = describe(command.target, command.operation, actual);
    if (text == null || states[command.target] == text) return false;
    states[command.target] = text;
    return true;
  }

  /// 명령 하나 → 상태 문구. 모르는 조합은 null(지어내지 않는다).
  static String? describe(String target, String operation, Object? value) {
    String? number(Object? v) => v is num
        ? v.round().toString()
        : v is String && num.tryParse(v) != null
            ? num.parse(v).round().toString()
            : null;
    final on = value == 'on' || value == true;
    final off = value == 'off' || value == false;
    if (operation == 'set_power') {
      return on
          ? '켜짐'
          : off
              ? '꺼짐'
              : null;
    }
    if (target == 'desk_lamp') {
      return switch (operation) {
        'set_brightness' || 'adjust_brightness' => number(value) == null
            ? '밝기 맞춤'
            : number(value) == '0'
                ? '꺼짐'
                : '밝기 ${number(value)}',
        'set_color_temperature' =>
          number(value) == null ? '빛 온도 맞춤' : '빛 온도 ${number(value)}단계',
        _ => null,
      };
    }
    if (target == 'vent_fan') {
      return switch (operation) {
        'set_level' => number(value) == null
            ? null
            : number(value) == '0'
                ? '꺼짐'
                : '${number(value)}단',
        'set_speed' => number(value) == null ? null : '바람 ${number(value)}%',
        'set_mode' => value == 'natural'
            ? '자연풍'
            : value == 'normal'
                ? '일반풍'
                : null,
        'set_timer' => number(value) == null
            ? null
            : '${((num.parse(number(value)!)) / 60).round()}분 뒤 꺼짐',
        _ => null,
      };
    }
    if (operation == 'set_mode' && value is String) {
      return switch (value) {
        'high' => '세게',
        'auto' => '자동',
        _ => null,
      };
    }
    return null;
  }

  String? text(String target) => states[target];

  /// '무드등 밝기 176 · 선풍기 2단'. 아무것도 모르면 null.
  String? get summary {
    final parts = [
      for (final target in names.keys)
        if (states[target] case final s?) '${names[target]} $s',
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }
}
