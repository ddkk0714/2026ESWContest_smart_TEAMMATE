// 집중 한 번의 흐름 — 시작부터 끝까지 hub 상태(state/phase)와 기기 명령(control/cmd)을 모은다.
// 마무리 화면의 '신호 → 피로 점수 → 개입' 그래프(session_chart.dart)가 이걸 그린다. 값은 받은 그대로이고,
// 보드에 이어지지 않은 집중(발표 모드 합성)은 아무것도 모으지 않는다.
library;

import '../pr56/display_state.dart';

/// 졸음 증거로 보는 ToF 자세(보드 모델 판정).
const drowsyPostures = {'face_down', 'drowsy', 'head_down'};

class TimelineSample {
  const TimelineSample({
    required this.at,
    required this.fatigue,
    required this.focus,
    this.keyIdle,
    this.posture,
    this.motion,
    this.co2,
  });
  final DateTime at;
  final double fatigue;
  final double focus;

  /// 최근 60 s 중 키 입력이 멈춘 비율(0~1). 키보드가 없으면 null.
  final double? keyIdle;
  final String? posture;

  /// mmWave 몸 움직임(motion_level).
  final int? motion;
  final int? co2;

  bool? get drowsyEvidence =>
      posture == null ? null : drowsyPostures.contains(posture);
}

class TimelineEvent {
  const TimelineEvent({required this.at, required this.label});
  final DateTime at;
  final String label;
}

class SessionTimeline {
  SessionTimeline({DateTime Function()? clock})
      : _clock = clock ?? DateTime.now;
  final DateTime Function() _clock;

  /// 아주 긴 집중에도 메모리가 늘지 않게 — 10 s 마다 오면 5.5 시간.
  static const maxSamples = 2000;

  final samples = <TimelineSample>[];
  final events = <TimelineEvent>[];

  void clear() {
    samples.clear();
    events.clear();
  }

  void add(DisplayState s) {
    samples.add(TimelineSample(
      at: _clock(),
      fatigue: s.fatigue.clamp(0.0, 1.0),
      focus: s.focus.clamp(0.0, 1.0),
      keyIdle: s.keystroke?.idleRatio,
      posture: s.present == false ? 'away' : s.tofPosture,
      motion: s.mmwaveMotionLevel,
      co2: s.co2Ppm,
    ));
    if (samples.length > maxSamples) samples.removeAt(0);
  }

  /// hub `control/cmd` 한 건. 같은 순간(2 s 안)에 나간 명령은 한 표시로 묶는다.
  void addCommand(Map<String, dynamic> data) {
    final label = commandLabel(data);
    if (label == null) return;
    final now = _clock();
    if (events.isNotEmpty &&
        now.difference(events.last.at).inSeconds.abs() <= 2) {
      final last = events.removeLast();
      events.add(TimelineEvent(
          at: last.at,
          label: last.label.contains(label)
              ? last.label
              : '${last.label} · $label'));
    } else {
      events.add(TimelineEvent(at: now, label: label));
    }
  }
}

/// 기기 명령 → 그래프에 적을 짧은 말. 모르는 명령은 null(표시 안 함).
String? commandLabel(Map<String, dynamic> data) {
  final target = data['target_id'];
  final operation = data['operation'];
  final value = data['value'];
  if (data['gate'] == 'undo') return '되돌리기';
  if (data['cause'] == 'absent') return '자리 비움 · 끄기';
  switch (target) {
    case 'desk_lamp':
      if (operation == 'adjust_brightness' &&
          value is Map &&
          value['percent'] is num) {
        final p = (value['percent'] as num).round();
        return p >= 0 ? '무드등 밝게 +$p %' : '무드등 어둡게 $p %';
      }
      if (operation == 'set_power') return value == 'off' ? '무드등 끄기' : '무드등 켜기';
      if (operation == 'set_brightness') return '무드등 밝기 $value';
      return '무드등';
    case 'music':
      return value is Map && value['on'] == false ? '음악 끄기' : '음악 켜기';
    case 'vent_fan':
    case 'fan':
      return value == 'off' ? '선풍기 끄기' : '선풍기 켜기';
    case 'speaker':
      return operation == 'chime' ? '알림음' : '음성 안내';
    case 'air_purifier':
      return '공기청정기';
  }
  return null;
}
