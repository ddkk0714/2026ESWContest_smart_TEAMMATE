import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'display_state.dart';

abstract interface class StateSource {
  String get label;
  Future<DisplayState> fetch();
  Future<void> feedback(String verdict);
  void close();
}

class HttpStateSource implements StateSource {
  HttpStateSource(String hubUrl)
      : _base = Uri.parse(hubUrl),
        _client = HttpClient()..connectionTimeout = const Duration(seconds: 2);

  final Uri _base;
  final HttpClient _client;

  @override
  String get label => _base.host;

  @override
  Future<DisplayState> fetch() async {
    final request = await _client.getUrl(_base.resolve('/api/state'));
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    final response = await request.close().timeout(const Duration(seconds: 2));
    final text = await utf8.decoder.bind(response).join();
    if (response.statusCode != HttpStatus.ok) {
      throw HttpException('state API ${response.statusCode}', uri: request.uri);
    }
    return DisplayState.fromEnvelope(jsonDecode(text) as Map<String, dynamic>);
  }

  @override
  Future<void> feedback(String verdict) async {
    final request = await _client.postUrl(_base.resolve('/api/feedback'));
    request.headers.contentType = ContentType.json;
    request.write(jsonEncode({
      'request_id': 'atlas-display',
      'verdict': verdict,
      'response_ms': 0,
    }));
    final response = await request.close().timeout(const Duration(seconds: 2));
    await response.drain<void>();
    if (response.statusCode != HttpStatus.accepted) {
      throw HttpException('feedback API ${response.statusCode}', uri: request.uri);
    }
  }

  @override
  void close() => _client.close(force: true);
}

class DemoStateSource implements StateSource {
  int _index = 0;

  static const _states = [
    ('START', 'start', 0.18, 0.12, '기준 상태를 측정하고 있어요'),
    ('FOCUS_PC', 'focus', 0.16, 0.24, 'PC 집중 상태'),
    ('FATIGUE_SUSPECT', 'fatigue', 0.32, 0.67, '피로 신호를 확인하고 있어요'),
    ('ACTION_ENV', 'fatigue', 0.35, 0.82, '환기를 권장해요'),
    ('RECOVERY', 'recovery', 0.20, 0.28, '상태가 회복되고 있어요'),
  ];

  @override
  String get label => '화면 내장 데모';

  @override
  Future<DisplayState> fetch() async {
    final item = _states[_index++ % _states.length];
    return DisplayState(
      fsmState: item.$1,
      phase: item.$2,
      context: 'pc',
      focus: item.$3,
      fatigue: item.$4,
      confidence: item.$4,
      gate: item.$2 == 'fatigue' ? 'suggest' : 'none',
      cause: item.$1 == 'ACTION_ENV' ? 'environment' : null,
      reasons: const [],
      sequence: _index,
      timestamp: DateTime.now(),
      present: true,
      co2Ppm: item.$2 == 'fatigue' ? 1180 : 720,
      lux: 410,
      scenario: item.$5,
    );
  }

  @override
  Future<void> feedback(String verdict) async {}

  @override
  void close() {}
}
