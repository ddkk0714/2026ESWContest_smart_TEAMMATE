import 'dart:math' as math;

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
              WidgetsBinding.instance.addPostFrameCallback((_) {
                if (mounted) _controller.value = _fitMatrix(viewport);
              });
            }
            return ClipRRect(
              borderRadius: BorderRadius.circular(22),
              child: ColoredBox(
                color: const Color(0xFFF6F8FC),
                child: InteractiveViewer(
                  transformationController: _controller,
                  constrained: false,
                  minScale: .25,
                  maxScale: 2.2,
                  boundaryMargin: const EdgeInsets.all(160),
                  child: CustomPaint(
                    key: const ValueKey('fsm-full-graph'),
                    size: const Size(1600, 1060),
                    painter:
                        _FsmPainter(current: widget.currentState, next: next),
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
            const SizedBox(width: 14),
            const Text('노랑: 현재  ·  민트: 다음 가능  ·  재실 없음 10분: IDLE',
                style: TextStyle(fontSize: 12, color: Color(0xFFB8C4D9))),
          ]),
        ),
      ),
      Positioned(
        right: 16,
        top: 14,
        child: IconButton.filledTonal(
          tooltip: '확대/이동 초기화',
          onPressed: () {
            final viewport = _viewport;
            if (viewport != null) _controller.value = _fitMatrix(viewport);
          },
          icon: const Icon(Icons.center_focus_weak),
        ),
      ),
    ]);
  }

  Matrix4 _fitMatrix(Size viewport) {
    const graph = Size(1600, 1060);
    final scale =
        math.min(viewport.width / graph.width, viewport.height / graph.height) *
            .96;
    final dx = (viewport.width - graph.width * scale) / 2;
    final dy = (viewport.height - graph.height * scale) / 2;
    return Matrix4.identity()
      ..translate(dx, dy)
      ..scale(scale);
  }
}

class _Node {
  const _Node(this.id, this.label, this.rect, this.color);
  final String id;
  final String label;
  final Rect rect;
  final Color color;
}

class _Edge {
  const _Edge(this.from, this.to, [this.label = '']);
  final String from;
  final String to;
  final String label;
}

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

final _nodes = <_Node>[
  _Node('IDLE', 'IDLE\n시스템 대기', const Rect.fromLTWH(650, 55, 300, 78), _idle),
  _Node('START', 'START\nBaseline 측정', const Rect.fromLTWH(650, 205, 300, 78),
      _start),
  _Node('CONTEXT_DETECT', 'CONTEXT_DETECT\n작업 맥락 판정',
      const Rect.fromLTWH(650, 315, 300, 78), _start),
  _Node('FOCUS_PC', 'FOCUS_PC\n컴퓨터 작업', const Rect.fromLTWH(190, 465, 300, 82),
      _focusPc),
  _Node('FOCUS_MIXED', 'FOCUS_MIXED\n혼용 컨텍스트',
      const Rect.fromLTWH(650, 465, 300, 82), _focusMixed),
  _Node('FOCUS_NPC', 'FOCUS_NPC\n비PC 작업',
      const Rect.fromLTWH(1110, 465, 300, 82), _focusNpc),
  _Node('FOCUS_BREAK', 'FOCUS_BREAK\n집중 저하 감지',
      const Rect.fromLTWH(135, 625, 310, 82), _focusPc),
  _Node('FATIGUE_SUSPECT', 'FATIGUE_SUSPECT\n피로 의심',
      const Rect.fromLTWH(495, 625, 310, 82), _start),
  _Node('FATIGUE', 'FATIGUE\n피로 확정', const Rect.fromLTWH(855, 625, 310, 82),
      _decision),
  _Node('CAUSE_ANALYSIS', 'CAUSE_ANALYSIS\n원인 분석',
      const Rect.fromLTWH(1215, 625, 310, 82), _analysis),
  _Node('ACTION_ENV', 'ACTION_ENV\n환경 조정',
      const Rect.fromLTWH(160, 835, 270, 82), _action),
  _Node('ACTION_POSTURE', 'ACTION_POSTURE\n자세 교정',
      const Rect.fromLTWH(485, 835, 270, 82), _action),
  _Node('ACTION_BREAK', 'ACTION_BREAK\n휴식 권유',
      const Rect.fromLTWH(810, 835, 270, 82), _action),
  _Node('MONITOR', 'MONITOR\n개입 효과 검증', const Rect.fromLTWH(1125, 790, 270, 82),
      _loop),
  _Node('ESCALATE', 'ESCALATE\n개입 격상', const Rect.fromLTWH(1125, 935, 270, 82),
      _decision),
  _Node(
      'REST', 'REST\n휴식 중', const Rect.fromLTWH(810, 955, 270, 72), _recovery),
  _Node('RECOVERY', 'RECOVERY\n회복 판정', const Rect.fromLTWH(485, 955, 270, 72),
      _recovery),
  _Node('END', 'END\n작업 종료', const Rect.fromLTWH(160, 955, 270, 72), _idle),
];

