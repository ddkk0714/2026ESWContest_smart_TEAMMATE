/// 앱 시작 때 MQTT 연결을 단계별로 확인하는 일회성 진단기.
///
/// 브로커에 못 붙으면 지금까지는 "재연결 중" 만 보였다. 여기서는 어디서 막혔는지를
/// ① TCP 도달 → ② MQTT CONNACK → ③ hub 생존(`deskmate/health/hub`) → ④ 첫
/// `deskmate/state/phase` 순으로 가려 [LinkCause] 로 돌려준다. 화면(연결 가이드)이
/// 그 원인으로 안내 문구를 고른다.
///
/// 진단용 클라이언트는 앱이 상시 구독하는 클라이언트와 따로 만들고 끝나면 닫는다 —
/// 진단이 실패해도 앱의 자동 재연결에는 영향이 없다.
///
/// 계획: docs/plan/app-connection-robustness-plan.md §5.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';

import 'link_status.dart' show LinkCause;

const _hubHealthTopic = 'deskmate/health/hub';
const _stateTopic = 'deskmate/state/phase';

enum MqttProbeStep { tcp, connack, hubHealth, firstState }

class MqttProbeResult {
  const MqttProbeResult({
    required this.ok,
    required this.cause,
    this.failedAt,
    this.detail,
    this.hubHealth,
    required this.elapsed,
  });

  /// 첫 state/phase 까지 받았으면 true.
  final bool ok;

  /// ok 면 [LinkCause.none].
  final LinkCause cause;

  /// ok 면 null.
  final MqttProbeStep? failedAt;

  /// 원인을 좁히는 원문(예외 문자열, CONNACK 코드).
  final String? detail;

  /// 받은 health/hub 의 status("online"/"offline"). 못 받았으면 null.
  final String? hubHealth;

  final Duration elapsed;
}

/// 각 단계를 "시작" 할 때 불린다. 화면의 진행 표시용.
typedef MqttProbeProgress = void Function(MqttProbeStep step);

