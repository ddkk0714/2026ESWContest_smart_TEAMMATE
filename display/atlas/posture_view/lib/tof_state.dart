/// Pi 4 자세 서비스(`deskmate_tofposture`, :8771)의 `GET /api/state` 응답 모델.
///
/// 서비스가 주는 JSON 을 그대로 화면이 쓸 수 있는 값으로 바꾼다. 모르는 키는 무시하고
/// 빠진 키는 기본값으로 채운다 — 서비스 쪽이 필드를 더하거나 빼도 화면이 죽지 않게.
/// 라벨 이름·순서는 서비스의 `model.labels` 를 그대로 따른다(화면이 라벨을 정하지 않는다).
library;

double _num(Object? v, [double fallback = 0]) {
  if (v is num) return v.toDouble();
  if (v is String) return double.tryParse(v) ?? fallback;
  return fallback;
}

int _int(Object? v, [int fallback = 0]) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) return int.tryParse(v) ?? fallback;
  return fallback;
}

bool _bool(Object? v, [bool fallback = false]) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  return fallback;
}

String _str(Object? v, [String fallback = '']) => v is String ? v : fallback;

Map<String, double> _probs(Object? v) {
  if (v is! Map) return const {};
  final out = <String, double>{};
  v.forEach((k, val) {
    if (k is String) out[k] = _num(val);
  });
  return out;
}

/// 센서 하나(`sensors[]` 항목)의 상태.
class SensorState {
  const SensorState({
    required this.name,
    required this.online,
    required this.posture,
    required this.conf,
    required this.hz,
    required this.frames,
    required this.seqGaps,
    required this.duplicates,
    required this.resets,
    required this.flipRows,
    required this.flipCols,
    required this.stale,
    required this.error,
  });

  final String name;
  final bool online;
  final String posture;
  final double conf;
  final double hz;
  final int frames;
  final int seqGaps;
  final int duplicates;
  final int resets;
  final bool flipRows;
  final bool flipCols;
  final bool stale;
  final String error;

  factory SensorState.fromJson(Map<Object?, Object?> j) => SensorState(
        name: _str(j['name'], '?'),
        online: _bool(j['online']),
        posture: _str(j['posture'], 'unknown'),
        conf: _num(j['conf']),
        hz: _num(j['hz']),
        frames: _int(j['frames']),
        seqGaps: _int(j['seq_gaps']),
        duplicates: _int(j['duplicates']),
        resets: _int(j['resets']),
        flipRows: _bool(j['flip_rows']),
        flipCols: _bool(j['flip_cols']),
        stale: _bool(j['stale']),
        error: _str(j['error']),
      );
}

/// `/api/state` 한 번의 응답.
class TofPostureState {
  const TofPostureState({
    required this.valid,
    required this.posture,
    required this.postureKo,
    required this.conf,
    required this.sinceMs,
    required this.present,
    required this.misplaced,
    required this.onlineSensors,
    required this.ticks,
    required this.instantPosture,
    required this.instantProbs,
    required this.smoothedProbs,
    required this.labels,
    required this.sensors,
    required this.modelFile,
    required this.modelClasses,
    required this.rateHz,
    required this.fusion,
    required this.flipRows,
    required this.flipCols,
  });

  /// 센서 입력이 있어 판정이 유효한가. false 면 posture 는 'unknown' 취급.
  final bool valid;

  /// 1 초 이동평균으로 고른 자세 라벨(영문 키).
  final String posture;

  /// 서비스가 같이 주는 한국어 이름. 비어 있으면 화면이 [labelKo] 로 채운다.
  final String postureKo;
  final double conf;
  final int sinceMs;
  final bool present;
  final bool misplaced;
  final int onlineSensors;
  final int ticks;

  /// 이번 순간값. 막대는 이것을 그린다(HTML 뷰와 같은 선택).
  final String instantPosture;
  final Map<String, double> instantProbs;
  final Map<String, double> smoothedProbs;

  /// 모델 라벨 순서. 막대 순서가 이것을 따른다.
  final List<String> labels;
  final List<SensorState> sensors;
  final String modelFile;
  final int modelClasses;
  final int rateHz;
  final String fusion;
  final bool flipRows;
  final bool flipCols;

  factory TofPostureState.fromJson(Map<Object?, Object?> j) {
    final instant = j['instant'];
    final model = j['model'];
    final instantProbs = instant is Map ? _probs(instant['probs']) : const <String, double>{};
    final smoothed = _probs(j['probs']);
    final probsForLabels = instantProbs.isNotEmpty ? instantProbs : smoothed;
    final rawLabels = model is Map ? model['labels'] : null;
    final labels = rawLabels is List
        ? rawLabels.whereType<String>().toList(growable: false)
        : probsForLabels.keys.toList(growable: false);
    final rawSensors = j['sensors'];
    final sensors = rawSensors is List
        ? rawSensors
            .whereType<Map<Object?, Object?>>()
            .map(SensorState.fromJson)
            .toList(growable: false)
        : const <SensorState>[];
    return TofPostureState(
      valid: _bool(j['valid']),
      posture: _str(j['posture'], 'unknown'),
      postureKo: _str(j['posture_ko']),
      conf: _num(j['conf']),
      sinceMs: _int(j['since_ms']),
      present: _bool(j['present']),
      misplaced: _bool(j['misplaced']),
      onlineSensors: _int(j['online_sensors']),
      ticks: _int(j['ticks']),
      instantPosture: instant is Map ? _str(instant['posture'], 'unknown') : 'unknown',
      instantProbs: instantProbs,
      smoothedProbs: smoothed,
      labels: labels,
      sensors: sensors,
      modelFile: model is Map ? _str(model['file']) : '',
      modelClasses: model is Map ? _int(model['classes']) : labels.length,
      rateHz: model is Map ? _int(model['rate_hz']) : 0,
      fusion: model is Map ? _str(model['fusion']) : '',
      flipRows: model is Map ? _bool(model['flip_rows']) : false,
      flipCols: model is Map ? _bool(model['flip_cols']) : false,
    );
  }

  /// 막대에 쓰는 확률. 순간값이 없으면(옛 서비스) 이동평균으로 대신한다.
  Map<String, double> get barProbs => instantProbs.isNotEmpty ? instantProbs : smoothedProbs;

  /// 막대 중 가장 큰 라벨. 그 줄만 색을 달리 칠한다.
  String get topBarLabel {
    final probs = barProbs;
    if (labels.isEmpty) return 'unknown';
    var top = labels.first;
    for (final l in labels) {
      if ((probs[l] ?? 0) > (probs[top] ?? 0)) top = l;
    }
    return top;
  }

  /// 이력 띠에 남길 라벨. 무효면 'unknown'.
  String get historyLabel => valid ? posture : 'unknown';

  String get modelFileName {
    final i = modelFile.lastIndexOf('/');
    return i < 0 ? modelFile : modelFile.substring(i + 1);
  }
}
