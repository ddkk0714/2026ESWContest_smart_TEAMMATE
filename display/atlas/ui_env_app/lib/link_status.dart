/// 연결 대상별 상태와, 그 상태를 정하는 규칙.
///
/// 연결 탭·경고 배지·상단 상태 줄이 모두 이 모델 하나를 읽는다. 화면마다 따로
/// "끊겼나" 를 판단하면 배지는 괜찮다는데 탭은 끊겼다고 하는 식으로 어긋난다.
///
/// 규칙은 전부 순수 함수다. 시각(`now`)을 인자로 받으므로 실기 없이 테스트로
/// 경계값을 잡을 수 있다. 화면은 1초 주기로 스냅샷을 새로 만들기만 하면 된다.
///
/// 계획: docs/plan/app-connection-robustness-plan.md §4·§5.
library;

/// 앱이 지켜보는 연결 대상. 순서가 연결 탭의 표시 순서다 — 위쪽이 먼저 붙어야
/// 아래쪽이 의미가 있다(MQTT 가 없으면 hub 도, 센서도 알 수 없다).
enum LinkId {
  mqtt,
  hub,
  mmwave,
  environment,
  keystroke,
  posture,
  speaker,
  lamp,
}

/// 상태. [unconfigured] 는 문제가 아니다 — 스피커를 안 쓰는 설치에서 배지가
/// 계속 뜨면 사람들이 배지를 무시하게 된다.
enum LinkHealth { ok, checking, degraded, down, unconfigured }

/// 왜 그 상태인지. 안내 문구는 이 값으로 고른다.
enum LinkCause {
  none,
  notConfigured,
  demo,
  networkUnreachable,
  brokerRefused,
  hubOffline,
  noState,
  stale,
  parseError,
  permissionDenied,
  deviceNotFound,
  disconnected,
}

class LinkStatus {
  const LinkStatus({
    required this.id,
    required this.health,
    this.cause = LinkCause.none,
    this.lastSeen,
    this.detail,
  });

  final LinkId id;
  final LinkHealth health;
  final LinkCause cause;

  /// 마지막으로 살아 있음을 확인한 시각. 연결 탭에 "n초 전" 으로 쓴다.
  final DateTime? lastSeen;

  /// 원인을 좁히는 데 도움이 되는 원문(예외 문자열, 주소). 화면에는 작게만 쓴다.
  final String? detail;

  /// 사람이 손을 써야 하는가. 배지와 상태 줄은 이것만 본다.
  bool get needsAttention =>
      health == LinkHealth.down || health == LinkHealth.degraded;

  String get label => linkLabel(id);

  String get guidance => linkGuidance(id, cause);

  @override
  bool operator ==(Object other) =>
      other is LinkStatus &&
      other.id == id &&
      other.health == health &&
      other.cause == cause &&
      other.lastSeen == lastSeen &&
      other.detail == detail;

  @override
  int get hashCode => Object.hash(id, health, cause, lastSeen, detail);

  @override
  String toString() => 'LinkStatus(${id.name}: ${health.name}/${cause.name})';
}

String linkLabel(LinkId id) => switch (id) {
      LinkId.mqtt => 'MQTT 브로커',
      LinkId.hub => 'Pi 4 hub',
      LinkId.mmwave => 'mmWave 센서',
      LinkId.environment => '환경 센서',
      LinkId.keystroke => '키스트로크',
      LinkId.posture => '자세(카메라)',
      LinkId.speaker => '스피커',
      LinkId.lamp => '램프',
    };

