import 'dart:async';

import 'package:flutter/material.dart';

import 'display_state.dart';
import 'state_source.dart';

const _hubUrl = String.fromEnvironment('DESKMATE_HUB_URL');

void main() => runApp(const DeskmateApp());

class DeskmateApp extends StatelessWidget {
  const DeskmateApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DESKMATE',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF09111F),
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF52D6C7),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const DashboardPage(),
    );
  }
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key});

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  late final StateSource _source;
  Timer? _timer;
  DisplayState? _state;
  String? _error;
  bool _busy = false;
  bool _demoCyclingEnabled = true;

  @override
  void initState() {
    super.initState();
    _source =
        _hubUrl.trim().isEmpty ? DemoStateSource() : HttpStateSource(_hubUrl);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_source is! DemoStateSource || _demoCyclingEnabled) _refresh();
    });
  }

  Future<void> _refresh() async {
    if (_busy) return;
    _busy = true;
    try {
      final next = await _source.fetch();
      if (mounted)
        setState(() {
          _state = next;
          _error = null;
        });
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      _busy = false;
    }
  }

  Future<void> _feedback(String verdict) async {
    try {
      await _source.feedback(verdict);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content:
                  Text(verdict == 'accept' ? '제안을 수락했습니다.' : '제안을 거절했습니다.')),
        );
      }
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('전송 실패: $error')));
    }
  }

  void _toggleDemoCycling() {
    if (_source is! DemoStateSource) return;
    setState(() => _demoCyclingEnabled = !_demoCyclingEnabled);
    if (_demoCyclingEnabled) _refresh();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 1),
        content: Text(
          _demoCyclingEnabled ? '자동 순환을 시작했습니다.' : '자동 순환을 멈췄습니다.',
        ),
      ),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    _source.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: state == null
              ? _Loading(error: _error)
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _Header(
                        source: _source.label,
                        online: _error == null,
                        sequence: state.sequence),
                    const SizedBox(height: 18),
                    Expanded(
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Expanded(flex: 6, child: _PhasePanel(state: state)),
                          const SizedBox(width: 18),
                          Expanded(
                              flex: 4,
                              child: Column(
                                children: [
                                  Expanded(child: _ScorePanel(state: state)),
                                  const SizedBox(height: 18),
                                  Expanded(child: _SensorPanel(state: state)),
                                ],
                              )),
                        ],
                      ),
                    ),
                    const SizedBox(height: 18),
                    _ActionBar(
                      state: state,
                      onFeedback: _feedback,
                      showDemoControl: _source is DemoStateSource,
                      demoCyclingEnabled: _demoCyclingEnabled,
                      onToggleDemoCycling: _toggleDemoCycling,
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header(
      {required this.source, required this.online, required this.sequence});
  final String source;
  final bool online;
  final int sequence;

  @override
  Widget build(BuildContext context) => Row(children: [
        const Icon(Icons.desktop_windows_rounded,
            color: Color(0xFF52D6C7), size: 30),
        const SizedBox(width: 12),
        const Text('DESKMATE',
            style: TextStyle(
                fontSize: 25, fontWeight: FontWeight.w800, letterSpacing: 1.2)),
        const Spacer(),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
              color: (online ? const Color(0xFF52D6C7) : Colors.redAccent)
                  .withOpacity(.12),
              borderRadius: BorderRadius.circular(99)),
          child: Row(children: [
            Icon(Icons.circle,
                size: 10,
                color: online ? const Color(0xFF52D6C7) : Colors.redAccent),
            const SizedBox(width: 8),
            Text('$source · #$sequence',
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ]),
        ),
      ]);
}

class _PhasePanel extends StatelessWidget {
  const _PhasePanel({required this.state});
  final DisplayState state;

  @override
  Widget build(BuildContext context) {
    final color = _phaseColor(state.phase);
    return _Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(_phaseLabel(state.phase),
            style: TextStyle(
                color: color, fontSize: 17, fontWeight: FontWeight.w700)),
        const Spacer(),
        Icon(_phaseIcon(state.phase), color: color, size: 60),
        const SizedBox(height: 14),
        Text(state.fsmState,
            style: const TextStyle(fontSize: 36, fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text(state.scenario ?? _stateMessage(state),
            style: const TextStyle(fontSize: 19, color: Color(0xFFB8C4D9))),
        const Spacer(),
        Row(children: [
          _Chip(label: '컨텍스트 ${state.context.toUpperCase()}'),
          const SizedBox(width: 8),
          _Chip(label: '게이트 ${state.gate.toUpperCase()}'),
          if (state.cause != null) ...[
            const SizedBox(width: 8),
            _Chip(label: '원인 ${state.cause}')
          ],
        ]),
      ]),
    );
  }
}

class _ScorePanel extends StatelessWidget {
  const _ScorePanel({required this.state});
  final DisplayState state;
  @override
  Widget build(BuildContext context) => _Panel(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('상태 지표',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
        const Spacer(),
        _Meter(
            label: '집중 저하', value: state.focus, color: const Color(0xFF69A9FF)),
        const SizedBox(height: 15),
        _Meter(
            label: '피로', value: state.fatigue, color: _phaseColor(state.phase)),
        const Spacer(),
        Text('판정 신뢰도 ${(state.confidence * 100).round()}%',
            style: const TextStyle(color: Color(0xFF9DABC2))),
      ]));
}

