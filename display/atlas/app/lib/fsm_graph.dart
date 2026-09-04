import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

class FsmGraphPage extends StatefulWidget {
  const FsmGraphPage({super.key, required this.currentState});

  final String currentState;

  @override
  State<FsmGraphPage> createState() => _FsmGraphPageState();
}

class _FsmGraphPageState extends State<FsmGraphPage> {
  final _controller = TransformationController();
  Size? _viewport;
  int _zoomLevel = 1;
  DateTime _lastWheelZoom = DateTime.fromMillisecondsSinceEpoch(0);

  @override
  void didUpdateWidget(covariant FsmGraphPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.currentState != widget.currentState && _zoomLevel > 1) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _applyZoom());
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final next = <String>{
      ...?_nextStates[widget.currentState],
      if (widget.currentState != 'IDLE' && widget.currentState != 'END') 'IDLE',
    };
    return Stack(children: [
      Positioned.fill(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final viewport = constraints.biggest;
            if (_viewport != viewport) {
              _viewport = viewport;
              WidgetsBinding.instance.addPostFrameCallback((_) => _applyZoom());
            }
            return ClipRRect(
              borderRadius: BorderRadius.circular(22),
              child: ColoredBox(
                color: const Color(0xFFF6F8FC),
                child: Listener(
                  behavior: HitTestBehavior.opaque,
                  onPointerSignal: _handlePointerSignal,
                  child: InteractiveViewer(
                    transformationController: _controller,
                    constrained: false,
                    // Pi 5의 휠 이벤트는 장치마다 증분 차이가 커서 자유 배율을
                    // 사용하지 않는다. 휠과 우측 스위치 모두 같은 3단계로 스냅한다.
                    scaleEnabled: false,
                    panEnabled: true,
                    minScale: .1,
                    maxScale: 4,
                    boundaryMargin: const EdgeInsets.all(220),
                    child: CustomPaint(
                      key: const ValueKey('fsm-full-graph'),
                      size: _graphSize,
                      painter:
                          _FsmPainter(current: widget.currentState, next: next),
                    ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
      Positioned(
        left: 16,
        top: 14,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: const Color(0xEE09111F),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.account_tree_outlined, color: Color(0xFF52D6C7)),
            const SizedBox(width: 9),
            Text('현재 ${widget.currentState}',
                style: const TextStyle(fontWeight: FontWeight.w800)),
          ]),
        ),
      ),
      Positioned(
        left: 16,
        top: 68,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
          decoration: BoxDecoration(
            color: const Color(0xEFFFFFFF),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: const Color(0xFFD9E0EA)),
          ),
          child: const Text(
            '노랑: 현재  ·  민트: 다음 가능  ·  휠: 단계 이동  ·  드래그: 화면 이동',
            style: TextStyle(
              fontSize: 11,
              color: Color(0xFF586579),
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ),
      Positioned(
        right: 16,
        top: 14,
        child: _ZoomSelector(level: _zoomLevel, onChanged: _setZoomLevel),
      ),
    ]);
  }

  void _handlePointerSignal(PointerSignalEvent event) {
    if (event is! PointerScrollEvent ||
        event.scrollDelta.dy == 0 ||
        event.scrollDelta.dy.abs() < event.scrollDelta.dx.abs()) {
      return;
    }
    final now = DateTime.now();
    if (now.difference(_lastWheelZoom) < const Duration(milliseconds: 220)) {
      return;
    }
    _lastWheelZoom = now;
    final delta = event.scrollDelta.dy < 0 ? 1 : -1;
    _setZoomLevel((_zoomLevel + delta).clamp(1, 3));
  }

  void _setZoomLevel(int level) {
    final clamped = level.clamp(1, 3);
    if (clamped != _zoomLevel) setState(() => _zoomLevel = clamped);
    _applyZoom(level: clamped);
  }

  void _applyZoom({int? level}) {
    final viewport = _viewport;
    if (!mounted || viewport == null || viewport.isEmpty) return;
    _controller.value = _zoomMatrix(viewport, level ?? _zoomLevel);
  }

  Matrix4 _zoomMatrix(Size viewport, int level) {
    final fitScale = math.min(
          viewport.width / _graphSize.width,
          viewport.height / _graphSize.height,
        ) *
        .96;
    final multiplier = switch (level) {
      1 => 1.0,
      2 => 1.65,
      _ => 2.45,
    };
    final scale = (fitScale * multiplier).clamp(.1, 3.2).toDouble();
    final currentNode = _nodesById[widget.currentState];
    final focus = level == 1 || currentNode == null
        ? _graphSize.center(Offset.zero)
        : _layoutRect(currentNode.rect).center;
    final target = level == 1
        ? viewport.center(Offset.zero)
        : Offset(viewport.width / 2, viewport.height / 2 + 24);
    final dx = target.dx - focus.dx * scale;
    final dy = target.dy - focus.dy * scale;
    return Matrix4.identity()
      ..translate(dx, dy)
      ..scale(scale);
  }
}

class _ZoomSelector extends StatelessWidget {
  const _ZoomSelector({required this.level, required this.onChanged});

  final int level;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('fsm-zoom-selector'),
        padding: const EdgeInsets.all(5),
        decoration: BoxDecoration(
          color: const Color(0xF509111F),
          borderRadius: BorderRadius.circular(15),
          border: Border.all(color: const Color(0xFF2B3950)),
          boxShadow: const [
            BoxShadow(
                color: Color(0x33000000), blurRadius: 14, offset: Offset(0, 4)),
          ],
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Padding(
            padding: EdgeInsets.only(left: 8, right: 7),
            child: Icon(Icons.zoom_in_map, size: 19, color: Color(0xFF9DABC2)),
          ),
          _ZoomButton(
              level: 1, label: '전체', selected: level == 1, onTap: onChanged),
          _ZoomButton(
              level: 2, label: '중간', selected: level == 2, onTap: onChanged),
          _ZoomButton(
              level: 3, label: '상세', selected: level == 3, onTap: onChanged),
        ]),
      );
}

