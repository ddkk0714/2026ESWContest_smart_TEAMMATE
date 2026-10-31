// V18 ↔ 보드(Pi 4 Mosquitto) 연결. 받은 것은 메모리에만 두고, 보내는 것은 세 가지뿐이다:
//   1) `deskmate/feedback/user`  — 제안 카드 응답(accept·reject·timeout)
//   2) `deskmate/profile/user`   — 지금 프로필의 선호(retain, 이름 없음)
//   3) `POST http://<보드>:8771/api/placement` — 장치 위치
// 기기 제어 명령(control/cmd)은 보내지 않는다. 블루투스 실행은 같은 앱의 DeveloperController 가 맡는다 —
// 보드에서 deskmate_display(블루투스 권한 있음)와 같이 띄우면 실행자가 둘이 되니 하나만 띄운다.
//
// 주소가 비었거나 보드에 닿지 않으면 [live] 가 false 이고, 화면은 V17 과 같은 메모리 동작을 쓴다.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../demo_model.dart';
import '../pr56/display_state.dart';
import '../developer/pr74/voice_alert.dart';
import 'auto_action.dart';
import 'device_board.dart';
import 'link_settings.dart';
import 'live_request.dart';
import 'payload.dart';
import 'placement_client.dart';
import 'profile_payload.dart';
import 'transport.dart';
import 'mqtt_watchdog.dart';

enum LinkStatus { off, connecting, connected }

class LiveWeather {
  const LiveWeather({this.temperatureC, this.condition});
  final double? temperatureC;
  final String? condition;

  String? get text {
    final label = switch (condition) {
      'clear' => '맑음',
      'cloudy' => '흐림',
      'rain' => '비',
      'snow' => '눈',
      'fog' => '안개',
      _ => null,
    };
    final parts = [
      if (temperatureC != null) '${temperatureC!.toStringAsFixed(1)}°',
      if (label != null) label,
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }
}

/// `deskmate/session/report` 에서 화면이 쓰는 숫자만.
class LiveReport {
  const LiveReport({this.durationS, this.fatigueEpisodes, this.interventions});
  final int? durationS;
  final int? fatigueEpisodes;
  final int? interventions;

  static LiveReport fromData(Map<String, dynamic> data) {
    final episodes = data['fatigue_episodes'];
    final counts = jsonMap(data['intervention_counts']);
    return LiveReport(
      durationS: (data['duration_s'] as num?)?.round(),
      fatigueEpisodes: episodes is List ? episodes.length : null,
      interventions: (counts['total'] as num?)?.round(),
    );
  }
}

class LiveLink extends ChangeNotifier {
  LiveLink({
    LiveTransportFactory? transportFactory,
    LinkSettingsStore? store,
    PlacementClient Function(String host)? placementFactory,
    DateTime Function()? clock,
    this.retryDelay = const Duration(seconds: 5),
    this.staleAfter = const Duration(seconds: 45),
  })  : _transportFactory = transportFactory ?? MqttLiveTransport.new,
        _store = store,
        _placementFactory = placementFactory ?? HttpPlacementClient.new,
        _clock = clock ?? DateTime.now {
    // node 는 개발자 앱(ui_env_app, `display`)과 hub 로그에서 구분되게 `pi5-ui`.
    // hub(mqtt_lines.route_mqtt_message → live.tick_once)·보드 C++ 브리지는 feedback 의 node 로 거르지 않는다(10-25 확인).
    notices.addListener(_notify);
    autoActions.addListener(_notify);
    _feedbackWriter = EnvelopeWriter(node: nodeName, clock: _clock);
    _profileWriter = EnvelopeWriter(node: nodeName, clock: _clock);
  }

  static const nodeName = 'pi5-ui';
  static const stateTopic = 'deskmate/state/phase';
  static const requestTopic = 'deskmate/interaction/request';
  static const commandTopic = 'deskmate/control/cmd';
  static const resultTopic = 'deskmate/control/result';
  static const healthTopic = 'deskmate/health/hub';
  static const reportTopic = 'deskmate/session/report';
  static const weatherTopic = 'deskmate/sensor/weather';
  static const keystrokeTopic = 'deskmate/sensor/keystroke';
  static const feedbackTopic = 'deskmate/feedback/user';

