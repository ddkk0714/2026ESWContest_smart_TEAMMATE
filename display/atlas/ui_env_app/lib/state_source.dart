import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';

import 'display_state.dart';
import 'session_report.dart';
import 'privacy_state.dart';

/// MQTT payload 바이트 → 문자열. hub 는 UTF-8 JSON 을 보낸다.
/// `MqttPublishPayload.bytesToStringAsString` 은 바이트 하나를 글자 하나로 읽어(Latin-1)
/// 한글이 `ì¡°ëª…` 처럼 깨진다(10-24 실기). 깨진 바이트가 섞여 있어도 화면은 멈추지 않게 대체 문자로 둔다.
String mqttPayloadText(List<int> bytes) => utf8.decode(bytes, allowMalformed: true);

/// hub 가 보낸 질문(`deskmate/interaction/request`)의 화면용 상세. kind 로 카드 문구가 갈린다.
class PendingRequest {
  const PendingRequest({
    required this.requestId,
    required this.kind,
    this.promptCode,
    this.explanation,
    this.minutes,
    this.why = const [],
    this.expiresInS,
  });

  final String requestId;

  /// break_suggest · env_suggest · posture_suggest · checkin_suggest · vent_suggest …
  final String kind;
  final String? promptCode;
  final String? explanation;

  /// vent_suggest 의 `data.minutes`(창문 여는 분). 없으면 null.
  final int? minutes;

  /// vent_suggest 의 `data.why[]` 이유 코드.
  final List<String> why;
  final int? expiresInS;

  static PendingRequest? fromData(Map<String, dynamic> data) {
    final id = data['request_id'];
    if (id is! String || id.isEmpty) return null;
    final minutes = data['minutes'] ?? data['ventilate_minutes'];
    final explanation = data['explanation'];
    final expires = data['expires_in_s'];
    return PendingRequest(
      requestId: id,
      kind: data['kind']?.toString() ?? 'unknown',
      promptCode: data['prompt_code']?.toString(),
      explanation: explanation is String && explanation.trim().isNotEmpty
          ? explanation.trim()
          : null,
      minutes: minutes is num ? minutes.round() : null,
      why: (data['why'] as List? ?? const [])
          .map((e) => e.toString())
          .toList(growable: false),
      expiresInS: expires is num ? expires.round() : null,
    );
  }
}

/// 사용자가 앱에서 직접 바꾼 기기 값(블루투스 탭의 밝기·단계 버튼). hub 가 "밝힌 뒤 직접 어둡게 = 밝음 싫음" 신호로 쓴다.
/// hub 명령 실행·국면 조명은 보내지 않는다. [from] 을 모르면(앱 시작 뒤 한 번도 안 씀) 키를 넣지 않는다.
Map<String, Object?> buildManualAdjustData({
  required String targetId,
  required int? from,
  required int to,
  required int tsMs,
}) =>
    {
      'kind': 'manual_adjust',
      'target_id': targetId,
      if (from != null) 'from': from,
      'to': to,
      'ts': tsMs,
    };

abstract interface class StateSource {
  String get label;
  bool get isConnected;
  String get connectionLabel;
  bool get supportsSensorTest;
  String? get displayMessage;
  SessionReport? get sessionReport;
  bool get hasPendingRequest;

  /// 지금 떠 있는 질문의 상세(kind·문구). 없으면 null.
  PendingRequest? get pendingRequest;

  /// `deskmate/feedback/user` 로 verdict 가 아닌 데이터(manual_adjust 등)를 공통 envelope 로 보낸다.
  /// 보낼 곳이 없는 소스(HTTP·데모)는 조용히 무시한다.
  Future<void> publishFeedbackData(Map<String, Object?> data);

  /// hub 가 준 질문의 응답 제한 시간(`expires_in_s`). 질문이 없거나 모르면 null.
  int? get pendingRequestExpiresInS;
  Future<DisplayState> fetch();