const _edges = <_Edge>[
  _Edge('IDLE', 'START', '터치'),
  _Edge('START', 'CONTEXT_DETECT', 'baseline ok'),
  _Edge('CONTEXT_DETECT', 'FOCUS_PC', 'PC > 70%'),
  _Edge('CONTEXT_DETECT', 'FOCUS_MIXED', '30~70%'),
  _Edge('CONTEXT_DETECT', 'FOCUS_NPC', 'PC < 30%'),
  _Edge('FOCUS_PC', 'FOCUS_MIXED'),
  _Edge('FOCUS_MIXED', 'FOCUS_PC'),
  _Edge('FOCUS_MIXED', 'FOCUS_NPC'),
  _Edge('FOCUS_NPC', 'FOCUS_MIXED'),
  _Edge('FOCUS_PC', 'FOCUS_BREAK', 'C_focus ↑'),
  _Edge('FOCUS_MIXED', 'FOCUS_BREAK', 'C_focus ↑'),
  _Edge('FOCUS_NPC', 'FOCUS_BREAK', 'C_focus ↑'),
  _Edge('FOCUS_PC', 'FATIGUE_SUSPECT', 'C_fatigue ↑'),
  _Edge('FOCUS_MIXED', 'FATIGUE_SUSPECT', 'C_fatigue ↑'),
  _Edge('FOCUS_NPC', 'FATIGUE_SUSPECT', 'C_fatigue ↑'),
  _Edge('FOCUS_BREAK', 'FATIGUE_SUSPECT', '재유도 실패'),
  _Edge('FOCUS_BREAK', 'FOCUS_MIXED', '재유도 성공'),
  _Edge('FATIGUE_SUSPECT', 'FATIGUE', '3분 지속'),
  _Edge('FATIGUE_SUSPECT', 'FOCUS_MIXED', '자연 회복'),
  _Edge('FATIGUE', 'CAUSE_ANALYSIS'),
  _Edge('CAUSE_ANALYSIS', 'ACTION_ENV', '환경성'),
  _Edge('CAUSE_ANALYSIS', 'ACTION_POSTURE', '자세성'),
  _Edge('CAUSE_ANALYSIS', 'ACTION_BREAK', '인지성'),
  _Edge('ACTION_ENV', 'MONITOR', '실행 완료'),
  _Edge('ACTION_POSTURE', 'MONITOR', '실행 완료'),
  _Edge('ACTION_BREAK', 'MONITOR', '거절'),
  _Edge('ACTION_BREAK', 'REST', '수락'),
  _Edge('MONITOR', 'RECOVERY', '개선'),
  _Edge('MONITOR', 'ESCALATE', '불변/악화'),
  _Edge('RECOVERY', 'FOCUS_MIXED', '회복'),
  _Edge('RECOVERY', 'ESCALATE', '실패'),
  _Edge('ESCALATE', 'CAUSE_ANALYSIS', '다른 개입'),
  _Edge('ESCALATE', 'REST', '옵션 소진'),
  _Edge('REST', 'RECOVERY', '최소 휴식'),
  _Edge('FOCUS_PC', 'END', '종료 터치'),
  _Edge('FOCUS_MIXED', 'END', '종료 터치'),
  _Edge('FOCUS_NPC', 'END', '종료 터치'),
];

final Map<String, Set<String>> _nextStates = {
  for (final node in _nodes)
    node.id: {
      for (final edge in _edges.where((edge) => edge.from == node.id)) edge.to
    },
};

class _FsmPainter extends CustomPainter {
  const _FsmPainter({required this.current, required this.next});

