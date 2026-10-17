// DESKMATE — 디스플레이 앱 (ATLAS Flutter)
// 첫 화면: 현재 작업 상태 대시보드.
// 데이터 모양은 docs/mqtt-topics.md 의 deskmate/state/phase 규약과 동일하게 맞춰,
// 나중에 mock 소스만 실제 MQTT 구독으로 교체하면 되도록 설계했다.
import 'dart:async';
import 'package:flutter/material.dart';

void main() => runApp(const DeskmateApp());

/// deskmate/state/phase 의 phase 값
enum Phase { start, focus, fatigue, end }

/// hub 가 발행하는 상태 페이로드와 1:1 대응하는 모델
class PhaseState {
  final Phase phase;
  final double confidence; // 0.0 ~ 1.0
  final String action; // auto | suggest | none
  final List<String> reason;
  const PhaseState({
    required this.phase,
    required this.confidence,
    required this.action,
    required this.reason,
  });

  // 실제 연동 시: PhaseState.fromJson(mqtt payload) 로 교체
}

class PhaseInfo {
  final String ko;
  final String en;
  final Color color;
  final IconData icon;
  final String desc;
  const PhaseInfo(this.ko, this.en, this.color, this.icon, this.desc);
}

const Map<Phase, PhaseInfo> kPhaseInfo = {
  Phase.start: PhaseInfo('시작', 'START', Color(0xFF5E9BFF), Icons.wb_sunny_outlined,
      '작업을 시작했어요. 환경을 맞추는 중'),
  Phase.focus: PhaseInfo('몰입', 'FOCUS', Color(0xFF37D0A0), Icons.center_focus_strong,
      '집중 흐름이 좋아요. 방해하지 않을게요'),
  Phase.fatigue: PhaseInfo('피로', 'FATIGUE', Color(0xFFF5B455), Icons.battery_2_bar,
      '피로가 쌓이고 있어요'),
  Phase.end: PhaseInfo('종료', 'END', Color(0xFF8A93A6), Icons.nightlight_round,
      '작업을 마무리했어요'),
};

/// reason 코드 → 사람이 읽는 한국어 라벨
const Map<String, String> kReasonLabel = {
  'posture_stable': '자세 안정',
  'posture_slouch': '자세 무너짐',
  'typing_rhythm_slow': '타이핑 느려짐',
  'typing_irregular': '리듬 불규칙',
  'correction_high': '오타 증가',
  'co2_rising': 'CO₂ 상승',
  'time_on_task': '장시간 작업',
  'present_static': '재실·정적',
};

