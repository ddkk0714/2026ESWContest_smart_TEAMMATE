import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'weather_snapshot.dart';

class WeatherSky extends StatelessWidget {
  const WeatherSky(
      {super.key,
      required this.kind,
      required this.isNight,
      this.dark = false});
  final WeatherKind kind;
  final bool isNight;
  final bool dark;
  @override
  Widget build(BuildContext context) => RepaintBoundary(
      child: CustomPaint(
          painter: _SkyPainter(kind, isNight, dark),
          child: const SizedBox.expand()));
}

class _SkyPainter extends CustomPainter {
  const _SkyPainter(this.kind, this.night, this.dark);
  final WeatherKind kind;
  final bool night;
  final bool dark;
  @override
  void paint(Canvas canvas, Size size) {
    final colors = dark
        ? [
            const Color(0xff263c33),
            night ? const Color(0xff293953) : const Color(0xff233c54)
          ]
        : night
            ? [const Color(0xfff9faff), const Color(0xffe9eef9)]
            : switch (kind) {
                WeatherKind.clear => [
                    const Color(0xfffbfdff),
                    const Color(0xffe6f3fc)
                  ],
                WeatherKind.cloudy => [
                    const Color(0xfffcfdff),
                    const Color(0xffeaf1f8)
                  ],
                WeatherKind.rain => [
                    const Color(0xfff9fcff),
                    const Color(0xffe4edf8)
                  ],
                WeatherKind.snow => [Colors.white, const Color(0xffedf5fc)],
                WeatherKind.storm => [
                    const Color(0xfffbfcff),
                    const Color(0xffe9edf9)
                  ],
                WeatherKind.fog => [
                    const Color(0xfffcfdff),
                    const Color(0xffeaf0f3)
                  ],
              };
    final detail = dark ? const Color(0xffb9d8f1) : const Color(0xff6194bc);
    canvas.drawRect(
        Offset.zero & size,
        Paint()
          ..shader = LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: colors)
              .createShader(Offset.zero & size));
    if (kind == WeatherKind.clear) {
      final center = Offset(size.width * .84, size.height * .18);
      canvas.drawCircle(
          center,
          size.width * .55,
          Paint()
            ..shader = RadialGradient(colors: [
              detail.withValues(alpha: night ? .025 : .055),
              detail.withValues(alpha: 0)
            ]).createShader(
                Rect.fromCircle(center: center, radius: size.width * .55)));
      if (night) {
        final moon = Path.combine(
            PathOperation.difference,
            Path()..addOval(Rect.fromCircle(center: center, radius: 18)),
            Path()
              ..addOval(Rect.fromCircle(
                  center: center + const Offset(9, -6), radius: 17)));
        canvas.drawPath(moon, Paint()..color = detail.withValues(alpha: .07));
        for (var i = 0; i < 20; i++) {
          canvas.drawCircle(
              Offset(((i * 37 + 17) % 100) / 100 * size.width,
                  ((i * 23 + 9) % 70) / 100 * size.height),
              i % 3 == 0 ? 1 : .6,
              Paint()..color = detail.withValues(alpha: .12));
        }
      }
    } else {
      final cloud = Paint()
        ..color = detail.withValues(alpha: .035)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 12);
      for (var i = 0; i < 3; i++) {
        final x = size.width * (.65 + i * .14);
        canvas.drawOval(
            Rect.fromCenter(
                center: Offset(x, size.height * (.16 + i * .035)),
                width: size.width * .55,
                height: size.height * .32),
            cloud);
      }
      if (kind == WeatherKind.rain || kind == WeatherKind.storm) {
        final line = Paint()
          ..color = detail.withValues(alpha: .055)
          ..strokeWidth = .8
          ..strokeCap = StrokeCap.round;
        for (var i = 0; i < 25; i++) {
          final p = Offset(((i * 31 + 13) % 100) / 100 * size.width,
              ((i * 29 + 5) % 100) / 100 * size.height);
          canvas.drawLine(p, p + const Offset(-3, 8), line);
        }
      }
      if (kind == WeatherKind.snow) {
        for (var i = 0; i < 18; i++) {
          canvas.drawCircle(
              Offset(((i * 41 + 11) % 100) / 100 * size.width,
                  ((i * 17 + 7) % 100) / 100 * size.height),
              i % 3 == 0 ? 2 : 1,
              Paint()..color = detail.withValues(alpha: .09));
        }
      }
      if (kind == WeatherKind.fog) {
        for (var i = 0; i < 4; i++) {
          canvas.drawOval(
              Rect.fromCenter(
                  center: Offset(size.width * .6, size.height * (.4 + i * .19)),
                  width: size.width * 1.4,
                  height: 18),
              cloud);
        }
      }
    }
  }

  @override
  bool shouldRepaint(_SkyPainter oldDelegate) =>
      kind != oldDelegate.kind ||
      night != oldDelegate.night ||
      dark != oldDelegate.dark;
}