  /// [verdict] = accept | reject | correct | timeout (mqtt-topics.md `feedback/user`).
  /// correct 면 [correctedState](FSM 상태 이름), [responseMs] 는 카드를 띄운 뒤 응답까지 걸린 시간.
  /// [note] 는 프리뷰 탭의 짧은 메모(선택, `data.note`). 키 값·원시 센서는 싣지 않는다.
  Future<void> feedback(String verdict,
      {String? correctedState, int? responseMs, String? note});
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

/// `deskmate/feedback/user` 의 `data`. MQTT·HTTP 두 경로가 같은 모양을 쓰게 한곳에 둔다.
///
/// `note` 는 프리뷰 탭의 메모(선택). 비어 있으면 키를 넣지 않고, 길이는 [kFeedbackNoteMaxChars] 로
/// 자른다 — 토픽 계약에 없던 필드라 짧은 문자열 하나로만 둔다.
Map<String, Object> buildFeedbackData({
  required String requestId,
  required String verdict,
  String? correctedState,
  int? responseMs,
  String? note,
}) {
  final trimmed = note?.trim() ?? '';
  final clipped = trimmed.length > kFeedbackNoteMaxChars
      ? trimmed.substring(0, kFeedbackNoteMaxChars)
      : trimmed;
  return {
    'request_id': requestId,
    'verdict': verdict,
    if (correctedState != null) 'corrected_state': correctedState,
    'response_ms': responseMs ?? 0,
    if (clipped.isNotEmpty) 'note': clipped,
  };
}

const kFeedbackNoteMaxChars = 200;

class HttpStateSource implements StateSource {
  HttpStateSource(String hubUrl)
      : _base = Uri.parse(hubUrl),
        _client = HttpClient()..connectionTimeout = const Duration(seconds: 2);

  final Uri _base;
  final HttpClient _client;

  @override
  String get label => _base.host;

  @override
  bool get isConnected => true;

  @override
  String get connectionLabel => 'HTTP 개발 연결';

  @override
  bool get supportsSensorTest => true;

  @override
  String? get displayMessage => null;

  @override
  SessionReport? get sessionReport => null;

  @override
  bool get hasPendingRequest => false;

  @override
  PendingRequest? get pendingRequest => null;

  @override
  Future<void> publishFeedbackData(Map<String, Object?> data) async {}