  final String current;
  final Set<String> next;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawColor(const Color(0xFFF6F8FC), BlendMode.src);
    _band(canvas, const Rect.fromLTWH(55, 25, 1490, 135), '대기',
        const Color(0xFF8B929C));
    _band(canvas, const Rect.fromLTWH(55, 175, 1490, 245), '시작',
        const Color(0xFFE4B640));
    _band(canvas, const Rect.fromLTWH(55, 440, 1490, 130), '몰입',
        const Color(0xFF6D8DE8));
    _band(canvas, const Rect.fromLTWH(55, 590, 1490, 145), '판단',
        const Color(0xFFF07C77));
    _band(canvas, const Rect.fromLTWH(55, 750, 1490, 285), '루프·회복 / 출력',
        const Color(0xFF66B86B));

    final byId = {for (final node in _nodes) node.id: node};
    for (final edge in _edges) {
      final active = edge.from == current && next.contains(edge.to);
      _edge(canvas, byId[edge.from]!.rect, byId[edge.to]!.rect, edge.label,
          active);
    }
    for (final node in _nodes) {
      _node(canvas, node, node.id == current, next.contains(node.id));
    }
  }

  void _band(Canvas canvas, Rect rect, String label, Color color) {
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(20)),
      Paint()..color = color.withValues(alpha: .065),
    );
    final text = TextPainter(
      text: TextSpan(
          text: label,
          style: TextStyle(
              color: color, fontSize: 19, fontWeight: FontWeight.w800)),
      textDirection: TextDirection.ltr,
    )..layout();
    text.paint(canvas, Offset(rect.left + 18, rect.top + 12));
  }

  void _edge(Canvas canvas, Rect from, Rect to, String label, bool active) {
    final start = _borderPoint(from, to.center);
    final end = _borderPoint(to, from.center);
    final color = active ? const Color(0xFF12A88D) : const Color(0xFF8792A3);
    final paint = Paint()
      ..color = color
      ..strokeWidth = active ? 4 : 1.6
      ..style = PaintingStyle.stroke;
    canvas.drawLine(start, end, paint);
    final angle = math.atan2(end.dy - start.dy, end.dx - start.dx);
    const arrow = 10.0;
    final path = Path()
      ..moveTo(end.dx, end.dy)
      ..lineTo(end.dx - arrow * math.cos(angle - .45),
          end.dy - arrow * math.sin(angle - .45))
      ..moveTo(end.dx, end.dy)
      ..lineTo(end.dx - arrow * math.cos(angle + .45),
          end.dy - arrow * math.sin(angle + .45));
    canvas.drawPath(path, paint);
    if (label.isNotEmpty) {
      final text = TextPainter(
        text: TextSpan(
            text: label,
            style: TextStyle(
                color: color,
                fontSize: 10,
                fontWeight: active ? FontWeight.w800 : FontWeight.w500)),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: 110);
      final middle = Offset((start.dx + end.dx) / 2, (start.dy + end.dy) / 2);
      text.paint(canvas, middle + const Offset(5, -13));
    }
  }

  Offset _borderPoint(Rect rect, Offset toward) {
    final center = rect.center;
    final dx = toward.dx - center.dx;
    final dy = toward.dy - center.dy;
    if (dx == 0 && dy == 0) return center;
    final scale = math.min(
      dx == 0 ? double.infinity : rect.width / 2 / dx.abs(),
      dy == 0 ? double.infinity : rect.height / 2 / dy.abs(),
    );
    return Offset(center.dx + dx * scale, center.dy + dy * scale);
  }

  void _node(Canvas canvas, _Node node, bool currentNode, bool nextNode) {
    final border = currentNode
        ? const Color(0xFFFFB000)
        : nextNode
            ? const Color(0xFF12A88D)
            : node.color.withValues(alpha: .95);
    canvas.drawRRect(
      RRect.fromRectAndRadius(node.rect, const Radius.circular(16)),
      Paint()..color = node.color,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(node.rect, const Radius.circular(16)),
      Paint()
        ..color = border
        ..style = PaintingStyle.stroke
        ..strokeWidth = currentNode
            ? 6
            : nextNode
                ? 4
                : 2,
    );
    final text = TextPainter(
      text: TextSpan(
        text: node.label,
        style: const TextStyle(
            color: Color(0xFF162033),
            fontSize: 15,
            height: 1.35,
            fontWeight: FontWeight.w800),
      ),
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: node.rect.width - 20);
    text.paint(
        canvas,
        Offset(node.rect.center.dx - text.width / 2,
            node.rect.center.dy - text.height / 2));
  }

  @override
  bool shouldRepaint(covariant _FsmPainter oldDelegate) =>
      oldDelegate.current != current || oldDelegate.next != next;
}
