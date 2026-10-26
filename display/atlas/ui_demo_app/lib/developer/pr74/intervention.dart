/// 개입 화면의 판단: 실행 이유 문구, 자동 실행 알림·제안 카드의 시간 관리, 정정 입력.
///
/// 멘토 피드백(09-22)대로 평소에는 위젯 화면을 두고, 개입이 필요할 때만 카드를 띄운다.
/// - 확신도 0.45~0.75(gate suggest): 제안 카드 — 수락·거절, 만료되면 무응답(timeout)
/// - 0.75 이상(gate auto): 실행 후 알림 — 왜 했는지와 되돌리기
/// - 언제든: "지금 상태가 아니에요" 정정(verdict correct) — 라벨로만 기록된다(W3)
///
/// 사람 응답(수락·거절·정정·무응답과 응답 시간)은 2단계 학습과 정량 평가의 라벨이므로,
/// 응답 시간은 카드를 띄운 시각부터 잰다.
///
/// 계획: docs/plan/next-development-plan.md §4 W1.
library;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';

// ---------------------------------------------------------------------------
// 문구
// ---------------------------------------------------------------------------

/// 자동 실행 뒤 알림 제목.
String autoActionTitle(DisplayState state) => switch (state.fsmState) {
      'ACTION_ENV' => '환경을 조정했어요',
      'ACTION_BREAK' => '휴식을 권하는 알림을 켰어요',
      'ACTION_POSTURE' => '자세를 바꿀 때라고 알렸어요',
      _ => '상태에 맞춰 조정했어요',
    };

/// 왜 개입하는지 한 줄. hub 의 `reasons` 는 내부 동작 코드라 쓰지 않고, 원인(cause)과
/// 환경 플래그(env_flags)·센서 값으로 사람이 읽을 문장을 만든다.
String interventionReason(DisplayState state) {
  switch (state.cause) {
    case 'environment':
      final parts = [
        for (final flag in state.envFlags)
          if (_envPhrase(flag, state) case final phrase?) phrase,
      ];
      if (parts.isNotEmpty) return parts.join(' · ');
      final co2 = state.co2Ppm;
      return co2 != null
          ? 'CO₂ $co2 ppm — 환경이 쾌적 범위를 벗어났어요'
          : '환경이 쾌적 범위를 벗어났어요';
    case 'cognitive':
      return '작업 시간이 길어지고 입력 리듬이 느려졌어요';
    case 'posture':
      return '움직임이 줄고 졸음 신호가 보여요';
    default:
      return state.scenario ?? '지금 상태를 보고 판단했어요';
  }
}

String? _envPhrase(String flag, DisplayState state) => switch (flag) {
      'co2_high' =>
        state.co2Ppm != null ? 'CO₂가 높아요 (${state.co2Ppm} ppm)' : 'CO₂가 높아요',
      'co2_rising' => 'CO₂가 빠르게 오르고 있어요',
      'too_hot' => state.temperatureC != null
          ? '방이 더워요 (${state.temperatureC!.toStringAsFixed(1)}°C)'
          : '방이 더워요',
      'too_cold' => state.temperatureC != null
          ? '방이 추워요 (${state.temperatureC!.toStringAsFixed(1)}°C)'
          : '방이 추워요',
      'too_humid' => state.humidityPct != null
          ? '습도가 높아요 (${state.humidityPct!.round()}%)'
          : '습도가 높아요',
      'too_dry' => state.humidityPct != null
          ? '공기가 건조해요 (${state.humidityPct!.round()}%)'
          : '공기가 건조해요',
      'too_dark' =>
        state.lux != null ? '조명이 어두워요 (${state.lux} lx)' : '조명이 어두워요',
      _ => null,
    };

String confidenceLabel(DisplayState state) =>
    '확신도 ${(state.confidence.clamp(0, 1) * 100).round()}%';

/// 정정 입력에서 고를 수 있는 국면(FSM 상태 이름, 화면 문구).
const correctionOptions = <(String, String)>[
  ('FOCUS_PC', '집중하고 있어요'),
  ('FATIGUE', '피곤해요'),
  ('REST', '쉬는 중이에요'),
  ('IDLE', '자리를 비웠어요'),
];

// ---------------------------------------------------------------------------
// 시간 관리
// ---------------------------------------------------------------------------

/// 자동 실행 알림 한 건.
class AutoNotice {
  AutoNotice._(this.key, this.title, this.reason, this.startedAt);

  final String key;
  final String title;
  final String reason;
  final DateTime startedAt;
  DateTime? _leftAt;
  bool _dismissed = false;
}

class InterventionTracker {
  InterventionTracker({
    this.autoNoticeHold = const Duration(seconds: 30),
    this.defaultExpiry = const Duration(seconds: 60),
  });

  /// 자동 실행 상태(ACTION_*)가 끝난 뒤에도 알림을 남겨 두는 시간. hub 는 다음 tick 에
  /// MONITOR 로 넘어가므로, 이게 없으면 사람이 읽기도 전에 알림이 사라진다.
  final Duration autoNoticeHold;

  /// hub 가 `expires_in_s` 를 주지 않았을 때의 제안 응답 시간.
  final Duration defaultExpiry;

