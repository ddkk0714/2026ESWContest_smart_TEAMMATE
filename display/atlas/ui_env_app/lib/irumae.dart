import 'dart:math' as math;
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

export 'irumae_motion.dart';
import 'irumae_motion.dart';

/// A continuous paint-only animation; the surrounding UI does not rebuild per frame.
class IrumaeAnimation extends StatefulWidget {
  const IrumaeAnimation({super.key, required this.mood});
  final IrumaeMood mood;
  @override
  State<IrumaeAnimation> createState() => _IrumaeAnimationState();
}

class _IrumaeAnimationState extends State<IrumaeAnimation>
    with TickerProviderStateMixin {
  static Future<ui.Image>? _atlas;
  ui.Image? _image;
  late final AnimationController _loop;
  late final AnimationController _transition;
  IrumaeMood? _previousMood;
  double _previousPhase = 0;
  bool _reducedMotion = true;

  @override
  void initState() {
    super.initState();
    _loop = AnimationController(
        vsync: this, duration: irumaeCycleDurations[widget.mood]);
    _transition = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 650), value: 1);
    _atlas ??= _load();
    _atlas!.then((image) {
      if (mounted) setState(() => _image = image);
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reducedMotion =
        MediaQuery.disableAnimationsOf(context) || !TickerMode.of(context);
    if (_reducedMotion) {
      _loop.stop(canceled: false);
      _transition.stop(canceled: false);
      _transition.value = 1;
    } else if (!_loop.isAnimating) {
      _loop.repeat();
    }
  }

  @override
  void didUpdateWidget(IrumaeAnimation oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.mood == widget.mood) return;
    _previousMood = oldWidget.mood;
    _previousPhase = _loop.value;
    _loop.duration = irumaeCycleDurations[widget.mood];
    _loop.value = 0;
    if (!_reducedMotion) {
      _loop.repeat();
      _transition.forward(from: 0);
    }
  }

  static Future<ui.Image> _load() async {
    final data = await rootBundle.load('assets/mascot/irumae-atlas-soft.png');
    final codec = await ui.instantiateImageCodec(data.buffer.asUint8List());
    final frame = await codec.getNextFrame();
    codec.dispose();
    return frame.image;
  }

  @override
  void dispose() {
    _loop.dispose();
    _transition.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Semantics(
        label: '이루매 ${irumaeLabels[widget.mood]}',
        image: true,
        child: RepaintBoundary(
            child: SizedBox.expand(
                child: CustomPaint(
          key: ValueKey('irumae-${widget.mood.name}'),
          painter: _SpritePainter(_image, widget.mood, _previousMood,
              _previousPhase, _loop, _transition, _reducedMotion),
        ))),
      );
}

class _SpritePainter extends CustomPainter {
  _SpritePainter(this.image, this.mood, this.previousMood, this.previousPhase,
      this.loop, this.transition, this.reducedMotion)
      : super(repaint: Listenable.merge([loop, transition]));
  final ui.Image? image;
  final IrumaeMood mood;
  final IrumaeMood? previousMood;
  final double previousPhase;
  final Animation<double> loop;
  final Animation<double> transition;
  final bool reducedMotion;
  static const edges = [0.0, 240.0, 475.0, 700.0, 900.0, 1122.0];

  @override
  void paint(Canvas canvas, Size size) {
    if (image == null) return;
    final t =
        reducedMotion ? 1.0 : Curves.easeInOutCubic.transform(transition.value);
    if (previousMood != null && t < 1) {
      // Add premultiplied images inside a layer, avoiding opacity dips on overlap.
      canvas.saveLayer(Offset.zero & size, Paint());
      _paintMood(canvas, size, previousMood!, previousPhase, 1 - t);
      _paintMood(canvas, size, mood, loop.value, t, additive: true);
      canvas.restore();
    } else {
      _paintMood(canvas, size, mood, loop.value, 1);
    }
  }

  void _paintMood(Canvas canvas, Size size, IrumaeMood current, double phase,
      double opacity,
      {bool additive = false}) {
    final atlas = image!;
    final row = current.index;
    final sx = atlas.width / 4;
    final top = edges[row] / 1122 * atlas.height;
    final h = (edges[row + 1] - edges[row]) / 1122 * atlas.height;
    final scale = math.min(size.width / sx, size.height / h);
    final dest = Rect.fromCenter(
        center: size.center(Offset.zero), width: sx * scale, height: h * scale);
    final pose = irumaePoseAt(current, phase, reducedMotion: reducedMotion);
    final breath =
        reducedMotion ? 0.0 : (1 - math.cos(phase * math.pi * 2)) / 2;
    canvas.saveLayer(
        Offset.zero & size,
        Paint()
          ..color = Colors.white.withValues(alpha: opacity)
          ..blendMode = additive ? BlendMode.plus : BlendMode.srcOver);
    if (current == IrumaeMood.working) _paintFlame(canvas, dest, breath);
    // A single crisp pose moves continuously, without double faces or hands.
    canvas.save();
    canvas.translate(
        dest.center.dx, dest.bottom + dest.height * pose.dyFraction);
    canvas.rotate(pose.angle);
    canvas.scale(1, pose.scaleY);
    canvas.translate(-dest.center.dx, -dest.bottom);
    canvas.drawImageRect(atlas, Rect.fromLTWH(sx * pose.frame, top, sx, h),
        dest, Paint()..filterQuality = FilterQuality.medium);
    canvas.restore();
    canvas.restore();
  }

  void _paintFlame(Canvas canvas, Rect dest, double breath) {
    final width = dest.width * (.62 + breath * .012);
    final height = dest.height * (.77 + breath * .012);
    canvas.save();
    canvas.translate(
        dest.center.dx - width / 2, dest.top + dest.height * .89 - height);
    canvas.scale(width, height);
    final flame = Path()
      ..moveTo(.12, 1)
      ..cubicTo(-.02, .84, .01, .71, .13, .58)
      ..quadraticBezierTo(.10, .72, .24, .72)
      ..cubicTo(.14, .46, .42, .36, .39, .13)
      ..quadraticBezierTo(.53, .26, .52, .37)
      ..cubicTo(.68, .23, .62, .12, .63, 0)
      ..cubicTo(.83, .23, .73, .34, .85, .51)
      ..quadraticBezierTo(.91, .42, .88, .35)
      ..cubicTo(1.06, .61, 1.04, .83, .88, 1)
      ..close();
    canvas.drawPath(
        flame, Paint()..color = const Color(0xffe9a06c).withValues(alpha: .20));
    canvas.restore();
  }

  @override
  bool shouldRepaint(_SpritePainter oldDelegate) =>
      image != oldDelegate.image ||
      mood != oldDelegate.mood ||
      previousMood != oldDelegate.previousMood ||
      previousPhase != oldDelegate.previousPhase ||
      reducedMotion != oldDelegate.reducedMotion;
}
