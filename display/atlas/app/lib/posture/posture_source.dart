import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'camera_view.dart';
import 'posture_state.dart';

/// 노드 링크 상태(`/health`).
///
/// 판정과 따로 보는 이유가 있다. **끊긴 링크는 살아 있는 링크와 똑같이 보인다** —
/// 마지막 프레임이 계속 재발행되므로 `/api/state` 만 보면 화면이 멀쩡해 보인다.
class LinkHealth {
  const LinkHealth({
    required this.stage,
    required this.stale,
    required this.fps,
    required this.frames,
    this.error,
  });

  /// 캘리브레이션 단계: clear · settle · background · sit · baseline · live.
  final String stage;
  final bool stale;
  final double fps;
  final int frames;
  final String? error;

  bool get healthy => !stale && error == null;
  bool get live => stage == 'live';

  static LinkHealth fromJson(Map<String, dynamic> json) => LinkHealth(
        stage: json['stage']?.toString() ?? 'unknown',
        stale: json['stale'] as bool? ?? false,
        fps: (json['fps'] as num?)?.toDouble() ?? 0,
        frames: (json['frames'] as num?)?.toInt() ?? 0,
        error: _text(json['link_error']),
      );

  static String? _text(Object? value) {
    if (value is! String) return null;
    final text = value.trim();
    return text.isEmpty ? null : text;
  }
}

/// 센서가 실제로 보고 있는 것. 화면에 그려 "지금 잡히고 있나" 를 눈으로 본다.
///
/// 판정은 마스크만 쓰지만, 사람이 보기에는 coverage 쪽이 더 읽힌다 - 마스크는
/// 흑백이라 몸이 잘렸는지 노이즈인지 구분이 안 된다.
class VisionSnapshot {
  const VisionSnapshot({
    required this.width,
    required this.height,
    this.mask,
    this.coverage,
    this.depthMm,
    this.maskAgeS,
  });

  final int width;
  final int height;

  /// 0/1 이진 마스크. RP2040 의 vision.c 가 만든 것.
  final List<int>? mask;

  /// zone 별 배경 대비 차이값 0~255. ESP 가 보내는 원본.
  final List<int>? coverage;

  /// 판정에 들어간 zone 거리(mm). 노트북 뷰어가 열화상으로 그리는 그 값이다.
  final List<double>? depthMm;

  /// 마지막 마스크가 몇 초 전 것인지.
  final double? maskAgeS;
}

abstract interface class PostureSource {
  /// 화면에 띄울 출처 이름.
  String get label;

  /// 기준 다시 잡기 버튼을 띄울지. 데모에는 다시 잡을 기준이 없다.
  bool get canCalibrate;

  Future<PostureState> fetch();

  /// 실패해도 화면을 멈추지 않는다. 못 읽으면 null.
  Future<LinkHealth?> health();

  Future<void> calibrate();

  void close();
}

/// Pi 4 자세 노드(`python -m deskmate_posture`)의 HTTP API 를 1Hz 로 긁는다.
///
/// 주소는 빌드 시 `--dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765` 로 준다.
/// 배포 스크립트의 `ATLAS_HUB_URL` 이 그대로 이 define 으로 들어간다.
class HttpPostureSource implements PostureSource {
  HttpPostureSource(String baseUrl, {this.capturesSeated = false})
      : _base = Uri.parse(baseUrl),
        _client = HttpClient()..connectionTimeout = const Duration(seconds: 2);

  /// 기준을 **앉은 자세 그대로** 한 장으로 잡는지.
  ///
  /// Pi 4 자세 노드는 빈 책상부터 재야 해서 사람이 비켜야 하지만, 스켈레톤
  /// 판정(`camsvc`)은 어깨와 코가 보이는 한 장이면 끝난다. 안내 문구가 반대로
  /// 나가면 사람이 자리를 비우고 기준은 사람 없는 프레임으로 잡힌다.
  final bool capturesSeated;

  final Uri _base;
  final HttpClient _client;

  static const _timeout = Duration(seconds: 2);

  @override
  String get label => '${_base.host}:${_base.hasPort ? _base.port : 8765}';

  @override
  bool get canCalibrate => true;

  @override
  Future<PostureState> fetch() async {
    final request = await _client.getUrl(_base.resolve('/api/state'));
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    final response = await request.close().timeout(_timeout);
    final text = await utf8.decoder.bind(response).join();
    // 첫 판정 전에는 노드가 503 을 낸다. 연결 실패와 같은 문구로 보이면
    // 켜자마자 뜨는 정상 상태를 고장으로 읽게 된다.
    if (response.statusCode == HttpStatus.serviceUnavailable) {
      throw const HttpException('자세 노드가 아직 첫 판정을 내지 않았습니다');
    }
    if (response.statusCode != HttpStatus.ok) {
      throw HttpException('state API ${response.statusCode}', uri: request.uri);
    }
    return PostureState.fromEnvelope(jsonDecode(text) as Map<String, dynamic>);
  }

  @override
  Future<LinkHealth?> health() async {
    try {
      final request = await _client.getUrl(_base.resolve('/health'));
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      final response = await request.close().timeout(_timeout);
      final text = await utf8.decoder.bind(response).join();
      if (response.statusCode != HttpStatus.ok) return null;
      return LinkHealth.fromJson(jsonDecode(text) as Map<String, dynamic>);
    } catch (_) {
      // 링크 표시는 부가 정보다. 여기서 던지면 판정까지 못 보여준다.
      return null;
    }
  }