/// 진단을 한 번 돌린다. 어떤 경우에도 예외를 던지지 않는다.
///
/// health/hub 를 제때 못 받는 것은 실패가 아니다 — 브로커에 retain 이 없을 수 있다.
/// 그때는 hubHealth 를 null 로 두고 ④ 로 간다. health 가 "offline" 이면 state 가
/// 와도 hub 실패로 본다: retain 된 state 는 hub 가 죽기 전의 마지막 값일 수 있다.
Future<MqttProbeResult> probeMqtt(
  String host,
  int port, {
  Duration tcpTimeout = const Duration(seconds: 2),
  Duration connackTimeout = const Duration(seconds: 3),
  Duration healthTimeout = const Duration(seconds: 3),
  Duration firstStateTimeout = const Duration(seconds: 10),
  MqttProbeProgress? onStep,
}) async {
  final watch = Stopwatch()..start();
  String? hubHealth;
  MqttProbeResult done(LinkCause cause, MqttProbeStep? failedAt,
          {String? detail}) =>
      MqttProbeResult(
        ok: cause == LinkCause.none,
        cause: cause,
        failedAt: failedAt,
        detail: detail,
        hubHealth: hubHealth,
        elapsed: watch.elapsed,
      );

  // ① TCP 도달. 브로커 프로토콜과 무관하게 "망이 닿는가" 만 본다.
  onStep?.call(MqttProbeStep.tcp);
  try {
    final socket = await Socket.connect(host, port, timeout: tcpTimeout);
    socket.destroy();
  } on Object catch (error) {
    return done(LinkCause.networkUnreachable, MqttProbeStep.tcp,
        detail: error.toString());
  }

  final clientId = 'deskmate-probe-${DateTime.now().microsecondsSinceEpoch}';
  final client = MqttServerClient.withPort(host, clientId, port)
    ..logging(on: false)
    ..keepAlivePeriod = 30
    ..autoReconnect = false
    ..connectionMessage =
        MqttConnectMessage().withClientIdentifier(clientId).startClean();

  final healthSeen = Completer<void>();
  final stateSeen = Completer<void>();
  StreamSubscription<List<MqttReceivedMessage<MqttMessage?>>>? updates;
  void onUpdates(List<MqttReceivedMessage<MqttMessage?>> messages) {
    for (final message in messages) {
      final publish = message.payload;
      if (publish is! MqttPublishMessage) continue;
      if (message.topic == _stateTopic) {
        // 도착만 본다. 내용 검증은 앱의 상시 클라이언트가 한다 — 여기서 스키마로
        // 걸러 내면 "상태가 안 온다" 와 "해석을 못 한다" 가 같은 실패로 섞인다.
        if (!stateSeen.isCompleted) stateSeen.complete();
      } else if (message.topic == _hubHealthTopic) {
        final status = _healthStatus(
            MqttPublishPayload.bytesToStringAsString(publish.payload.message));
        if (status != null) {
          hubHealth = status;
          if (!healthSeen.isCompleted) healthSeen.complete();
        }
      }
    }
  }

  try {
    // ② CONNACK.
    onStep?.call(MqttProbeStep.connack);
    MqttClientConnectionStatus? status;
    try {
      status = await client.connect().timeout(connackTimeout);
    } on Object catch (error) {
      // 거부 코드가 오면 mqtt_client 는 반환 대신 예외를 던진다. 코드가 있으면 그것을
      // 앞에 둔다 — 인증 거부(notAuthorized)와 무응답은 조치가 다르다.
      final code = client.connectionStatus?.returnCode;
      return done(LinkCause.brokerRefused, MqttProbeStep.connack,
          detail: code != null &&
                  code != MqttConnectReturnCode.connectionAccepted &&
                  code != MqttConnectReturnCode.noneSpecified
              ? 'CONNACK ${code.name}: $error'
              : error.toString());
    }
    if (status?.state != MqttConnectionState.connected) {
      return done(LinkCause.brokerRefused, MqttProbeStep.connack,
          detail: 'CONNACK ${status?.returnCode?.name ?? '응답 없음'}');
    }
    updates = client.updates?.listen(onUpdates);
    client.subscribe(_hubHealthTopic, MqttQos.atMostOnce);
    client.subscribe(_stateTopic, MqttQos.atMostOnce);

    // ③ hub 생존. state 가 먼저 오면 더 기다리지 않는다 — retain 두 개는 보통 같이 온다.
    onStep?.call(MqttProbeStep.hubHealth);
    try {
      await Future.any([healthSeen.future, stateSeen.future])
          .timeout(healthTimeout);
    } on TimeoutException {
      // health 없음은 실패가 아니다.
    }
    if (hubHealth == 'offline') {
      return done(LinkCause.hubOffline, MqttProbeStep.hubHealth,
          detail: 'deskmate/health/hub = offline');
    }

    // ④ 첫 상태. 기준은 진단 시작부터의 시간이다.
    onStep?.call(MqttProbeStep.firstState);
    final left = firstStateTimeout - watch.elapsed;
    if (!stateSeen.isCompleted) {
      try {
        await stateSeen.future.timeout(left.isNegative ? Duration.zero : left);
      } on TimeoutException {
        return done(LinkCause.noState, MqttProbeStep.firstState,
            detail: hubHealth == null
                ? 'health/hub 도 받지 못함'
                : 'health/hub = $hubHealth');
      }
    }
    // 상태를 기다리는 사이에 offline 이 도착했으면 그쪽이 원인이다.
    if (hubHealth == 'offline') {
      return done(LinkCause.hubOffline, MqttProbeStep.hubHealth,
          detail: 'deskmate/health/hub = offline');
    }
    return done(LinkCause.none, null);
  } on Object catch (error) {
    return done(LinkCause.brokerRefused, MqttProbeStep.connack,
        detail: error.toString());
  } finally {
    await updates?.cancel();
    try {
      client.disconnect();
    } on Object {
      // 이미 끊긴 클라이언트는 그대로 둔다.
    }
  }
}

/// health payload 의 status. envelope(`{"data":{"status":..}}`)과 평면
/// (`{"status":..}`) 둘 다 받는다(mqtt-topics.md, C++ 브리지는 평면을 쓴다).
String? _healthStatus(String text) {
  try {
    final json = jsonDecode(text);
    if (json is! Map) return null;
    final data = json['data'];
    final status = (data is Map ? data['status'] : null) ?? json['status'];
    return status is String && status.isNotEmpty ? status : null;
  } on FormatException {
    return null;
  }
}
