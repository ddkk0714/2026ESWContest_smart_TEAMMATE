/// Pi 4 자세 서비스에서 상태를 읽어 오는 쪽.
///
/// 화면은 [TofSource] 만 보고, 실제 HTTP 는 [HttpTofSource] 가 한다. 테스트는 가짜
/// 소스를 넣어 네트워크 없이 화면을 그린다.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'tof_state.dart';

abstract class TofSource {
  /// 이 소스가 보는 주소(화면 표시용).
  String get baseUrl;

  Future<TofPostureState> fetch();

  void close() {}
}

/// `GET <baseUrl>/api/state`. 응답 본문은 UTF-8 JSON 이다(posture_ko 가 한글).
class HttpTofSource implements TofSource {
  HttpTofSource(this.baseUrl, {Duration timeout = const Duration(seconds: 2)})
      : _timeout = timeout,
        _client = HttpClient()..connectionTimeout = timeout;

  @override
  final String baseUrl;
  final Duration _timeout;
  final HttpClient _client;

  Uri get _stateUri => Uri.parse('$baseUrl/api/state');

  @override
  Future<TofPostureState> fetch() async {
    final request = await _client.getUrl(_stateUri).timeout(_timeout);
    // 0.3 초마다 부르므로 캐시가 끼면 화면이 멈춘다.
    request.headers.set(HttpHeaders.cacheControlHeader, 'no-store');
    final response = await request.close().timeout(_timeout);
    final body = await utf8.decodeStream(response).timeout(_timeout);
    if (response.statusCode != HttpStatus.ok) {
      throw HttpException('HTTP ${response.statusCode}', uri: _stateUri);
    }
    final decoded = jsonDecode(body);
    if (decoded is! Map) {
      throw const FormatException('/api/state 응답이 JSON 객체가 아니다');
    }
    return TofPostureState.fromJson(decoded);
  }

  @override
  void close() => _client.close(force: true);
}

/// 입력한 글자를 서비스 주소로 다듬는다.
///
/// `172.20.10.3` 처럼 IP 만 치면 `http://` 와 기본 포트 8771 을 붙이고,
/// `http://host:port/` 처럼 다 치면 뒤의 `/` 만 뗀다. 모양이 아니면 null.
String? normalizeBaseUrl(String raw, {int defaultPort = 8771}) {
  var text = raw.trim();
  if (text.isEmpty) return null;
  if (!text.contains('://')) text = 'http://$text';
  final uri = Uri.tryParse(text);
  if (uri == null || uri.host.isEmpty) return null;
  if (uri.scheme != 'http' && uri.scheme != 'https') return null;
  if (uri.path.isNotEmpty && uri.path != '/') return null;
  final port = uri.hasPort ? uri.port : defaultPort;
  return '${uri.scheme}://${uri.host}:$port';
}