/// 원인별 권장 조치. 문구는 현장에서 바로 할 수 있는 일 하나로 끝낸다 —
/// 배경 설명은 연결 탭의 상세나 문서가 맡는다.
String linkGuidance(LinkId id, LinkCause cause) {
  switch (cause) {
    case LinkCause.none:
      return '';
    case LinkCause.notConfigured:
      return switch (id) {
        LinkId.mqtt => '브로커 주소가 없습니다. 연결 탭에서 Pi 4 IP 를 입력하세요.',
        LinkId.speaker || LinkId.lamp => '연결 탭에서 기기를 검색해 선택하세요.',
        _ => '설정이 없습니다.',
      };
    case LinkCause.demo:
      return '데모 화면입니다. 실제 연결을 쓰려면 연결 탭에서 주소를 입력하세요.';
    case LinkCause.networkUnreachable:
      return 'Pi 4 에 닿지 않습니다. 전원·케이블·같은 망인지 확인하고, IP 가 바뀌었으면 주소를 다시 입력하세요.';
    case LinkCause.brokerRefused:
      return '브로커가 응답하지 않습니다. Pi 4 에서 pi4-broker-start.sh 를 실행하세요.';
    case LinkCause.hubOffline:
      return 'hub 가 꺼져 있습니다. Pi 4 에서 pi4-hub-activate.sh 를 실행하세요.';
    case LinkCause.noState:
      return 'hub 는 떠 있는데 상태가 오지 않습니다. 센서 연결과 hub 로그를 확인하세요.';
    case LinkCause.stale:
      return switch (id) {
        LinkId.mqtt || LinkId.hub => '상태 수신이 끊겼습니다. 자동으로 다시 연결하는 중입니다.',
        LinkId.mmwave ||
        LinkId.environment =>
          'ESP32 값이 들어오지 않습니다. ESP32 전원과 UART 배선을 확인하세요.',
        LinkId.keystroke =>
          'PC 수집기 값이 끊겼습니다. PC 에서 python -m collector 가 돌고 있는지 확인하세요.',
        LinkId.posture => '자세 판정이 멈췄습니다. 카메라와 판정 서비스를 확인하세요.',
        LinkId.speaker || LinkId.lamp => '기기 응답이 없습니다. 전원을 확인하세요.',
      };
    case LinkCause.parseError:
      return '받은 메시지를 해석하지 못했습니다. 앱과 hub 버전이 맞는지 확인하세요.';
    case LinkCause.permissionDenied:
      return switch (id) {
        LinkId.posture =>
          '보드 직결 권한이 없습니다. UART D-Bus 정책 파일을 설치하고 dbus 를 reload 하세요.',
        _ => '권한이 없습니다. Atlas 권한 요청을 허용하세요.',
      };
    case LinkCause.deviceNotFound:
      return switch (id) {
        LinkId.posture => '카메라를 찾지 못했습니다. 판정 서비스(camsvc)와 ESP32-CAM 연결을 확인하세요.',
        _ => '기기를 찾지 못했습니다. 전원과 페어링 모드를 확인하세요.',
      };
    case LinkCause.disconnected:
      return '연결이 끊겼습니다. 자동으로 다시 연결하는 중입니다.';
  }
}

/// 판정 기준 시간. 값은 계획 §4 의 자동 복구 규칙과 맞춘다.
class LinkRules {
  const LinkRules({
    this.stateStaleAfter = const Duration(seconds: 15),
    this.firstStateTimeout = const Duration(seconds: 10),
    this.keystrokeStaleAfter = const Duration(seconds: 10),
  });

  /// 상태가 이만큼 안 오면 MQTT 가 "연결됨" 이어도 끊긴 것으로 본다.
  /// mqtt_client 의 연결 플래그는 재연결 중 흔들려서 그것만 믿을 수 없다.
  final Duration stateStaleAfter;

  /// 연결 직후 첫 상태를 기다리는 시간. 넘으면 hub 쪽 문제로 본다(§5 ④).
  final Duration firstStateTimeout;

  /// collector 는 1 Hz 로 보낸다. 10 초면 순간 끊김과 진짜 중단을 가를 수 있다.
  final Duration keystrokeStaleAfter;
}

