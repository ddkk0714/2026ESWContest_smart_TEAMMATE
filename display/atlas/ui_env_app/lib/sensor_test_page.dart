import 'package:flutter/material.dart';

import 'app_motion.dart';
import 'display_state.dart';
import 'state_source.dart';

class SensorTestPage extends StatefulWidget {
  const SensorTestPage({
    super.key,
    required this.source,
    required this.state,
    required this.onConnect,
    required this.onStateChanged,
    required this.onOverride,
    this.overridden = false,
  });

  final StateSource source;
  final DisplayState state;
  final Future<void> Function(String url) onConnect;
  final ValueChanged<DisplayState> onStateChanged;

  /// 임의의 FSM 상태로 화면을 고정한다. null 이면 고정을 풀고 라이브로 돌아간다.
  final ValueChanged<DisplayState?> onOverride;

  /// 지금 고정 중인가. 버튼 상태와 안내 문구가 이걸 본다.
  final bool overridden;

  @override
  State<SensorTestPage> createState() => _SensorTestPageState();
}

class _SensorTestPageState extends State<SensorTestPage> {
  bool _present = true;
  double _pcRatio = .9;
  bool _busy = false;
  String? _message;
  final _hubController = TextEditingController(text: 'http://:8765');
  final Map<String, TestSignalInput> _signals = {
    'keystroke': const TestSignalInput(),
    'posture': const TestSignalInput(),
    'respiration': const TestSignalInput(available: false),
    'environment': const TestSignalInput(),
    'elapsed': const TestSignalInput(),
  };

  static const _labels = {
    'keystroke': '키스트로크',
    'posture': 'ToF 자세',
    'respiration': '호흡',
    'environment': '환경',
    'elapsed': '경과 시간',
  };

  TestSensorInput get _input => TestSensorInput(
        present: _present,
        pcRatio: _pcRatio,
        signals: Map.unmodifiable(_signals),
      );

