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
    this.lux,
    this.scenario,
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
  final int? lux;
  final String? scenario;

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
      present: sensors['present'] as bool?,
      co2Ppm: (sensors['co2_ppm'] as num?)?.toInt(),
      lux: (sensors['lux'] as num?)?.toInt(),
      scenario: sensors['scenario']?.toString(),
    );
  }

  static Map<String, dynamic> _map(Object? value) {
    if (value is Map<String, dynamic>) return value;
    if (value is Map) return value.map((key, value) => MapEntry(key.toString(), value));
    return const {};
  }

  static String _requiredString(Map<String, dynamic> data, String key) {
    final value = data[key];
    if (value is! String || value.isEmpty) throw FormatException('missing $key');
    return value;
  }

  static double _unit(Object? value) {
    final number = (value as num?)?.toDouble() ?? 0;
    return number.clamp(0.0, 1.0).toDouble();
  }
}