/// MQTT 상태 흐름. §5 의 ①~④ 중 어디서 막혔는지를 [LinkCause] 로 돌려준다.
///
/// [connectError] 는 마지막 연결 시도의 실패 종류다(진단기가 채운다).
/// 연결 후에는 [lastStateAt] 의 신선도가 기준이다.
LinkStatus evaluateMqtt({
  required DateTime now,
  required bool configured,
  bool demo = false,
  bool connected = false,
  DateTime? connectedAt,
  DateTime? lastStateAt,
  LinkCause? connectError,
  String? parseError,
  String? detail,
  LinkRules rules = const LinkRules(),
}) {
  if (demo) {
    return const LinkStatus(
        id: LinkId.mqtt,
        health: LinkHealth.unconfigured,
        cause: LinkCause.demo);
  }
  if (!configured) {
    return const LinkStatus(
        id: LinkId.mqtt,
        health: LinkHealth.down,
        cause: LinkCause.notConfigured);
  }
  if (parseError != null) {
    return LinkStatus(
        id: LinkId.mqtt,
        health: LinkHealth.degraded,
        cause: LinkCause.parseError,
        lastSeen: lastStateAt,
        detail: parseError);
  }
  final fresh = lastStateAt != null &&
      now.difference(lastStateAt) <= rules.stateStaleAfter;
  if (fresh) {
    return LinkStatus(
        id: LinkId.mqtt,
        health: LinkHealth.ok,
        lastSeen: lastStateAt,
        detail: detail);
  }
  if (!connected) {
    // 한 번이라도 상태를 받았으면 "끊겼다", 아니면 어디서 막혔는지를 말한다.
    if (lastStateAt != null) {
      return LinkStatus(
          id: LinkId.mqtt,
          health: LinkHealth.down,
          cause: LinkCause.disconnected,
          lastSeen: lastStateAt,
          detail: detail);
    }
    if (connectError == null) {
      return LinkStatus(
          id: LinkId.mqtt, health: LinkHealth.checking, detail: detail);
    }
    return LinkStatus(
        id: LinkId.mqtt,
        health: LinkHealth.down,
        cause: connectError,
        detail: detail);
  }
  // 연결은 됐는데 상태가 없다.
  if (lastStateAt == null) {
    final waited =
        connectedAt == null ? Duration.zero : now.difference(connectedAt);
    if (waited <= rules.firstStateTimeout) {
      return LinkStatus(
          id: LinkId.mqtt, health: LinkHealth.checking, detail: detail);
    }
    return LinkStatus(
        id: LinkId.mqtt,
        health: LinkHealth.degraded,
        cause: LinkCause.noState,
        detail: detail);
  }
  return LinkStatus(
      id: LinkId.mqtt,
      health: LinkHealth.degraded,
      cause: LinkCause.stale,
      lastSeen: lastStateAt,
      detail: detail);
}

/// hub 생존. `deskmate/health/hub` 의 status(online/offline, LWT)를 받는다.
/// 메시지를 아직 못 받았으면 상태 수신 여부로 대신 판단한다 — 상태가 오면
/// hub 는 살아 있다.
LinkStatus evaluateHub({
  required DateTime now,
  required LinkStatus mqtt,
  String? healthStatus,
  DateTime? healthAt,
  DateTime? lastStateAt,
  LinkRules rules = const LinkRules(),
}) {
  if (mqtt.cause == LinkCause.demo) {
    return const LinkStatus(
        id: LinkId.hub, health: LinkHealth.unconfigured, cause: LinkCause.demo);
  }
  if (healthStatus == 'offline') {
    return LinkStatus(
        id: LinkId.hub,
        health: LinkHealth.down,
        cause: LinkCause.hubOffline,
        lastSeen: healthAt);
  }
  final stateFresh = lastStateAt != null &&
      now.difference(lastStateAt) <= rules.stateStaleAfter;
  if (stateFresh) {
    return LinkStatus(
        id: LinkId.hub, health: LinkHealth.ok, lastSeen: lastStateAt);
  }
  // 브로커에 못 붙었으면 hub 는 알 수 없다. 원인은 MQTT 줄이 이미 말한다.
  if (mqtt.health != LinkHealth.ok &&
      mqtt.cause != LinkCause.noState &&
      mqtt.cause != LinkCause.stale) {
    return const LinkStatus(id: LinkId.hub, health: LinkHealth.checking);
  }
  if (healthStatus == 'online') {
    return LinkStatus(
        id: LinkId.hub,
        health: LinkHealth.degraded,
        cause: LinkCause.noState,
        lastSeen: healthAt);
  }
  return LinkStatus(
      id: LinkId.hub,
      health: LinkHealth.degraded,
      cause: lastStateAt == null ? LinkCause.noState : LinkCause.stale,
      lastSeen: lastStateAt);
}

/// hub 가 `sensor_summary` 에 실어 보내는 센서(mmWave·환경). hub 는 신선한 값만
/// 싣는다. 그래서 상태는 신선한데 그 항목이 빠져 있으면 센서 쪽이 끊긴 것이다.
/// 상태 자체가 오래됐으면 센서는 판단하지 않는다(원인은 MQTT/hub 줄에 있다).
LinkStatus evaluateSummarySensor({
  required LinkId id,
  required DateTime now,
  required bool summaryHasSensor,
  DateTime? lastStateAt,
  DateTime? lastSensorAt,
  LinkRules rules = const LinkRules(),
}) {
  assert(id == LinkId.mmwave || id == LinkId.environment);
  final stateFresh = lastStateAt != null &&
      now.difference(lastStateAt) <= rules.stateStaleAfter;
  if (!stateFresh) {
    return LinkStatus(
        id: id, health: LinkHealth.checking, lastSeen: lastSensorAt);
  }
  if (summaryHasSensor) {
    return LinkStatus(id: id, health: LinkHealth.ok, lastSeen: lastStateAt);
  }
  return LinkStatus(
      id: id,
      health: LinkHealth.down,
      cause: LinkCause.stale,
      lastSeen: lastSensorAt);
}