  Future<void> _send(String command,
      {int advanceSeconds = 30, String? event}) async {
    if (_busy || !widget.source.supportsSensorTest) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await widget.source.sendTestFrame(_input,
          command: command, advanceSeconds: advanceSeconds, event: event);
      // Preview API accepts first, then its demo loop publishes the real FSM result.
      await Future<void>.delayed(const Duration(milliseconds: 1200));
      final next = await widget.source.fetch();
      widget.onStateChanged(next);
      if (mounted) {
        setState(() => _message =
            command == 'reset' ? '빠른 시작 완료' : '가상 시간 +$advanceSeconds초');
      }
    } catch (error) {
      if (mounted) setState(() => _message = '전송 실패: $error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _change(String key, {double? focus, double? fatigue, bool? available}) {
    final old = _signals[key]!;
    setState(() {
      _signals[key] = TestSignalInput(
        focus: focus ?? old.focus,
        fatigue: fatigue ?? old.fatigue,
        available: available ?? old.available,
      );
    });
  }

  Future<void> _connect() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await widget.onConnect(_hubController.text);
    } catch (error) {
      if (mounted) setState(() => _message = '연결 실패: $error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _hubController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedSwitcher(
        duration: AppMotion.normal,
        switchInCurve: AppMotion.standardCurve,
        switchOutCurve: AppMotion.reverseCurve,
        transitionBuilder: AppMotion.fadeScaleTransition,
        child: KeyedSubtree(
          key: ValueKey(widget.source.supportsSensorTest),
          child: _buildContent(context),
        ),
      );

  Widget _buildContent(BuildContext context) {
    if (!widget.source.supportsSensorTest) {
      // Pi 4 가 없어도 화면은 확인할 수 있어야 한다. 왼쪽은 붙이고 싶을 때 쓰는
      // 연결 폼, 오른쪽은 허브 없이 임의의 FSM 상태로 화면을 고정하는 패널이다.
      return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Expanded(flex: 5, child: _buildConnectForm(context)),
        const SizedBox(width: 18),
        Expanded(flex: 7, child: _buildOverridePanel(context)),
      ]);
    }
    return _buildLiveContent(context);
  }

  Widget _buildConnectForm(BuildContext context) {
    return Center(
        child: SizedBox(
          width: 520,
          child: _TestPanel(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.hub_outlined,
                  size: 42, color: Color(0xFF52D6C7)),
              const SizedBox(height: 12),
              const Text('Pi 4 FSM Hub 연결',
                  style: TextStyle(fontSize: 21, fontWeight: FontWeight.w800)),
              const SizedBox(height: 8),
              const Text(
                '센서 테스트는 Pi 4의 실제 FSMEngine을 통해 작동합니다.\n'
                '주소는 앱을 종료하면 저장되지 않습니다.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Color(0xFFB8C4D9)),
              ),
              const SizedBox(height: 16),
              TextField(
                key: const ValueKey('hub-url-input'),
                controller: _hubController,
                decoration: const InputDecoration(
                  labelText: 'Hub URL',
                  hintText: 'http://192.168.0.20:8765',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              FilledButton.icon(
                key: const ValueKey('hub-connect'),
                onPressed: _busy ? null : _connect,
                icon: const Icon(Icons.link),
                label: const Text('연결'),
              ),
              AnimatedSize(
                duration: AppMotion.content,
                curve: AppMotion.standardCurve,
                child: AnimatedSwitcher(
                  duration: AppMotion.content,
                  switchInCurve: AppMotion.standardCurve,
                  switchOutCurve: AppMotion.reverseCurve,
                  transitionBuilder: AppMotion.fadeTransition,
                  child: _message == null
                      ? const SizedBox.shrink(
                          key: ValueKey('no-connect-message'))
                      : Padding(
                          key: ValueKey(_message),
                          padding: const EdgeInsets.only(top: 10),
                          child: Text(_message!,
                              style: const TextStyle(color: Color(0xFFFF7B7B))),
                        ),
                ),
              ),
            ]),
          ),
        ),
      );
  }

  /// 허브 없이 임의의 FSM 상태로 화면을 고정한다.
  ///
  /// 실제 FSM 을 돌리는 게 아니라 **각 상태에서 화면이 어떻게 보이는지**를
  /// 확인하는 도구다. 고정하는 동안 라이브 갱신은 화면에 반영되지 않는다 -
  /// 그래서 상단에 고정 중이라는 표시가 뜨고, 여기서 바로 풀 수 있다.
  Widget _buildOverridePanel(BuildContext context) {
    final current = widget.state.fsmState;
    return _TestPanel(
      child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(children: [
              const Icon(Icons.science_outlined, size: 22),
              const SizedBox(width: 8),
              const Text('FSM 상태 바로 지정',
                  style:
                      TextStyle(fontSize: 19, fontWeight: FontWeight.w800)),
              const Spacer(),
              if (widget.overridden)
                TextButton.icon(
                  key: const ValueKey('override-clear'),
                  onPressed: () => widget.onOverride(null),
                  icon: const Icon(Icons.play_arrow_rounded, size: 18),
                  label: const Text('라이브로'),
                ),
            ]),
            const SizedBox(height: 6),
            Text(
              widget.overridden
                  ? '테스트 상태로 고정돼 있습니다. 상태 탭에서 그 국면 화면을 볼 수 있습니다.'
                  : 'Pi 4 없이 각 상태의 화면을 확인합니다. 누르면 상태 탭이 그 국면으로 바뀝니다.',
              style: Theme.of(context).textTheme.labelMedium,
            ),
            const SizedBox(height: 12),
            Flexible(
              child: SingleChildScrollView(
                child: Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final entry in kFsmPhaseByState.entries)
                      ChoiceChip(
                        key: ValueKey('override-${entry.key}'),
                        label: Text(entry.key),
                        selected: widget.overridden && current == entry.key,
                        onSelected: (_) => widget.onOverride(
                            buildOverrideState(entry.key, widget.state)),
                      ),
                  ],
                ),
              ),
            ),
          ]),
    );
  }

  Widget _buildLiveContent(BuildContext context) {
    return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Expanded(
        flex: 7,
        child: _TestPanel(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              const Text('센서 기여도 조정',
                  style: TextStyle(fontSize: 19, fontWeight: FontWeight.w800)),
              const Spacer(),
              const Text('재실'),
              Switch(
                  value: _present,
                  onChanged: (value) => setState(() => _present = value)),
            ]),
            const SizedBox(height: 6),
            const Text(
              '원시 센서값이 아닌 baseline 대비 0~100% 정규화 기여도입니다. '
              '판정 임계값과 가중치는 Pi 4 fsm.yaml을 그대로 사용합니다.',
              style: TextStyle(fontSize: 12, color: Color(0xFF8FA0B9)),
            ),
            const SizedBox(height: 8),
            Expanded(
              child: ListView(
                children: [
                  _PercentSlider(
                    label: 'PC 사용 비율',
                    value: _pcRatio,
                    color: const Color(0xFF69A9FF),
                    onChanged: (value) => setState(() => _pcRatio = value),
                  ),
                  for (final entry in _signals.entries)
                    _SignalSliders(
                      label: _labels[entry.key]!,
                      value: entry.value,
                      onFocus: (value) => _change(entry.key, focus: value),
                      onFatigue: (value) => _change(entry.key, fatigue: value),
                      onAvailable: (value) =>
                          _change(entry.key, available: value),
                    ),
                ],
              ),
            ),
          ]),
        ),
      ),
      const SizedBox(width: 18),
      Expanded(
        flex: 3,
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _TestPanel(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text('현재 FSM',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
              const SizedBox(height: 10),
              Text(widget.state.fsmState,
                  style: const TextStyle(
                      color: Color(0xFF52D6C7),
                      fontSize: 24,
                      fontWeight: FontWeight.w900)),
              const SizedBox(height: 7),
              Text(
                  '집중 저하 ${(widget.state.focus * 100).round()}%  ·  '
                  '피로 ${(widget.state.fatigue * 100).round()}%',
                  style: const TextStyle(color: Color(0xFFB8C4D9))),
            ]),
          ),
          const SizedBox(height: 14),
          Expanded(
            child: _TestPanel(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    FilledButton.icon(
                      key: const ValueKey('sensor-test-reset'),
                      onPressed: _busy
                          ? null
                          : () => _send('reset', advanceSeconds: 0),
                      icon: const Icon(Icons.restart_alt),
                      label: const Text('빠른 시작'),
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      key: const ValueKey('sensor-test-tick'),
                      onPressed: _busy ? null : () => _send('tick'),
                      icon: const Icon(Icons.skip_next),
                      label: const Text('30초 판정'),
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      onPressed: _busy
                          ? null
                          : () => _send('tick', advanceSeconds: 180),
                      icon: const Icon(Icons.fast_forward),
                      label: const Text('3분 빠른 진행'),
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      onPressed: _busy
                          ? null
                          : () => _send('tick', event: 'action_done'),
                      icon: const Icon(Icons.done_all),
                      label: const Text('개입 완료'),
                    ),
                    const Spacer(),
                    AnimatedSize(
                      duration: AppMotion.content,
                      curve: AppMotion.standardCurve,
                      child: AnimatedSwitcher(
                        duration: AppMotion.content,
                        switchInCurve: AppMotion.standardCurve,
                        switchOutCurve: AppMotion.reverseCurve,
                        transitionBuilder: AppMotion.fadeTransition,
                        child: _busy
                            ? const LinearProgressIndicator(
                                key: ValueKey('sensor-test-busy'))
                            : const SizedBox.shrink(
                                key: ValueKey('sensor-test-idle')),
                      ),
                    ),
                    AnimatedSize(
                      duration: AppMotion.content,
                      curve: AppMotion.standardCurve,
                      child: AnimatedSwitcher(
                        duration: AppMotion.content,
                        switchInCurve: AppMotion.standardCurve,
                        switchOutCurve: AppMotion.reverseCurve,
                        transitionBuilder: AppMotion.fadeTransition,
                        child: _message == null
                            ? const SizedBox.shrink(
                                key: ValueKey('no-sensor-test-message'))
                            : Padding(
                                key: ValueKey(_message),
                                padding: const EdgeInsets.only(top: 8),
                                child: Text(
                                  _message!,
                                  style: const TextStyle(
                                      fontSize: 12, color: Color(0xFF9DABC2)),
                                ),
                              ),
                      ),
                    ),
                  ]),
            ),
          ),
        ]),
      ),
    ]);
  }
}

