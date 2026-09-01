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
    final payload = utf8.encode(jsonEncode({
      'request_id': 'atlas-display',
      'verdict': verdict,
      'response_ms': 0,
    }));
    // contentLength 를 안 정하면 Dart 는 chunked 로 보낸다. 허브의 preview_api 는
    // Content-Length 를 필수로 읽어서 없으면 400 invalid_feedback 으로 떨어진다.
    // 실기에서 '진행' 을 눌러도 hub 가 못 받던 원인이라 명시적으로 지정한다.
    request.contentLength = payload.length;
    request.add(payload);
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

  /// 국면마다 다른 타이핑 프로파일. focus 는 빠르고 규칙적, fatigue 는
  /// 느려지고 흔들리며 오타 교정이 는다. 실제 값은 collector 가 준다.
  static const _states = [
    (
      fsm: 'START',
      phase: 'start',
      focus: 0.18,
      fatigue: 0.12,
      scenario: '기준 상태를 측정하고 있어요',
      ks: (
        dwell: 96.0,
        dwellSd: 19.0,
        flight: 152.0,
        flightSd: 54.0,
        idle: 0.24,
        correction: 0.05,
        mouse: 0.6,
        events: 121,
        typing: true,
      ),
    ),
    (
      fsm: 'FOCUS_PC',
      phase: 'focus',
      focus: 0.16,
      fatigue: 0.24,
      scenario: 'PC 집중 상태',
      ks: (
        dwell: 88.0,
        dwellSd: 15.0,
        flight: 131.0,
        flightSd: 38.0,
        idle: 0.11,
        correction: 0.03,
        mouse: 0.4,
        events: 214,
        typing: true,
      ),
    ),
    (
      fsm: 'FATIGUE_SUSPECT',
      phase: 'fatigue',
      focus: 0.32,
      fatigue: 0.67,
      scenario: '피로 신호를 확인하고 있어요',
      ks: (
        dwell: 109.0,
        dwellSd: 31.0,
        flight: 186.0,
        flightSd: 104.0,
        idle: 0.29,
        correction: 0.09,
        mouse: 1.1,
        events: 147,
        typing: true,
      ),
    ),
    (
      fsm: 'ACTION_ENV',
      phase: 'fatigue',
      focus: 0.35,
      fatigue: 0.82,
      scenario: '환기를 권장해요',
      ks: (
        dwell: 124.0,
        dwellSd: 42.0,
        flight: 233.0,
        flightSd: 158.0,
        idle: 0.41,
        correction: 0.13,
        mouse: 1.6,
        events: 92,
        typing: false,
      ),
    ),
    (
      fsm: 'RECOVERY',
      phase: 'recovery',
      focus: 0.20,
      fatigue: 0.28,
      scenario: '상태가 회복되고 있어요',
      ks: (
        dwell: 91.0,
        dwellSd: 18.0,
        flight: 142.0,
        flightSd: 47.0,
        idle: 0.16,
        correction: 0.04,
        mouse: 0.5,
        events: 178,
        typing: true,
      ),
    ),
  ];

  @override
  String get label => '화면 내장 데모';

  @override
  Future<DisplayState> fetch() async {
    final item = _states[_index++ % _states.length];
    final now = DateTime.now();
    return DisplayState(
      fsmState: item.fsm,
      phase: item.phase,
      context: 'pc',
      focus: item.focus,
      fatigue: item.fatigue,
      confidence: item.fatigue,
      gate: item.phase == 'fatigue' ? 'suggest' : 'none',
      cause: item.fsm == 'ACTION_ENV' ? 'environment' : null,
      reasons: const [],
      sequence: _index,
      timestamp: now,
      present: true,
      co2Ppm: item.phase == 'fatigue' ? 1180 : 720,
      lux: 410,
      scenario: item.scenario,
      keystroke: KeystrokeMetrics(
        node: 'pc-collector',
        windowS: 60,
        eventCount: item.ks.events,
        dwellMeanMs: item.ks.dwell,
        dwellStdMs: item.ks.dwellSd,
        flightMeanMs: item.ks.flight,
        flightStdMs: item.ks.flightSd,
        idleRatio: item.ks.idle,
        correctionRate: item.ks.correction,
        mouseEventRate: item.ks.mouse,
        typingActive: item.ks.typing,
        valid: true,
        timestamp: now,
      ),
    );
  }

  @override
  Future<void> feedback(String verdict) async {}

  @override
  void close() {}
}
