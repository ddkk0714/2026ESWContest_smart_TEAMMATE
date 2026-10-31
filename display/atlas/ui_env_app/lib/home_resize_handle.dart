import 'package:flutter/material.dart';

/// Corner grip: resize directly during the drag, without opening a size menu.
class HomeResizeHandle extends StatefulWidget {
  const HomeResizeHandle(
      {super.key,
      required this.label,
      required this.span,
      required this.onChanged});
  final String label;
  final int span;
  final ValueChanged<int> onChanged;
  @override
  State<HomeResizeHandle> createState() => _HomeResizeHandleState();
}

class _HomeResizeHandleState extends State<HomeResizeHandle> {
  double _dx = 0;
  bool _dragging = false;
  @override
  Widget build(BuildContext context) {
    final ink = Theme.of(context).colorScheme.primary;
    return Semantics(
      label: '${widget.label} 크기, ${widget.span}칸. 모서리를 끌어 조절',
      onIncrease: widget.span == 1 ? () => widget.onChanged(2) : null,
      onDecrease: widget.span == 2 ? () => widget.onChanged(1) : null,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanStart: (_) => setState(() {
          _dx = 0;
          _dragging = true;
        }),
        onPanUpdate: (details) {
          _dx += details.delta.dx + details.delta.dy * .5;
          if (_dx > 38 && widget.span == 1) {
            widget.onChanged(2);
            _dx = 0;
          } else if (_dx < -38 && widget.span == 2) {
            widget.onChanged(1);
            _dx = 0;
          }
        },
        onPanEnd: (_) => setState(() => _dragging = false),
        onPanCancel: () => setState(() => _dragging = false),
        child: AnimatedScale(
          scale: _dragging ? 1.12 : 1,
          duration: const Duration(milliseconds: 180),
          child: SizedBox(
              width: 44,
              height: 44,
              child: CustomPaint(painter: _CornerGrip(ink))),
        ),
      ),
    );
  }
}

class _CornerGrip extends CustomPainter {
  const _CornerGrip(this.color);
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final line = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4
      ..strokeCap = StrokeCap.round;
    canvas.drawPath(
        Path()
          ..moveTo(17, 33)
          ..lineTo(25, 33)
          ..quadraticBezierTo(33, 33, 33, 25)
          ..lineTo(33, 17),
        line);
  }

  @override
  bool shouldRepaint(_CornerGrip oldDelegate) => color != oldDelegate.color;
}