class _SignalSliders extends StatelessWidget {
  const _SignalSliders({
    required this.label,
    required this.value,
    required this.onFocus,
    required this.onFatigue,
    required this.onAvailable,
  });

  final String label;
  final TestSignalInput value;
  final ValueChanged<double> onFocus;
  final ValueChanged<double> onFatigue;
  final ValueChanged<bool> onAvailable;

  @override
  Widget build(BuildContext context) => Opacity(
        opacity: value.available ? 1 : .48,
        child: Column(children: [
          Row(children: [
            SizedBox(
                width: 96,
                child: Text(label,
                    style: const TextStyle(fontWeight: FontWeight.w700))),
            Expanded(
              child: _PercentSlider(
                  label: '집중 저하',
                  value: value.focus,
                  color: const Color(0xFF69A9FF),
                  enabled: value.available,
                  onChanged: onFocus),
            ),
            Expanded(
              child: _PercentSlider(
                  label: '피로',
                  value: value.fatigue,
                  color: const Color(0xFFFFB45E),
                  enabled: value.available,
                  onChanged: onFatigue),
            ),
            Switch(value: value.available, onChanged: onAvailable),
          ]),
          const Divider(height: 5, color: Color(0xFF24344C)),
        ]),
      );
}

class _PercentSlider extends StatelessWidget {
  const _PercentSlider({
    required this.label,
    required this.value,
    required this.color,
    required this.onChanged,
    this.enabled = true,
  });

