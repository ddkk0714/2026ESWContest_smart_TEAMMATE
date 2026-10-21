/// 프리뷰 탭이 Pi 4 에서 ToF 원본 격자와 자세 라벨을 읽어 오는 쪽.
///
/// 두 주소를 본다(둘 다 `hub/atlas-tof`):
///   - 중계기 `http://<pi4>:8772/raw` — 센서별 마지막 0x05 프레임. 64 칸 거리(mm)·상태값.
///   - 자세 서비스 `http://<pi4>:8771/api/state` — 1 초 이동평균 자세 라벨·확신.
///
/// 받은 격자는 **펌웨어가 이미 방향(flags)을 적용한 것**이라 여기서 다시 뒤집지 않는다
/// (10-19 실기 확정, FLIP_ROWS=1·FLIP_COLS=1). 화면은 배열 순서 그대로 row-major 로 그린다.
///
/// 원본 격자는 Pi 4 메모리에만 있고 MQTT 로는 나가지 않는다(운영 ToF raw 미전송 원칙).
/// 이 클라이언트는 같은 LAN 의 Pi 5 가 **프리뷰 모드에서만** 직접 읽는 개발용 경로다.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// VL53L8CX 의 target_status 중 거리를 믿어도 되는 값. 나머지는 "없음" 으로 칠한다.
const kTofValidStatus = {5, 6, 9, 10};

int _int(Object? v, [int fallback = 0]) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) return int.tryParse(v) ?? fallback;
  return fallback;
}

double _num(Object? v, [double fallback = 0]) {
  if (v is num) return v.toDouble();
  if (v is String) return double.tryParse(v) ?? fallback;
  return fallback;
}

bool _bool(Object? v, [bool fallback = false]) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  return fallback;
}

String _str(Object? v, [String fallback = '']) => v is String ? v : fallback;

List<int> _ints(Object? v) =>
    v is List ? v.map((e) => _int(e, -1)).toList(growable: false) : const [];

/// 센서 하나의 마지막 원본 프레임(`/raw` 의 `sensors[]` 한 항목).
class TofRawFrame {
  const TofRawFrame({
    required this.id,
    required this.have,
    this.width = 8,
    this.height = 8,
    this.distMm = const [],
    this.status = const [],
    this.ageMs,
    this.frameId,
    this.frameSeq,
    this.flags = 0,
  });

  /// 0 = A(왼쪽) · 1 = B.
  final int id;

  /// 아직 한 프레임도 못 받았으면 false. 그때 나머지 필드는 비어 있다.
  final bool have;
  final int width;
  final int height;

  /// row-major 거리(mm). −1 은 측정 없음.
  final List<int> distMm;

  /// 칸별 상태값. 비어 있으면(옛 중계기) 거리만으로 판단한다.
  final List<int> status;
  final int? ageMs;
  final int? frameId;
  final int? frameSeq;

  /// 펌웨어가 보낸 방향 플래그(bit0 rows · bit1 cols). 표시용 정보일 뿐 다시 적용하지 않는다.
  final int flags;

  factory TofRawFrame.fromJson(Map<Object?, Object?> j) {
    final have = _bool(j['have']);
    return TofRawFrame(
      id: _int(j['id']),
      have: have,
      width: have ? _int(j['w'], 8) : 8,
      height: have ? _int(j['h'], 8) : 8,
      distMm: have ? _ints(j['dist_mm']) : const [],
      status: have ? _ints(j['status']) : const [],
      ageMs: j['age_ms'] is num ? _int(j['age_ms']) : null,
      frameId: j['frame_id'] is num ? _int(j['frame_id']) : null,
      frameSeq: j['frame_seq'] is num ? _int(j['frame_seq']) : null,
      flags: _int(j['flags']),
    );
  }

  int get zoneCount => width * height;

  /// 거리를 믿어도 되는 칸인가: 거리 > 0 이고 상태값이 [kTofValidStatus] 에 든다.
  bool isValidZone(int i) {
    if (!have || i < 0 || i >= distMm.length) return false;
    if (distMm[i] <= 0) return false;
    if (status.length > i && !kTofValidStatus.contains(status[i])) return false;
    return true;
  }

  /// 칸의 거리(mm). 무효면 −1.
  int zoneMm(int i) => isValidZone(i) ? distMm[i] : -1;

  int zoneStatus(int i) => status.length > i ? status[i] : -1;

  List<int> get validDistances => [
        for (var i = 0; i < distMm.length; i++)
          if (isValidZone(i)) distMm[i],
      ];

  int get validCount => validDistances.length;

  int? get minMm {
    final v = validDistances;
    if (v.isEmpty) return null;
    return v.reduce((a, b) => a < b ? a : b);
  }

  int? get medianMm {
    final v = validDistances..sort();
    if (v.isEmpty) return null;
    return v[v.length >> 1];
  }
}

/// `GET :8772/raw` 한 번의 응답.
class TofRawSnapshot {
  const TofRawSnapshot({
    required this.sensors,
    this.frames = 0,
    this.type5 = 0,
    this.bad = 0,
  });

  final List<TofRawFrame> sensors;
  final int frames;
  final int type5;
  final int bad;

