import 'package:flutter/material.dart';
import 'control_state.dart';
import 'display_state.dart';
import 'deskmate_theme.dart';
import 'dashboard_view.dart' show SoftPanel;

String adviceText(Map<String, dynamic> advice) =>
    advice['kind'] == 'manual_ventilation'
        ? 'CO₂ · 환기장치 없음: 창문을 열어 환기해 주세요. 환기를 위한 가전 명령은 없습니다.'
        : '${advice['sensor']} · 사용할 수 있는 가전 없음: 환경을 직접 조정해 주세요.';

class EnvironmentControlSuggestion extends StatelessWidget {
  const EnvironmentControlSuggestion(
      {super.key,
      required this.state,
      required this.proposal,
      required this.connected,
      required this.onFeedback});
  final DisplayState state;
  final ControlProposal? proposal;
  final bool connected;
  final ValueChanged<String> onFeedback;
  @override
  Widget build(BuildContext context) {
    final plan = proposal?.plan ?? state.control;
    final ready = connected &&
        proposal != null &&
        state.fsmState == 'ACTION_ENV' &&
        state.gate == 'suggest' &&
        proposal!.matches(
            current: state.control,
            hubBootId: state.hubBootId,
            stateTime: state.timestamp,
            now: DateTime.now());
    final theme = Theme.of(context).textTheme;
    return Center(
        child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 600),
            child: SoftPanel(
                raised: true,
                padding: const EdgeInsets.all(24),
                child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(plan?.label ?? '서버 제어 계획 확인 필요',
                          style: theme.labelMedium),
                      const SizedBox(height: 8),
                      Text(
                          plan?.ventilationOnly == true
                              ? '직접 환기해 주세요'
                              : plan?.adviceOnly == true
                                  ? '환경 조정 안내'
                                  : '서버가 제안한 환경 제어',
                          style: theme.headlineSmall),
                      const SizedBox(height: 12),
                      Flexible(
                          child: SingleChildScrollView(
                              child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  children: [
                            for (final command in plan?.commands ??
                                <ControlCommandState>[]) ...[
                              Text(command.description,
                                  key: ValueKey('plan-${command.id}'),
                                  style: theme.titleMedium),
                              Text(
                                  '원인: ${command.reason.isEmpty ? plan!.cause : command.reason}',
                                  style: theme.bodyMedium),
                              const SizedBox(height: 8),
                            ],
                            for (final advice
                                in plan?.advice ?? <Map<String, dynamic>>[])
                              Padding(
                                  padding: const EdgeInsets.only(bottom: 8),
                                  child: Text(adviceText(advice))),
                          ]))),
                      const SizedBox(height: 8),
                      Text(
                          ready
                              ? '표시된 계획만 수락합니다. 수락은 실행 완료가 아닙니다.'
                              : '만료·연결·서버 계획 불일치 또는 계획 누락: 실행 수락이 차단되었습니다.',
                          key: const ValueKey('control-plan-validation'),
                          style: theme.bodySmall),
                      const SizedBox(height: 12),
                      Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                        OutlinedButton(
                            onPressed: proposal == null
                                ? null
                                : () => onFeedback('reject'),
                            child: const Text('괜찮아요')),
                        const SizedBox(width: 10),
                        FilledButton(
                            key: const ValueKey('control-accept'),
                            onPressed:
                                ready ? () => onFeedback('accept') : null,
                            child: Text(plan?.adviceOnly == true
                                ? '안내 확인'
                                : '이 계획 수락')),
                      ]),
                    ]))));
  }
}

/// A status strip remains visible in MONITOR; no success animation is inferred
/// from FSM transitions, publication, HTTP acceptance, or Mock state.
class EnvironmentControlResult extends StatelessWidget {
  const EnvironmentControlResult(
      {super.key, required this.control, this.onUndo});
  final ControlState control;
  final VoidCallback? onUndo;
  @override
  Widget build(BuildContext context) => ConstrainedBox(
      constraints: const BoxConstraints(maxHeight: 88),
      child: Container(
          key: const ValueKey('environment-control-result'),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
              color: DeskmateColors.surfaceRaised,
              borderRadius: BorderRadius.circular(12)),
          child: SingleChildScrollView(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                Text('${control.label} · ${control.outcomeLabel}'),
                for (final command in control.commands)
                  Text(
                      '${command.description} · ${control.isMock ? "모의 결과: " : ""}${command.statusLabel}'
                      '${command.error == null ? "" : " · ${command.error}"}'),
                if (onUndo != null &&
                    control.commands.any((command) =>
                        ['executing', 'succeeded'].contains(command.status)))
                  Align(
                      alignment: Alignment.centerRight,
                      child: TextButton(
                          key: const ValueKey('environment-control-undo'),
                          onPressed: onUndo,
                          child: const Text('되돌리기 요청'))),
                for (final advice in control.advice) Text(adviceText(advice)),
              ]))));
}
