/// Pi 4 자세 노드가 내보내는 한 장면.
///
/// 모양을 정하는 곳은 `espcam_with_decision` 의
/// `pi/deskmate_posture/envelope.py` · `preview_envelope()` 이다. 그쪽은 허브가
/// 붙기 전에도 Pi 5 화면이 살아 있도록 **허브 FSM 과 똑같은 껍데기**에 자세 하나를
/// 담아 보낸다. 그래서 이 앱은 껍데기(`fsm_state` · `c_focus` …)보다
/// `sensor_summary.posture` 블록을 먼저 읽는다 — 껍데기 값은 자세에서 되만든
/// 것이라 같은 것을 두 번 읽는 셈이고, 좁혀지면서 잃은 정보가 있다.
library;

/// 판정이 내는 라벨. `posture.py` 의 UPRIGHT/SLUMP/RECLINE/DROWSY/ABSENT 와
/// 기준을 아직 못 잡은 두 상태(BASELINE/UNKNOWN)까지가 전부다.
enum PostureLabel { upright, slump, recline, drowsy, absent, baseline, unknown }

/// `posture_detail` — 판정이 실제로 뭐라고 했는지가 그대로 담긴 규약 밖 필드.
const _detailNames = <String, PostureLabel>{
  'upright': PostureLabel.upright,
  'slump': PostureLabel.slump,
  'recline': PostureLabel.recline,
  'drowsy': PostureLabel.drowsy,
  'absent': PostureLabel.absent,
  'baseline': PostureLabel.baseline,
  'unknown': PostureLabel.unknown,
};

/// `posture` — 계약 enum(`docs/data-spec.md` 6.1). 여기에는 `drowsy` 가 없어서
/// 꾸벅임이 `lean_forward` 로 좁혀져 들어온다. 그래서 `posture_detail` 이 있으면
/// 그쪽을 먼저 보고, 없을 때만 이 표로 되돌린다.
const _contractNames = <String, PostureLabel>{
  'upright': PostureLabel.upright,
  'slouch': PostureLabel.slump,
  'lean_back': PostureLabel.recline,
  'lean_forward': PostureLabel.drowsy,
  'away': PostureLabel.absent,
  'unknown': PostureLabel.unknown,
};

class PostureState {
  const PostureState({
    required this.label,
    required this.present,
    required this.valid,
    required this.motion,
    required this.coverage,
    required this.focusDrop,
    required this.fatigue,
    required this.reasons,
    required this.sequence,
    required this.timestamp,
    this.headDeltaMm,
    this.nodPerMin,
    this.scenario,
    this.node,
  });

  final PostureLabel label;

  /// 책상 앞에 사람이 있는지. 화면이 제일 먼저 답해야 하는 질문이다.
  final bool present;

  /// 이 판정을 믿어도 되는지. 기준을 잡는 중(BASELINE/UNKNOWN)이거나 센서
  /// 프레임이 묵었으면 노드가 내려 준다. **자리 비움은 valid 다** — 못 잰 것이
  /// 아니라 잰 결과다.
  final bool valid;

  final double motion;
  final double coverage;

  /// 허브 `Signal(phi, delta)` 로 그대로 들어가는 값. 화면에서는 '집중 저하'와
  /// '피로'로 부른다(옆 앱 `display/atlas/app` 과 같은 이름).
  final double focusDrop;
  final double fatigue;

  final List<String> reasons;
  final int sequence;
  final DateTime timestamp;

  /// 바른 자세 기준 대비 머리의 **센서와의 거리** 변화(mm). 겉보기 크기에서
  /// 되만든 값이라 엎드리면 가까워져 음수, 젖히면 멀어져 양수다. 머리 높이가
  /// 아니다. 머리를 못 찾은 프레임에서는 null 로 온다.
  final double? headDeltaMm;

  /// 분당 꾸벅임 횟수. 계약 단위가 Hz 라 노드가 60 으로 나눠 보내고, 화면은
  /// 사람이 세는 단위로 되돌린다.
  final double? nodPerMin;

  /// 노드가 만든 한 줄 안내. 캘리브레이션 중에는 남은 초까지 말해 준다.
  final String? scenario;

