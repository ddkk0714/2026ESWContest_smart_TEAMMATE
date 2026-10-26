// Pi 4 자세 서비스(:8771)의 장치 위치 API. ui_env_app/lib/placement_client.dart 의 복사본에서
// V18 이 쓰는 POST 만 남겼다.
//
// 계약(2026-10-25): `POST /api/placement` 본문 `{"placement":"left|center|right"}` → 200 + JSON.
// 400 = 잘못된 값, 404 = 옛 서비스(기능 없음), 500 = 모델을 못 읽어 기존 유지.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

enum PlacementFailure { unreachable, unsupported, invalid, serverError, other }

class PlacementException implements Exception {
  const PlacementException(this.failure, this.message);
  final PlacementFailure failure;
  final String message;

  /// 화면에 잠깐 보일 한 줄. 실패해도 프로필 저장·다음 질문은 막지 않는다.
  String get userText => switch (failure) {
        PlacementFailure.unreachable => '보드에 닿지 않아 위치는 이 화면에만 기억해 둘게요.',
        PlacementFailure.unsupported => '보드가 아직 위치 설정을 받지 않아요.',
        _ => '위치를 보드에 맞추지 못해 이전 설정을 그대로 둘게요.',
      };

  @override
  String toString() => 'PlacementException(${failure.name}: $message)';
}

abstract class PlacementClient {
  /// 성공하면 서비스가 돌려준 placement(left|center|right|unknown).
  Future<String> apply(String placement);
  void close() {}
}

class HttpPlacementClient implements PlacementClient {
  HttpPlacementClient(this.host,
      {this.port = 8771, Duration timeout = const Duration(seconds: 3)})
      : _timeout = timeout,
        _client = HttpClient()..connectionTimeout = timeout;

  final String host;
  final int port;
  final Duration _timeout;
  final HttpClient _client;

  Uri get uri =>
      Uri(scheme: 'http', host: host, port: port, path: '/api/placement');

  @override
  Future<String> apply(String placement) async {
    final HttpClientResponse response;
    final String body;
    try {
      final request = await _client.postUrl(uri).timeout(_timeout);
      request.headers.set(HttpHeaders.cacheControlHeader, 'no-store');
      request.headers.contentType = ContentType.json;
      final payload = utf8.encode(jsonEncode({'placement': placement}));
      // Content-Length 없이 chunked 로 보내면 작은 서버가 400 으로 떨어진다(feedback API 와 같은 이유).
      request.contentLength = payload.length;
      request.add(payload);
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
    switch (response.statusCode) {
      case HttpStatus.ok:
        try {
          final decoded = jsonDecode(body);
          if (decoded is Map && decoded['placement'] is String) {
            return decoded['placement'] as String;
          }
        } on FormatException {
          // 아래에서 other 로
        }
        throw const PlacementException(PlacementFailure.other, '응답 형식');
      case HttpStatus.notFound:
        throw const PlacementException(PlacementFailure.unsupported, '404');
      case HttpStatus.badRequest:
        throw const PlacementException(PlacementFailure.invalid, '400');
      case HttpStatus.internalServerError:
        throw const PlacementException(PlacementFailure.serverError, '500');
      default:
        throw PlacementException(
            PlacementFailure.other, 'HTTP ${response.statusCode}');
    }
  }

  @override
  void close() => _client.close(force: true);
}

/// 화면에 쓰는 위치 이름.
String placementKo(String? placement) => switch (placement) {
      'left' => '왼쪽',
      'right' => '오른쪽',
      _ => '가운데',
    };

/// '왼쪽으로' · '가운데로' · '오른쪽으로'.
String placementPhrase(String? placement) =>
    placement == 'left' || placement == 'right'
        ? '${placementKo(placement)}으로'
        : '${placementKo(placement)}로';
