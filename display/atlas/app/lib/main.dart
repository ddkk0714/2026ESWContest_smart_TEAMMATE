import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'display_state.dart';
import 'keystroke_capture.dart';
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

  // 보드에 꽂힌 키보드를 앱이 직접 잡는다. hub 가 주는 collector 지표보다 이걸 우선한다.
  final _capture = KeystrokeCapture();
  // 벽시계는 뒤로 갈 수 있어 이벤트 간격 계산에 쓰지 않는다.
  final _clock = Stopwatch();
  KeystrokeMetrics? _localKeystroke;
  int _liveKeys = 0;

  double get _now => _clock.elapsedMicroseconds / 1e6;

  @override
  void initState() {
    super.initState();
    _clock.start();
    HardwareKeyboard.instance.addHandler(_onKey);
    _source =
        _hubUrl.trim().isEmpty ? DemoStateSource() : HttpStateSource(_hubUrl);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_source is! DemoStateSource || _demoCyclingEnabled) _refresh();
      _sampleKeystroke();
    });
  }

  /// 키 이벤트는 소비하지 않는다(항상 false). 타건 수만 즉시 반영해 화면이 살아 보이게 한다.
  bool _onKey(KeyEvent event) {
    final counted = _capture.handle(event, _now);
    if (counted && mounted) setState(() => _liveKeys = _capture.pressTotal);
    return false;
  }

  /// collector 규약과 같은 1Hz 로 창을 뽑는다.
  void _sampleKeystroke() {
    if (!_capture.hasData) return;
    final sample = _capture.extract(_now);
    if (mounted) setState(() => _localKeystroke = sample);
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
    HardwareKeyboard.instance.removeHandler(_onKey);
    _clock.stop();
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
                          const SizedBox(width: 18),
                          Expanded(
                            flex: 4,
                            child: _KeystrokePanel(
                              // 보드 키보드가 잡히면 그걸 쓰고, 없으면 hub 가 준 collector 지표를 쓴다.
                              metrics: _localKeystroke ?? state.keystroke,
                              reference: _localKeystroke != null
                                  ? DateTime.now()
                                  : state.timestamp,
                              liveKeys: _localKeystroke != null ? _liveKeys : null,
                            ),
                          ),
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

/// 화면 강조용 경계값. FSM 판정 임계값이 아니라 색만 바꾸는 힌트다.
/// 판정 임계값은 hub/deskmate_hub/config/*.yaml 에만 둔다.
const _ksWarnCv = 0.55;
const _ksWarnIdle = 0.35;
const _ksWarnCorrection = 0.09;

/// collector 표본이 이보다 묵으면 값을 흐리고 '수신 끊김' 으로 표시한다.
/// collector 가 죽어도 hub 는 국면을 계속 내보내므로 이게 없으면
/// 마지막 값이 화면에 그대로 굳는다.
const _ksStaleAfter = Duration(seconds: 5);

/// 화면이 7인치라 세로가 귀하다. 카드 격자 대신 _SensorPanel 과 같은
/// 세로 목록으로 두고 가로 한 칸을 차지한다.
class _KeystrokePanel extends StatelessWidget {
  const _KeystrokePanel({
    required this.metrics,
    required this.reference,
    this.liveKeys,
  });

  final KeystrokeMetrics? metrics;

  /// 신선도를 재는 기준 시각. hub 지표면 envelope 의 ts, 보드 캡처면 지금이다.
  final DateTime reference;

  /// 보드 키보드를 직접 잡는 중일 때의 누적 타건 수. hub 지표면 null.
  final int? liveKeys;

  @override
  Widget build(BuildContext context) {
    final ks = metrics;
    final age = ks?.ageFrom(reference);
    final stale = ks == null || age == null || age > _ksStaleAfter;
    final live = !stale && (ks.valid ?? true);

    return _Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // 3열 배치라 이 칸이 좁다. 800px 화면에서는 제목+칩이 폭을 넘겨서
        // 잘리는 대신 통째로 줄어들게 둔다.
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Row(children: [
            const Text('키스트로크',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
            const SizedBox(width: 10),
            _KsStatusChip(metrics: ks, live: live),
          ]),
        ),
        const Spacer(),
        _KsRow(
          label: '체류',
          value: ks?.dwellMeanMs == null ? '--' : '${_ms(ks!.dwellMeanMs)} ms',
          live: live,
        ),
        _KsRow(
          label: '비행',
          value:
              ks?.flightMeanMs == null ? '--' : '${_ms(ks!.flightMeanMs)} ms',
          live: live,
        ),
        _KsRow(
          label: '리듬 불규칙',
          value:
              ks?.flightCv == null ? '--' : ks!.flightCv!.toStringAsFixed(2),
          warn: (ks?.flightCv ?? 0) >= _ksWarnCv,
          live: live,
        ),
        _KsRow(
          label: '입력 공백',
          value: ks?.idleRatio == null ? '--' : '${_pct(ks!.idleRatio)}%',
          warn: (ks?.idleRatio ?? 0) >= _ksWarnIdle,
          live: live,
        ),
        _KsRow(
          label: '오타 교정',
          value:
              ks?.correctionRate == null ? '--' : '${_pct(ks!.correctionRate)}%',
          warn: (ks?.correctionRate ?? 0) >= _ksWarnCorrection,
          live: live,
        ),
        const Spacer(),
        Text(_ksMeta(ks, age, live, liveKeys),
            style: const TextStyle(fontSize: 11, color: Color(0xFF6B7C96))),
      ]),
    );
  }
}

