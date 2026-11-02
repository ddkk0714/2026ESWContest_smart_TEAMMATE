import 'dart:math' as math;

import 'package:flutter/material.dart';

/// 장치 위치 질문의 답: 위에서 내려다본 책상 그림 + 1 왼쪽 · 2 가운데 · 3 오른쪽.
///
/// 양옆 자리는 모니터 양 끝 가까이, 앉은 사람 정면에서 약 40°(±50° 이내) 쪽에 그린다.
/// Pi 4 자세 서비스가 위치별 모델을 고르는 기준과 같은 세 칸이다.
class PlacementChoice extends StatelessWidget {
  const PlacementChoice(
      {super.key,
      required this.value,
      required this.onChanged,
      this.compact = false});
  final String value;
  final ValueChanged<String> onChanged;
  final bool compact;

  static const options = [
    ('left', '왼쪽'),
    ('center', '가운데'),
    ('right', '오른쪽'),
  ];

  @override
  Widget build(BuildContext context) => Column(
          key: const ValueKey('placement-choice'),
          mainAxisSize: MainAxisSize.min,
          children: [
            GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapUp: (details) {
                  final width = compact ? 300.0 : 320.0;
                  final x = details.localPosition.dx / width;
                  onChanged(x < .36
                      ? 'left'
                      : x > .64
                          ? 'right'
                          : 'center');
                },
                child: Semantics(
                    label: '책상을 위에서 본 그림, 장치 ${options.firstWhere((o) => o.$1 == value, orElse: () => options[1]).$2}',
                    child: CustomPaint(
                        key: const ValueKey('placement-sketch'),
                        size: compact ? const Size(300, 130) : const Size(320, 140),
                        painter: DeskSketchPainter(value)))),
            const SizedBox(height: 10),
            Wrap(spacing: 12, runSpacing: 8, alignment: WrapAlignment.center, children: [
              for (final (index, option) in options.indexed)
                ChoiceChip(
                  key: ValueKey('placement-${option.$1}'),
                  label: Text('${index + 1} ${option.$2}',
                      style: TextStyle(fontSize: compact ? 16 : 20)),
                  padding: compact
                      ? const EdgeInsets.symmetric(horizontal: 8, vertical: 6)
                      : const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                  selected: value == option.$1,
                  selectedColor: const Color(0xFFE0E9D9),
                  onSelected: (_) => onChanged(option.$1),
                ),
            ]),
          ]);
}

class DeskSketchPainter extends CustomPainter {
  DeskSketchPainter(this.selected);
  final String selected;

  static const green = Color(0xFF356C59);
  static const ink = Color(0xFF263B37);
  static const line = Color(0xFFD5DFCF);

  /// 장치 자리(가로 비율, 세로 비율). 사람 머리(0.5, 0.92)에서 본 각도: 양옆 약 40°.
  static Offset spot(String placement, Size size) {
    final person = Offset(size.width * .5, size.height * .92);
    const y = .30;
    final dy = person.dy - size.height * y;
    final dx = dy * math.tan(40 * math.pi / 180);
    return switch (placement) {
      'left' => Offset(person.dx - dx, size.height * y),
      'right' => Offset(person.dx + dx, size.height * y),
      _ => Offset(person.dx, size.height * (y + .06)),
    };
  }

  @override
  void paint(Canvas canvas, Size size) {
    final desk = RRect.fromRectAndRadius(
        Rect.fromLTWH(4, 4, size.width - 8, size.height * .78),
        const Radius.circular(18));
    canvas.drawRRect(desk, Paint()..color = const Color(0xFFFFFEF8));
    canvas.drawRRect(
        desk,
        Paint()
          ..color = line
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2);
    // 모니터: 책상 안쪽 가운데의 긴 막대
    final monitor = RRect.fromRectAndRadius(
        Rect.fromCenter(
            center: Offset(size.width * .5, size.height * .16),
            width: size.width * .40,
            height: size.height * .07),
        const Radius.circular(4));
    canvas.drawRRect(monitor, Paint()..color = ink.withValues(alpha: .55));
    // 사람: 어깨와 머리(위에서 본 모습)
    final person = Offset(size.width * .5, size.height * .92);
    canvas.drawOval(
        Rect.fromCenter(
            center: person.translate(0, size.height * .06),
            width: size.width * .28,
            height: size.height * .16),
        Paint()..color = green.withValues(alpha: .18));
    canvas.drawCircle(person, size.height * .075,
        Paint()..color = green.withValues(alpha: .45));
    for (final placement in ['left', 'center', 'right']) {
      final center = spot(placement, size);
      final chosen = placement == selected;
      if (chosen) {
        canvas.drawLine(
            person,
            center,
            Paint()
              ..color = green.withValues(alpha: .35)
              ..strokeWidth = 2);
      }
      final box = RRect.fromRectAndRadius(
          Rect.fromCenter(center: center, width: 26, height: 20),
          const Radius.circular(6));
      canvas.drawRRect(
          box,
          Paint()
            ..color = chosen ? green : Colors.transparent
            ..style = PaintingStyle.fill);
      canvas.drawRRect(
          box,
          Paint()
            ..color = chosen ? green : ink.withValues(alpha: .25)
            ..style = PaintingStyle.stroke
            ..strokeWidth = chosen ? 2 : 1.4);
    }
  }

  @override
  bool shouldRepaint(DeskSketchPainter oldDelegate) =>
      oldDelegate.selected != selected;
}