class WeatherGlyph extends StatelessWidget {
  const WeatherGlyph(
      {super.key,
      required this.kind,
      this.isNight = false,
      this.size = 28,
      this.color});
  final WeatherKind kind;
  final bool isNight;
  final double size;
  final Color? color;
  @override
  Widget build(BuildContext context) => Semantics(
      label: weatherLabels[kind],
      image: true,
      child: CustomPaint(
          size: Size.square(size),
          painter: _GlyphPainter(
              kind, isNight, color ?? Theme.of(context).colorScheme.primary)));
}

class _GlyphPainter extends CustomPainter {
  const _GlyphPainter(this.kind, this.night, this.ink);
  final WeatherKind kind;
  final bool night;
  final Color ink;
  @override
  void paint(Canvas canvas, Size size) {
    final s = size.shortestSide;
    canvas.scale(s, s);
    final cloudInk = Paint()..color = ink.withValues(alpha: .82);
    if (kind == WeatherKind.clear) {
      if (night) {
        canvas.drawPath(
            Path.combine(
                PathOperation.difference,
                Path()
                  ..addOval(Rect.fromCircle(
                      center: const Offset(.5, .5), radius: .33)),
                Path()
                  ..addOval(Rect.fromCircle(
                      center: const Offset(.67, .34), radius: .3))),
            cloudInk);
      } else {
        final sun = Paint()
          ..color = const Color(0xffcfaa6c)
          ..strokeWidth = .055
          ..strokeCap = StrokeCap.round;
        canvas.drawCircle(const Offset(.5, .5), .23, sun);
        for (var i = 0; i < 8; i++) {
          final a = i * math.pi / 4;
          canvas.drawLine(
              Offset(.5 + math.cos(a) * .34, .5 + math.sin(a) * .34),
              Offset(.5 + math.cos(a) * .43, .5 + math.sin(a) * .43),
              sun);
        }
      }
      return;
    }
    if (kind == WeatherKind.fog) {
      final line = Paint()
        ..color = ink.withValues(alpha: .8)
        ..strokeWidth = .07
        ..strokeCap = StrokeCap.round;
      for (var i = 0; i < 3; i++) {
        canvas.drawLine(Offset(i == 1 ? .13 : .22, .3 + i * .2),
            Offset(i == 1 ? .87 : .78, .3 + i * .2), line);
      }
      return;
    }
    final cloud = Path()
      ..moveTo(.2, .62)
      ..cubicTo(.02, .62, .02, .36, .22, .34)
      ..cubicTo(.23, .08, .60, .05, .67, .30)
      ..cubicTo(.94, .26, 1, .61, .78, .62)
      ..close();
    canvas.drawPath(cloud, cloudInk);
    if (kind == WeatherKind.rain) {
      final rain = Paint()
        ..color = ink.withValues(alpha: .7)
        ..strokeWidth = .055
        ..strokeCap = StrokeCap.round;
      for (final x in [.3, .5, .7]) {
        canvas.drawLine(Offset(x, .73), Offset(x - .07, .89), rain);
      }
    } else if (kind == WeatherKind.snow) {
      final snow = Paint()
        ..color = ink.withValues(alpha: .6)
        ..strokeWidth = .035
        ..strokeCap = StrokeCap.round;
      for (final x in [.3, .55, .8]) {
        for (var i = 0; i < 3; i++) {
          final a = i * math.pi / 3;
          canvas.drawLine(
              Offset(x - math.cos(a) * .065, .82 - math.sin(a) * .065),
              Offset(x + math.cos(a) * .065, .82 + math.sin(a) * .065),
              snow);
        }
      }
    } else if (kind == WeatherKind.storm) {
      canvas.drawPath(
          Path()
            ..moveTo(.49, .59)
            ..lineTo(.34, .82)
            ..lineTo(.49, .8)
            ..lineTo(.44, .98)
            ..lineTo(.68, .70)
            ..lineTo(.52, .72)
            ..close(),
          Paint()..color = const Color(0xffcba566));
    }
  }

  @override
  bool shouldRepaint(_GlyphPainter oldDelegate) =>
      kind != oldDelegate.kind ||
      night != oldDelegate.night ||
      ink != oldDelegate.ink;
}
