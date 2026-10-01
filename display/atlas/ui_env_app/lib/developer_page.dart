/// 개발자 화면 — 앱의 모든 화면을 버튼으로 바로 띄워 보고, 오른쪽 미리보기로 즉시 확인한다.
///
/// 실제 FSM 을 돌리는 게 아니라 **각 상태에서 화면이 어떻게 보이는지**를 보는 도구다.
/// 누르는 동안 라이브 상태는 화면에 반영되지 않고(상단에 '테스트 상태 고정'), 여기서 응답 버튼을
/// 눌러도 hub 로 보내지 않는다 — 실제 ESM 라벨이 섞이지 않게(main.dart 가 막는다).
///
/// 예전 '센서 테스트'(Pi 4 HTTP 8765 로 테스트 프레임을 보내 FSM 을 돌리던 화면)는 hub 가 live 모드일 때
/// 테스트 프레임을 쓰지 않아 동작하지 않았고, 연결하면 MQTT 소스를 HTTP 로 갈아 끼워 실제 연결을 끊었다.
/// FSM 동작 확인은 PC 의 tools/demo_dryrun.py · rehearsal_local.py 가 맡는다(10-01 정리).
library;

import 'package:flutter/material.dart';

import 'display_state.dart';

/// 버튼 하나 = 화면 하나. pending 이면 hub 가 질문을 보낸 것처럼 제안 카드를 띄운다.
class DevPreset {
  const DevPreset(this.key, this.label, this.fsmState,
      {this.gate = 'none', this.pending = false, this.icon});

  final String key;
  final String label;
  final String fsmState;
  final String gate;
  final bool pending;
  final IconData? icon;
}

const devPhaseScreens = <DevPreset>[
  DevPreset('idle', '대기 위젯', 'IDLE', icon: Icons.wb_twilight_rounded),
  DevPreset('start', '기준선 측정', 'START', icon: Icons.tune_rounded),
  DevPreset('focus', '몰입', 'FOCUS_PC', icon: Icons.center_focus_strong),
  DevPreset('suspect', '피로 의심', 'FATIGUE_SUSPECT',
      icon: Icons.warning_amber_rounded),
  DevPreset('rest', '휴식', 'REST', icon: Icons.self_improvement),
  DevPreset('recovery', '회복', 'RECOVERY', icon: Icons.healing_rounded),
  DevPreset('end', '종료 리포트', 'END', icon: Icons.summarize_outlined),
];

const devAutoNotices = <DevPreset>[
  DevPreset('auto-env', '환경 조정', 'ACTION_ENV',
      gate: 'auto', icon: Icons.air_rounded),
  DevPreset('auto-posture', '자세 알림', 'ACTION_POSTURE',
      gate: 'auto', icon: Icons.accessibility_new_rounded),
  DevPreset('auto-break', '휴식 알림', 'ACTION_BREAK',
      gate: 'auto', icon: Icons.free_breakfast_outlined),
];

const devSuggestions = <DevPreset>[
  DevPreset('suggest-env', '환경 제안', 'ACTION_ENV',
      gate: 'suggest', pending: true, icon: Icons.air_rounded),
  DevPreset('suggest-posture', '자세 제안', 'ACTION_POSTURE',
      gate: 'suggest', pending: true, icon: Icons.accessibility_new_rounded),
  DevPreset('suggest-break', '휴식 제안', 'ACTION_BREAK',
      gate: 'suggest', pending: true, icon: Icons.free_breakfast_outlined),
];

/// 환경 이유 문구에 쓰는 플래그와 그 문구가 그럴듯해지는 대표값.
const devEnvFlags = <String, String>{
  'co2_high': 'CO₂ 높음',
  'co2_rising': 'CO₂ 상승',
  'too_hot': '더움',
  'too_cold': '추움',
  'too_humid': '습함',
  'too_dry': '건조',
  'too_dark': '어두움',
};

class DeveloperPage extends StatelessWidget {
  const DeveloperPage({
    super.key,
    required this.state,
    required this.overridden,
    required this.activePreset,
    required this.envFlags,
    required this.onPreset,
    required this.onEnvFlags,
    required this.onClear,
    required this.preview,
  });

  /// 지금 미리보기에 그려지는 상태(고정 중이면 고정 상태).
  final DisplayState state;
  final bool overridden;