class _SensorPanel extends StatelessWidget {
  const _SensorPanel({required this.state});
  final DisplayState state;
  @override
  Widget build(BuildContext context) => _Panel(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('센서 요약',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
        const Spacer(),
        _ValueRow(
            label: '재실',
            value: state.present == null
                ? '대기'
                : state.present!
                    ? '감지'
                    : '없음'),
        _ValueRow(
            label: 'CO₂',
            value: state.co2Ppm == null ? '대기' : '${state.co2Ppm} ppm'),
        _ValueRow(
            label: '조도', value: state.lux == null ? '대기' : '${state.lux} lx'),
        const Spacer(),
      ]));
}

class _ActionBar extends StatelessWidget {
  const _ActionBar({
    required this.state,
    required this.onFeedback,
    required this.showDemoControl,
    required this.demoCyclingEnabled,
    required this.onToggleDemoCycling,
  });
  final DisplayState state;
  final ValueChanged<String> onFeedback;
  final bool showDemoControl;
  final bool demoCyclingEnabled;
  final VoidCallback onToggleDemoCycling;
  @override
  Widget build(BuildContext context) {
    final needsAnswer = state.phase == 'fatigue' && state.gate != 'none';
    return SizedBox(
        height: 74,
        child: _Panel(
            child: Row(children: [
          Icon(
              needsAnswer
                  ? Icons.notifications_active_outlined
                  : Icons.check_circle_outline,
              color: needsAnswer
                  ? const Color(0xFFFFB45E)
                  : const Color(0xFF52D6C7)),
          const SizedBox(width: 12),
          Expanded(
              child: Text(
                  needsAnswer ? '지금 제안한 조치를 진행할까요?' : '상태를 계속 확인하고 있습니다.',
                  style: const TextStyle(
                      fontSize: 17, fontWeight: FontWeight.w600))),
          if (needsAnswer) ...[
            OutlinedButton(
                onPressed: () => onFeedback('reject'),
                child: const Text('아니요')),
            const SizedBox(width: 10),
            FilledButton(
                onPressed: () => onFeedback('accept'), child: const Text('진행')),
          ],
          if (showDemoControl) ...[
            const SizedBox(width: 12),
            OutlinedButton.icon(
              key: const ValueKey('demo-cycle-toggle'),
              onPressed: onToggleDemoCycling,
              icon: Icon(demoCyclingEnabled
                  ? Icons.pause_rounded
                  : Icons.play_arrow_rounded),
              label: Text('자동 순환: ${demoCyclingEnabled ? 'ON' : 'OFF'}'),
            ),
          ],
        ])));
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
            color: const Color(0xFF121D2E),
            borderRadius: BorderRadius.circular(22),
            border: Border.all(color: const Color(0xFF24344C))),
        child: child,
      );
}

class _Meter extends StatelessWidget {
  const _Meter({required this.label, required this.value, required this.color});
  final String label;
  final double value;
  final Color color;
  @override
  Widget build(BuildContext context) => Column(children: [
        Row(children: [
          Text(label),
          const Spacer(),
          Text('${(value * 100).round()}%',
              style: TextStyle(color: color, fontWeight: FontWeight.w800))
        ]),
        const SizedBox(height: 7),
        LinearProgressIndicator(
            value: value,
            minHeight: 8,
            borderRadius: BorderRadius.circular(9),
            color: color,
            backgroundColor: const Color(0xFF26344A)),
      ]);
}

class _ValueRow extends StatelessWidget {
  const _ValueRow({required this.label, required this.value});
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(children: [
        Text(label, style: const TextStyle(color: Color(0xFF9DABC2))),
        const Spacer(),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w700))
      ]));
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label});
  final String label;
  @override
  Widget build(BuildContext context) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
      decoration: BoxDecoration(
          color: const Color(0xFF1C2A40),
          borderRadius: BorderRadius.circular(10)),
      child: Text(label,
          style: const TextStyle(fontSize: 12, color: Color(0xFFB8C4D9))));
}

class _Loading extends StatelessWidget {
  const _Loading({this.error});
  final String? error;
  @override
  Widget build(BuildContext context) => Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        const CircularProgressIndicator(),
        const SizedBox(height: 18),
        Text(error ?? 'FSM 상태를 기다리고 있습니다.')
      ]));
}

String _phaseLabel(String phase) =>
    const {
      'idle': '대기',
      'start': '시작',
      'focus': '집중',
      'fatigue': '피로 감지',
      'recovery': '회복',
      'end': '종료'
    }[phase] ??
    phase;
String _stateMessage(DisplayState state) => state.phase == 'fatigue'
    ? '작업 상태를 확인해 주세요.'
    : state.phase == 'focus'
        ? '안정적으로 작업 중입니다.'
        : '상태를 분석하고 있습니다.';
Color _phaseColor(String phase) =>
    const {
      'idle': Color(0xFF9DABC2),
      'start': Color(0xFF69A9FF),
      'focus': Color(0xFF52D6C7),
      'fatigue': Color(0xFFFFB45E),
      'recovery': Color(0xFFB58CFF),
      'end': Color(0xFF9DABC2)
    }[phase] ??
    const Color(0xFF52D6C7);
IconData _phaseIcon(String phase) =>
    const {
      'idle': Icons.bedtime_outlined,
      'start': Icons.tune,
      'focus': Icons.center_focus_strong,
      'fatigue': Icons.warning_amber_rounded,
      'recovery': Icons.spa_outlined,
      'end': Icons.flag_outlined
    }[phase] ??
    Icons.insights;
