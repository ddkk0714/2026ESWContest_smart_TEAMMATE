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
    this.mmwave,
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
  final MmwaveSummary? mmwave;

  factory DisplayState.fromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['schema_version'] != '1.0') {
      throw const FormatException('unsupported schema_version');
    }
    final data = _map(envelope['data']);
    final sensors = _map(data['sensor_summary']);
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
      mmwave: MmwaveSummary.fromJson(sensors['mmwave']),
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

/// `sensor_summary.mmwave` — C1001 레이더가 올린 값 중 화면에 쓰는 것만.
///
/// 계약(docs/data-spec.md)상 심박·호흡은 락온이 풀리면 값 자체가 오지 않거나
/// `*_valid=false` 로 온다. 그때 0 으로 채우면 화면이 "심박 0" 으로 읽히므로
/// null 로 둔다. 판정 자체는 ESP32 가 이미 끝내서 `drowsyState` 로 보낸다.
class MmwaveSummary {
  const MmwaveSummary({
    this.motionState,
    this.motionLevel,
    this.distanceCm,
    this.respBpm,
    this.heartBpm,
    this.drowsyState,
  });

  final String? motionState;
  final int? motionLevel;
  final int? distanceCm;
  final int? respBpm;
  final int? heartBpm;
  final String? drowsyState;

  static MmwaveSummary? fromJson(Object? value) {
    if (value is! Map) return null;
    final data = DisplayState._map(value);
    return MmwaveSummary(
      motionState: data['motion_state']?.toString(),
      motionLevel: DisplayState._nullableInt(data['motion_level']),
      distanceCm: DisplayState._nullableInt(data['distance_cm']),
      respBpm: _validated(data, 'resp_bpm', 'resp_valid'),
      heartBpm: _validated(data, 'heart_bpm', 'heart_valid'),
      drowsyState: data['drowsy_state']?.toString(),
    );
  }

  /// `*_valid` 가 명시적으로 false 면 값이 있어도 버린다. 펌웨어는 추정기가
  /// 수렴하기 전의 값을 그 플래그로 구분해서 보낸다.
  static int? _validated(Map<String, dynamic> data, String key, String validKey) {
    if (data[validKey] == false) return null;
    return DisplayState._nullableInt(data[key]);
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