class DeskmateApp extends StatelessWidget {
  const DeskmateApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DESKMATE',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        fontFamily: 'Roboto',
        scaffoldBackgroundColor: const Color(0xFF0F1420),
      ),
      home: const DashboardScreen(),
    );
  }
}

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});
  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  // --- mock 소스: 실제로는 MQTT deskmate/state/phase 스트림으로 대체 ---
  static const _mock = <PhaseState>[
    PhaseState(phase: Phase.start, confidence: 0.90, action: 'auto', reason: ['present_static']),
    PhaseState(phase: Phase.focus, confidence: 0.82, action: 'auto', reason: ['posture_stable', 'present_static']),
    PhaseState(phase: Phase.fatigue, confidence: 0.55, action: 'suggest', reason: ['posture_slouch', 'typing_rhythm_slow', 'co2_rising']),
    PhaseState(phase: Phase.focus, confidence: 0.76, action: 'auto', reason: ['posture_stable']),
    PhaseState(phase: Phase.end, confidence: 0.88, action: 'none', reason: ['time_on_task']),
  ];
  int _i = 0;
  Timer? _timer;
  String? _feedback; // 사용자가 카드에 응답한 결과

  PhaseState get _state => _mock[_i];

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 4), (_) {
      setState(() {
        _i = (_i + 1) % _mock.length;
        _feedback = null;
      });
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  // 실제 연동 시: deskmate/feedback/user 로 발행
  void _respond(String verdict) => setState(() => _feedback = verdict);

  @override
  Widget build(BuildContext context) {
    final s = _state;
    final info = kPhaseInfo[s.phase]!;
    return Scaffold(
      body: Container(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0, -0.5),
            radius: 1.2,
            colors: [info.color.withOpacity(0.16), const Color(0xFF0F1420)],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _header(),
                const Spacer(),
                _phaseHero(info, s),
                const SizedBox(height: 28),
                _reasonChips(s),
                const Spacer(),
                if (s.action == 'suggest') _suggestionCard(info),
                _footer(s),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _header() => Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(children: [
            const Icon(Icons.desk, color: Color(0xFF37D0A0), size: 22),
            const SizedBox(width: 8),
            const Text('DESKMATE',
                style: TextStyle(fontWeight: FontWeight.w800, letterSpacing: 2, fontSize: 18)),
          ]),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.06),
              borderRadius: BorderRadius.circular(20),
            ),
            child: const Text('MOCK · 실데이터 아님',
                style: TextStyle(fontSize: 11, color: Color(0xFF9AA6BD))),
          ),
        ],
      );

  Widget _phaseHero(PhaseInfo info, PhaseState s) => Column(
        children: [
          Container(
            width: 190,
            height: 190,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: info.color.withOpacity(0.12),
              border: Border.all(color: info.color, width: 3),
              boxShadow: [BoxShadow(color: info.color.withOpacity(0.35), blurRadius: 40, spreadRadius: 4)],
            ),
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Icon(info.icon, color: info.color, size: 56),
              const SizedBox(height: 8),
              Text(info.ko, style: TextStyle(fontSize: 34, fontWeight: FontWeight.w800, color: info.color)),
              Text(info.en, style: const TextStyle(fontSize: 12, letterSpacing: 3, color: Color(0xFF9AA6BD))),
            ]),
          ),
          const SizedBox(height: 22),
          Text(info.desc, style: const TextStyle(fontSize: 16, color: Color(0xFFE8ECF4))),
          const SizedBox(height: 16),
          _confidenceBar(info.color, s.confidence),
        ],
      );

  Widget _confidenceBar(Color color, double c) => SizedBox(
        width: 260,
        child: Column(children: [
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            const Text('신뢰도', style: TextStyle(fontSize: 12, color: Color(0xFF9AA6BD))),
            Text('${(c * 100).round()}%',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: color)),
          ]),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
              value: c,
              minHeight: 8,
              backgroundColor: Colors.white.withOpacity(0.08),
              valueColor: AlwaysStoppedAnimation(color),
            ),
          ),
        ]),
      );

  Widget _reasonChips(PhaseState s) => Wrap(
        alignment: WrapAlignment.center,
        spacing: 8,
        runSpacing: 8,
        children: s.reason
            .map((r) => Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1E2536),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: const Color(0xFF2A3348)),
                  ),
                  child: Text(kReasonLabel[r] ?? r,
                      style: const TextStyle(fontSize: 13, color: Color(0xFFC7D4EE))),
                ))
            .toList(),
      );

  Widget _suggestionCard(PhaseInfo info) {
    if (_feedback != null) {
      final ok = _feedback == 'accept';
      return _cardShell(
        info.color,
        Row(children: [
          Icon(ok ? Icons.check_circle : Icons.cancel, color: info.color),
          const SizedBox(width: 10),
          Text(ok ? '휴식을 시작할게요' : '알겠어요, 계속 진행할게요',
              style: const TextStyle(fontSize: 15)),
        ]),
      );
    }
    return _cardShell(
      info.color,
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('잠깐 쉬는 건 어때요?',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        const Text('피로 신호가 보여요. 5분 스트레칭을 제안해요.',
            style: TextStyle(fontSize: 13, color: Color(0xFF9AA6BD))),
        const SizedBox(height: 14),
        Row(children: [
          _btn('수락', info.color, () => _respond('accept'), filled: true),
          const SizedBox(width: 10),
          _btn('나중에', const Color(0xFF9AA6BD), () => _respond('reject')),
        ]),
      ]),
    );
  }

  Widget _cardShell(Color color, Widget child) => Container(
        margin: const EdgeInsets.only(bottom: 16),
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: const Color(0xFF171D2B),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: color.withOpacity(0.5)),
        ),
        child: child,
      );

  Widget _btn(String label, Color color, VoidCallback onTap, {bool filled = false}) => Expanded(
        child: Material(
          color: filled ? color : Colors.transparent,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
            side: BorderSide(color: color),
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Center(
                child: Text(label,
                    style: TextStyle(
                        fontWeight: FontWeight.w700,
                        color: filled ? const Color(0xFF0F1420) : color)),
              ),
            ),
          ),
        ),
      );

  Widget _footer(PhaseState s) => Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text('source: fsm · action: ${s.action}',
              style: const TextStyle(fontSize: 11, color: Color(0xFF5A667E))),
        ],
      );
}
