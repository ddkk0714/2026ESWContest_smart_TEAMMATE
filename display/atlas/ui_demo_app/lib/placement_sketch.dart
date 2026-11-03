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
                  final size =
                      compact ? const Size(300, 130) : const Size(320, 140);
                  final x = details.localPosition.dx;
                  // 누른 곳과 가로로 가장 가까운 자리
                  onChanged(options
                      .map((o) => o.$1)
                      .reduce((a, b) =>
                          (DeskSketchPainter.spot(a, size).dx - x).abs() <=
                                  (DeskSketchPainter.spot(b, size).dx - x).abs()
                              ? a
                              : b));
                },
                child: Semantics(
                    label: '책상을 위에서 본 그림, 장치 ${options.firstWhere((o) => o.$1 == value, orElse: () => options[1]).$2}',
                    // 고르면 기기가 같은 거리의 호를 따라 새 자리로 미끄러진다
                    child: TweenAnimationBuilder<double>(
                        tween: Tween(end: DeskSketchPainter.angleOf(value)),
                        duration: const Duration(milliseconds: 380),
                        curve: Curves.easeOutCubic,
                        builder: (context, angle, _) => CustomPaint(
                            key: const ValueKey('placement-sketch'),
                            size: compact
                                ? const Size(300, 130)
                                : const Size(320, 140),
                            painter: DeskSketchPainter(value, angle: angle))))),
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
  /// [angle] 은 기기가 그려질 각도(도). 고를 때 호를 따라 움직이게 애니메이션 값을 넣는다.
  DeskSketchPainter(this.selected, {double? angle})
      : angle = angle ?? angleOf(selected);
  final String selected;
  final double angle;

  static const green = Color(0xFF356C59);
  static const ink = Color(0xFF263B37);
  static const line = Color(0xFFD5DFCF);

  /// 사람 머리에서 세 자리까지의 거리(높이 비율). 세 자리 모두 같은 원 위에 있다.
  static const radius = .54;

  static double angleOf(String placement) => switch (placement) {
        'left' => -40.0,
        'right' => 40.0,
        _ => 0.0,
      };

  /// 사람 머리 위치(위에서 본 그림). 세 자리는 여기서 같은 거리다.
  static Offset person(Size size) =>
      Offset(size.width * .5, size.height * .86);

  /// 사람 머리를 중심으로 [degrees] 방향, 같은 거리의 점.
  static Offset spotAt(double degrees, Size size) {
    final r = size.height * radius;
    final a = degrees * math.pi / 180;
    return person(size) + Offset(r * math.sin(a), -r * math.cos(a));
  }

  /// 장치 자리. 사람 머리(0.5, 0.86)를 중심으로 같은 거리, 정면 0° · 양옆 40°.
  static Offset spot(String placement, Size size) =>
      spotAt(angleOf(placement), size);

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height;
    canvas.clipRect(Offset.zero & size);
    final head = person(size);
    final outline = Paint()
      ..color = line
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;

    // 책상: 앱 카드와 같은 크림 면 + 얇은 테두리
    final deskRect = Rect.fromLTWH(4, 4, w - 8, h * .74);
    final desk = RRect.fromRectAndRadius(deskRect, const Radius.circular(20));
    canvas.drawRRect(desk, Paint()..color = const Color(0xFFFFFEF8));
    canvas.drawRRect(desk, outline);

    // 모니터와 키보드: 회색 윤곽만
    canvas.drawRRect(
        RRect.fromRectAndRadius(
            Rect.fromCenter(
                center: Offset(w * .5, h * .11),
                width: w * .40,
                height: h * .045),
            const Radius.circular(3)),
        Paint()..color = ink.withValues(alpha: .55));
    canvas.drawRRect(
        RRect.fromRectAndRadius(
            Rect.fromCenter(
                center: Offset(w * .5, h * .58),
                width: w * .22,
                height: h * .09),
            const Radius.circular(4)),
        outline);

    // 같은 거리의 점선 호
    final r = h * radius;
    final arcPaint = Paint()
      ..color = line
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..strokeCap = StrokeCap.round;
    for (double d = -54; d < 54; d += 6) {
      canvas.drawArc(Rect.fromCircle(center: head, radius: r),
          (d - 90) * math.pi / 180, 2.6 * math.pi / 180, false, arcPaint);
    }

    // 고른 자리에서 사람 쪽으로 옅은 감지 범위
    final device = spotAt(angle, size);
    final toPerson = head - device;
    final facing = math.atan2(toPerson.dy, toPerson.dx);
    const spread = 18 * math.pi / 180;
    canvas.drawPath(
        Path()
          ..moveTo(device.dx, device.dy)
          ..arcTo(Rect.fromCircle(center: device, radius: toPerson.distance),
              facing - spread, spread * 2, false)
          ..close(),
        Paint()..color = green.withValues(alpha: .08));

    // 사람: 어깨와 머리, 앱 초록의 옅은 톤
    canvas.drawOval(
        Rect.fromCenter(
            center: head.translate(0, h * .10),
            width: w * .26,
            height: h * .17),
        Paint()..color = const Color(0xFFE0E9D9));
    canvas.drawCircle(
        head, h * .075, Paint()..color = green.withValues(alpha: .55));

    // 세 자리: 빈 칸(고른 자리는 기기가 덮는다)
    for (final placement in ['left', 'center', 'right']) {
      final c = spot(placement, size);
      canvas.save();
      canvas.translate(c.dx, c.dy);
      canvas.rotate(angleOf(placement) * math.pi / 180);
      final slot = RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset.zero, width: 26, height: 18),
          const Radius.circular(6));
      canvas.drawRRect(slot, Paint()..color = const Color(0xFFF4F2EB));
      canvas.drawRRect(slot, outline);
      canvas.restore();
    }

    // 기기: 사람을 향한 초록 칩 + ToF 두 개
    canvas.save();
    canvas.translate(device.dx, device.dy);
    canvas.rotate(angle * math.pi / 180);
    canvas.drawRRect(
        RRect.fromRectAndRadius(
            Rect.fromCenter(center: Offset.zero, width: 30, height: 20),
            const Radius.circular(7)),
        Paint()..color = green);
    final eye = Paint()..color = const Color(0xFFE0E9D9);
    canvas.drawCircle(const Offset(-6, 5), 2.2, eye);
    canvas.drawCircle(const Offset(6, 5), 2.2, eye);
    canvas.restore();
  }

  @override
  bool shouldRepaint(DeskSketchPainter oldDelegate) =>
      oldDelegate.selected != selected || oldDelegate.angle != angle;
}
