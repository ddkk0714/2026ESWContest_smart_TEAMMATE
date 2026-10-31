import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'home_widgets.dart';

const lampLevelLabels = ['끄기', '매우 어둡게', '어둡게', '보통', '밝게', '매우 밝게', '최대'];
const lampLevelValues = [0, 26, 64, 115, 166, 217, 255];
const lampToneLabels = [
  '아주 따뜻하게',
  '따뜻하게',
  '조금 따뜻하게',
  '중간',
  '조금 하얗게',
  '하얗게',
  '아주 하얗게'
];
// The documented iLink hardware command supports five CCT levels (warm=5).
const lampToneCommands = [5, 4, 4, 3, 2, 2, 1];

class ApplianceArt extends StatelessWidget {
  const ApplianceArt(
      {super.key,
      required this.music,
      this.size = 110,
      this.level = 4,
      this.tone = 3});
  final bool music;
  final double size;
  final int level;
  final int tone;
  @override
  Widget build(BuildContext context) => Semantics(
      image: true,
      label: music ? '음표 스피커' : '조명',
      child: CustomPaint(
          size: Size.square(size),
          painter: _AppliancePainter(music, level, tone)));
}

class _AppliancePainter extends CustomPainter {
  _AppliancePainter(this.music, this.level, this.tone);
  final bool music;
  final int level;
  final int tone;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 120, size.height / 120);
    final shadow = Paint()
      ..color = const Color(0xff2c67a0).withValues(alpha: .12)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 5);
    canvas.drawOval(const Rect.fromLTWH(21, 101, 80, 9), shadow);
    if (music) {
      final body = RRect.fromRectAndRadius(
          const Rect.fromLTWH(23, 14, 74, 88), const Radius.circular(24));
      canvas.drawRRect(
          body,
          Paint()
            ..shader = const LinearGradient(
                    colors: [Color(0xffe5f5ff), Color(0xff9ccaf0)],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight)
                .createShader(body.outerRect));
      canvas.drawRRect(
          body,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.5
            ..color = const Color(0xff6ba6d4));
      for (var y = 76; y < 94; y += 5) {
        for (var x = 37; x < 86; x += 5) {
          canvas.drawCircle(Offset(x.toDouble(), y.toDouble()), .75,
              Paint()..color = const Color(0xff6699be).withValues(alpha: .45));
        }
      }
      final note = Paint()..color = const Color(0xff2c67a0);
      canvas.drawRRect(
          RRect.fromRectAndRadius(
              const Rect.fromLTWH(61, 30, 5, 30), const Radius.circular(2)),
          note);
      canvas.drawOval(const Rect.fromLTWH(47, 53, 19, 13), note);
      final flag = Path()
        ..moveTo(64, 30)
        ..quadraticBezierTo(87, 33, 76, 47)
        ..quadraticBezierTo(79, 36, 64, 40)
        ..close();
      canvas.drawPath(flag, note);
      _face(canvas, 55, 70, 71, 70);
    } else {
      final light = Color.lerp(
          const Color(0xffffcf75), const Color(0xffe9f5ff), tone / 6)!;
      if (level > 0) {
        final glow = Path()
          ..moveTo(37, 48)
          ..lineTo(10, 103)
          ..quadraticBezierTo(60, 119, 110, 103)
          ..lineTo(83, 48)
          ..close();
        canvas.drawPath(
            glow,
            Paint()
              ..shader = LinearGradient(colors: [
                light.withValues(alpha: .40 * level / 6),
                light.withValues(alpha: 0)
              ], begin: Alignment.topCenter, end: Alignment.bottomCenter)
                  .createShader(const Rect.fromLTWH(10, 45, 100, 70)));
      }
      canvas.drawRRect(
          RRect.fromRectAndRadius(
              const Rect.fromLTWH(56, 46, 8, 49), const Radius.circular(4)),
          Paint()..color = const Color(0xff7eafd4));
      final base = const Rect.fromLTWH(31, 92, 58, 13);
      canvas.drawRRect(
          RRect.fromRectAndRadius(base, const Radius.circular(8)),
          Paint()
            ..shader = const LinearGradient(
                    colors: [Color(0xffd4edff), Color(0xff7db5df)])
                .createShader(base));
      final shade = Path()
        ..moveTo(42, 17)
        ..quadraticBezierTo(60, 8, 78, 17)
        ..lineTo(94, 51)
        ..quadraticBezierTo(60, 62, 26, 51)
        ..close();
      canvas.drawPath(
          shade,
          Paint()
            ..shader = const LinearGradient(
                    colors: [Color(0xfff0faff), Color(0xffa6d1f2)],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight)
                .createShader(const Rect.fromLTWH(26, 14, 68, 44)));
      canvas.drawPath(
          shade,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.5
            ..color = const Color(0xff78aed7));
      canvas.drawOval(const Rect.fromLTWH(26, 44, 68, 14),
          Paint()..color = level == 0 ? const Color(0xffb1c8d8) : light);
      _face(canvas, 51, 34, 69, 34);
    }
    canvas.restore();
  }

  void _face(Canvas canvas, double x1, double y1, double x2, double y2) {
    final ink = Paint()..color = const Color(0xff376384);
    canvas.drawCircle(Offset(x1, y1), 1.6, ink);
    canvas.drawCircle(Offset(x2, y2), 1.6, ink);
    canvas.drawArc(
        Rect.fromLTWH((x1 + x2) / 2 - 4, y1 + 1, 8, 5),
        0,
        math.pi,
        false,
        Paint()
          ..color = ink.color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.4
          ..strokeCap = StrokeCap.round);
  }

  @override
  bool shouldRepaint(_AppliancePainter old) =>
      music != old.music || level != old.level || tone != old.tone;
}