  @override
  int? get pendingRequestExpiresInS => null;

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
  Future<void> feedback(String verdict,
      {String? correctedState, int? responseMs, String? note}) async {
    // HTTP 미리보기 API 는 accept·reject·correct 만 받는다(개발용 경로). 무응답은
    // 보낼 곳이 없어 버린다 — 제품 경로(MQTT)에서만 기록된다.
    if (verdict == 'timeout') return;
    final request = await _client.postUrl(_base.resolve('/api/feedback'));
    request.headers.contentType = ContentType.json;
    final payload = utf8.encode(jsonEncode(buildFeedbackData(
      requestId: 'atlas-display',
      verdict: verdict,
      correctedState: correctedState,
      responseMs: responseMs,
      note: note,
    )));
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
class MqttStateSource implements StateSource, PrivacySource {
  MqttStateSource(this._host,
      {required this.port, this.onControlCommand, this.onTopicEvent})
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
  static const _messageTopic = 'deskmate/display/message';
  static const _reportTopic = 'deskmate/session/report';
  static const _hubHealthTopic = 'deskmate/health/hub';
  static const _keystrokeTopic = 'deskmate/sensor/keystroke';
  static const _feedbackTopic = 'deskmate/feedback/user';
  static const _controlTopic = 'deskmate/control/cmd';
  static const _controlResultTopic = 'deskmate/control/result';

  final String _host;
  final int port;
  final Future<Map<String, dynamic>?> Function(Map<String, dynamic>)?
      onControlCommand;

  /// 개발자 모드의 명령 로그가 받는 콜백 — `control/cmd`·`control/result`·`interaction/request`·
  /// `feedback/user` 의 envelope 를 받은 그대로(retain 제외) 넘긴다. 해석·실행은 바꾸지 않는다.
  /// 이 콜백이 있으면 result·feedback 토픽도 구독한다(앱이 낸 결과도 브로커를 거쳐 돌아온다).
  final void Function(String topic, Map<String, dynamic> envelope)?
      onTopicEvent;
  static const _loggedTopics = {
    _controlTopic,
    _controlResultTopic,
    _requestTopic,
    _feedbackTopic,
  };
  bool _closed = false;
  final String _bootId;
  final MqttServerClient _client;
  StreamSubscription<List<MqttReceivedMessage<MqttMessage?>>>? _updates;
  Future<void>? _connection;
  DisplayState? _latest;
  String? _displayMessage;
  SessionReport? _sessionReport;
  KeystrokeMetrics? _pcKeystroke;

  /// Read the 1 Hz PC topic rather than the hub's 10-second phase snapshot.
  KeystrokeMetrics? get pcKeystroke => _pcKeystroke;

  /// 마지막으로 해석에 실패한 메시지. 화면이 왜 안 움직이는지 말해 주려고 둔다.
  String? _lastParseError;
  PrivacyState? _privacyState;
  final Map<String, Completer<PrivacyState>> _privacyRequests = {};
  @override
  PrivacyState? get privacyState => _privacyState;

  @override
  Future<PrivacyState> changePrivacy(String action) async {
    if (!['grant', 'revoke', 'delete'].contains(action)) {
      throw ArgumentError('Unknown privacy action');
    }
    final state = _privacyState;
    if (state?.available != true ||
        (action == 'grant' && state?.policyReady != true)) {
      throw StateError('Privacy policy or service unavailable');
    }
    await _ensureConnected();
    final requestId = 'privacy-$_bootId-${++_sequence}';
    final completion = Completer<PrivacyState>();
    _privacyRequests[requestId] = completion;
    try {
      final payload = jsonEncode({
        'schema_version': '1.0',
        'node': 'display',
        'data': {
          'kind': 'personalization',
          'request_id': requestId,
          'action': action,
          'confirmed': true,
          'policy_version': state!.policyVersion,
          'hub_boot_id': state.hubBootId
        }
      });
      final builder = MqttClientPayloadBuilder()..addUTF8String(payload);
      _client.publishMessage(
          _feedbackTopic, MqttQos.atLeastOnce, builder.payload!);
      return await completion.future.timeout(const Duration(seconds: 35));
    } finally {
      _privacyRequests.remove(requestId);
    }
  }

  String? _pendingRequestId;
  int? _pendingExpiresInS;
  PendingRequest? _pendingRequest;
  int _sequence = 0;
  String? _hubHealth;
  DateTime? _hubHealthAt;

  /// 연결 탭·주소 변경 화면이 현재 값을 채우는 데 쓴다.
  String get host => _host;

  /// `deskmate/health/hub` 의 status(online/offline, LWT·retain). 못 받았으면 null.
  /// 상태가 안 올 때 hub 가 죽은 건지(offline) FSM 출력이 없는 건지 가르는 근거다.
  String? get hubHealth => _hubHealth;
  DateTime? get hubHealthAt => _hubHealthAt;

  /// 마지막 메시지 해석 실패. 없으면 null.
  String? get lastParseError => _lastParseError;

  @override
  String get label => 'MQTT $_host:$port';

  @override
  bool get isConnected =>
      _client.connectionStatus?.state == MqttConnectionState.connected;

  @override
  String get connectionLabel {
    final failure = _lastParseError;
    if (failure != null) return '메시지 해석 실패';
    return isConnected ? 'MQTT 연결됨' : 'MQTT 재연결 중';
  }

  @override
  bool get supportsSensorTest => false;

  @override
  String? get displayMessage => _displayMessage;

  @override
  SessionReport? get sessionReport => _sessionReport;

  @override
  bool get hasPendingRequest => _pendingRequestId != null;

  @override
  PendingRequest? get pendingRequest =>
      _pendingRequestId == null ? null : _pendingRequest;

  @override
  int? get pendingRequestExpiresInS =>
      _pendingRequestId == null ? null : _pendingExpiresInS;

  @override
  Future<void> publishFeedbackData(Map<String, Object?> data) async {
    await _ensureConnected();
    final payload = jsonEncode({
      'schema_version': '1.0',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000,
      'node': 'display',
      'boot_id': _bootId,
      'seq': ++_sequence,
      'data': data,
    });
    final builder = MqttClientPayloadBuilder()..addUTF8String(payload);
    _client.publishMessage(_feedbackTopic, MqttQos.atLeastOnce, builder.payload!);
  }

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
      _client.subscribe(_messageTopic, MqttQos.atLeastOnce);
      _client.subscribe(_reportTopic, MqttQos.atLeastOnce);
      _client.subscribe(_hubHealthTopic, MqttQos.atLeastOnce);
      _client.subscribe(_keystrokeTopic, MqttQos.atMostOnce);
      if (onControlCommand != null || onTopicEvent != null) {
        _client.subscribe(_controlTopic, MqttQos.atLeastOnce);
      }
      if (onTopicEvent != null) {
        _client.subscribe(_controlResultTopic, MqttQos.atLeastOnce);
        _client.subscribe(_feedbackTopic, MqttQos.atLeastOnce);
      }
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
        final text = mqttPayloadText(publish.payload.message);
        if (text.isEmpty && received.topic == _reportTopic) {
          // 빈 retain = 브로커에서 리포트를 지웠다. 지난 숫자를 계속 보여 주지 않는다.
          _sessionReport = null;
          continue;
        }
        final envelope = jsonDecode(text) as Map<String, dynamic>;
        final logger = onTopicEvent;
        if (logger != null &&
            _loggedTopics.contains(received.topic) &&
            publish.header?.retain != true) {
          logger(received.topic, envelope);
        }
        if (received.topic == _stateTopic) {
          _latest = DisplayState.fromEnvelope(envelope);
          // 질문이 있는 동안 FSM 이 ACTION_* 를 벗어나면 카드도 내린다. 다만 상태와 무관한
          // 질문(checkin_suggest·vent_suggest)은 응답·만료까지 둔다.
          if (!_latest!.fsmState.startsWith('ACTION_') &&
              !_isStateIndependent(_pendingRequest?.kind)) {
            _pendingRequestId = null;
            _pendingExpiresInS = null;
            _pendingRequest = null;
          }
          final data = envelope['data'];
          final summary = data is Map ? data['sensor_summary'] : null;
          _privacyState = PrivacyState.fromSummary(summary);
          final acknowledged = _privacyState;
          if (acknowledged != null &&
              ['succeeded', 'failed'].contains(acknowledged.status)) {
            final waiter = _privacyRequests[acknowledged.requestId];
            if (waiter != null && !waiter.isCompleted) {
              waiter.complete(acknowledged);
            }
          }
        } else if (received.topic == _keystrokeTopic) {
          _pcKeystroke = KeystrokeMetrics.fromEnvelope(envelope);
        } else if (received.topic == _controlTopic) {
          // Control events must never replay from retained messages on startup.
          if (publish.header?.retain == true ||
              envelope['schema_version'] != '1.0') {
            continue;
          }
          final data = envelope['data'];
          if (data is Map<String, dynamic>) {
            unawaited(_handleControl(data));
          }
        } else if (received.topic == _requestTopic) {
          final data = envelope['data'];
          if (data is Map<String, dynamic>) {
            final requestId = data['request_id'];
            if (requestId is String && requestId.isNotEmpty) {
              _pendingRequestId = requestId;
              final expires = data['expires_in_s'];
              _pendingExpiresInS = expires is num ? expires.round() : null;
              _pendingRequest = PendingRequest.fromData(data);
            }
          }
        } else if (received.topic == _hubHealthTopic) {
          // envelope({"data":{"status":..}})과 평면({"status":..}) 둘 다 받는다.
          final data = envelope['data'];
          final status =
              (data is Map ? data['status'] : null) ?? envelope['status'];
          if (status is String && status.isNotEmpty) {
            _hubHealth = status;
            _hubHealthAt = DateTime.now();
          }
        } else if (received.topic == _reportTopic) {
          _sessionReport = SessionReport.fromEnvelope(envelope);
        } else if (received.topic == _messageTopic) {
          if (envelope['schema_version'] != '1.0') continue;
          final data = envelope['data'];
          if (data is Map<String, dynamic>) {
            _displayMessage = (data['text'] as String?)?.trim();
            if (_displayMessage?.isEmpty ?? false) _displayMessage = null;
          }
        }
      } catch (error) {
        // 손상됐거나 다른 schema 의 메시지는 display 를 멈추지 않는다. 다만
        // **조용히 삼키지는 않는다** - 전에는 여기서 걸리면 화면이 첫 상태를
        // 영영 못 받고 멈춰 있는데 아무 단서도 남지 않았다.
        _lastParseError = '${received.topic}: $error';
        stderr.writeln('[display] 메시지 해석 실패 $_lastParseError');
      }
    }
  }

