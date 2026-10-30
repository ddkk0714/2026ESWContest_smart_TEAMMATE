/// 기기별 "현재 상태" 판 — hub 가 내린 **마지막 명령 기준**이다. 블루투스로 실제 실행됐는지와 무관하다.
///
/// 주변기기 블루투스가 안 붙어도 "현재: 선풍기 ON · 풍량 2" 처럼 hub 가 원하는 상태를 글로 보려는 것이다(10-25).
/// `deskmate/control/cmd` 의 target_id 마다 operation → value 를 덮어쓰며 모으고(set_power 와 set_level 은 따로 남는다),
/// `deskmate/control/result` 가 같은 command_id 로 오면 그 기기의 마지막 결과(✓ 성공 · ✗ 실패 이유)를 붙인다.
/// undo 명령도 그냥 "마지막 명령" 이라 값을 되돌려 쓴다.
library;

import 'package:flutter/foundation.dart';

import 'command_log.dart' show CommandLog;

class DeviceState {
  DeviceState(this.target);

  final String target;

  /// operation → value (마지막 값). 예: {set_power: on, set_level: 2}
  final Map<String, Object?> values = {};
  String? lastCommandId;
  String? lastGate;
  DateTime? lastAt;

  /// adjust_brightness 결과의 기준값(previous). 다음 명령이 오면 지운다. 있으면 "밝기 110→176".
  int? brightnessFrom;

  /// 마지막 명령의 결과 status(succeeded·failed·…). 아직 없으면 null.
  String? resultStatus;
  String? resultError;

  /// `ON · 풍량 2` 처럼 값만. 아무 명령도 없으면 "-".
  String get valueText {
    if (values.isEmpty) return '-';
    final parts = <String>[];
    for (final e in values.entries) {
      final v = e.value;
      parts.add(switch (e.key) {
        'set_power' => v.toString().toUpperCase(),
        'set_level' => '풍량 $v',
        'set_speed' => '속도 $v',
        'set_brightness' =>
          brightnessFrom == null ? '밝기 $v' : '밝기 $brightnessFrom→$v',
        'adjust_brightness' => '밝기 $v',
        'set_mode' => '모드 $v',
        'set_timer' => '타이머 $v s',
        _ => '${e.key} $v',
      });
    }
    return parts.join(' · ');
  }

  /// 결과 표시: ✓ 성공 · ✗ 실패(이유) · … 결과 없음. 명령이 없으면 빈 문자열.
  String get resultMark {
    if (values.isEmpty) return '';
    return switch (resultStatus) {
      'succeeded' => '✓',
      'failed' || 'timeout' || 'cancelled' =>
        '✗ 제어 못함${resultError == null ? '' : '($resultError)'}',
      null => '… 결과 없음',
      _ => resultStatus!,
    };
  }

  /// `선풍기 ON · 풍량 2 ✓`. [available] 이 false 면(앱에 그 기기가 등록돼 있지 않음) 뒤에 "기기 없음 · 제어 못함".
  String line({bool? available}) {
    final mark = resultMark;
    final missing = available == false ? ' (기기 없음 · 제어 못함)' : '';
    return '${deviceLabel(target)} $valueText${mark.isEmpty ? '' : ' $mark'}$missing';
  }
}

/// control.yaml 의 target_id → 화면 이름.
String deviceLabel(String target) => switch (target) {
      'vent_fan' => '선풍기',
      'desk_lamp' => '램프',
      'air_purifier' => '공기청정기',
      'plug_1' => '플러그',
      _ => target,
    };

class DeviceStateBoard extends ChangeNotifier {
  /// 항상 이 순서로 보여 준다(명령이 없어도 "-" 로). 다른 target 은 뒤에 붙는다.
  static const knownTargets = ['vent_fan', 'desk_lamp', 'air_purifier'];

  final Map<String, DeviceState> _devices = {
    for (final t in knownTargets) t: DeviceState(t),
  };
  final Map<String, String> _commandTarget = {};

  List<DeviceState> get devices => List.unmodifiable(_devices.values);
  DeviceState? operator [](String target) => _devices[target];

  /// `현재: 선풍기 ON · 풍량 2 ✓ / 램프 밝기 230 ✗ 제어 못함(lamp_not_configured) / 공기청정기 -`
  /// [available] = target → 앱에 그 기기가 등록돼 있는지(블루투스 주소 저장). 없으면 표시만 한다.
  String summary({Map<String, bool> available = const {}}) =>
      '현재: ${_devices.values.map((d) => d.line(available: available[d.target])).join(' / ')}';

  /// MQTT envelope 한 건. control/cmd·control/result 만 본다(다른 토픽은 무시).
  void addMessage(String topic, Map<String, dynamic> envelope, {DateTime? now}) {
    final raw = envelope['data'];
    if (raw is! Map) return;
    final data = raw.map((k, v) => MapEntry(k.toString(), v));
    if (topic == CommandLog.topicCmd) {
      _onCommand(data, now ?? DateTime.now());
    } else if (topic == CommandLog.topicResult) {
      _onResult(data);
    }
  }

  void _onCommand(Map<String, dynamic> data, DateTime at) {
    final target = data['target_id'];
    final op = data['operation'];
    if (target is! String || target.isEmpty || op is! String || op.isEmpty) {
      return;
    }
    final device = _devices.putIfAbsent(target, () => DeviceState(target));
    device.brightnessFrom = null;
    if (op == 'adjust_brightness') {
      // 결과가 오기 전에는 "+60%" 로. 결과의 actual 이 오면 set_brightness 값으로 바뀐다.
      final v = data['value'];
      final p = v is Map ? v['percent'] : null;
      device.values.remove('set_brightness');
      device.values[op] = p is num ? '${p >= 0 ? '+' : ''}${p.round()}%' : '?';
    } else {
      device.values.remove('adjust_brightness');
      device.values[op] = data['value'];
    }
    device.lastGate = data['gate']?.toString();
    device.lastAt = at;
    device.resultStatus = null;
    device.resultError = null;
    final id = data['command_id'];
    if (id is String && id.isNotEmpty) {
      device.lastCommandId = id;
      _commandTarget[id] = target;
      if (_commandTarget.length > 128) {
        _commandTarget.remove(_commandTarget.keys.first);
      }
    }
    notifyListeners();
  }

  void _onResult(Map<String, dynamic> data) {
    final id = data['command_id'];
    if (id is! String) return;
    final target = _commandTarget[id];
    final device = target == null ? null : _devices[target];
    // 마지막 명령에 대한 결과만 붙인다. 지난 명령의 늦은 결과는 현재 상태를 흔들지 않는다.
    if (device == null || device.lastCommandId != id) return;
    device.resultStatus = data['status']?.toString();
    device.resultError = data['error_code']?.toString();
    final actual = data['actual'];
    if (device.values.containsKey('adjust_brightness') && actual is num) {
      device.values.remove('adjust_brightness');
      device.values['set_brightness'] = actual.round();
      final previous = data['previous'];
      device.brightnessFrom = previous is num ? previous.round() : null;
    }
    notifyListeners();
  }

  void clear() {
    for (final d in _devices.values) {
      d.values.clear();
      d.lastCommandId = null;
      d.lastGate = null;
      d.lastAt = null;
      d.resultStatus = null;
      d.resultError = null;
    }
    _commandTarget.clear();
    notifyListeners();
  }
}