  final String? node;

  /// 기준을 잡는 중이라 아직 판정이 아닌 상태.
  bool get calibrating =>
      label == PostureLabel.baseline || label == PostureLabel.unknown;

  /// 고쳐 앉으라고 말할 상태.
  bool get needsCorrection =>
      valid &&
      (label == PostureLabel.slump ||
          label == PostureLabel.recline ||
          label == PostureLabel.drowsy);

  Duration ageFrom(DateTime now) {
    final age = now.difference(timestamp);
    return age.isNegative ? Duration.zero : age;
  }

  factory PostureState.fromEnvelope(Map<String, dynamic> envelope) {
    // 모르는 schema 를 억지로 읽으면 화면은 멀쩡한데 값이 틀린 상태가 된다.
    if (envelope['schema_version'] != '1.0') {
      throw const FormatException('unsupported schema_version');
    }
    final data = _map(envelope['data']);
    final sensors = _map(data['sensor_summary']);
    final posture = _map(sensors['posture']);

    final label = _label(posture, data);
    final present = posture['present'] as bool? ??
        sensors['present'] as bool? ??
        label != PostureLabel.absent;
    final calibrating =
        label == PostureLabel.baseline || label == PostureLabel.unknown;
    final nodHz = _finite(posture['nod_rate_hz']);

    return PostureState(
      label: label,
      present: present,
      // 노드가 valid 를 안 실어 보내면 노드와 같은 규칙으로 여기서 정한다.
      valid: posture['valid'] as bool? ?? !calibrating,
      motion: _unit(posture['motion_score']),
      coverage: _unit(posture['coverage_ratio']),
      focusDrop: _unit(posture['phi'] ?? data['c_focus']),
      fatigue: _unit(posture['delta'] ?? data['c_fatigue']),
      reasons: (data['reasons'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      sequence: (envelope['seq'] as num?)?.toInt() ?? 0,
      timestamp: DateTime.fromMillisecondsSinceEpoch(
        (((envelope['ts'] as num?)?.toDouble() ?? 0) * 1000).round(),
      ),
      headDeltaMm: _finite(posture['head_delta_mm']),
      nodPerMin: nodHz == null ? null : nodHz * 60,
      scenario: _text(sensors['scenario']),
      node: _text(envelope['node']),
    );
  }

  /// `posture_detail` → 계약 enum → `fsm_state` 순으로 본다. 마지막 칸이 있는
  /// 이유는 노드가 `POSTURE_<라벨>` 을 거기 한 번 더 박아 두기 때문이다.
  static PostureLabel _label(
      Map<String, dynamic> posture, Map<String, dynamic> data) {
    final detail = _text(posture['posture_detail'])?.toLowerCase();
    final contract = _text(posture['posture'])?.toLowerCase();
    final fsm = _text(data['fsm_state']);
    final fromFsm = fsm != null && fsm.startsWith('POSTURE_')
        ? _detailNames[fsm.substring('POSTURE_'.length).toLowerCase()]
        : null;
    return _detailNames[detail] ??
        _contractNames[contract] ??
        fromFsm ??
        PostureLabel.unknown;
  }

  static Map<String, dynamic> _map(Object? value) {
    if (value is Map<String, dynamic>) return value;
    if (value is Map) {
      return value.map((key, value) => MapEntry(key.toString(), value));
    }
    return const {};
  }

  static String? _text(Object? value) {
    if (value is! String) return null;
    final text = value.trim();
    return text.isEmpty ? null : text;
  }

  /// null 과 NaN/Inf 를 같이 거른다. 노드가 이미 걸러 보내지만, 0 으로 채워진
  /// 값과 '못 잰 값'을 화면에서 구분하려면 여기서도 null 을 지켜야 한다.
  static double? _finite(Object? value) {
    final number = (value as num?)?.toDouble();
    if (number == null || !number.isFinite) return null;
    return number;
  }

  static double _unit(Object? value) {
    final number = _finite(value);
    if (number == null) return 0;
    return number.clamp(0.0, 1.0).toDouble();
  }
}