  /// 카메라가 지금 보고 있는 그림 한 장과 그 위의 랜드마크.
  ///
  /// 판정 서비스만 내준다. 아직 프레임이 없으면(503) null 이다.
  Future<PreviewFrame?> preview() async {
    final request = await _client.getUrl(_base.resolve('/preview.raw'));
    final response = await request.close().timeout(_timeout);
    if (response.statusCode != HttpStatus.ok) {
      await response.drain<void>();
      return null;
    }
    final chunks = await response.toList();
    final builder = BytesBuilder(copy: false);
    for (final chunk in chunks) {
      builder.add(chunk);
    }
    return PreviewFrame.parse(builder.takeBytes());
  }

  @override
  Future<void> calibrate() async {
    final request = await _client.postUrl(_base.resolve('/api/calibrate'));
    request.headers.contentType = ContentType.json;
    // 본문이 필요 없는 요청이지만 길이를 명시해 보낸다. 길이를 안 주면 Dart 가
    // chunked 로 보내고, 노드는 Content-Length 만큼만 읽은 뒤 연결을 닫는다.
    final payload = utf8.encode('{}');
    request.contentLength = payload.length;
    request.add(payload);
    final response = await request.close().timeout(_timeout);
    await response.drain<void>();
    if (response.statusCode != HttpStatus.accepted) {
      throw HttpException('calibrate API ${response.statusCode}',
          uri: request.uri);
    }
  }

  @override
  void close() => _client.close(force: true);
}

/// Pi 4 없이 화면만 볼 때. `DESKMATE_HUB_URL` 없이 빌드하면 이쪽이 돈다.
class DemoPostureSource implements PostureSource {
  int _index = 0;

  static const _script = <({
    PostureLabel label,
    bool present,
    bool valid,
    double? headMm,
    double? nod,
    double motion,
    double coverage,
    double focusDrop,
    double fatigue,
    List<String> reasons,
    String scenario,
  })>[
    (
      label: PostureLabel.upright,
      present: true,
      valid: true,
      headMm: -4.2,
      nod: 0.0,
      motion: 0.08,
      coverage: 0.21,
      focusDrop: 0.12,
      fatigue: 0.10,
      reasons: ['posture_only'],
      scenario: '바른 자세 · 자세 판정만 표시 중 (허브 미연결)',
    ),
    (
      label: PostureLabel.slump,
      present: true,
      valid: true,
      headMm: -104.2,
      nod: 0.0,
      motion: 0.03,
      coverage: 0.19,
      focusDrop: 0.28,
      fatigue: 0.86,
      reasons: ['posture_only', 'head_dropped_and_held'],
      scenario: '엎드림 · 자세 판정만 표시 중 (허브 미연결)',
    ),
    (
      label: PostureLabel.drowsy,
      present: true,
      valid: true,
      headMm: -52.0,
      nod: 4.5,
      motion: 0.22,
      coverage: 0.20,
      focusDrop: 0.41,
      fatigue: 0.72,
      reasons: ['posture_only', 'nods_per_min_4.5'],
      scenario: '졸음 (꾸벅임) · 자세 판정만 표시 중 (허브 미연결)',
    ),
    (
      label: PostureLabel.recline,
      present: true,
      valid: true,
      headMm: 88.0,
      nod: 0.0,
      motion: 0.11,
      coverage: 0.16,
      focusDrop: 0.22,
      fatigue: 0.48,
      reasons: ['posture_only', 'moved_away_from_sensor'],
      scenario: '뒤로 젖힘 · 자세 판정만 표시 중 (허브 미연결)',
    ),
    (
      label: PostureLabel.absent,
      present: false,
      valid: true,
      headMm: null,
      nod: 0.0,
      motion: 0.0,
      coverage: 0.01,
      focusDrop: 0.0,
      fatigue: 0.0,
      reasons: ['posture_only', 'desk_empty'],
      scenario: '자리 비움 · 자세 판정만 표시 중 (허브 미연결)',
    ),
    (
      label: PostureLabel.baseline,
      present: true,
      valid: false,
      headMm: null,
      nod: 0.0,
      motion: 0.09,
      coverage: 0.18,
      focusDrop: 0.0,
      fatigue: 0.0,
      reasons: ['posture_only', 'posture 24/60'],
      scenario: '2/2 바른 자세 기준을 재는 중입니다',
    ),
  ];

  @override
  String get label => '화면 내장 데모';

  @override
  bool get canCalibrate => false;

  @override
  Future<PostureState> fetch() async {
    final item = _script[_index++ % _script.length];
    return PostureState(
      label: item.label,
      present: item.present,
      valid: item.valid,
      motion: item.motion,
      coverage: item.coverage,
      focusDrop: item.focusDrop,
      fatigue: item.fatigue,
      reasons: item.reasons,
      sequence: _index,
      timestamp: DateTime.now(),
      headDeltaMm: item.headMm,
      nodPerMin: item.nod,
      scenario: item.scenario,
      node: 'demo',
    );
  }

  @override
  Future<LinkHealth?> health() async => null;

  @override
  Future<void> calibrate() async =>
      throw UnsupportedError('데모에는 다시 잡을 기준이 없습니다.');

  @override
  void close() {}
}