  /// 마지막으로 누른 버튼의 key. 18상태 칩은 'fsm-' 뒤에 상태 이름.
  final String? activePreset;
  final Set<String> envFlags;
  final ValueChanged<DevPreset> onPreset;
  final ValueChanged<Set<String>> onEnvFlags;
  final VoidCallback onClear;

  /// main 이 실제 대시보드와 같은 조립으로 만든 미리보기.
  final Widget preview;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(
        width: 360,
        child: _Panel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                const Icon(Icons.developer_mode, size: 22),
                const SizedBox(width: 8),
                Text('개발자 화면',
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w800)),
                const Spacer(),
                if (overridden)
                  TextButton.icon(
                    key: const ValueKey('dev-clear'),
                    onPressed: onClear,
                    icon: const Icon(Icons.play_arrow_rounded, size: 18),
                    label: const Text('라이브로'),
                  ),
              ]),
              Text(
                overridden
                    ? '테스트 상태로 고정 중 · 응답은 hub 로 보내지 않습니다'
                    : '버튼을 누르면 오른쪽 미리보기와 상태 탭이 그 화면이 됩니다',
                style: theme.textTheme.labelMedium,
              ),
              const SizedBox(height: 10),
              Expanded(
                child: ListView(children: [
                  _Section('국면 화면', [
                    for (final p in devPhaseScreens) _presetButton(p),
                  ]),
                  _Section('자동 실행 알림 (확신도 ≥ 0.75)', [
                    for (final p in devAutoNotices) _presetButton(p),
                  ]),
                  _Section('제안 카드 (0.45~0.75, 30초)', [
                    for (final p in devSuggestions) _presetButton(p),
                  ]),
                  _Section('환경 이유 (자동 알림·제안·대기 화면에 반영)', [
                    for (final e in devEnvFlags.entries)
                      FilterChip(
                        key: ValueKey('dev-flag-${e.key}'),
                        label: Text(e.value),
                        selected: envFlags.contains(e.key),
                        onSelected: (on) => onEnvFlags(on
                            ? ({...envFlags, e.key})
                            : (envFlags.difference({e.key}))),
                      ),
                  ]),
                  _Section('FSM 18상태', [
                    for (final s in kFsmPhaseByState.keys)
                      ChoiceChip(
                        key: ValueKey('override-$s'),
                        label: Text(s, style: const TextStyle(fontSize: 11)),
                        visualDensity: VisualDensity.compact,
                        selected: overridden && activePreset == 'fsm-$s',
                        onSelected: (_) => onPreset(DevPreset('fsm-$s', s, s,
                            gate: _actionStates.contains(s)
                                ? 'suggest'
                                : 'none')),
                      ),
                  ]),
                ]),
              ),
            ],
          ),
        ),
      ),
      const SizedBox(width: 14),
      Expanded(
        child: _Panel(
          padding: const EdgeInsets.all(10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                '미리보기 · ${state.fsmState} · gate ${state.gate}'
                '${state.envFlags.isEmpty ? '' : ' · ${state.envFlags.join(', ')}'}',
                key: const ValueKey('dev-preview-label'),
                style: theme.textTheme.labelMedium,
              ),
              const SizedBox(height: 6),
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(14),
                  child: FittedBox(
                    // 실제 상태 탭 영역 크기로 그린 뒤 줄여서 보여 준다.
                    child: SizedBox(width: 936, height: 470, child: preview),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    ]);
  }

  Widget _presetButton(DevPreset p) {
    final selected = overridden && activePreset == p.key;
    final child = Row(mainAxisSize: MainAxisSize.min, children: [
      if (p.icon != null) ...[Icon(p.icon, size: 16), const SizedBox(width: 6)],
      Text(p.label),
    ]);
    return selected
        ? FilledButton(
            key: ValueKey('dev-${p.key}'),
            onPressed: () => onPreset(p),
            child: child)
        : OutlinedButton(
            key: ValueKey('dev-${p.key}'),
            onPressed: () => onPreset(p),
            child: child);
  }
}

const _actionStates = {'ACTION_ENV', 'ACTION_POSTURE', 'ACTION_BREAK'};