  AutoNotice? _auto;
  DateTime? _shownAt;
  DateTime? _expiresAt;
  bool _timeoutSent = false;

  /// 화면에 띄울 자동 실행 알림. 없거나 사람이 확인했으면 null.
  AutoNotice? get autoNotice =>
      _auto != null && !_auto!._dismissed ? _auto : null;

  /// 새 상태를 받을 때마다 부른다.
  void update(
    DisplayState state, {
    required bool hasPendingRequest,
    int? expiresInS,
    required DateTime now,
  }) {
    final inAuto = state.fsmState.startsWith('ACTION_') && state.gate == 'auto';
    final current = _auto;
    if (inAuto) {
      final key = '${state.fsmState}:${state.cause}';
      // 같은 건이 이어지는 동안에는 새로 띄우지 않는다(확인한 알림이 다시 뜨지 않게).
      // 끝났다가 다시 들어오면 새 건이다.
      if (current == null || current.key != key || current._leftAt != null) {
        _auto = AutoNotice._(
            key, autoActionTitle(state), interventionReason(state), now);
      }
    } else if (current != null) {
      current._leftAt ??= now;
      if (now.difference(current._leftAt!) > autoNoticeHold) _auto = null;
    }

    if (hasPendingRequest) {
      if (_shownAt == null) {
        _shownAt = now;
        _expiresAt = now.add(
            expiresInS != null ? Duration(seconds: expiresInS) : defaultExpiry);
        _timeoutSent = false;
      }
    } else {
      _shownAt = null;
      _expiresAt = null;
    }
  }

  /// 사람이 알림을 확인했다(또는 되돌리기를 눌렀다).
  void dismissAuto() => _auto?._dismissed = true;

  /// 제안 카드를 띄운 뒤 지금까지의 시간. 카드가 없으면 null.
  int? responseMs(DateTime now) =>
      _shownAt == null ? null : now.difference(_shownAt!).inMilliseconds;

  /// 제안 카드의 남은 시간(0 이상). 카드가 없으면 null.
  Duration? remaining(DateTime now) {
    final expires = _expiresAt;
    if (expires == null) return null;
    final left = expires.difference(now);
    return left.isNegative ? Duration.zero : left;
  }

  /// 만료됐으면 true 를 **한 번만** 돌려준다. 호출부가 timeout 을 보낸다.
  bool takeTimeout(DateTime now) {
    final expires = _expiresAt;
    if (expires == null || _timeoutSent || now.isBefore(expires)) return false;
    _timeoutSent = true;
    return true;
  }
}

// ---------------------------------------------------------------------------
// 화면 조각
// ---------------------------------------------------------------------------

/// 자동 실행 뒤 알림. 왜 했는지와 되돌리기.
class AutoActionView extends StatelessWidget {
  const AutoActionView({
    super.key,
    required this.notice,
    required this.state,
    required this.onUndo,
    required this.onDismiss,
  });

  final AutoNotice notice;
  final DisplayState state;
  final VoidCallback onUndo;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 460),
        child: Container(
          key: const ValueKey('auto-action-card'),
          padding: const EdgeInsets.all(30),
          decoration: BoxDecoration(
            color: DeskmateColors.surfaceRaised,
            borderRadius: BorderRadius.circular(DeskmateRadius.panel),
            border: Border.all(color: DeskmateColors.line),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                const Icon(Icons.auto_mode_rounded,
                    size: 18, color: DeskmateColors.accentStrong),
                const SizedBox(width: 8),
                Text('자동으로 실행했어요', style: theme.textTheme.labelMedium),
                const Spacer(),
                Text(confidenceLabel(state),
                    style: theme.textTheme.labelMedium),
              ]),
              const SizedBox(height: 14),
              Text(notice.title, style: theme.textTheme.headlineMedium),
              const SizedBox(height: 12),
              Text('이유: ${notice.reason}',
                  key: const ValueKey('auto-action-reason'),
                  style: theme.textTheme.bodyLarge),
              const SizedBox(height: 18),
              Text('원하지 않으면 되돌릴 수 있어요.', style: theme.textTheme.labelMedium),
              const SizedBox(height: 14),
              Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                OutlinedButton(
                  key: const ValueKey('auto-undo'),
                  onPressed: onUndo,
                  child: const Text('되돌리기'),
                ),
                const SizedBox(width: 10),
                FilledButton(
                  key: const ValueKey('auto-dismiss'),
                  onPressed: onDismiss,
                  child: const Text('확인'),
                ),
              ]),
            ],
          ),
        ),
      ),
    );
  }
}

/// "지금 상태가 아니에요" — 네 국면 중 하나를 고른다. 취소하면 null.
Future<String?> showCorrectionSheet(BuildContext context) =>
    showModalBottomSheet<String>(
      context: context,
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('지금 상태를 알려 주세요',
                  style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 4),
              const Text('판정은 바꾸지 않고 기록만 해요. 다음 판단을 다듬는 데 써요.'),
              const SizedBox(height: 14),
              Wrap(spacing: 10, runSpacing: 10, children: [
                for (final (state, label) in correctionOptions)
                  OutlinedButton(
                    key: ValueKey('correct-$state'),
                    onPressed: () => Navigator.pop(context, state),
                    child: Text(label),
                  ),
              ]),
            ],
          ),
        ),
      ),
    );