class DevicePreviewCard extends StatelessWidget {
  const DevicePreviewCard(
      {super.key,
      required this.music,
      required this.nickname,
      required this.status,
      required this.onTap});
  final bool music;
  final String nickname;
  final String status;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return AspectRatio(
        aspectRatio: 1,
        child: HomeTile(
            onTap: onTap,
            label: '$nickname ${music ? '스피커' : '조명'} $status',
            padding: 15,
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(
                  child: Center(
                      child: FittedBox(
                          fit: BoxFit.scaleDown,
                          child: ApplianceArt(music: music, size: 115)))),
              Text(nickname,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                      fontSize: 19, fontWeight: FontWeight.w600)),
              const SizedBox(height: 3),
              Text(music ? '블루투스 스피커' : '블루투스 조명',
                  style:
                      TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
              const SizedBox(height: 7),
              Row(children: [
                Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(
                        color: status == '연결됨'
                            ? const Color(0xff63a889)
                            : scheme.onSurfaceVariant,
                        shape: BoxShape.circle)),
                const SizedBox(width: 5),
                Expanded(
                    child: Text(status,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            fontSize: 11, color: scheme.onSurfaceVariant)))
              ]),
            ])));
  }
}

class LampControlCard extends StatelessWidget {
  const LampControlCard(
      {super.key,
      required this.level,
      required this.tone,
      required this.busy,
      required this.onLevel,
      required this.onLevelEnd,
      required this.onTone,
      required this.onToneEnd,
      required this.hardwareFiveSteps});
  final int level;
  final int tone;
  final bool busy;
  final ValueChanged<int> onLevel;
  final ValueChanged<int> onLevelEnd;
  final ValueChanged<int> onTone;
  final ValueChanged<int> onToneEnd;
  final bool hardwareFiveSteps;
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    Widget slider(bool warmth) =>
        Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Icon(warmth ? Icons.thermostat_rounded : Icons.light_mode_outlined,
                size: 22, color: scheme.primary),
            const SizedBox(width: 9),
            Expanded(
                child: Text(warmth ? '색온도' : '밝기',
                    style: const TextStyle(fontSize: 20))),
            Flexible(
                child: Text(
                    warmth ? lampToneLabels[tone] : lampLevelLabels[level],
                    style: TextStyle(fontSize: 17, color: scheme.primary)))
          ]),
          const SizedBox(height: 12),
          Stack(alignment: Alignment.center, children: [
            if (warmth)
              Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: Container(
                      height: 9,
                      decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(8),
                          gradient: const LinearGradient(colors: [
                            Color(0xffffcd77),
                            Color(0xfffff5dc),
                            Color(0xffc8e6ff)
                          ])))),
            SliderTheme(
                data: SliderTheme.of(context).copyWith(
                    thumbColor: scheme.primary,
                    activeTrackColor:
                        warmth ? Colors.transparent : scheme.primary,
                    inactiveTrackColor: warmth
                        ? Colors.transparent
                        : scheme.primary.withValues(alpha: .16),
                    activeTickMarkColor:
                        warmth ? scheme.primary.withValues(alpha: .35) : null,
                    inactiveTickMarkColor:
                        warmth ? scheme.primary.withValues(alpha: .35) : null),
                child: Slider(
                    key: ValueKey(
                        warmth ? 'lamp-tone-slider' : 'lamp-level-slider'),
                    min: 0,
                    max: 6,
                    divisions: 6,
                    value: (warmth ? tone : level).toDouble(),
                    label:
                        warmth ? lampToneLabels[tone] : lampLevelLabels[level],
                    onChanged: busy
                        ? null
                        : (v) => (warmth ? onTone : onLevel)(v.round()),
                    onChangeEnd: busy
                        ? null
                        : (v) => (warmth ? onToneEnd : onLevelEnd)(v.round()))),
          ]),
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            Text(warmth ? '따뜻한 노란빛' : '끄기',
                style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
            Text('7단계',
                style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
            Text(warmth ? '깨끗한 하얀빛' : '최대 밝기',
                style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
          ]),
        ]);
    return HomeTile(
        padding: 22,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ApplianceArt(music: false, size: 100, level: level, tone: tone),
          const SizedBox(height: 12),
          slider(false),
          const SizedBox(height: 24),
          slider(true),
          if (hardwareFiveSteps)
            Padding(
                padding: const EdgeInsets.only(top: 17),
                child: Text('연결된 조명의 색온도 5단계에 맞춰 적용해요.',
                    style: TextStyle(
                        fontSize: 11, color: scheme.onSurfaceVariant))),
          if (busy)
            const Padding(
                padding: EdgeInsets.only(top: 14),
                child: LinearProgressIndicator()),
        ]));
  }
}