  /// 빠른 시연 버튼 → hub demo_mode.py {"speed": "fast"|"normal"}. 보내기만 한다(지금 모드는 state/phase 로 받는다).
  static const demoModeTopic = 'deskmate/demo/mode';
  static const subscriptions = [
    stateTopic,
    requestTopic,
    commandTopic,
    resultTopic,
    healthTopic,
    reportTopic,
    weatherTopic,
    feedbackTopic,
    keystrokeTopic,
  ];

  final _events = StreamController<LiveMessage>.broadcast(sync: true);
  Stream<LiveMessage> get messages => _events.stream;

  final LiveTransportFactory _transportFactory;
  final LinkSettingsStore? _store;
  final PlacementClient Function(String host) _placementFactory;
  final DateTime Function() _clock;
  final Duration retryDelay;

  /// 이 시간 동안 state/phase 가 없으면 보드 값을 쓰지 않고 메모리 동작으로 돌아간다(hub 는 10 s 마다 보낸다).
  final Duration staleAfter;
  late final EnvelopeWriter _feedbackWriter;
  late final EnvelopeWriter _profileWriter;

  String host = '';
  int port = LinkSettings.defaultPort;

  /// 지금 주소가 어디서 왔는지(연결 상태 칸 표시용).
  LinkSource hostSource = LinkSource.off;
  LinkStatus status = LinkStatus.off;
  final notices = NoticeBoard();
  DisplayState? state;
  DateTime? stateAt;
  LiveRequest? request;
  final devices = DeviceBoard();

  /// hub 자동 실행(무드등·선풍기·공기청정기) 카드 — 무엇을 왜 했는지 + 되돌리기(auto_action.dart).
  final autoActions = AutoActionBoard();
  LiveWeather? weather;
  LiveReport? report;

  /// `deskmate/health/hub` status(online|offline). 못 받았으면 null.
  String? hubHealth;

  /// state/phase 를 새로 받을 때마다. 화면 흐름(집중·쉬기·종료)을 맞추는 데 쓴다.
  void Function(DisplayState state)? onState;

  LiveTransport? _transport;
  StreamSubscription<LiveMessage>? _messageSub;
  StreamSubscription<bool>? _connectionSub;
  Timer? _retry;
  Timer? _expiry;
  Timer? _watchTimer;
  final _watchdog = MqttWatchdog();
  int? _lastStateSequence;
  Persona? _profile;
  int _generation = 0;
  bool _disposed = false;

  /// 보드 값을 화면에 쓸 수 있는가: 연결돼 있고 최근 state/phase 가 있다.
  bool get live {
    final at = stateAt;
    return status == LinkStatus.connected &&
        state != null &&
        at != null &&
        _clock().difference(at) < staleAfter;
  }

  bool get configured => host.isNotEmpty;

  /// 이 연결의 시계(자동 실행 카드의 되돌리기 남은 시간을 같은 시계로 잰다).
  DateTime get now => _clock();

  /// 저장된 주소를 읽고, 있으면 연결을 시작한다.
  void start() {
    final saved = _store?.load() ?? const LinkSettings();
    host = saved.host;
    port = saved.port;
    hostSource = saved.source;
    if (configured) unawaited(_connect());
  }

  /// 연결 설정을 바꾼다. 빈 host 면 연결을 끊고 메모리 동작만 쓴다. 저장하지 못하면 false.
  bool configure(String rawHost, int newPort) {
    host = normalizeHost(rawHost);
    port = newPort > 0 && newPort < 65536 ? newPort : LinkSettings.defaultPort;
    final saved = _store?.save(LinkSettings(host: host, port: port)) ?? true;
    hostSource = host.isEmpty ? LinkSource.off : LinkSource.saved;
    _teardown();
    if (configured) {
      unawaited(_connect());
    } else {
      status = LinkStatus.off;
      _notify();
    }
    return saved;
  }

