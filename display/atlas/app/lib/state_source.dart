import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';

import 'display_state.dart';

abstract interface class StateSource {
  String get label;
  bool get supportsSensorTest;
  Future<DisplayState> fetch();
  Future<void> feedback(String verdict);
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event});
  void close();
}

class TestSignalInput {
  const TestSignalInput(
      {this.focus = 0.1, this.fatigue = 0.1, this.available = true});

  final double focus;
  final double fatigue;
  final bool available;

  Map<String, Object> toJson() => {
        'phi': focus.clamp(0.0, 1.0),
        'delta': fatigue.clamp(0.0, 1.0),
        'available': available,
      };
}

class TestSensorInput {
  const TestSensorInput({
    this.present = true,
    this.pcRatio = 0.9,
    required this.signals,
  });

  final bool present;
  final double pcRatio;
  final Map<String, TestSignalInput> signals;

  Map<String, Object> toJson() => {
        'present': present,
        'pc_ratio': pcRatio.clamp(0.0, 1.0),
        'signals': signals.map((key, value) => MapEntry(key, value.toJson())),
      };
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
  bool get supportsSensorTest => true;

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
      throw HttpException('feedback API ${response.statusCode}',
          uri: request.uri);
    }
  }

  @override
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event}) async {
    final request = await _client.postUrl(_base.resolve('/api/test-frame'));
    request.headers.contentType = ContentType.json;
    final body = <String, Object?>{
      ...input.toJson(),
      'command': command,
      'advance_sec': advanceSeconds,
      if (event != null) 'event': event,
    };
    final payload = utf8.encode(jsonEncode(body));
    request.contentLength = payload.length;
    request.add(payload);
    final response = await request.close().timeout(const Duration(seconds: 2));
    await response.drain<void>();
    if (response.statusCode != HttpStatus.accepted) {
      throw HttpException('test-frame API ${response.statusCode}',
          uri: request.uri);
    }
  }

  @override
  void close() => _client.close(force: true);
}

/// Pi 4 Mosquitto broker를 직접 구독하는 최종 display 경로다.
///
/// HTTP source는 Atlas 앱과 Hub를 따로 검증하기 위한 개발용 대체 수단으로
/// 남겨 둔다. MQTT가 끊겨도 display는 재연결할 뿐, Pi 4의 FSM에는 영향을
/// 주지 않는다.
class MqttStateSource implements StateSource {
  MqttStateSource(this._host, {required this.port})
      : _bootId = 'display-${DateTime.now().microsecondsSinceEpoch}',
        _client = MqttServerClient.withPort(
          _host,
          'deskmate-display-${DateTime.now().microsecondsSinceEpoch}',
          port,
        ) {
    _client.logging(on: false);
    _client.keepAlivePeriod = 30;
    _client.autoReconnect = true;
    _client.resubscribeOnAutoReconnect = true;
    _client.connectionMessage = MqttConnectMessage()
        .withClientIdentifier(_client.clientIdentifier)
        .startClean()
        .withWillQos(MqttQos.atLeastOnce);
    _updates = _client.updates?.listen(_onUpdates);
  }

  static const _stateTopic = 'deskmate/state/phase';
  static const _requestTopic = 'deskmate/interaction/request';
  static const _feedbackTopic = 'deskmate/feedback/user';

  final String _host;
  final int port;
  final String _bootId;
  final MqttServerClient _client;
  StreamSubscription<List<MqttReceivedMessage<MqttMessage?>>>? _updates;
  Future<void>? _connection;
  DisplayState? _latest;
  String? _pendingRequestId;
  int _sequence = 0;

  @override
  String get label => 'MQTT $_host:$port';

  @override
  bool get supportsSensorTest => false;

  @override
  Future<DisplayState> fetch() async {
    await _ensureConnected();
    final state = _latest;
    if (state == null) {
      throw StateError('MQTT 상태 메시지를 기다리고 있습니다.');
    }
    return state;
  }

  Future<void> _ensureConnected() {
    if (_client.connectionStatus?.state == MqttConnectionState.connected) {
      return Future.value();
    }
    return _connection ??= _connect().whenComplete(() => _connection = null);
  }

  Future<void> _connect() async {
    try {
      final response = await _client.connect();
      if (response?.state != MqttConnectionState.connected) {
        _client.disconnect();
        throw StateError('MQTT broker 연결에 실패했습니다.');
      }
      _updates ??= _client.updates?.listen(_onUpdates);
      _client.subscribe(_stateTopic, MqttQos.atLeastOnce);
      _client.subscribe(_requestTopic, MqttQos.atLeastOnce);
    } catch (_) {
      _client.disconnect();
      rethrow;
    }
  }

  void _onUpdates(List<MqttReceivedMessage<MqttMessage?>>? messages) {
    for (final received in messages ?? const []) {
      final publish = received.payload;
      if (publish is! MqttPublishMessage) continue;
      try {
        final text = MqttPublishPayload.bytesToStringAsString(
          publish.payload.message,
        );
        final envelope = jsonDecode(text) as Map<String, dynamic>;
        if (received.topic == _stateTopic) {
          _latest = DisplayState.fromEnvelope(envelope);
        } else if (received.topic == _requestTopic) {
          final data = envelope['data'];
          if (data is Map<String, dynamic>) {
            final requestId = data['request_id'];
            if (requestId is String && requestId.isNotEmpty) {
              _pendingRequestId = requestId;
            }
          }
        }
      } on FormatException {
        // 손상됐거나 다른 schema의 메시지는 display를 멈추지 않는다.
      } on TypeError {
        // JSON envelope가 아닌 토픽 오발행도 같은 방식으로 무시한다.
      }
    }
  }

  @override
  Future<void> feedback(String verdict) async {
    await _ensureConnected();
    final payload = jsonEncode({
      'schema_version': '1.0',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000,
      'node': 'display',
      'boot_id': _bootId,
      'seq': ++_sequence,
      'data': {
        'request_id': _pendingRequestId ?? 'atlas-display',
        'verdict': verdict,
        'response_ms': 0,
      },
    });
    final builder = MqttClientPayloadBuilder()..addString(payload);
    _client.publishMessage(
      _feedbackTopic,
      MqttQos.atLeastOnce,
      builder.payload!,
    );
  }

  @override
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event}) {
    throw UnsupportedError('MQTT display 연결에서는 Pi 4 센서 테스트 API를 사용하지 않습니다.');
  }

  @override
  void close() {
    _updates?.cancel();
    _client.disconnect();
  }
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
  bool get supportsSensorTest => false;

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
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event}) {
    throw UnsupportedError('Hub 연결 빌드에서만 센서 테스트를 사용할 수 있습니다.');
  }

  @override
  void close() {}
}
