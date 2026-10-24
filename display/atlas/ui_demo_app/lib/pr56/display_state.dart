// ui_env_app/lib/display_state.dart 의 복사본(2026-10-25, V18). 두 앱이 같은 state/phase 를 같은 뜻으로 읽게
// 바꿀 때는 양쪽을 함께 고친다. PR #56 원본보다 ToF 자세·2단계 관찰·개인화 설명 필드가 늘었다.
class DisplayState {
  const DisplayState({
    required this.fsmState,
    required this.phase,
    required this.context,
    required this.focus,
    required this.fatigue,
    required this.confidence,
    required this.gate,
    required this.reasons,
    required this.sequence,
    required this.timestamp,
    this.cause,
    this.present,
    this.co2Ppm,
    this.temperatureC,
    this.humidityPct,
    this.lux,
    this.scenario,
    this.keystroke,
    this.mmwaveMotionState,
    this.mmwaveMotionLevel,
    this.mmwaveDistanceCm,
    this.mmwaveHeartBpm,
    this.mmwaveRespBpm,
    this.mmwaveDrowsyState,
    this.interventionExplanation,
    this.envFlags = const [],
    this.tofPosture,
    this.tofPostureConfidence,
    this.tofPostureSource,
    this.presenceSource,
    this.inRoom,
    this.personalizationStatus,
    this.personalizationLabel,
    this.personalizationConfidence,
  });

  final String fsmState;
  final String phase;
  final String context;
  final double focus;
  final double fatigue;
  final double confidence;
  final String gate;
  final String? cause;
  final List<String> reasons;
  final int sequence;
  final DateTime timestamp;
  final bool? present;
  final int? co2Ppm;
  final double? temperatureC;
  final double? humidityPct;
  final int? lux;
  final String? scenario;
  final KeystrokeMetrics? keystroke;
  final String? mmwaveMotionState;
  final int? mmwaveMotionLevel;
  final int? mmwaveDistanceCm;
  final int? mmwaveHeartBpm;

  /// 호흡수. `resp_valid` 가 true 일 때만 값이 있다(락온이 풀린 구간은 0 이 아니라 없음).
  final int? mmwaveRespBpm;

  /// mmWave 졸음 판정: NOPERSON | NOLOCK | WARMUP | AWAKE | DROWSY. 없으면 null.
  final String? mmwaveDrowsyState;

  /// 허브 개입 선택 정책이 남긴 설명 한 줄(`sensor_summary.personalize.explanation`). 없으면 null.
  final String? interventionExplanation;

  /// 환경이 왜 문제인지(hub `sensor_summary.env_flags`): co2_high · co2_rising · too_hot ·
  /// too_cold · too_humid · too_dry · too_dark. 제안·자동 실행 카드의 "이유" 문구가 된다.
  final List<String> envFlags;

  /// hub 가 FSM 입력으로 실제로 쓴 ToF 자세(`sensor_summary.tof.posture`). 없으면 null.
  final String? tofPosture;
  final double? tofPostureConfidence;

  /// 그 자세가 어디서 왔는지(`sensor_summary.tof.posture_source`): service | mlp | rule.
  final String? tofPostureSource;

  /// 재실 판정의 근거(`sensor_summary.presence_source`), 예: mmwave · tof · both.
  final String? presenceSource;

  /// 책상은 비어도 방에는 있는지(`sensor_summary.in_room`, mmWave 기준). 없으면 null.
  final bool? inRoom;

  /// 2단계 피로 관찰(`sensor_summary.personalization`): status(warming_up·predicted…)·label·confidence. 없으면 null.
  final String? personalizationStatus;
  final String? personalizationLabel;
  final double? personalizationConfidence;

