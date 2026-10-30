/// Pi 4 자세 서비스(:8771)의 장치 위치 API 클라이언트.
///
/// 계약(2026-10-25):
///   - `GET  /api/placement`  → `{"placement":"left|center|right|unknown","model":"…",
///                              "options":["left","center","right"],"models":{…},"changed_ms":…,"switches":n}`
///                             옛 판(기능 없음)은 404.
///   - `POST /api/placement` 본문 `{"placement":"left"}` → 200 + 같은 JSON(서비스가 재시작 없이 모델 교체·GRU 리셋·저장).
///                             400 = 잘못된 키, 500 = 모델 못 읽음(기존 유지).
/// 호스트·포트는 프리뷰·개발자 모드가 쓰는 자세 서비스와 같다(`preview_host`, 8771).
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// `/api/placement` 응답.
class PlacementInfo {
  const PlacementInfo({
    required this.placement,
    this.model,
    this.options = const ['left', 'center', 'right'],
    this.models = const {},
    this.changedMs,
    this.switches,
  });

  /// left | center | right | unknown
  final String placement;

  /// 지금 올라간 모델(경로나 이름). 서비스가 안 주면 null.
  final String? model;
  final List<String> options;

  /// 위치 → 모델 파일. 서비스가 안 주면 빈 맵.
  final Map<String, String> models;
  final int? changedMs;
  final int? switches;

  factory PlacementInfo.fromJson(Map<Object?, Object?> j) {
    final rawModels = j['models'];
    return PlacementInfo(
      placement: j['placement'] is String ? j['placement'] as String : 'unknown',
      model: j['model'] is String && (j['model'] as String).isNotEmpty
          ? j['model'] as String
          : null,
      options: j['options'] is List
          ? (j['options'] as List).map((e) => e.toString()).toList(growable: false)
          : const ['left', 'center', 'right'],
      models: rawModels is Map
          ? {
              for (final e in rawModels.entries)
                if (e.value != null) e.key.toString(): e.value.toString()
            }
          : const {},
      changedMs: j['changed_ms'] is num ? (j['changed_ms'] as num).toInt() : null,
      switches: j['switches'] is num ? (j['switches'] as num).toInt() : null,
    );
  }

  /// 화면용 한 줄: `left · opt_l8_c256.json`.
  String get summary => model == null ? placement : '$placement · $model';
}

enum PlacementFailure {
  /// 연결 안 됨(주소 없음·거부·시간 초과).
  unreachable,

  /// 404 — 이 기능이 없는 옛 서비스.
  unsupported,

  /// 400 — 서비스가 값을 거절.
  invalid,

  /// 500 — 모델을 못 읽어 기존 유지.
  serverError,

  /// 그 밖의 상태 코드·깨진 응답.
  other,
}

class PlacementException implements Exception {
  const PlacementException(this.failure, this.message);
  final PlacementFailure failure;
  final String message;

  /// 사용자에게 보일 이유 한 줄.
  String get userText => switch (failure) {
        PlacementFailure.unreachable => 'Pi 4 자세 서비스에 연결되지 않아요 ($message)',
        PlacementFailure.unsupported => 'Pi 4 자세 서비스가 옛 판이에요 — 위치 선택을 지원하지 않아요 (404)',
        PlacementFailure.invalid => '서비스가 값을 거절했어요 (400 $message)',
        PlacementFailure.serverError => '모델을 읽지 못해 이전 설정을 유지했어요 (500 $message)',
        PlacementFailure.other => '응답을 읽지 못했어요 ($message)',
      };

  @override
  String toString() => 'PlacementException(${failure.name}: $message)';
}

abstract class PlacementClient {
  String get host;
  Future<PlacementInfo> fetch();
  Future<PlacementInfo> apply(String placement);
  void close() {}
}

class HttpPlacementClient implements PlacementClient {
  HttpPlacementClient(
    this.host, {
    this.port = 8771,
    Duration timeout = const Duration(seconds: 3),
  })  : _timeout = timeout,
        _client = HttpClient()..connectionTimeout = timeout;

  @override
  final String host;
  final int port;
  final Duration _timeout;
  final HttpClient _client;

  Uri get uri => Uri(scheme: 'http', host: host, port: port, path: '/api/placement');

  @override
  Future<PlacementInfo> fetch() => _call(null);

  @override
  Future<PlacementInfo> apply(String placement) => _call(placement);

  Future<PlacementInfo> _call(String? placement) async {
    final HttpClientResponse response;
    String body;
    try {
      final request = await (placement == null
              ? _client.getUrl(uri)
              : _client.postUrl(uri))
          .timeout(_timeout);
      request.headers.set(HttpHeaders.cacheControlHeader, 'no-store');
      if (placement != null) {
        request.headers.contentType = ContentType.json;
        final payload = utf8.encode(jsonEncode({'placement': placement}));
        // Content-Length 없이 chunked 로 보내면 작은 서버가 400 으로 떨어진다(feedback API 와 같은 이유).
        request.contentLength = payload.length;
        request.add(payload);
      }
      response = await request.close().timeout(_timeout);
      body = await utf8.decodeStream(response).timeout(_timeout);
    } on TimeoutException {
      throw const PlacementException(PlacementFailure.unreachable, '시간 초과');
    } on SocketException catch (e) {
      throw PlacementException(
          PlacementFailure.unreachable, e.osError?.message ?? e.message);
    } on HttpException catch (e) {
      throw PlacementException(PlacementFailure.unreachable, e.message);
    }
    final code = response.statusCode;
    final detail = _errorDetail(body);
    switch (code) {
      case HttpStatus.ok:
        final Object? decoded;
        try {
          decoded = jsonDecode(body);
        } on FormatException catch (e) {
          throw PlacementException(PlacementFailure.other, 'JSON 아님: ${e.message}');
        }
        if (decoded is! Map) {
          throw const PlacementException(
              PlacementFailure.other, '응답이 JSON 객체가 아니다');
        }
        return PlacementInfo.fromJson(decoded);
      case HttpStatus.notFound:
        throw PlacementException(PlacementFailure.unsupported, detail);
      case HttpStatus.badRequest:
        throw PlacementException(PlacementFailure.invalid, detail);
      case HttpStatus.internalServerError:
        throw PlacementException(PlacementFailure.serverError, detail);
      default:
        throw PlacementException(PlacementFailure.other, 'HTTP $code $detail');
    }
  }

  /// 오류 본문의 `error`/`message` 필드나 짧은 평문.
  static String _errorDetail(String body) {
    final text = body.trim();
    if (text.isEmpty) return '';
    try {
      final decoded = jsonDecode(text);
      if (decoded is Map) {
        final m = decoded['error'] ?? decoded['message'] ?? decoded['detail'];
        if (m != null) return m.toString();
      }
    } on FormatException {
      // 평문 그대로
    }
    return text.length > 80 ? '${text.substring(0, 80)}…' : text;
  }

  @override
  void close() => _client.close(force: true);
}

/// 화면에 쓰는 위치 이름.
String placementKo(String? placement) => switch (placement) {
      'left' => '왼쪽',
      'center' => '가운데',
      'right' => '오른쪽',
      'unknown' => '미정',
      null => '미설정',
      _ => placement,
    };
