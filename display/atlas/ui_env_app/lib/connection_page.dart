/// 연결 탭, 경고 배지, 상단 상태 줄.
///
/// 결정(2026-09-29): 문제가 있을 때만 경고 배지가 뜨고 누르면 이 탭으로 온다.
/// 상단 상태 줄은 누르면 사라지고, 새 문제가 생기면 다시 뜬다. 판단은 전부
/// link_status.dart 의 규칙 하나를 쓴다 — 화면마다 따로 판단하면 어긋난다.
///
/// 계획: docs/plan/app-connection-robustness-plan.md §4 "2. 연결 탭과 리로드".
library;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';
import 'link_status.dart';

/// 앱이 지금 무엇으로 상태를 받는가.
enum StateSourceKind { demo, http, mqtt }

/// 앱의 여러 조각(상태 소스·시작 점검·자세 화면·블루투스)을 스냅샷 하나로 모은다.
/// 순수 함수라 테스트로 조합을 고정할 수 있다.
LinkSnapshot assembleLinkSnapshot({
  required DateTime now,
  required StateSourceKind kind,
  String? mqttAddress,
  bool mqttConnected = false,
  DateTime? mqttConnectedAt,
  DateTime? lastStateAt,
  LinkCause? startupFailure,
  String? parseError,
  String? hubHealth,
  DateTime? hubHealthAt,
  DisplayState? state,
  DateTime? keystrokeAt,
  LinkStatus? posture,
  LinkStatus? speaker,
  LinkStatus? lamp,
  LinkRules rules = const LinkRules(),
}) {
  final list = <LinkStatus>[];
  switch (kind) {
    case StateSourceKind.demo:
      // 데모에서 센서가 "없다" 고 배지를 띄우면 데모 시연 내내 경고가 뜬다.
      for (final id in [
        LinkId.mqtt,
        LinkId.hub,
        LinkId.mmwave,
        LinkId.environment
      ]) {
        list.add(LinkStatus(
            id: id, health: LinkHealth.unconfigured, cause: LinkCause.demo));
      }
    case StateSourceKind.http:
      list.add(const LinkStatus(
          id: LinkId.mqtt,
          health: LinkHealth.unconfigured,
          cause: LinkCause.notConfigured,
          detail: 'HTTP 개발 연결 사용 중'));
      final fresh = lastStateAt != null &&
          now.difference(lastStateAt) <= rules.stateStaleAfter;
      list.add(fresh
          ? LinkStatus(
              id: LinkId.hub, health: LinkHealth.ok, lastSeen: lastStateAt)
          : LinkStatus(
              id: LinkId.hub,
              health:
                  lastStateAt == null ? LinkHealth.checking : LinkHealth.down,
              cause:
                  lastStateAt == null ? LinkCause.none : LinkCause.disconnected,
              lastSeen: lastStateAt));
      list.addAll(_sensors(now, state, lastStateAt, rules));
    case StateSourceKind.mqtt:
      // 시작 점검의 실패 중 연결 자체(①②)만 MQTT 원인으로 쓴다. hub 쪽(③④)은
      // hub 줄이 health·상태 신선도로 따로 판단한다.
      final connectError = startupFailure == LinkCause.networkUnreachable ||
              startupFailure == LinkCause.brokerRefused
          ? startupFailure
          : null;
      final mqtt = evaluateMqtt(
          now: now,
          configured: true,
          connected: mqttConnected,
          connectedAt: mqttConnectedAt,
          lastStateAt: lastStateAt,
          connectError: connectError,
          parseError: parseError,
          detail: mqttAddress,
          rules: rules);
      list.add(mqtt);
      list.add(evaluateHub(
          now: now,
          mqtt: mqtt,
          healthStatus: hubHealth,
          healthAt: hubHealthAt,
          lastStateAt: lastStateAt,
          rules: rules));
      list.addAll(_sensors(now, state, lastStateAt, rules));
  }
  list.add(evaluateKeystroke(now: now, sampleAt: keystrokeAt, rules: rules));
  list.add(posture ??
      const LinkStatus(id: LinkId.posture, health: LinkHealth.checking));
  list.add(speaker ??
      const LinkStatus(
          id: LinkId.speaker,
          health: LinkHealth.unconfigured,
          cause: LinkCause.notConfigured));
  list.add(lamp ??
      const LinkStatus(
          id: LinkId.lamp,
          health: LinkHealth.unconfigured,
          cause: LinkCause.notConfigured));
  return LinkSnapshot(list);
}