  factory DisplayState.fromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['schema_version'] != '1.0') {
      throw const FormatException('unsupported schema_version');
    }
    final data = _map(envelope['data']);
    final sensors = _map(data['sensor_summary']);
    final mmwave = _map(sensors['mmwave']);
    final personalize = _map(sensors['personalize']);
    final explanation = personalize['explanation'];
    final tof = _map(sensors['tof']);
    final observation = _map(sensors['personalization']);
    return DisplayState(
      fsmState: _requiredString(data, 'fsm_state'),
      phase: _requiredString(data, 'phase'),
      context: _requiredString(data, 'context'),
      focus: _unit(data['c_focus']),
      fatigue: _unit(data['c_fatigue']),
      confidence: _unit(data['confidence']),
      gate: data['gate']?.toString() ?? 'none',
      cause: data['cause']?.toString(),
      reasons: (data['reasons'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      sequence: (envelope['seq'] as num?)?.toInt() ?? 0,
      timestamp: DateTime.fromMillisecondsSinceEpoch(
        (((envelope['ts'] as num?)?.toDouble() ?? 0) * 1000).round(),
      ),
      present: sensors['present'] is bool ? sensors['present'] as bool : null,
      co2Ppm: _nullableInt(sensors['co2_ppm']),
      temperatureC: _nullableDouble(sensors['temp_c']),
      humidityPct: _nullableDouble(sensors['humidity_pct']),
      lux: _nullableInt(sensors['lux']),
      scenario: sensors['scenario']?.toString(),
      keystroke: KeystrokeMetrics.fromJson(sensors['keystroke']),
      mmwaveMotionState: mmwave['motion_state']?.toString(),
      mmwaveMotionLevel: _nullableInt(mmwave['motion_level']),
      mmwaveDistanceCm: _nullableInt(mmwave['distance_cm']),
      mmwaveHeartBpm: mmwave['heart_valid'] == true
          ? _nullableInt(mmwave['heart_bpm'])
          : null,
      mmwaveRespBpm: mmwave['resp_valid'] == true
          ? _nullableInt(mmwave['resp_bpm'])
          : null,
      mmwaveDrowsyState: mmwave['drowsy_state']?.toString(),
      interventionExplanation:
          explanation is String && explanation.trim().isNotEmpty
              ? explanation.trim()
              : null,
      envFlags: (sensors['env_flags'] as List? ?? const [])
          .map((value) => value.toString())
          .toList(growable: false),
      tofPosture: tof['posture']?.toString(),
      tofPostureConfidence: _nullableDouble(tof['posture_confidence']),
      tofPostureSource: tof['posture_source']?.toString(),
      presenceSource: sensors['presence_source']?.toString(),
      inRoom: sensors['in_room'] is bool ? sensors['in_room'] as bool : null,
      personalizationStatus: observation['status']?.toString(),
      personalizationLabel: observation['label']?.toString(),
      personalizationConfidence: _nullableDouble(observation['confidence']),
    );
  }

  static Map<String, dynamic> _map(Object? value) {
    if (value is Map<String, dynamic>) return value;
    if (value is Map) {
      return value.map((key, value) => MapEntry(key.toString(), value));
    }
    return const {};
  }

  static String _requiredString(Map<String, dynamic> data, String key) {
    final value = data[key];
    if (value is! String || value.isEmpty) {
      throw FormatException('missing $key');
    }
    return value;
  }

  static double _unit(Object? value) {
    final number = (value as num?)?.toDouble() ?? 0;
    return number.clamp(0.0, 1.0).toDouble();
  }

  static int? _nullableInt(Object? value) {
    final number = value is num ? value : null;
    if (number == null || !number.isFinite) return null;
    return number.toInt();
  }

  static double? _nullableDouble(Object? value) {
    final number = value is num ? value.toDouble() : null;
    if (number == null || !number.isFinite) return null;
    return number;
  }
}