  Future<void> _handleControl(Map<String, dynamic> command) async {
    final handler = onControlCommand;
    if (_closed || handler == null) return;
    try {
      final result = await handler(command);
      if (result == null || _closed || !isConnected) return;
      final builder = MqttClientPayloadBuilder()
        ..addUTF8String(jsonEncode({
          'schema_version': '1.0',
          'ts': DateTime.now().millisecondsSinceEpoch / 1000,
          'node': 'display-control',
          'boot_id': _bootId,
          'seq': ++_sequence,
          'data': result,
        }));
      _client.publishMessage(
          _controlResultTopic, MqttQos.atLeastOnce, builder.payload!);
    } catch (error) {
      stderr.writeln('[display] 제어 명령 처리 실패: $error');
    }
  }

  @override
  Future<void> feedback(String verdict,
      {String? correctedState, int? responseMs, String? note}) async {
    await _ensureConnected();
    final payload = jsonEncode({
      'schema_version': '1.0',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000,
      'node': 'display',
      'boot_id': _bootId,
      'seq': ++_sequence,
      'data': buildFeedbackData(
        requestId: _pendingRequestId ?? 'atlas-display',
        verdict: verdict,
        correctedState: correctedState,
        responseMs: responseMs,
        note: note,
      ),
    });
    final builder = MqttClientPayloadBuilder()..addUTF8String(payload);
    _client.publishMessage(
      _feedbackTopic,
      MqttQos.atLeastOnce,
      builder.payload!,
    );
    _pendingRequestId = null;
    _pendingExpiresInS = null;
    _pendingRequest = null;
  }