  factory TofRawSnapshot.fromJson(Map<Object?, Object?> j) {
    final raw = j['sensors'];
    return TofRawSnapshot(
      sensors: raw is List
          ? raw
              .whereType<Map<Object?, Object?>>()
              .map(TofRawFrame.fromJson)
              .toList(growable: false)
          : const [],
      frames: _int(j['frames']),
      type5: _int(j['type5']),
      bad: _int(j['bad']),
    );
  }

  TofRawFrame? sensor(int id) {
    for (final s in sensors) {
      if (s.id == id) return s;
    }
    return null;
  }
}

/// `GET :8771/api/state` 중 프리뷰가 쓰는 부분. 모르는 키는 무시한다.
class TofPostureLabel {
  const TofPostureLabel({
    required this.valid,
    required this.posture,
    required this.postureKo,
    required this.conf,
    required this.present,
    required this.misplaced,
    required this.onlineSensors,
  });

  final bool valid;
  final String posture;
  final String postureKo;
  final double conf;
  final bool present;
  final bool misplaced;
  final int onlineSensors;

  factory TofPostureLabel.fromJson(Map<Object?, Object?> j) => TofPostureLabel(
        valid: _bool(j['valid']),
        posture: _str(j['posture'], 'unknown'),
        postureKo: _str(j['posture_ko']),
        conf: _num(j['conf']),
        present: _bool(j['present']),
        misplaced: _bool(j['misplaced']),
        onlineSensors: _int(j['online_sensors']),
      );

  /// 화면에 쓸 이름. 서비스가 한국어를 주면 그것, 아니면 표에서, 그것도 없으면 영문 키.
  String get label =>
      postureKo.isNotEmpty ? postureKo : (kTofPostureKo[posture] ?? posture);
}

/// 자세 서비스 라벨의 한국어 이름(서비스가 `posture_ko` 를 안 줄 때의 대비).
/// `hub/atlas-tof/pc/posture_view.html` 의 KO 표와 같은 값 — 노트북 뷰와 다르게 부르지 않게.
const kTofPostureKo = <String, String>{
  'upright': '바른자세',
  'lean_forward': '상체 앞으로',
  'recline': '젖힘',
  'face_down': '엎드림',
  'chin_rest': '턱괴기',
  'drowsy': '졸음',
  'away': '자리비움',
  'misplaced': '위치 재조정',
  'forward_head': '거북목',
  'unknown': '모름',
};

abstract class PreviewTofSource {
  /// 이 소스가 보는 Pi 4 주소(IP 또는 호스트 이름).
  String get host;

  Future<TofRawSnapshot> fetchRaw();

  Future<TofPostureLabel> fetchPosture();

  void close() {}
}

class HttpPreviewTofSource implements PreviewTofSource {
  HttpPreviewTofSource(
    this.host, {
    this.rawPort = 8772,
    this.posturePort = 8771,
    Duration timeout = const Duration(seconds: 2),
  })  : _timeout = timeout,
        _client = HttpClient()..connectionTimeout = timeout;

  @override
  final String host;
  final int rawPort;
  final int posturePort;
  final Duration _timeout;
  final HttpClient _client;

  Uri get rawUri => Uri(scheme: 'http', host: host, port: rawPort, path: '/raw');
  Uri get postureUri =>
      Uri(scheme: 'http', host: host, port: posturePort, path: '/api/state');

  @override
  Future<TofRawSnapshot> fetchRaw() async =>
      TofRawSnapshot.fromJson(await _getJson(rawUri));

  @override
  Future<TofPostureLabel> fetchPosture() async =>
      TofPostureLabel.fromJson(await _getJson(postureUri));

  Future<Map<Object?, Object?>> _getJson(Uri uri) async {
    final request = await _client.getUrl(uri).timeout(_timeout);
    // 0.3 초마다 부르므로 캐시가 끼면 화면이 멈춘다.
    request.headers.set(HttpHeaders.cacheControlHeader, 'no-store');
    final response = await request.close().timeout(_timeout);
    final body = await utf8.decodeStream(response).timeout(_timeout);
    if (response.statusCode != HttpStatus.ok) {
      throw HttpException('HTTP ${response.statusCode}', uri: uri);
    }
    final decoded = jsonDecode(body);
    if (decoded is! Map) {
      throw FormatException('${uri.path} 응답이 JSON 객체가 아니다');
    }
    return decoded;
  }

  @override
  void close() => _client.close(force: true);
}

/// 입력한 글자를 Pi 4 호스트로 다듬는다.
///
/// `172.20.10.3` 은 그대로, `http://172.20.10.3:8771/` 처럼 다 치면 호스트만 남긴다
/// (포트는 8771·8772 로 정해져 있어 버린다). 모양이 아니면 null.
String? normalizePreviewHost(String raw) {
  var text = raw.trim();
  if (text.isEmpty) return null;
  if (text.contains('://')) {
    final uri = Uri.tryParse(text);
    if (uri == null || uri.host.isEmpty) return null;
    text = uri.host;
  } else if (text.contains(':') || text.contains('/')) {
    final uri = Uri.tryParse('http://$text');
    if (uri == null || uri.host.isEmpty) return null;
    text = uri.host;
  }
  // Uri.parse 는 "not a host" 도 호스트로 받아 %20 으로 감싼다. IP 나 이름만.
  if (!RegExp(r'^[A-Za-z0-9.\-]+$').hasMatch(text)) return null;
  return text;
}