/// `deskmate/sensor/keystroke` 규약 필드를 화면용으로 담는다.
///
/// 계약상 키 값은 들어오지 않는다. 타이밍 통계만 있다.
/// 없는 값은 0 이 아니라 null 로 둔다. 0 으로 채우면 화면이
/// "리듬 완벽 · 마우스 정지" 로 잘못 읽힌다.
class KeystrokeMetrics {
  const KeystrokeMetrics({
    this.node,
    this.windowS,
    this.eventCount,
    this.dwellMeanMs,
    this.dwellStdMs,
    this.flightMeanMs,
    this.flightStdMs,
    this.idleRatio,
    this.correctionRate,
    this.mouseEventRate,
    this.typingActive,
    this.valid,
    this.timestamp,
    double? flightCv,
  }) : _flightCv = flightCv;

  final String? node;
  final int? windowS;
  final int? eventCount;
  final double? dwellMeanMs;
  final double? dwellStdMs;
  final double? flightMeanMs;
  final double? flightStdMs;
  final double? idleRatio;
  final double? correctionRate;
  final double? mouseEventRate;
  final bool? typingActive;
  final bool? valid;
  final DateTime? timestamp;

  final double? _flightCv;

  /// flight 의 변동계수(σ/μ). 리듬이 얼마나 불규칙한지를 본다.
  ///
  /// collector 가 `flight_cv` 를 실어 보내면 그 값을, 아니면 여기서 만든다.
  /// 규약 추가분이라 아직 안 올 수 있어 파생 계산을 남겨 둔다.
  double? get flightCv {
    if (_flightCv != null) return _flightCv;
    final mean = flightMeanMs;
    final std = flightStdMs;
    if (mean == null || std == null || mean <= 0) return null;
    return std / mean;
  }

  /// hub envelope 기준으로 이 표본이 얼마나 묵었는지.
  ///
  /// collector 가 죽어도 hub 는 국면을 계속 내보내므로, 마지막 값이
  /// 화면에 그대로 굳는 걸 막으려면 나이를 따로 봐야 한다.
  Duration? ageFrom(DateTime reference) {
    final ts = timestamp;
    if (ts == null) return null;
    final age = reference.difference(ts);
    return age.isNegative ? Duration.zero : age;
  }

  static KeystrokeMetrics? fromJson(Object? value) {
    if (value is! Map) return null;
    final data = DisplayState._map(value);
    if (data.isEmpty) return null;
    return KeystrokeMetrics(
      node: data['node']?.toString(),
      windowS: (data['window_s'] as num?)?.toInt(),
      eventCount: (data['event_count'] as num?)?.toInt(),
      dwellMeanMs: _positive(data['dwell_mean_ms']),
      dwellStdMs: _positive(data['dwell_std_ms']),
      flightMeanMs: _positive(data['flight_mean_ms']),
      flightStdMs: _positive(data['flight_std_ms']),
      idleRatio: _ratio(data['idle_ratio']),
      correctionRate: _ratio(data['correction_rate']),
      mouseEventRate: _positive(data['mouse_event_rate']),
      flightCv: _positive(data['flight_cv']),
      typingActive: data['typing_active'] as bool?,
      valid: data['valid'] as bool?,
      timestamp: _time(data['ts']),
    );
  }

  static KeystrokeMetrics? fromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['schema_version'] != '1.0') {
      throw const FormatException('unsupported keystroke schema_version');
    }
    final data = DisplayState._map(envelope['data']);
    if (data.isEmpty) return null;
    return fromJson({
      ...data,
      'node': envelope['node'] ?? data['node'],
      'ts': envelope['ts'],
    });
  }

  static double? _positive(Object? value) {
    final number = (value as num?)?.toDouble();
    if (number == null || !number.isFinite || number < 0) return null;
    return number;
  }

  static double? _ratio(Object? value) {
    final number = (value as num?)?.toDouble();
    if (number == null || !number.isFinite) return null;
    return number.clamp(0.0, 1.0).toDouble();
  }

  static DateTime? _time(Object? value) {
    final seconds = (value as num?)?.toDouble();
    if (seconds == null || seconds <= 0) return null;
    return DateTime.fromMillisecondsSinceEpoch((seconds * 1000).round());
  }
}