/// 키스트로크는 collector 가 자체 `ts` 를 싣는다(mqtt-topics.md). 그 시각으로 본다.
/// 보드에 꽂힌 키보드를 앱이 직접 잡는 경우도 있어서, 값이 한 번도 없으면
/// 문제가 아니라 미사용으로 본다.
LinkStatus evaluateKeystroke({
  required DateTime now,
  DateTime? sampleAt,
  LinkRules rules = const LinkRules(),
}) {
  if (sampleAt == null) {
    return const LinkStatus(
        id: LinkId.keystroke,
        health: LinkHealth.unconfigured,
        cause: LinkCause.notConfigured);
  }
  if (now.difference(sampleAt) <= rules.keystrokeStaleAfter) {
    return LinkStatus(
        id: LinkId.keystroke, health: LinkHealth.ok, lastSeen: sampleAt);
  }
  return LinkStatus(
      id: LinkId.keystroke,
      health: LinkHealth.degraded,
      cause: LinkCause.stale,
      lastSeen: sampleAt);
}

/// 한 시점의 모든 연결 상태. 화면은 이것만 받는다.
class LinkSnapshot {
  LinkSnapshot(Iterable<LinkStatus> statuses)
      : _byId = {for (final s in statuses) s.id: s};

  final Map<LinkId, LinkStatus> _byId;

  /// 연결 탭 순서([LinkId] 선언 순)로.
  List<LinkStatus> get all => [
        for (final id in LinkId.values)
          if (_byId[id] != null) _byId[id]!
      ];

  LinkStatus? operator [](LinkId id) => _byId[id];

  /// 손을 써야 하는 것만, 심각한 것(down) 먼저, 같으면 연결 탭 순서.
  List<LinkStatus> get attention {
    final list = all.where((s) => s.needsAttention).toList();
    list.sort((a, b) {
      final severity = _severity(b.health).compareTo(_severity(a.health));
      return severity != 0 ? severity : a.id.index.compareTo(b.id.index);
    });
    return list;
  }

  /// 경고 배지를 띄울지.
  bool get hasProblem => attention.isNotEmpty;

  /// 배지·상태 줄에 쓸 한 줄. 문제가 없으면 null.
  String? get headline {
    final list = attention;
    if (list.isEmpty) return null;
    final first = list.first;
    final rest = list.length - 1;
    return rest == 0 ? '${first.label} 확인 필요' : '${first.label} 외 $rest건 확인 필요';
  }

  /// 지금 문제들의 "모양". 상단 상태 줄을 닫을 때 이 값을 기억해 두고, 값이
  /// 달라졌을 때만 다시 띄운다 — 같은 문제로 계속 다시 뜨면 닫는 의미가 없고,
  /// 새 문제를 숨기면 닫은 게 위험해진다. lastSeen 처럼 매초 바뀌는 값은 뺀다.
  String get problemSignature => attention
      .map((s) => '${s.id.name}:${s.health.name}:${s.cause.name}')
      .join('|');

  static int _severity(LinkHealth health) => switch (health) {
        LinkHealth.down => 2,
        LinkHealth.degraded => 1,
        _ => 0,
      };
}

/// 상단 상태 줄을 닫았는지 기억한다. 닫은 뒤 문제 모양이 바뀌면 다시 보인다.
class StatusBarDismissal {
  String? _dismissedSignature;

  /// 지금 스냅샷으로 상태 줄을 보여야 하는가.
  bool shouldShow(LinkSnapshot snapshot) {
    if (!snapshot.hasProblem) {
      // 문제가 사라지면 기억도 지운다 — 같은 문제가 나중에 다시 생기면 다시 알려야 한다.
      _dismissedSignature = null;
      return false;
    }
    return snapshot.problemSignature != _dismissedSignature;
  }

  /// 사람이 상태 줄을 눌러 닫았다.
  void dismiss(LinkSnapshot snapshot) {
    _dismissedSignature = snapshot.problemSignature;
  }
}