List<LinkStatus> _sensors(
    DateTime now, DisplayState? state, DateTime? lastStateAt, LinkRules rules) {
  // hub 는 신선한 센서만 sensor_summary 에 싣는다. 항목이 있으면 살아 있는 것이다.
  final hasMmwave = state != null &&
      (state.present != null ||
          state.mmwaveMotionState != null ||
          state.mmwaveDistanceCm != null);
  final hasEnvironment = state != null &&
      (state.co2Ppm != null ||
          state.temperatureC != null ||
          state.humidityPct != null ||
          state.lux != null);
  return [
    evaluateSummarySensor(
        id: LinkId.mmwave,
        now: now,
        summaryHasSensor: hasMmwave,
        lastStateAt: lastStateAt,
        rules: rules),
    evaluateSummarySensor(
        id: LinkId.environment,
        now: now,
        summaryHasSensor: hasEnvironment,
        lastStateAt: lastStateAt,
        rules: rules),
  ];
}

String linkHealthLabel(LinkStatus status) => switch (status.health) {
      LinkHealth.ok => '정상',
      LinkHealth.checking => '확인 중',
      LinkHealth.degraded => '불안정',
      LinkHealth.down => '끊김',
      LinkHealth.unconfigured => status.cause == LinkCause.demo ? '데모' : '미사용',
    };

Color _healthColor(LinkHealth health) => switch (health) {
      LinkHealth.ok => DeskmateColors.accentStrong,
      LinkHealth.degraded => DeskmateColors.warning,
      LinkHealth.down => DeskmateColors.offline,
      LinkHealth.checking || LinkHealth.unconfigured => DeskmateColors.inkFaint,
    };

String _ago(DateTime now, DateTime? at) {
  if (at == null) return '';
  final seconds = now.difference(at).inSeconds;
  if (seconds < 60) return '$seconds초 전';
  if (seconds < 3600) return '${seconds ~/ 60}분 전';
  return '${seconds ~/ 3600}시간 전';
}

/// 연결 대상마다 누를 수 있는 조치. null 이면 버튼을 그리지 않는다
/// (키스트로크는 PC 에서 고칠 일이라 앱에서 할 수 있는 게 없다).
String? linkActionLabel(LinkId id) => switch (id) {
      LinkId.mqtt ||
      LinkId.hub ||
      LinkId.mmwave ||
      LinkId.environment =>
        '다시 연결',
      LinkId.posture => '다시 찾기',
      LinkId.speaker || LinkId.lamp => '기기 설정',
      LinkId.keystroke => null,
    };

class ConnectionPage extends StatelessWidget {
  const ConnectionPage({
    super.key,
    required this.snapshot,
    required this.now,
    required this.sourceLabel,
    required this.onAction,
    required this.onReloadAll,
    required this.onChangeAddress,
  });

  final LinkSnapshot snapshot;
  final DateTime now;
  final String sourceLabel;
  final ValueChanged<LinkId> onAction;
  final VoidCallback onReloadAll;
  final VoidCallback onChangeAddress;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      children: [
        Row(children: [
          Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('연결 상태', style: theme.textTheme.headlineSmall),
              const SizedBox(height: 4),
              Text(sourceLabel,
                  key: const ValueKey('connection-source'),
                  style: const TextStyle(color: DeskmateColors.inkMuted)),
            ]),
          ),
          OutlinedButton(
            key: const ValueKey('connection-change-address'),
            onPressed: onChangeAddress,
            child: const Text('주소 변경'),
          ),
          const SizedBox(width: 8),
          FilledButton.icon(
            key: const ValueKey('connection-reload-all'),
            onPressed: onReloadAll,
            icon: const Icon(Icons.refresh_rounded),
            label: const Text('전체 리로드'),
          ),
        ]),
        const SizedBox(height: 16),
        for (final status in snapshot.all)
          _LinkRow(
            status: status,
            now: now,
            onAction: linkActionLabel(status.id) == null
                ? null
                : () => onAction(status.id),
          ),
      ],
    );
  }
}