class _Section extends StatelessWidget {
  const _Section(this.title, this.children);
  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title,
              style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: Color(0xFF9DABC2))),
          const SizedBox(height: 6),
          Wrap(spacing: 6, runSpacing: 6, children: children),
        ]),
      );
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child, this.padding = const EdgeInsets.all(16)});
  final Widget child;
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding,
        decoration: BoxDecoration(
          color: const Color(0xFF121D2E),
          borderRadius: BorderRadius.circular(22),
          border: Border.all(color: const Color(0xFF24344C)),
        ),
        child: child,
      );
}

/// FSM 18 상태와 그 국면. 허브의 presentation.py `_PHASE_BY_STATE` 와 같은 표다.
/// 한쪽만 바뀌면 화면이 엉뚱한 국면을 그리므로 값을 그대로 옮겨 적는다.
const kFsmPhaseByState = <String, String>{
  'IDLE': 'idle',
  'START': 'start',
  'CONTEXT_DETECT': 'start',
  'FOCUS_PC': 'focus',
  'FOCUS_MIXED': 'focus',
  'FOCUS_NPC': 'focus',
  'FOCUS_BREAK': 'focus',
  'FATIGUE_SUSPECT': 'fatigue',
  'FATIGUE': 'fatigue',
  'CAUSE_ANALYSIS': 'fatigue',
  'MONITOR': 'fatigue',
  'ESCALATE': 'fatigue',
  'ACTION_ENV': 'fatigue',
  'ACTION_POSTURE': 'fatigue',
  'ACTION_BREAK': 'fatigue',
  'REST': 'recovery',
  'RECOVERY': 'recovery',
  'END': 'end',
};

/// 고른 상태로 화면이 그럴듯하게 보이도록 숫자를 채운 표본을 만든다.
///
/// 실제 FSM 을 돌리는 게 아니라 **화면을 확인하려는** 것이다. 국면에 맞는 집중도·피로도·원인과
/// gate 를 넣고, 환경 플래그를 고르면 그 문구가 그럴듯해지는 값으로 센서값을 바꾼다.
/// 나머지 센서값은 지금 보이는 값을 물려받아 화면이 비어 보이지 않게 한다.
DisplayState buildOverrideState(String fsmState, DisplayState base,
    {String? gate, Set<String> envFlags = const {}}) {
  final phase = kFsmPhaseByState[fsmState] ?? 'idle';
  final fatigue = switch (phase) {
    'fatigue' => gate == 'auto' ? 0.82 : 0.72,
    'recovery' => 0.35,
    _ => 0.08,
  };
  final focus = switch (phase) {
    'focus' => 0.82,
    'start' => 0.4,
    _ => 0.12,
  };
  final cause = switch (fsmState) {
    'ACTION_ENV' => 'environment',
    'ACTION_POSTURE' => 'posture',
    'ACTION_BREAK' => 'cognitive',
    _ => null,
  };
  // 제안 화면은 gate 가 none 이 아닐 때만 뜬다.
  final effectiveGate =
      gate ?? (_actionStates.contains(fsmState) ? 'suggest' : 'none');
  final flags = [
    for (final f in devEnvFlags.keys)
      if (envFlags.contains(f)) f,
  ];
  return DisplayState(
    fsmState: fsmState,
    phase: phase,
    context: base.context,
    focus: focus,
    fatigue: fatigue,
    confidence: focus > fatigue ? focus : fatigue,
    gate: effectiveGate,
    cause: cause,
    reasons: const ['test_override'],
    sequence: base.sequence,
    timestamp: DateTime.now(),
    present: base.present ?? true,
    co2Ppm: envFlags.contains('co2_high') || envFlags.contains('co2_rising')
        ? 1280
        : base.co2Ppm,
    temperatureC: envFlags.contains('too_hot')
        ? 28.6
        : envFlags.contains('too_cold')
            ? 17.5
            : base.temperatureC,
    humidityPct: envFlags.contains('too_humid')
        ? 72
        : envFlags.contains('too_dry')
            ? 22
            : base.humidityPct,
    lux: envFlags.contains('too_dark') ? 80 : base.lux,
    scenario: 'test-override',
    keystroke: base.keystroke,
    mmwaveMotionState: base.mmwaveMotionState,
    mmwaveMotionLevel: base.mmwaveMotionLevel,
    mmwaveDistanceCm: base.mmwaveDistanceCm,
    mmwaveHeartBpm: base.mmwaveHeartBpm,
    envFlags: flags,
  );
}
