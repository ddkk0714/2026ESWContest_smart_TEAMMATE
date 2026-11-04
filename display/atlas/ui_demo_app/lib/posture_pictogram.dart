import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'pr56/display_state.dart';

/// 집중 상세 '지금 자세' 카드의 그림과 색. 판정은 hub 가 하고 여기서는 보이는 모습만 고른다.
///
/// 키는 hub ToF 자세 클래스(home_items.dart postureKo 와 같다). 자리에 없으면 'away'.
String? postureKey(DisplayState s) =>
    s.present == false ? 'away' : s.tofPosture;

/// 자세 무리별 색: (그림 색, 카드 배경).
({Color ink, Color tile}) postureColors(String? key) => switch (key) {
      'upright' => (
          ink: const Color(0xFF356C59),
          tile: const Color(0xFFE6EBDD)
        ),
      'recline' || 'lean_left' || 'lean_right' || 'chin_rest' => (
          ink: const Color(0xFFA8741A),
          tile: const Color(0xFFF5ECD7)
        ),
      'lean_forward' || 'forward_head' || 'head_down' => (
          ink: const Color(0xFFC0583A),
          tile: const Color(0xFFF6E2DA)
        ),
      'drowsy' || 'face_down' => (
          ink: const Color(0xFF5D5F99),
          tile: const Color(0xFFE6E5F2)
        ),
      _ => (ink: const Color(0xFF7A8580), tile: const Color(0xFFEDEEEA)),
    };

class PosturePictogram extends StatelessWidget {
  const PosturePictogram(
      {super.key, required this.posture, required this.color, this.size = 48});
  final String? posture;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => CustomPaint(
      size: Size.square(size), painter: _PosturePainter(posture, color));
}

class _PosturePainter extends CustomPainter {
  _PosturePainter(this.posture, this.color);
  final String? posture;
  final Color color;

  static double _rad(double deg) => deg * math.pi / 180;

  /// 각도(세로에서 앞쪽으로 + 도) 방향으로 길이만큼.
  static Offset _go(Offset from, double deg, double length) =>
      from + Offset(math.sin(_rad(deg)), -math.cos(_rad(deg))) * length;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 48);
    final stroke = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final fill = Paint()..color = color;
    final faint = Paint()
      ..color = color.withValues(alpha: .35)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeCap = StrokeCap.round;

    switch (posture) {
      case 'lean_left' || 'lean_right':
        _front(canvas, posture == 'lean_left' ? -18 : 18, stroke, fill, faint);
        return;
      case 'away':
        _furniture(canvas, faint, chairBack: 0);
        canvas.drawCircle(const Offset(18, 13), 4.5, faint);
        return;
      case 'misplaced':
        _furniture(canvas, faint, chairBack: 0);
        _question(canvas, const Offset(32, 13), stroke, fill);
        return;
      case null:
        _furniture(canvas, faint, chairBack: 0);
        for (final x in [14.0, 20.0, 26.0]) {
          canvas.drawCircle(Offset(x, 16), 1.8, fill);
        }
        return;
    }

    // 옆에서 본 모습(오른쪽이 책상). 몸통·목 각도(세로에서 앞으로 +).
    final (torso, neck) = switch (posture) {
      'lean_forward' => (32.0, 32.0),
      'forward_head' => (6.0, 48.0),
      'head_down' => (8.0, 80.0),
      'recline' => (-24.0, -14.0),
      'face_down' => (62.0, 88.0),
      'chin_rest' => (18.0, 30.0),
      'drowsy' => (4.0, 70.0),
      _ => (0.0, 0.0), // upright
    };
    _furniture(canvas, faint, chairBack: posture == 'recline' ? -16 : 0);
    const hip = Offset(16, 33);
    final shoulder = _go(hip, torso, 14);
    final head = _go(shoulder, neck, 6.5);
    canvas.drawPath(
        Path()
          ..moveTo(31, 42)
          ..lineTo(30, 33)
          ..lineTo(hip.dx, hip.dy)
          ..lineTo(shoulder.dx, shoulder.dy),
        stroke);
    canvas.drawCircle(head, 4.6, fill);
    // 팔: 책상 위로, 턱 괴기는 팔꿈치에서 턱으로
    if (posture == 'chin_rest') {
      const elbow = Offset(33, 26);
      canvas.drawPath(
          Path()
            ..moveTo(shoulder.dx, shoulder.dy)
            ..lineTo(elbow.dx, elbow.dy)
            ..lineTo(head.dx + 2, head.dy + 4),
          stroke);
    } else {
      final hand =
          posture == 'recline' ? const Offset(28, 28) : const Offset(36, 25);
      canvas.drawLine(shoulder, hand, stroke);
    }
    if (posture == 'drowsy') {
      final thin = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round;
      _zee(canvas, const Offset(29, 9), 5, thin);
      _zee(canvas, const Offset(36, 2), 7, thin);
    }
  }

  /// 의자(왼쪽)와 책상(오른쪽). chairBack 은 등받이 기울기(도, 뒤로 -).
  void _furniture(Canvas canvas, Paint paint, {required double chairBack}) {
    canvas.drawLine(const Offset(10, 35), const Offset(25, 35), paint);
    canvas.drawLine(
        const Offset(10, 35), _go(const Offset(10, 35), chairBack, 15), paint);
    canvas.drawLine(const Offset(17, 35), const Offset(17, 44), paint);
    canvas.drawLine(const Offset(30, 26), const Offset(46, 26), paint);
    canvas.drawLine(const Offset(42, 26), const Offset(42, 44), paint);
  }

  /// 앞에서 본 모습(좌우 기울어짐). tilt 는 오른쪽이 +.
  void _front(
      Canvas canvas, double tilt, Paint stroke, Paint fill, Paint faint) {
    canvas.drawLine(const Offset(8, 42), const Offset(40, 42), faint); // 바닥
    canvas.drawRRect(
        RRect.fromRectAndRadius(
            const Rect.fromLTWH(14, 14, 20, 24), const Radius.circular(4)),
        faint); // 등받이
    const hip = Offset(24, 36);
    final shoulder = _go(hip, tilt, 15);
    final head = _go(shoulder, tilt * 1.3, 7);
    canvas.drawLine(hip, shoulder, stroke);
    final across = Offset(math.cos(_rad(tilt)), math.sin(_rad(tilt))) * 7;
    canvas.drawLine(shoulder - across, shoulder + across, stroke);
    canvas.drawCircle(head, 4.6, fill);
    canvas.drawLine(hip - const Offset(5, 0), hip + const Offset(5, 0), stroke);
  }

  /// 졸음 표시 'z' 를 선으로(글꼴에 기대지 않는다).
  void _zee(Canvas canvas, Offset at, double size, Paint paint) =>
      canvas.drawPath(
          Path()
            ..moveTo(at.dx, at.dy)
            ..lineTo(at.dx + size, at.dy)
            ..lineTo(at.dx, at.dy + size)
            ..lineTo(at.dx + size, at.dy + size),
          paint);

  /// 물음표를 선으로.
  void _question(Canvas canvas, Offset c, Paint stroke, Paint fill) {
    canvas.drawPath(
        Path()
          ..addArc(
              Rect.fromCircle(center: c, radius: 5), math.pi, math.pi * 1.5)
          ..lineTo(c.dx, c.dy + 8),
        stroke);
    canvas.drawCircle(Offset(c.dx, c.dy + 13), 1.9, fill);
  }

  @override
  bool shouldRepaint(_PosturePainter old) =>
      old.posture != posture || old.color != color;
}