  final String label;
  final double value;
  final Color color;
  final ValueChanged<double> onChanged;
  final bool enabled;

  @override
  Widget build(BuildContext context) => Row(children: [
        SizedBox(
            width: 74,
            child: Text(label, style: const TextStyle(fontSize: 11))),
        Expanded(
          child: SliderTheme(
            data: SliderTheme.of(context)
                .copyWith(activeTrackColor: color, thumbColor: color),
            child: Slider(
              value: value,
              divisions: 20,
              onChanged: enabled ? onChanged : null,
            ),
          ),
        ),
        SizedBox(
            width: 38,
            child: Text('${(value * 100).round()}%',
                textAlign: TextAlign.end,
                style: const TextStyle(
                    fontSize: 11, fontWeight: FontWeight.w700))),
      ]);
}

class _TestPanel extends StatelessWidget {
  const _TestPanel({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(18),
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
/// 실제 FSM 을 돌리는 게 아니라 **화면을 확인하려는** 것이다. 그래서 국면에
/// 맞는 집중도·피로도와, 제안 화면이 뜨도록 ACTION_* 에는 gate 를 넣는다.
/// 센서값은 지금 보이는 값을 그대로 물려받아 화면이 비어 보이지 않게 한다.
DisplayState buildOverrideState(String fsmState, DisplayState base) {
  final phase = kFsmPhaseByState[fsmState] ?? 'idle';
  final fatigue = switch (phase) {
    'fatigue' => 0.78,
    'recovery' => 0.35,
    _ => 0.08,
  };
  final focus = switch (phase) {
    'focus' => 0.82,
    'start' => 0.4,
    _ => 0.12,
  };
  const actionStates = {'ACTION_ENV', 'ACTION_POSTURE', 'ACTION_BREAK'};
  final cause = switch (fsmState) {
    'ACTION_ENV' => 'environment',
    'ACTION_POSTURE' => 'posture',
    'ACTION_BREAK' => 'sustained_load',
    _ => null,
  };
  return DisplayState(
    fsmState: fsmState,
    phase: phase,
    context: base.context,
    focus: focus,
    fatigue: fatigue,
    confidence: focus > fatigue ? focus : fatigue,
    // 제안 화면은 gate 가 none 이 아닐 때만 뜬다.
    gate: actionStates.contains(fsmState) ? 'suggest' : 'none',
    cause: cause,
    reasons: const ['test_override'],
    sequence: base.sequence,
    timestamp: DateTime.now(),
    present: base.present,
    co2Ppm: base.co2Ppm,
    temperatureC: base.temperatureC,
    humidityPct: base.humidityPct,
    lux: base.lux,
    scenario: 'test-override',
    keystroke: base.keystroke,
    mmwaveMotionState: base.mmwaveMotionState,
    mmwaveMotionLevel: base.mmwaveMotionLevel,
    mmwaveDistanceCm: base.mmwaveDistanceCm,
    mmwaveHeartBpm: base.mmwaveHeartBpm,
  );
}
