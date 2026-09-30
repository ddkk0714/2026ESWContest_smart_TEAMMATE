/// 앱 시작 때의 MQTT 점검과, 실패했을 때 보여 주는 연결 가이드.
///
/// 지금까지는 브로커에 못 붙으면 "MQTT 재연결 중" 만 떠서, 현장에서 망이 문제인지
/// 브로커가 꺼졌는지 hub 가 죽었는지 알 수 없었다. 여기서 진단 ①~④ 중 어디서
/// 막혔는지 보여 주고, 사람이 할 수 있는 조치(다시 시도·주소 변경·데모로 계속)를
/// 같은 자리에 둔다. **앱은 막지 않는다** — 가이드는 상태를 못 받았을 때 대기
/// 화면 자리에만 뜨고, 상태가 오면 곧바로 평소 화면으로 넘어간다.
///
/// 계획: docs/plan/app-connection-robustness-plan.md §5.
library;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'link_status.dart';
import 'mqtt_probe.dart';

/// 진단기 모양. 실제로는 [probeMqtt], 테스트에서는 가짜를 넣는다.
typedef MqttProber = Future<MqttProbeResult> Function(String host, int port,
    {MqttProbeProgress? onStep});

/// 점검 한 번의 진행과 결과. 화면은 이것만 보고 그린다.
class MqttStartupCheck extends ChangeNotifier {
  MqttStartupCheck({MqttProber? prober}) : _prober = prober ?? probeMqtt;

  final MqttProber _prober;
  int _generation = 0;

  String? _host;
  int _port = 1883;
  MqttProbeStep? _running;
  final Set<MqttProbeStep> _passed = {};
  MqttProbeResult? _result;

  String? get host => _host;
  int get port => _port;

  /// 지금 진행 중인 단계. 끝났거나 시작 전이면 null.
  MqttProbeStep? get running => _running;

  MqttProbeResult? get result => _result;
  bool get isRunning => _host != null && _result == null;
  bool get failed => _result != null && !_result!.ok;

  bool passed(MqttProbeStep step) => _passed.contains(step);

  /// 점검을 새로 돌린다. 앞선 점검이 늦게 끝나도 결과를 덮지 않는다 — 주소를
  /// 바꾼 뒤에 옛 주소의 실패가 도착해 가이드를 다시 띄우면 안 된다.
  Future<MqttProbeResult> run(String host, int port) async {
    final generation = ++_generation;
    _host = host;
    _port = port;
    _running = null;
    _passed.clear();
    _result = null;
    notifyListeners();
    final result = await _prober(host, port, onStep: (step) {
      if (generation != _generation) return;
      if (_running != null) _passed.add(_running!);
      _running = step;
      notifyListeners();
    });
    if (generation == _generation) {
      if (result.ok) {
        _passed.addAll(MqttProbeStep.values);
      } else if (_running != null && _running != result.failedAt) {
        _passed.add(_running!);
      }
      _running = null;
      _result = result;
      notifyListeners();
    }
    return result;
  }

  /// 점검을 버린다(데모로 전환 등). 진행 중인 점검의 결과는 무시된다.
  void reset() {
    _generation++;
    _host = null;
    _running = null;
    _passed.clear();
    _result = null;
    notifyListeners();
  }
}

String probeStepLabel(MqttProbeStep step) => switch (step) {
      MqttProbeStep.tcp => '네트워크 도달',
      MqttProbeStep.connack => '브로커 응답',
      MqttProbeStep.hubHealth => 'hub 생존',
      MqttProbeStep.firstState => '상태 수신',
    };

/// 실패 원인에 맞는 안내. hub 쪽 원인은 hub 문구를, 나머지는 MQTT 문구를 쓴다.
String probeGuidance(MqttProbeResult result) {
  final id = switch (result.cause) {
    LinkCause.hubOffline || LinkCause.noState => LinkId.hub,
    _ => LinkId.mqtt,
  };
  return linkGuidance(id, result.cause);
}

/// 대기 화면 자리에 들어가는 점검 카드.
class ConnectionGuidePanel extends StatelessWidget {
  const ConnectionGuidePanel({
    super.key,
    required this.check,
    required this.onRetry,
    required this.onChangeAddress,
    required this.onDemo,
  });

  final MqttStartupCheck check;
  final VoidCallback onRetry;
  final VoidCallback onChangeAddress;
  final VoidCallback onDemo;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: check,
        builder: (context, _) {
          final result = check.result;
          final theme = Theme.of(context);
          return ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('Pi 4 연결 점검', style: theme.textTheme.titleLarge),
                    const SizedBox(height: 4),
                    Text(
                      'MQTT ${check.host ?? '-'}:${check.port}',
                      key: const ValueKey('guide-address'),
                      style: const TextStyle(color: DeskmateColors.inkMuted),
                    ),
                    const SizedBox(height: 16),
                    for (final step in MqttProbeStep.values)
                      _StepRow(
                        step: step,
                        state: _stepState(step, result),
                      ),
                    if (check.failed) ...[
                      const SizedBox(height: 16),
                      Text(
                        probeGuidance(result!),
                        key: const ValueKey('guide-message'),
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                      if (result.detail != null) ...[
                        const SizedBox(height: 6),
                        Text(
                          result.detail!,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: DeskmateColors.inkMuted),
                        ),
                      ],
                    ],
                    const SizedBox(height: 20),
                    Wrap(
                      alignment: WrapAlignment.end,
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        TextButton(
                          key: const ValueKey('guide-demo'),
                          onPressed: onDemo,
                          child: const Text('데모로 계속'),
                        ),
                        OutlinedButton(
                          key: const ValueKey('guide-change-address'),
                          onPressed: onChangeAddress,
                          child: const Text('주소 변경'),
                        ),
                        FilledButton(
                          key: const ValueKey('guide-retry'),
                          onPressed: check.isRunning ? null : onRetry,
                          child: const Text('다시 시도'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      );

  _StepState _stepState(MqttProbeStep step, MqttProbeResult? result) {
    if (check.passed(step)) return _StepState.passed;
    if (check.running == step) return _StepState.running;
    if (result != null && !result.ok && result.failedAt == step) {
      return _StepState.failed;
    }
    return _StepState.pending;
  }
}

enum _StepState { pending, running, passed, failed }

class _StepRow extends StatelessWidget {
  const _StepRow({required this.step, required this.state});

  final MqttProbeStep step;
  final _StepState state;

  @override
  Widget build(BuildContext context) {
    final Widget icon = switch (state) {
      _StepState.running => const SizedBox.square(
          dimension: 18, child: CircularProgressIndicator(strokeWidth: 2)),
      _StepState.passed => const Icon(Icons.check_circle_rounded,
          size: 20, color: DeskmateColors.accentStrong),
      _StepState.failed => const Icon(Icons.cancel_rounded,
          size: 20, color: DeskmateColors.offline),
      _StepState.pending => const Icon(Icons.radio_button_unchecked_rounded,
          size: 20, color: DeskmateColors.inkFaint),
    };
    return Padding(
      key: ValueKey('guide-step-${step.name}-${state.name}'),
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        icon,
        const SizedBox(width: 12),
        Text('${step.index + 1}. ${probeStepLabel(step)}'),
      ]),
    );
  }
}