  Future<void> _connect() async {
    final generation = ++_generation;
    _watchTimer?.cancel();
    _retry?.cancel();
    final transport = _transportFactory(host, port);
    _transport = transport;
    status = LinkStatus.connecting;
    _notify();
    _messageSub = transport.messages.listen(_onMessage);
    _connectionSub = transport.connection.listen((up) {
      if (generation != _generation) return;
      status = up ? LinkStatus.connected : LinkStatus.connecting;
      if (up) _publishProfile();
      _notify();
    });
    _watchTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (generation != _generation || _disposed) return;
      if (_watchdog.shouldReconnect(_clock(), stateAt)) {
        _teardown(keepGeneration: true);
        unawaited(_connect());
      }
    });
    final ok = await transport.connect();
    if (generation != _generation || _disposed) return;
    if (!ok) {
      status = LinkStatus.connecting;
      _notify();
      _teardown(keepGeneration: true);
      _retry = Timer(retryDelay, () {
        if (generation == _generation && !_disposed) unawaited(_connect());
      });
      return;
    }
    for (final topic in subscriptions) {
      transport.subscribe(topic);
    }
    status = LinkStatus.connected;
    _publishProfile();
    _notify();
  }

  void _teardown({bool keepGeneration = false}) {
    if (!keepGeneration) _generation++;
    _retry?.cancel();
    _watchTimer?.cancel();
    _messageSub?.cancel();
    _connectionSub?.cancel();
    _messageSub = null;
    _connectionSub = null;
    _transport?.close();
    _transport = null;
    if (!keepGeneration) {
      notices.dismiss(_clock());
      state = null;
      stateAt = null;
      _lastStateSequence = null;
      _watchdog.stateReceived();
      _clearRequest();
    }
  }

  void _onMessage(LiveMessage message) {
    if (!_disposed) _events.add(message);
    try {
      if (message.text.isEmpty) {
        if (message.topic == reportTopic) report = null;
        _notify();
        return;
      }
      final envelope = jsonMap(jsonDecode(message.text));
      final data = jsonMap(envelope['data']);
      switch (message.topic) {
        case stateTopic:
          final next = DisplayState.fromEnvelope(envelope);
          state = next;
          if (isFreshState(
              mqtt: true,
              previousSequence: _lastStateSequence,
              sequence: next.sequence)) {
            _lastStateSequence = next.sequence;
            stateAt = _clock();
            _watchdog.stateReceived();
          }
          // hub 가 ACTION_* 를 벗어나면 그 상태의 질문은 내린다. checkin·vent 는 상태와 무관하게 응답·만료까지 둔다.
          final pending = request;
          if (pending != null &&
              !pending.stateIndependent &&
              !next.fsmState.startsWith('ACTION_')) {
            _clearRequest();
          }
          onState?.call(next);
        case requestTopic:
          if (message.retained) return; // 지난 질문이 다시 뜨지 않게
          if (ScreenNotice.isNoticeRequest(data)) {
            notices.show(ScreenNotice.fromRequest(data, _clock()));
            return;
          }
          final next = LiveRequest.fromData(data, _clock());
          if (next == null) return;
          request = next;
          _expiry?.cancel();
          final seconds = next.expiresInS;
          if (seconds != null) {
            _expiry = Timer(Duration(seconds: seconds), () {
              if (identical(request, next)) unawaited(answer('timeout'));
            });
          }
        case commandTopic:
          if (message.retained) return;
          devices.onCommand(data);
          autoActions.onCommand(data, state, _clock());
        case resultTopic:
          if (message.retained) return;
          devices.onResult(data);
          autoActions.onResult(data);
        case healthTopic:
          final health = data['status'] ?? envelope['status'];
          if (health is String && health.isNotEmpty) hubHealth = health;
        case reportTopic:
          report = LiveReport.fromData(data);
        case weatherTopic:
          final temp = data['temp_c'];
          weather = LiveWeather(
              temperatureC: temp is num ? temp.toDouble() : null,
              condition: data['condition']?.toString());
      }
      _notify();
    } on Object catch (error) {
      // 깨진·다른 schema 의 메시지는 화면을 멈추지 않는다. 내용(프로필·센서)은 남기지 않고 토픽만 알린다.
      debugPrint('[v18] 메시지 해석 실패 ${message.topic}: ${error.runtimeType}');
    }
  }

  /// Developer tools share this connection instead of opening a second executor.
  bool publishData(String topic, Map<String, Object?> data) {
    final transport = _transport;
    if (transport == null || !transport.isConnected) return false;
    transport.publish(topic, _feedbackWriter.encode(data));
    return true;
  }

  /// 지금 카드에 답한다. verdict = accept | reject | timeout. 보냈으면 true.
  Future<bool> answer(String verdict) async {
    final pending = request;
    if (pending == null) return false;
    _clearRequest();
    final transport = _transport;
    var sent = false;
    if (transport != null && transport.isConnected) {
      transport.publish(feedbackTopic,
          _feedbackWriter.encode(pending.feedback(verdict, _clock())));
      sent = true;
    }
    _notify();
    return sent;
  }

  /// 자동 실행 카드의 되돌리기. hub 는 질문 없는 reject(request_id atlas-display)를 직전 자동 실행의 되돌리기로 받는다 —
  /// 디스패처가 undo_window_sec(60 s) 안이면 이전 밝기로 되돌리고, 개인화 정책은 그 방법에 undo 보상을 준다. 보냈으면 true.
  bool undoAutoAction() {
    final card = autoActions.current(_clock());
    if (card == null || !card.canUndo(_clock())) return false;
    final ms = _clock().difference(card.executedAt).inMilliseconds;
    final sent = publishData(
        feedbackTopic,
        buildFeedbackData(
            requestId: 'atlas-display',
            verdict: 'reject',
            responseMs: ms < 0 ? 0 : ms));
    if (sent) autoActions.markUndoing(_clock());
    return sent;
  }

  /// 빠른 시연 켜기·끄기. hub 가 시연 프로파일이 아니면 받아도 무시한다(fsm.yaml demo_fast.enabled). 보냈으면 true.
  bool setDemoSpeed(bool fast) =>
      publishData(demoModeTopic, {'speed': fast ? 'fast' : 'normal'});

  void dismissNotice() {
    final now = _clock();
    final notice = notices.dismiss(now);
    final id = notice?.requestId;
    if (notice == null || id == null) return;
    final elapsed = now.difference(notice.shownAt).inMilliseconds;
    publishData(
        feedbackTopic,
        buildFeedbackData(
            requestId: id,
            verdict: 'dismiss',
            responseMs: elapsed < 0 ? 0 : elapsed));
  }

  void _clearRequest() {
    _expiry?.cancel();
    _expiry = null;
    request = null;
  }

  /// 프로필이 만들어지거나 바뀌거나 골라질 때. 연결 전이면 기억했다가 연결되면 보낸다.
  void publishProfile(Persona persona) {
    _profile = persona;
    _publishProfile();
  }

  void _publishProfile() {
    final persona = _profile;
    final transport = _transport;
    if (persona == null || transport == null || !transport.isConnected) return;
    transport.publish(profileTopic, _profileWriter.encode(profileData(persona)),
        retain: true);
  }

  /// 장치 위치를 Pi 4 자세 서비스에 보낸다. 주소가 없으면 시도하지 않고 null,
  /// 그 밖에는 화면에 잠깐 보일 한 줄(성공·실패)을 돌려준다. 실패해도 예외를 내지 않는다.
  Future<String?> sendPlacement(String placement) async {
    if (!configured) return null;
    PlacementClient? client;
    try {
      client = _placementFactory(host);
      final applied = await client.apply(placement);
      return '장치 위치를 ${placementPhrase(applied)} 맞췄어요.';
    } on PlacementException catch (error) {
      return error.userText;
    } on Object {
      return const PlacementException(PlacementFailure.other, '').userText;
    } finally {
      client?.close();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _teardown();
    notices.removeListener(_notify);
    notices.dispose();
    autoActions.removeListener(_notify);
    autoActions.dispose();
    _events.close();
    super.dispose();
  }
}