class _KsStatusChip extends StatelessWidget {
  const _KsStatusChip({required this.metrics, required this.live});
  final KeystrokeMetrics? metrics;
  final bool live;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch ((live, metrics?.typingActive)) {
      (false, _) => ('수신 끊김', const Color(0xFFFF7B7B)),
      (true, true) => ('타이핑 중', const Color(0xFF52D6C7)),
      (true, false) => ('입력 없음', const Color(0xFF9DABC2)),
      // typing_active 는 규약 추가분이라 안 올 수 있다. 그때는 수신만 알린다.
      (true, null) => ('수신 중', const Color(0xFF69A9FF)),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(label,
          style: TextStyle(
              fontSize: 11, color: color, fontWeight: FontWeight.w700)),
    );
  }
}

class _KsRow extends StatelessWidget {
  const _KsRow({
    required this.label,
    required this.value,
    required this.live,
    this.warn = false,
  });

  final String label;
  final String value;
  final bool live;
  final bool warn;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(children: [
          Text(label, style: const TextStyle(color: Color(0xFF9DABC2))),
          const Spacer(),
          Text(value,
              style: TextStyle(
                fontWeight: FontWeight.w700,
                color: !live
                    ? const Color(0xFF6B7C96)
                    : warn
                        ? const Color(0xFFFFB45E)
                        : Colors.white,
              )),
        ]),
      );
}

String _ms(double? value) => value == null ? '--' : value.round().toString();
String _pct(double? value) =>
    value == null ? '--' : (value * 100).round().toString();

String _ksMeta(
    KeystrokeMetrics? metrics, Duration? age, bool live, int? liveKeys) {
  if (metrics == null) return 'collector 대기';
  return [
    metrics.node ?? 'collector',
    if (metrics.windowS != null) '${metrics.windowS}초 윈도',
    if (liveKeys != null) '${liveKeys}타',
    if (live) '수신 중' else if (age != null) '${age.inSeconds}초 전',
  ].join(' · ');
}

/// 테스트에서 키스트로크 패널만 따로 띄우기 위한 통로.
/// 패널 자체는 비공개로 두고 노출은 이 한 줄로 제한한다.
class KeystrokePanelForTest extends StatelessWidget {
  const KeystrokePanelForTest({
    super.key,
    required this.metrics,
    required this.reference,
    this.liveKeys,
  });
  final KeystrokeMetrics? metrics;
  final DateTime reference;
  final int? liveKeys;
  @override
  Widget build(BuildContext context) => _KeystrokePanel(
      metrics: metrics, reference: reference, liveKeys: liveKeys);
}