class _ZoomButton extends StatelessWidget {
  const _ZoomButton({
    required this.level,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final int level;
  final String label;
  final bool selected;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) => Semantics(
        button: true,
        selected: selected,
        label: '$level단계 $label',
        child: InkWell(
          key: ValueKey('fsm-zoom-$level'),
          borderRadius: BorderRadius.circular(10),
          onTap: () => onTap(level),
          child: AnimatedContainer(
            key: ValueKey(selected
                ? 'fsm-zoom-selected-$level'
                : 'fsm-zoom-level-$level'),
            duration: const Duration(milliseconds: 140),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
            decoration: BoxDecoration(
              color: selected ? const Color(0xFF177E73) : Colors.transparent,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Text(
              '$level $label',
              style: TextStyle(
                color: selected ? Colors.white : const Color(0xFFB8C4D9),
                fontSize: 12,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
        ),
      );
}

class _Node {
  const _Node(this.id, this.title, this.detail, this.rect, this.color);
  final String id;
  final String title;
  final String detail;
  final Rect rect;
  final Color color;
}

class _Transition {
  const _Transition(this.from, this.to);
  final String from;
  final String to;
}

class _VisualEdge {
  const _VisualEdge({
    required this.fromStates,
    required this.points,
    this.toState,
    this.label = '',
    this.labelAt,
    this.dashed = false,
  });
  final List<String> fromStates;
  final String? toState;
  final List<Offset> points;
  final String label;
  final Offset? labelAt;
  final bool dashed;
}

// 세로 흐름을 압축해 16:9 Pi 5 화면에서 전체 보기가 폭을 충분히 쓴다.
// 노드와 라벨의 글자는 압축하지 않아 작은 배율에서도 읽을 수 있다.
const _layoutYScale = .76;
const _graphSize = Size(1860, 1125);
const _focusStates = ['FOCUS_PC', 'FOCUS_MIXED', 'FOCUS_NPC'];
const _idle = Color(0xFFE8EAED);
const _start = Color(0xFFFFE8A8);
const _focusPc = Color(0xFFCFE2FF);
const _focusMixed = Color(0xFFFFF2B8);
const _focusNpc = Color(0xFFD9F0CF);
const _decision = Color(0xFFFFD3CF);
const _analysis = Color(0xFFC8D7FF);
const _loop = Color(0xFFDDE0F6);
const _action = Color(0xFFE5D5F0);
const _recovery = Color(0xFFD2EAC8);

Rect _layoutRect(Rect rect) => Rect.fromLTWH(
      rect.left,
      rect.top * _layoutYScale,
      rect.width,
      rect.height * _layoutYScale,
    );

Offset _layoutPoint(Offset point) => Offset(point.dx, point.dy * _layoutYScale);

const _nodes = <_Node>[
  _Node(
      'IDLE',
      'IDLE  /  시스템 대기',
      'do: ToF · dual monitor standby\nout: idle screen',
      Rect.fromLTWH(750, 48, 360, 104),
      _idle),
  _Node(
      'START',
      'START  /  Baseline 측정',
      'entry: baseline timer start\ndo: capture(ToF · 호흡 · keystroke rate)',
      Rect.fromLTWH(750, 210, 360, 108),
      _start),
  _Node(
      'CONTEXT_DETECT',
      'CONTEXT_DETECT  /  작업 맥락 판정',
      'do: monitor(키스트로크 · active · PC power)\ncalc: PC ratio (15min window)',
      Rect.fromLTWH(725, 345, 410, 108),
      _start),
  _Node(
      'FOCUS_PC',
      'FOCUS_PC  /  컴퓨터 작업',
      'C_fatigue · C_focus (설정 주기)\n주력: 키스트로크  ·  보조: ToF·호흡·환경',
      Rect.fromLTWH(105, 525, 385, 126),
      _focusPc),
  _Node(
      'FOCUS_MIXED',
      'FOCUS_MIXED  /  혼용 컨텍스트',
      'calc: PC ratio → weight\nC = blend(PC, NPC)',
      Rect.fromLTWH(737, 525, 385, 126),
      _focusMixed),
  _Node(
      'FOCUS_NPC',
      'FOCUS_NPC  /  비PC 작업',
      'C_fatigue · C_focus (설정 주기)\n주력: ToF·호흡  ·  보조: 환경·시간',
      Rect.fromLTWH(1370, 525, 385, 126),
      _focusNpc),
  _Node(
      'FOCUS_BREAK',
      'FOCUS_BREAK  /  집중 저하 감지',
      'entry: focus cause · micro intervention\ndo: poll C_focus (설정 시간)',
      Rect.fromLTWH(140, 785, 370, 126),
      _focusPc),
  _Node(
      'FATIGUE_SUSPECT',
      'FATIGUE_SUSPECT  /  피로 의심',
      'entry: warning(yellow) · esc timer\ndo: poll C_fatigue (설정 주기)',
      Rect.fromLTWH(745, 785, 370, 126),
      _start),
  _Node(
      'FATIGUE',
      'FATIGUE  /  피로 확정',
      'entry: fatigue event 기록\ndo: CAUSE_ANALYSIS 결과 대기',
      Rect.fromLTWH(1350, 785, 370, 126),
      _decision),
  _Node(
      'CAUSE_ANALYSIS',
      'CAUSE_ANALYSIS  /  원인 분석',
      'dominant = argmax(wᵢ·δᵢ)\nroute: 환경 · 자세 · 인지',
      Rect.fromLTWH(120, 980, 390, 126),
      _analysis),
  _Node(
      'MONITOR',
      'MONITOR  /  효과 검증',
      'measure C_fatigue trend (설정 구간)\nupdate AI policy(reward)',
      Rect.fromLTWH(575, 1090, 370, 126),
      _loop),
  _Node(
      'ESCALATE',
      'ESCALATE  /  개입 격상',
      'remaining option → 원인 재분석\n옵션 소진 → 강제 휴식',
      Rect.fromLTWH(1035, 980, 370, 126),
      _decision),
  _Node(
      'RECOVERY',
      'RECOVERY  /  회복 판정',
      'poll C_fatigue + C_focus (설정 주기)\n회복 / 실패 판정',
      Rect.fromLTWH(1460, 1090, 330, 126),
      _recovery),
  _Node('END', 'END  /  작업 종료', 'entry: save session log\nout: display summary',
      Rect.fromLTWH(65, 1310, 285, 126), _idle),
  _Node(
      'ACTION_ENV',
      'ACTION_ENV  /  환경 조정',
      'entry: ThinQ API (조명 · 환기)\ndo: await API response',
      Rect.fromLTWH(385, 1310, 285, 126),
      _action),
  _Node(
      'ACTION_POSTURE',
      'ACTION_POSTURE  /  자세 교정',
      'entry: posture alert\ndo: await correction (설정 시간)',
      Rect.fromLTWH(705, 1310, 285, 126),
      _action),
  _Node(
      'ACTION_BREAK',
      'ACTION_BREAK  /  휴식 권유',
      'entry: display break suggestion\ndo: await touch(user decision)',
      Rect.fromLTWH(1025, 1310, 285, 126),
      _action),
  _Node(
      'REST',
      'REST  /  휴식 중',
      'entry: rest timer start\ndo: maintain env · ambient display',
      Rect.fromLTWH(1380, 1310, 335, 126),
      _recovery),
];

final _nodesById = {for (final node in _nodes) node.id: node};

const _transitions = <_Transition>[
  _Transition('IDLE', 'START'),
  _Transition('START', 'CONTEXT_DETECT'),
  _Transition('CONTEXT_DETECT', 'FOCUS_PC'),
  _Transition('CONTEXT_DETECT', 'FOCUS_MIXED'),
  _Transition('CONTEXT_DETECT', 'FOCUS_NPC'),
  _Transition('FOCUS_PC', 'FOCUS_MIXED'),
  _Transition('FOCUS_MIXED', 'FOCUS_PC'),
  _Transition('FOCUS_MIXED', 'FOCUS_NPC'),
  _Transition('FOCUS_NPC', 'FOCUS_MIXED'),
  _Transition('FOCUS_PC', 'FOCUS_BREAK'),
  _Transition('FOCUS_MIXED', 'FOCUS_BREAK'),
  _Transition('FOCUS_NPC', 'FOCUS_BREAK'),
  _Transition('FOCUS_PC', 'FATIGUE_SUSPECT'),
  _Transition('FOCUS_MIXED', 'FATIGUE_SUSPECT'),
  _Transition('FOCUS_NPC', 'FATIGUE_SUSPECT'),
  _Transition('FOCUS_BREAK', 'FATIGUE_SUSPECT'),
  _Transition('FOCUS_BREAK', 'FOCUS_MIXED'),
  _Transition('FATIGUE_SUSPECT', 'FATIGUE'),
  _Transition('FATIGUE_SUSPECT', 'FOCUS_MIXED'),
  _Transition('FATIGUE', 'CAUSE_ANALYSIS'),
  _Transition('CAUSE_ANALYSIS', 'ACTION_ENV'),
  _Transition('CAUSE_ANALYSIS', 'ACTION_POSTURE'),
  _Transition('CAUSE_ANALYSIS', 'ACTION_BREAK'),
  _Transition('ACTION_ENV', 'MONITOR'),
  _Transition('ACTION_POSTURE', 'MONITOR'),
  _Transition('ACTION_BREAK', 'MONITOR'),
  _Transition('ACTION_BREAK', 'REST'),
  _Transition('MONITOR', 'RECOVERY'),
  _Transition('MONITOR', 'ESCALATE'),
  _Transition('RECOVERY', 'FOCUS_MIXED'),
  _Transition('RECOVERY', 'ESCALATE'),
  _Transition('ESCALATE', 'CAUSE_ANALYSIS'),
  _Transition('ESCALATE', 'REST'),
  _Transition('REST', 'RECOVERY'),
  _Transition('FOCUS_PC', 'END'),
  _Transition('FOCUS_MIXED', 'END'),
  _Transition('FOCUS_NPC', 'END'),
];

final Map<String, Set<String>> _nextStates = {
  for (final node in _nodes)
    node.id: {
      for (final transition
          in _transitions.where((transition) => transition.from == node.id))
        transition.to,
    },
};

const _visualEdges = <_VisualEdge>[
  _VisualEdge(
      fromStates: ['IDLE'],
      toState: 'START',
      label: 'TouchEvent',
      labelAt: Offset(1005, 180),
      points: [Offset(930, 152), Offset(930, 210)]),
  _VisualEdge(
      fromStates: ['START'],
      toState: 'CONTEXT_DETECT',
      label: 'timer ∧ baseline_ok',
      labelAt: Offset(1030, 332),
      points: [Offset(930, 318), Offset(930, 345)]),
  _VisualEdge(
      fromStates: ['CONTEXT_DETECT'],
      toState: 'FOCUS_PC',
      label: 'PC ratio → PC',
      labelAt: Offset(535, 477),
      points: [
        Offset(805, 453),
        Offset(805, 485),
        Offset(298, 485),
        Offset(298, 525)
      ]),
  _VisualEdge(
      fromStates: ['CONTEXT_DETECT'],
      toState: 'FOCUS_MIXED',
      label: 'PC ratio → MIXED',
      labelAt: Offset(1035, 484),
      points: [Offset(930, 453), Offset(930, 525)]),
  _VisualEdge(
      fromStates: ['CONTEXT_DETECT'],
      toState: 'FOCUS_NPC',
      label: 'PC ratio → NPC',
      labelAt: Offset(1320, 477),
      points: [
        Offset(1055, 453),
        Offset(1055, 485),
        Offset(1562, 485),
        Offset(1562, 525)
      ]),
  _VisualEdge(
      fromStates: ['FOCUS_PC'],
      toState: 'FOCUS_MIXED',
      label: '재판정 → MIXED',
      labelAt: Offset(614, 548),
      points: [Offset(490, 552), Offset(737, 552)]),
  _VisualEdge(
      fromStates: ['FOCUS_MIXED'],
      toState: 'FOCUS_PC',
      label: '재판정 → PC',
      labelAt: Offset(614, 633),
      points: [Offset(737, 624), Offset(490, 624)]),
  _VisualEdge(
      fromStates: ['FOCUS_MIXED'],
      toState: 'FOCUS_NPC',
      label: '재판정 → MIXED',
      labelAt: Offset(1246, 548),
      points: [Offset(1122, 552), Offset(1370, 552)]),
  _VisualEdge(
      fromStates: ['FOCUS_NPC'],
      toState: 'FOCUS_MIXED',
      label: '재판정 → NPC',
      labelAt: Offset(1246, 633),
      points: [Offset(1370, 624), Offset(1122, 624)]),
  // 세 몰입 상태는 같은 30초 판정 게이트로 모인다.
  _VisualEdge(fromStates: [
    'FOCUS_PC'
  ], points: [
    Offset(298, 651),
    Offset(298, 684),
    Offset(855, 684),
    Offset(855, 706)
  ]),
  _VisualEdge(
      fromStates: ['FOCUS_MIXED'],
      points: [Offset(930, 651), Offset(930, 706)]),
  _VisualEdge(fromStates: [
    'FOCUS_NPC'
  ], points: [
    Offset(1562, 651),
    Offset(1562, 684),
    Offset(1005, 684),
    Offset(1005, 706)
  ]),
  _VisualEdge(
      fromStates: _focusStates,
      toState: 'FOCUS_BREAK',
      label: 'C_focus 판정',
      labelAt: Offset(575, 758),
      points: [
        Offset(870, 750),
        Offset(870, 767),
        Offset(325, 767),
        Offset(325, 785)
      ]),
  _VisualEdge(
      fromStates: _focusStates,
      toState: 'FATIGUE_SUSPECT',
      label: 'C_fatigue 판정',
      labelAt: Offset(1025, 767),
      points: [Offset(990, 750), Offset(990, 785)]),
  _VisualEdge(
      fromStates: ['FOCUS_BREAK'],
      toState: 'FATIGUE_SUSPECT',
      label: '재유도 실패',
      labelAt: Offset(627, 811),
      points: [Offset(510, 811), Offset(745, 811)]),
  _VisualEdge(
      fromStates: ['FOCUS_BREAK'],
      toState: 'FOCUS_MIXED',
      label: '재유도 성공',
      labelAt: Offset(350, 701),
      points: [
        Offset(140, 850),
        Offset(78, 850),
        Offset(78, 668),
        Offset(790, 668),
        Offset(790, 651)
      ]),
  _VisualEdge(
      fromStates: ['FATIGUE_SUSPECT'],
      toState: 'FATIGUE',
      label: '피로 확정 기준 지속',
      labelAt: Offset(1232, 811),
      points: [Offset(1115, 811), Offset(1350, 811)]),
  _VisualEdge(
      fromStates: ['FATIGUE_SUSPECT'],
      toState: 'FOCUS_MIXED',
      label: '자연 회복',
      labelAt: Offset(1060, 701),
      points: [
        Offset(1080, 785),
        Offset(1080, 668),
        Offset(1070, 668),
        Offset(1070, 651)
      ]),
  _VisualEdge(
      fromStates: ['FATIGUE'],
      toState: 'CAUSE_ANALYSIS',
      label: 'dominant term',
      labelAt: Offset(930, 956),
      points: [
        Offset(1535, 911),
        Offset(1535, 955),
        Offset(315, 955),
        Offset(315, 980)
      ]),
  _VisualEdge(
      fromStates: ['CAUSE_ANALYSIS'],
      toState: 'ACTION_ENV',
      label: '환경성',
      labelAt: Offset(528, 1265),
      points: [
        Offset(230, 1106),
        Offset(230, 1264),
        Offset(528, 1264),
        Offset(528, 1310)
      ]),
  _VisualEdge(
      fromStates: ['CAUSE_ANALYSIS'],
      toState: 'ACTION_POSTURE',
      label: '자세성',
      labelAt: Offset(832, 1278),
      points: [
        Offset(315, 1106),
        Offset(315, 1278),
        Offset(848, 1278),
        Offset(848, 1310)
      ]),
  _VisualEdge(
      fromStates: ['CAUSE_ANALYSIS'],
      toState: 'ACTION_BREAK',
      label: '인지성',
      labelAt: Offset(1160, 1292),
      points: [
        Offset(400, 1106),
        Offset(400, 1292),
        Offset(1168, 1292),
        Offset(1168, 1310)
      ]),
  _VisualEdge(
      fromStates: ['ACTION_ENV'],
      toState: 'MONITOR',
      label: '실행 완료',
      labelAt: Offset(548, 1190),
      points: [Offset(528, 1310), Offset(528, 1150), Offset(575, 1150)]),
  _VisualEdge(
      fromStates: ['ACTION_POSTURE'],
      toState: 'MONITOR',
      label: '실행 완료',
      labelAt: Offset(848, 1245),
      points: [Offset(848, 1310), Offset(848, 1216)]),
  _VisualEdge(
      fromStates: ['ACTION_BREAK'],
      toState: 'MONITOR',
      label: '거절',
      labelAt: Offset(1030, 1235),
      points: [
        Offset(1085, 1310),
        Offset(1085, 1235),
        Offset(900, 1235),
        Offset(900, 1216)
      ]),
  _VisualEdge(
      fromStates: ['ACTION_BREAK'],
      toState: 'REST',
      label: '수락',
      labelAt: Offset(1344, 1340),
      points: [Offset(1310, 1340), Offset(1380, 1340)]),
  _VisualEdge(
      fromStates: ['MONITOR'],
      toState: 'RECOVERY',
      label: 'C↓ · 개선',
      labelAt: Offset(1200, 1177),
      points: [Offset(945, 1177), Offset(1460, 1177)]),
  _VisualEdge(
      fromStates: ['MONITOR'],
      toState: 'ESCALATE',
      label: 'C↑/불변',
      labelAt: Offset(994, 1080),
      points: [
        Offset(945, 1120),
        Offset(1000, 1120),
        Offset(1000, 1043),
        Offset(1035, 1043)
      ]),
  _VisualEdge(
      fromStates: ['RECOVERY'],
      toState: 'FOCUS_MIXED',
      label: '회복 기준 충족 · 복귀',
      labelAt: Offset(1650, 775),
      dashed: true,
      points: [
        Offset(1790, 1153),
        Offset(1822, 1153),
        Offset(1822, 668),
        Offset(1090, 668),
        Offset(1090, 651)
      ]),
  _VisualEdge(
      fromStates: ['RECOVERY'],
      toState: 'ESCALATE',
      label: '회복 실패 기준 충족',
      labelAt: Offset(1305, 1194),
      dashed: true,
      points: [Offset(1460, 1195), Offset(1220, 1195), Offset(1220, 1106)]),
  _VisualEdge(
      fromStates: ['ESCALATE'],
      toState: 'CAUSE_ANALYSIS',
      label: 'remaining options',
      labelAt: Offset(773, 1005),
      points: [Offset(1035, 1008), Offset(510, 1008)]),
  _VisualEdge(
      fromStates: ['ESCALATE'],
      toState: 'REST',
      label: '옵션 소진 · 강제 REST',
      labelAt: Offset(1630, 1025),
      points: [
        Offset(1405, 1043),
        Offset(1805, 1043),
        Offset(1805, 1288),
        Offset(1548, 1288),
        Offset(1548, 1310)
      ]),
  _VisualEdge(
      fromStates: ['REST'],
      toState: 'RECOVERY',
      label: '최소 휴식 완료',
      labelAt: Offset(1670, 1260),
      dashed: true,
      points: [
        Offset(1660, 1310),
        Offset(1660, 1245),
        Offset(1625, 1245),
        Offset(1625, 1216)
      ]),
  _VisualEdge(
      fromStates: _focusStates,
      toState: 'END',
      label: '작업 종료 터치',
      labelAt: Offset(120, 1180),
      dashed: true,
      points: [
        Offset(105, 610),
        Offset(52, 610),
        Offset(52, 1285),
        Offset(208, 1285),
        Offset(208, 1310)
      ]),
];

class _FsmPainter extends CustomPainter {
  const _FsmPainter({required this.current, required this.next});
  final String current;
  final Set<String> next;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawColor(const Color(0xFFF6F8FC), BlendMode.src);
    _band(canvas, const Rect.fromLTWH(45, 25, 1770, 140), '대기',
        const Color(0xFF8B929C));
    _band(canvas, const Rect.fromLTWH(45, 180, 1770, 290), '시작',
        const Color(0xFFE4B640));
    _band(canvas, const Rect.fromLTWH(45, 485, 1770, 195), '몰입',
        const Color(0xFF6D8DE8));
    _band(canvas, const Rect.fromLTWH(45, 695, 1770, 235), '판단',
        const Color(0xFFF07C77));
    _band(canvas, const Rect.fromLTWH(45, 945, 1770, 315), '루프·회복',
        const Color(0xFF66B86B));
    _band(canvas, const Rect.fromLTWH(45, 1275, 1770, 185), '출력',
        const Color(0xFFAA6BC2));

    _idleRule(canvas);
    for (final edge in _visualEdges) {
      final active = edge.fromStates.contains(current) &&
          (edge.toState == null || next.contains(edge.toState));
      _edge(canvas, edge, active);
    }
    _decisionGateway(canvas, _focusStates.contains(current));
    for (final node in _nodes) {
      _node(canvas, node, node.id == current, next.contains(node.id));
    }
  }

  void _band(Canvas canvas, Rect rect, String label, Color color) {
    rect = _layoutRect(rect);
    final rrect = RRect.fromRectAndRadius(rect, const Radius.circular(20));
    canvas.drawRRect(rrect, Paint()..color = color.withValues(alpha: .065));
    _drawDashedRRect(canvas, rrect, color.withValues(alpha: .55));
    canvas.save();
    canvas.translate(rect.left + 24, rect.center.dy);
    canvas.rotate(-math.pi / 2);
    final text = TextPainter(
      text: TextSpan(
          text: label,
          style: TextStyle(
              color: color, fontSize: 20, fontWeight: FontWeight.w900)),
      textDirection: TextDirection.ltr,
    )..layout();
    text.paint(canvas, Offset(-text.width / 2, -text.height / 2));
    canvas.restore();
  }

  void _idleRule(Canvas canvas) {
    final rect = _layoutRect(const Rect.fromLTWH(1160, 70, 560, 58));
    final rrect = RRect.fromRectAndRadius(rect, const Radius.circular(12));
    canvas.drawRRect(rrect, Paint()..color = const Color(0xEFFFFFFF));
    _drawDashedRRect(canvas, rrect, const Color(0xFF9AA4B3));
    final text = TextPainter(
      text: const TextSpan(
        text: '※ 모든 활성 상태에서 재실 없음 기준 충족 → IDLE',
        style: TextStyle(
            color: Color(0xFF515D6F),
            fontSize: 13,
            fontWeight: FontWeight.w700),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: rect.width - 24);
    text.paint(
        canvas, Offset(rect.left + 12, rect.center.dy - text.height / 2));
  }

  void _decisionGateway(Canvas canvas, bool active) {
    final rect = _layoutRect(const Rect.fromLTWH(805, 706, 250, 44));
    final color = active ? const Color(0xFF12A88D) : const Color(0xFF6D7788);
    final rrect = RRect.fromRectAndRadius(rect, const Radius.circular(22));
    canvas.drawRRect(rrect, Paint()..color = const Color(0xFFF8FAFD));
    canvas.drawRRect(
        rrect,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = active ? 3 : 1.5);
    final text = TextPainter(
      text: TextSpan(
        text: '30초 동시 판정  C_focus · C_fatigue',
        style:
            TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w800),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: rect.width - 22);
    text.paint(
        canvas,
        Offset(
            rect.center.dx - text.width / 2, rect.center.dy - text.height / 2));
  }

  void _edge(Canvas canvas, _VisualEdge edge, bool active) {
    if (edge.points.length < 2) return;
    final points = edge.points.map(_layoutPoint).toList(growable: false);
    final color = active ? const Color(0xFF079C83) : const Color(0xFF788599);
    final paint = Paint()
      ..color = color
      ..strokeWidth = active ? 4 : 1.55
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..style = PaintingStyle.stroke;
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (final point in points.skip(1)) {
      path.lineTo(point.dx, point.dy);
    }
    edge.dashed
        ? _drawDashedPath(canvas, path, paint)
        : canvas.drawPath(path, paint);
    _arrow(canvas, points[points.length - 2], points.last, paint);
    if (edge.label.isNotEmpty) {
      final labelAt = edge.labelAt == null
          ? _longestMiddle(points)
          : _layoutPoint(edge.labelAt!);
      _edgeLabel(canvas, edge.label, labelAt, color, active);
    }
  }

  void _arrow(Canvas canvas, Offset before, Offset end, Paint paint) {
    final angle = math.atan2(end.dy - before.dy, end.dx - before.dx);
    const arrow = 10.0;
    final path = Path()
      ..moveTo(end.dx, end.dy)
      ..lineTo(end.dx - arrow * math.cos(angle - .48),
          end.dy - arrow * math.sin(angle - .48))
      ..moveTo(end.dx, end.dy)
      ..lineTo(end.dx - arrow * math.cos(angle + .48),
          end.dy - arrow * math.sin(angle + .48));
    canvas.drawPath(path, paint);
  }

  Offset _longestMiddle(List<Offset> points) {
    var longest = -1.0;
    var middle = points.first;
    for (var index = 1; index < points.length; index++) {
      final a = points[index - 1];
      final b = points[index];
      final length = (b - a).distanceSquared;
      if (length > longest) {
        longest = length;
        middle = Offset((a.dx + b.dx) / 2, (a.dy + b.dy) / 2);
      }
    }
    return middle;
  }

  void _edgeLabel(
      Canvas canvas, String label, Offset center, Color color, bool active) {
    final text = TextPainter(
      text: TextSpan(
          text: label,
          style: TextStyle(
              color: active ? const Color(0xFF087A69) : const Color(0xFF596679),
              fontSize: 10.5,
              fontWeight: active ? FontWeight.w900 : FontWeight.w700)),
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: 180);
    final rect = Rect.fromCenter(
        center: center, width: text.width + 12, height: text.height + 6);
    final rrect = RRect.fromRectAndRadius(rect, const Radius.circular(6));
    canvas.drawRRect(rrect, Paint()..color = const Color(0xF5F8FAFD));
    canvas.drawRRect(
        rrect,
        Paint()
          ..color = color.withValues(alpha: active ? .55 : .18)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1);
    text.paint(
        canvas,
        Offset(
            rect.center.dx - text.width / 2, rect.center.dy - text.height / 2));
  }

  void _node(Canvas canvas, _Node node, bool currentNode, bool nextNode) {
    final rect = _layoutRect(node.rect);
    final border = currentNode
        ? const Color(0xFFFFA800)
        : nextNode
            ? const Color(0xFF0AA58C)
            : _darken(node.color, .28);
    final rrect = RRect.fromRectAndRadius(rect, const Radius.circular(16));
    canvas.drawShadow(Path()..addRRect(rrect), const Color(0x26000000),
        currentNode ? 9 : 4, true);
    canvas.drawRRect(rrect, Paint()..color = node.color);
    canvas.drawRRect(
        rrect,
        Paint()
          ..color = border
          ..style = PaintingStyle.stroke
          ..strokeWidth = currentNode
              ? 6
              : nextNode
                  ? 4
                  : 1.8);

    final title = TextPainter(
      text: TextSpan(
          text: node.title,
          style: const TextStyle(
              color: Color(0xFF172235),
              fontSize: 15,
              fontWeight: FontWeight.w900)),
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: rect.width - 24);
    title.paint(canvas, Offset(rect.center.dx - title.width / 2, rect.top + 9));

    final dividerY = rect.top + 34;
    canvas.drawLine(
        Offset(rect.left + 22, dividerY),
        Offset(rect.right - 22, dividerY),
        Paint()
          ..color = const Color(0xFF38465A).withValues(alpha: .36)
          ..strokeWidth = 1);
    final detail = TextPainter(
      text: TextSpan(
          text: node.detail,
          style: const TextStyle(
              color: Color(0xFF354258),
              fontSize: 11.5,
              height: 1.42,
              fontWeight: FontWeight.w600)),
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
      maxLines: 3,
      ellipsis: '…',
    )..layout(maxWidth: rect.width - 24);
    detail.paint(
        canvas,
        Offset(rect.center.dx - detail.width / 2,
            dividerY + (rect.bottom - dividerY - detail.height) / 2));
  }

  Color _darken(Color color, double amount) =>
      Color.lerp(color, Colors.black, amount)!;

  void _drawDashedPath(Canvas canvas, Path path, Paint paint) {
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final end = math.min(distance + 9, metric.length);
        canvas.drawPath(metric.extractPath(distance, end), paint);
        distance = end + 6;
      }
    }
  }

  void _drawDashedRRect(Canvas canvas, RRect rect, Color color) {
    _drawDashedPath(
        canvas,
        Path()..addRRect(rect),
        Paint()
          ..color = color
          ..strokeWidth = 1.4
          ..style = PaintingStyle.stroke);
  }

  @override
  bool shouldRepaint(covariant _FsmPainter oldDelegate) =>
      oldDelegate.current != current || oldDelegate.next != next;
}