  static bool _isStateIndependent(String? kind) =>
      kind == 'checkin_suggest' || kind == 'vent_suggest';

  @override
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event}) {
    throw UnsupportedError('MQTT display 연결에서는 Pi 4 센서 테스트 API를 사용하지 않습니다.');
  }

  @override
  void close() {
    _closed = true;
    for (final request in _privacyRequests.values) {
      if (!request.isCompleted) {
        request.completeError(StateError('Connection closed'));
      }
    }
    _privacyRequests.clear();
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
  bool get isConnected => true;

  @override
  String get connectionLabel => '내장 데모';

  @override
  bool get supportsSensorTest => false;

  @override
  String? get displayMessage => null;

  @override
  SessionReport? get sessionReport {
    // 데모 빌드는 Pi 4 MQTT broker 없이도 결과 화면을 현장에서 확인할 수
    // 있도록 고정된 예시 리포트를 제공한다. MQTT/HTTP 연결에서는 이 값을
    // 사용하지 않고 Hub가 발행한 실제 세션 리포트만 표시한다.
    final endedAt = DateTime.now();
    return SessionReport(
      startedAt: endedAt.subtract(const Duration(minutes: 52)),
      endedAt: endedAt,
      durationSeconds: 52 * 60,
      focusSeconds: 38 * 60,
      focusRatio: 0.73,
      fatigueEpisodes: 2,
      interventionTotal: 3,
      interventionRecovered: 2,
      breakAcceptRate: 0.67,
      stateDurations: const {
        'FOCUS_PC': 2280,
        'RECOVERY': 420,
        'FATIGUE_SUSPECT': 240,
        'START': 180,
      },
    );
  }

  @override
  bool get hasPendingRequest => false;

  @override
  PendingRequest? get pendingRequest => null;

  @override
  Future<void> publishFeedbackData(Map<String, Object?> data) async {}

  @override
  int? get pendingRequestExpiresInS => null;

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
  Future<void> feedback(String verdict,
      {String? correctedState, int? responseMs, String? note}) async {}

  @override
  Future<void> sendTestFrame(TestSensorInput input,
      {required String command, int advanceSeconds = 30, String? event}) {
    throw UnsupportedError('Hub 연결 빌드에서만 센서 테스트를 사용할 수 있습니다.');
  }

  @override
  void close() {}
}