class _LinkRow extends StatelessWidget {
  const _LinkRow({required this.status, required this.now, this.onAction});

  final LinkStatus status;
  final DateTime now;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = _healthColor(status.health);
    final showGuidance = status.needsAttention ||
        status.cause == LinkCause.notConfigured && status.id == LinkId.mqtt;
    final ago = _ago(now, status.lastSeen);
    return Card(
      key: ValueKey('link-row-${status.id.name}'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Icon(Icons.circle, size: 12, color: color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Text(status.label,
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(width: 8),
                Text(linkHealthLabel(status),
                    key: ValueKey('link-health-${status.id.name}'),
                    style:
                        TextStyle(color: color, fontWeight: FontWeight.w600)),
                if (ago.isNotEmpty) ...[
                  const SizedBox(width: 8),
                  Text(ago,
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: DeskmateColors.inkMuted)),
                ],
              ]),
              if (showGuidance) ...[
                const SizedBox(height: 4),
                Text(status.guidance,
                    key: ValueKey('link-guidance-${status.id.name}')),
              ],
              if (status.detail != null && status.needsAttention) ...[
                const SizedBox(height: 2),
                Text(status.detail!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: DeskmateColors.inkMuted)),
              ],
            ]),
          ),
          if (onAction != null)
            TextButton(
              key: ValueKey('link-action-${status.id.name}'),
              onPressed: onAction,
              child: Text(linkActionLabel(status.id)!),
            ),
        ]),
      ),
    );
  }
}

/// 문제가 있을 때만 보이는 경고 배지. 누르면 연결 탭으로 간다.
class LinkProblemBadge extends StatelessWidget {
  const LinkProblemBadge(
      {super.key, required this.snapshot, required this.onTap});

  final LinkSnapshot snapshot;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final problems = snapshot.attention;
    if (problems.isEmpty) return const SizedBox.shrink();
    return IconButton(
      key: const ValueKey('link-problem-badge'),
      tooltip: snapshot.headline,
      onPressed: onTap,
      icon: Badge(
        label: Text('${problems.length}'),
        backgroundColor: problems.first.health == LinkHealth.down
            ? DeskmateColors.offline
            : DeskmateColors.warning,
        child: const Icon(Icons.warning_amber_rounded),
      ),
    );
  }
}

/// 상단 상태 줄. 누르면 사라진다([StatusBarDismissal] 이 기억한다).
class LinkStatusBar extends StatelessWidget {
  const LinkStatusBar(
      {super.key, required this.snapshot, required this.onDismiss});

  final LinkSnapshot snapshot;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final problems = snapshot.attention;
    if (problems.isEmpty) return const SizedBox.shrink();
    final first = problems.first;
    final color = first.health == LinkHealth.down
        ? DeskmateColors.offline
        : DeskmateColors.warning;
    return Material(
      color: color.withValues(alpha: 0.14),
      borderRadius: BorderRadius.circular(DeskmateRadius.control),
      child: InkWell(
        key: const ValueKey('link-status-bar'),
        borderRadius: BorderRadius.circular(DeskmateRadius.control),
        onTap: onDismiss,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: Row(children: [
            Icon(Icons.warning_amber_rounded, size: 18, color: color),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                '${snapshot.headline} · ${first.guidance}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: 8),
            const Icon(Icons.close_rounded,
                size: 16, color: DeskmateColors.inkMuted),
          ]),
        ),
      ),
    );
  }
}
