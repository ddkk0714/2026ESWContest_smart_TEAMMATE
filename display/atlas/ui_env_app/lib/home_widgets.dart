import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'co2_grade.dart';
import 'irumae.dart';
import 'workspace_store.dart';

// Body labels and selected Home labels keep their original supplied faces.
const homeKoreanTextStyle =
    TextStyle(fontFamily: 'High1Wonchuri', fontWeight: FontWeight.w600);
const homeHandwritingTextStyle =
    TextStyle(fontFamily: 'HakgyoansimGeurimilgi', fontWeight: FontWeight.w400);

class HomeTile extends StatelessWidget {
  const HomeTile(
      {super.key,
      required this.child,
      this.color,
      this.background,
      this.onTap,
      this.label,
      this.padding = 18});
  final Widget child;
  final Color? color;
  final Widget? background;
  final VoidCallback? onTap;
  final String? label;
  final double padding;
  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final radius = BorderRadius.circular(28);
    final surface =
        color ?? (dark ? const Color(0xff1b2e43) : const Color(0xffffffff));
    final content = InkWell(
        onTap: onTap,
        onLongPress: onTap == null ? null : () {},
        child: Padding(padding: EdgeInsets.all(padding), child: child));
    return Semantics(
        label: label,
        button: onTap != null,
        child: DecoratedBox(
            decoration: BoxDecoration(borderRadius: radius, boxShadow: [
              BoxShadow(
                  color: const Color(0xff143a62)
                      .withValues(alpha: dark ? .20 : .085),
                  blurRadius: 16,
                  offset: const Offset(0, 5)),
            ]),
            child: Material(
                color: surface,
                shape: RoundedRectangleBorder(
                    borderRadius: radius,
                    side: BorderSide(
                        color: dark
                            ? Colors.white.withValues(alpha: .20)
                            : const Color(0xff2c67a0).withValues(alpha: .30),
                        width: 1)),
                clipBehavior: Clip.antiAlias,
                child: background == null
                    ? content
                    : Stack(fit: StackFit.expand, children: [
                        Positioned.fill(
                            child: IgnorePointer(child: background)),
                        content,
                      ]))));
  }
}

class StandbyClockTile extends StatelessWidget {
  const StandbyClockTile(
      {super.key, required this.now, this.compact = false, this.onOpen});
  final DateTime now;
  final bool compact;
  final VoidCallback? onOpen;
  String _two(int n) => n.toString().padLeft(2, '0');
  @override
  Widget build(BuildContext context) {
    final ink = Theme.of(context).colorScheme.primary;
    final dark = Theme.of(context).brightness == Brightness.dark;
    if (compact) {
      return HomeTile(
        onTap: onOpen,
        label: '시계 열기',
        color: dark ? const Color(0xff203c59) : const Color(0xffe0f0fd),
        padding: 14,
        child: Semantics(
          key: const ValueKey('workspace-clock'),
          label: '현재 시각 ${_two(now.hour)}:${_two(now.minute)}',
          child: RepaintBoundary(
            child: TweenAnimationBuilder<double>(
              tween: Tween(
                  begin: now.millisecondsSinceEpoch / 1000,
                  end: now.millisecondsSinceEpoch / 1000),
              duration: const Duration(milliseconds: 850),
              builder: (context, seconds, _) => CustomPaint(
                key: const ValueKey('home-analog-clock'),
                painter: _AnalogClockPainter(seconds, now.timeZoneOffset, ink,
                    Theme.of(context).colorScheme.surface),
                child: const SizedBox.expand(),
              ),
            ),
          ),
        ),
      );
    }
    return HomeTile(
        onTap: onOpen,
        label: '시계 열기',
        color: dark ? const Color(0xff203c59) : const Color(0xffe0f0fd),
        padding: 22,
        child: LayoutBuilder(builder: (context, bounds) {
          final size = math.min(
              bounds.maxWidth * (bounds.maxWidth < 220 ? .34 : .295),
              bounds.maxHeight * .70);
          Widget digits(String value, String key, double angle) => Expanded(
              child: Center(
                  child: Transform.rotate(
                      angle: angle,
                      child: FittedBox(
                          fit: BoxFit.scaleDown,
                          child: Text(value,
                              key: ValueKey(key),
                              style: TextStyle(
                                  color: ink,
                                  fontFamily: 'LGEIHeadline',
                                  fontSize: size,
                                  fontWeight: FontWeight.w600,
                                  height: 1,
                                  letterSpacing: -size * .035))))));
          final dot = math.max(4.0, size * .075);
          return Semantics(
              key: const ValueKey('workspace-clock'),
              label: '현재 시각 ${_two(now.hour)}:${_two(now.minute)}',
              child: ExcludeSemantics(
                  child: Center(
                      child: Row(children: [
                digits(_two(now.hour), 'clock-hour', -.028),
                SizedBox(
                    key: const ValueKey('clock-colon'),
                    width: size * .25,
                    height: size * .72,
                    child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Container(
                              width: dot,
                              height: dot,
                              decoration: BoxDecoration(
                                  color: ink, shape: BoxShape.circle)),
                          SizedBox(height: size * .17),
                          Container(
                              width: dot,
                              height: dot,
                              decoration: BoxDecoration(
                                  color: ink, shape: BoxShape.circle)),
                        ])),
                digits(_two(now.minute), 'clock-minute', .028),
              ]))));
        }));
  }
}

class _AnalogClockPainter extends CustomPainter {
  const _AnalogClockPainter(this.seconds, this.offset, this.ink, this.surface);
  final double seconds;
  final Duration offset;
  final Color ink;
  final Color surface;
  @override
  void paint(Canvas canvas, Size size) {
    final r = size.shortestSide / 2;
    canvas.translate(size.width / 2, size.height / 2);
    canvas.drawCircle(
        Offset.zero, r, Paint()..color = surface.withValues(alpha: .7));
    final tick = Paint()..strokeCap = StrokeCap.round;
    for (var i = 0; i < 60; i++) {
      final major = i % 5 == 0;
      final angle = i * math.pi / 30 - math.pi / 2;
      tick
        ..color = ink.withValues(alpha: major ? .75 : .25)
        ..strokeWidth = major ? 2 : 1;
      final outer = r * .91;
      final inner = r * (major ? .83 : .87);
      canvas.drawLine(Offset(math.cos(angle) * inner, math.sin(angle) * inner),
          Offset(math.cos(angle) * outer, math.sin(angle) * outer), tick);
    }
    for (final n in [12, 3, 6, 9]) {
      final angle = n * math.pi / 6 - math.pi / 2;
      final label = TextPainter(
          text: TextSpan(
              text: '$n',
              style: TextStyle(
                  fontFamily: 'LGEIHeadline', fontSize: r * .22, color: ink)),
          textDirection: TextDirection.ltr)
        ..layout();
      label.paint(
          canvas,
          Offset(math.cos(angle) * r * .66 - label.width / 2,
              math.sin(angle) * r * .66 - label.height / 2));
    }
    final localSeconds = seconds + offset.inSeconds;
    void hand(double angle, double length, double width, Color color) {
      final a = angle - math.pi / 2;
      canvas.drawLine(
          Offset(-math.cos(a) * r * .08, -math.sin(a) * r * .08),
          Offset(math.cos(a) * r * length, math.sin(a) * r * length),
          Paint()
            ..color = color
            ..strokeWidth = width
            ..strokeCap = StrokeCap.round);
    }

    hand(localSeconds / 43200 * math.pi * 2, .47, r * .065, ink);
    hand(localSeconds / 3600 * math.pi * 2, .73, r * .043, ink);
    hand(localSeconds / 60 * math.pi * 2, .79, 1.3, const Color(0xffd18248));
    canvas.drawCircle(Offset.zero, r * .045, Paint()..color = ink);
  }

  @override
  bool shouldRepaint(_AnalogClockPainter oldDelegate) =>
      seconds != oldDelegate.seconds ||
      offset != oldDelegate.offset ||
      ink != oldDelegate.ink ||
      surface != oldDelegate.surface;
}

class MonthCalendarTile extends StatelessWidget {
  const MonthCalendarTile(
      {super.key,
      required this.now,
      required this.events,
      required this.onOpen});
  final DateTime now;
  final List<DeskEvent> events;
  final VoidCallback onOpen;
  @override
  Widget
      build(BuildContext context) => LayoutBuilder(builder: (context, bounds) {
            final small = bounds.maxHeight < 180;
            final scheme = Theme.of(context).colorScheme;
            final first = DateTime(now.year, now.month).weekday % 7;
            final days = DateTime(now.year, now.month + 1, 0).day;
            final weeks = ((first + days) / 7).ceil();
            return HomeTile(
                onTap: onOpen,
                label: '월간 캘린더와 일정 보기',
                padding: small ? 12 : 18,
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(children: [
                        Text('${now.month}월',
                            style: homeHandwritingTextStyle.copyWith(
                                fontSize: small ? 21 : 26,
                                color: scheme.primary)),
                        const SizedBox(width: 8),
                        Text('${now.year}',
                            style: TextStyle(
                                fontSize: 12, color: scheme.onSurfaceVariant)),
                      ]),
                      SizedBox(height: small ? 5 : 10),
                      Row(
                          children: List.generate(
                              7,
                              (i) => Expanded(
                                  child: Center(
                                      child: Text(
                                          [
                                            '일',
                                            '월',
                                            '화',
                                            '수',
                                            '목',
                                            '금',
                                            '토'
                                          ][i],
                                          style: TextStyle(
                                              fontSize: small ? 12 : 14,
                                              color: i == 0
                                                  ? const Color(0xffd7634a)
                                                  : scheme
                                                      .onSurfaceVariant)))))),
                      const SizedBox(height: 4),
                      Expanded(
                          child: Column(
                              children: List.generate(
                                  weeks,
                                  (week) => Expanded(
                                          child: Row(
                                              children: List.generate(7, (day) {
                                        final n = week * 7 + day - first + 1;
                                        final valid = n >= 1 && n <= days;
                                        final today = valid && n == now.day;
                                        final scheduled = valid
                                            ? events
                                                .where((e) =>
                                                    e.date.year == now.year &&
                                                    e.date.month == now.month &&
                                                    e.date.day == n)
                                                .length
                                            : 0;
                                        return Expanded(child: LayoutBuilder(
                                            builder: (context, cell) {
                                          final font = math.min(
                                              small ? 12.0 : 16.0,
                                              math.max(
                                                  10.0, cell.maxHeight * .58));
                                          return Center(
                                              child: Container(
                                                  width: math.min(
                                                      cell.maxHeight - 1, 32),
                                                  height: math.min(
                                                      cell.maxHeight - 1, 32),
                                                  decoration: BoxDecoration(
                                                      color: today
                                                          ? scheme.primary
                                                          : Colors.transparent,
                                                      shape: BoxShape.circle),
                                                  child: Stack(
                                                      alignment:
                                                          Alignment.center,
                                                      children: [
                                                        if (valid)
                                                          Text('$n',
                                                              key: ValueKey(
                                                                  'home-calendar-day-$n'),
                                                              style: TextStyle(
                                                                  fontSize:
                                                                      font,
                                                                  height: 1,
                                                                  fontWeight: today
                                                                      ? FontWeight
                                                                          .w600
                                                                      : FontWeight
                                                                          .w400,
                                                                  color: today
                                                                      ? scheme
                                                                          .onPrimary
                                                                      : day == 0
                                                                          ? const Color(
                                                                              0xffd7634a)
                                                                          : scheme
                                                                              .onSurface)),
                                                        if (scheduled > 0)
                                                          Positioned(
                                                              bottom: 1,
                                                              child: Semantics(
                                                                  label:
                                                                      '$scheduled개 일정',
                                                                  child: Row(
                                                                      key: ValueKey(
                                                                          'home-calendar-events-$n'),
                                                                      mainAxisSize:
                                                                          MainAxisSize
                                                                              .min,
                                                                      children: [
                                                                        for (var dot =
                                                                                0;
                                                                            dot <
                                                                                math.min(scheduled,
                                                                                    3);
                                                                            dot++)
                                                                          Padding(
                                                                              padding: const EdgeInsets.symmetric(horizontal: 1),
                                                                              child: Container(width: 3, height: 3, decoration: BoxDecoration(color: today ? scheme.onPrimary : scheme.primary, shape: BoxShape.circle))),
                                                                        if (scheduled >
                                                                            3)
                                                                          Text(
                                                                              '+${scheduled - 3}',
                                                                              style: TextStyle(fontSize: 7, height: 1, color: today ? scheme.onPrimary : scheme.primary)),
                                                                      ]))),
                                                      ])));
                                        }));
                                      })))))),
                    ]));
          });
}

class EnvironmentTile extends StatelessWidget {
  const EnvironmentTile(
      {super.key,
      required this.temperature,
      required this.humidity,
      required this.grade,
      this.onOpen});
  final double? temperature;
  final double? humidity;
  final Co2Band? grade;
  final VoidCallback? onOpen;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, bounds) {
        final small = bounds.maxHeight < 180 || bounds.maxWidth < 180;
        final scheme = Theme.of(context).colorScheme;
        Widget row(Widget icon, String label, String value,
                {String unit = '', bool status = false}) =>
            Expanded(
                child: Row(children: [
              SizedBox(width: small ? 22 : 29, child: Center(child: icon)),
              SizedBox(width: small ? 6 : 10),
              Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                    Text(label,
                        style: TextStyle(
                            fontSize: small ? 11 : 13,
                            color: scheme.onSurfaceVariant)),
                    const SizedBox(height: 2),
                    FittedBox(
                        fit: BoxFit.scaleDown,
                        alignment: Alignment.centerLeft,
                        child: Text.rich(
                            TextSpan(children: [
                              TextSpan(
                                  text: value,
                                  style: TextStyle(
                                      fontSize: status
                                          ? (small ? 14 : 18)
                                          : (small ? 17 : 24),
                                      fontWeight: status
                                          ? homeHandwritingTextStyle.fontWeight
                                          : FontWeight.w400,
                                      fontFamily: status
                                          ? homeHandwritingTextStyle.fontFamily
                                          : null,
                                      letterSpacing: 0)),
                              if (unit.isNotEmpty)
                                TextSpan(
                                    text: unit,
                                    style: TextStyle(
                                        fontSize: small ? 10 : 12,
                                        color: scheme.onSurfaceVariant))
                            ]),
                            style: const TextStyle(height: 1.15)))
                  ]))
            ]));
        return HomeTile(
            onTap: onOpen,
            padding: small ? 12 : 17,
            color: Theme.of(context).brightness == Brightness.dark
                ? const Color(0xff1b3047)
                : const Color(0xfff8fcff),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (!small)
                Text('실내',
                    style: homeHandwritingTextStyle.copyWith(
                        fontSize: 22, color: scheme.onSurfaceVariant)),
              row(
                  Icon(Icons.thermostat_rounded,
                      size: small ? 21 : 27, color: scheme.primary),
                  '온도',
                  temperature?.toStringAsFixed(1) ?? '—',
                  unit: ' °C'),
              row(
                  Icon(Icons.water_drop_rounded,
                      size: small ? 20 : 25, color: scheme.primary),
                  '습도',
                  '${humidity?.round() ?? '—'}',
                  unit: ' %'),
              row(
                  SizedBox(
                      width: small ? 21 : 27,
                      height: small ? 21 : 27,
                      child: CustomPaint(
                          painter: _MoodFace(grade?.face, scheme.primary))),
                  'CO₂',
                  grade?.label ?? '확인 중',
                  status: true),
            ]));
      });
}

/// Code-drawn emoji faces stay visible on the board without an emoji font.
class _MoodFace extends CustomPainter {
  _MoodFace(this.level, this.color);
  final int? level;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final r = size.shortestSide / 2;
    final center = size.center(Offset.zero);
    final fill = Paint()..color = color.withValues(alpha: .13);
    final line = Paint()
      ..color = color
      ..strokeWidth = 1.7
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    canvas.drawCircle(center, r - 1, fill);
    canvas.drawCircle(center, r - 1, line);
    if (level == null) {
      canvas.drawLine(Offset(r * .7, r), Offset(r * 1.3, r), line);
      return;
    }
    for (final x in [.65, 1.35]) {
      canvas.drawCircle(
          Offset(r * x, r * .8), r * .075, Paint()..color = color);
    }
    final mouth = Path()..moveTo(r * .55, r * 1.25);
    mouth.quadraticBezierTo(
        r,
        r *
            (level! >= 3
                ? 1.8
                : level! == 2
                    ? 1.25
                    : .85),
        r * 1.45,
        r * 1.25);
    canvas.drawPath(mouth, line);
    if (level == 4) {
      canvas.drawCircle(Offset(r * .45, r * 1.1), r * .09,
          fill..color = color.withValues(alpha: .4));
      canvas.drawCircle(Offset(r * 1.55, r * 1.1), r * .09, fill);
    }
    if (level == 0) {
      canvas.drawLine(Offset(r * .5, r * .45), Offset(r * .8, r * .55), line);
      canvas.drawLine(Offset(r * 1.5, r * .45), Offset(r * 1.2, r * .55), line);
    }
  }

  @override
  bool shouldRepaint(_MoodFace oldDelegate) =>
      level != oldDelegate.level || color != oldDelegate.color;
}

class FocusCountdownTile extends StatelessWidget {
  const FocusCountdownTile(
      {super.key,
      required this.seconds,
      required this.minutes,
      required this.running,
      required this.onOpen,
      required this.mood,
      this.todaySeconds = 0,
      this.todaySessions = 0});
  final int seconds;
  final int minutes;
  final bool running;
  final VoidCallback onOpen;
  final IrumaeMood mood;
  final int todaySeconds;
  final int todaySessions;
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, bounds) {
        final scheme = Theme.of(context).colorScheme;
        final small = bounds.maxHeight < 180 || bounds.maxWidth < 180;
        final elapsed = (minutes * 60 - seconds).clamp(0, minutes * 60);
        final text =
            '${(elapsed ~/ 60).toString().padLeft(2, '0')}:${(elapsed % 60).toString().padLeft(2, '0')}';
        final mascot = IrumaeAnimation(mood: mood);
        final timer = Text(text,
            key: const ValueKey('home-focus-countdown'),
            style: homeHandwritingTextStyle.copyWith(
                color: scheme.primary,
                fontSize: small ? 38 : 48,
                fontWeight: FontWeight.w400,
                height: 1.1,
                letterSpacing: 0));
        return HomeTile(
            onTap: onOpen,
            label: '집중 경과 시간과 목표 시간, 집중 시간 설정',
            padding: small ? 12 : 14,
            color: Theme.of(context).brightness == Brightness.dark
                ? const Color(0xff1e3751)
                : const Color(0xffedf7ff),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(children: [
                    Icon(Icons.local_fire_department_rounded,
                        color: const Color(0xffbc652e), size: small ? 21 : 25),
                    const SizedBox(width: 5),
                    Text('집중',
                        style: homeHandwritingTextStyle.copyWith(
                            color: scheme.onSurfaceVariant,
                            fontSize: small ? 17 : 22)),
                  ]),
                  Expanded(
                      child: small
                          ? Row(children: [
                              Expanded(child: mascot),
                              Expanded(
                                  child: FittedBox(
                                      fit: BoxFit.scaleDown, child: timer))
                            ])
                          : Column(children: [
                              Expanded(child: mascot),
                              FittedBox(fit: BoxFit.scaleDown, child: timer)
                            ])),
                  const SizedBox(height: 5),
                  Text('목표 $minutes min',
                      key: const ValueKey('home-focus-goal'),
                      style: homeHandwritingTextStyle.copyWith(
                          color: scheme.onSurfaceVariant,
                          fontSize: small ? 15 : 18,
                          fontWeight: FontWeight.w500)),
                  if (todaySessions > 0)
                    Text('오늘 ${todaySeconds ~/ 60} min · $todaySessions회 완료',
                        key: const ValueKey('home-focus-today'),
                        style: TextStyle(
                            fontSize: small ? 10 : 12,
                            color: scheme.onSurfaceVariant)),
                  const SizedBox(height: 7),
                  ClipRRect(
                      borderRadius: BorderRadius.circular(5),
                      child: LinearProgressIndicator(
                          value: (1 - seconds / (minutes * 60)).clamp(0.0, 1.0),
                          minHeight: 3,
                          color: scheme.primary,
                          backgroundColor:
                              scheme.onSurface.withValues(alpha: .1))),
                ]));
      });
}

class HomeMusicTile extends StatelessWidget {
  const HomeMusicTile(
      {super.key,
      required this.title,
      required this.playing,
      required this.position,
      required this.duration,
      required this.onPlaylist,
      required this.onToggle,
      required this.onPrevious,
      required this.onNext,
      required this.favorite,
      required this.onFavorite,
      required this.onVolume,
      this.onSeek});
  final String title;
  final bool playing;
  final Duration position;
  final Duration? duration;
  final VoidCallback onPlaylist;
  final VoidCallback onToggle;
  final VoidCallback onPrevious;
  final VoidCallback onNext;
  final bool favorite;
  final VoidCallback onFavorite;
  final VoidCallback onVolume;
  final ValueChanged<Duration>? onSeek;
  String _time(Duration value) =>
      '${value.inMinutes}:${(value.inSeconds % 60).toString().padLeft(2, '0')}';
  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, bounds) {
        final small = bounds.maxHeight < 180;
        final compact = bounds.maxWidth < 280;
        final narrow = bounds.maxWidth < 180;
        final scheme = Theme.of(context).colorScheme;
        final parts = title.split(' · ');
        final length = duration?.inMilliseconds ?? 0;
        final progress = length > 0
            ? (position.inMilliseconds / length).clamp(0.0, 1.0)
            : 0.0;
        Widget control(IconData icon, String label, VoidCallback action,
                {bool main = false}) =>
            IconButton(
                tooltip: label,
                onPressed: action,
                padding: EdgeInsets.zero,
                constraints: BoxConstraints.tightFor(
                    width: main ? (small ? 38 : 48) : (compact ? 32 : 42),
                    height: main ? (small ? 36 : 48) : (small ? 30 : 40)),
                visualDensity: VisualDensity.compact,
                style: IconButton.styleFrom(
                  minimumSize: Size.zero,
                  fixedSize: Size(
                      main ? (small ? 38 : 48) : (compact ? 32 : 42),
                      main ? (small ? 36 : 48) : (small ? 30 : 40)),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  padding: EdgeInsets.zero,
                ),
                color: scheme.primary,
                icon: Icon(icon,
                    size: main ? (small ? 30 : 40) : (compact ? 24 : 28)));
        return HomeTile(
            onTap: onPlaylist,
            label: '플레이리스트 열기',
            padding: small || narrow
                ? 10
                : compact
                    ? 18
                    : 22,
            color: Theme.of(context).brightness == Brightness.dark
                ? const Color(0xff1c334e)
                : const Color(0xffe8f3fc),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                mainAxisAlignment: MainAxisAlignment.start,
                children: [
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Expanded(
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                          Text(
                              parts.length > 1
                                  ? parts.skip(1).join(' · ')
                                  : title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: homeHandwritingTextStyle.copyWith(
                                  height: 1.15,
                                  fontSize: compact
                                      ? (small ? 17 : 22)
                                      : (small ? 18 : 26))),
                          const SizedBox(height: 3),
                          Text(parts.length > 1 ? parts.first : '내 플레이리스트',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: homeHandwritingTextStyle.copyWith(
                                  height: 1.15,
                                  fontSize: small
                                      ? 13
                                      : compact
                                          ? 15
                                          : 17,
                                  color: scheme.onSurfaceVariant)),
                        ])),
                  ]),
                  const Spacer(),
                  Column(children: [
                    if (!narrow || !small)
                      Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(_time(position),
                                style: TextStyle(
                                    fontSize: 11,
                                    height: 1.1,
                                    color: scheme.onSurfaceVariant)),
                            Text(duration == null ? '—:—' : _time(duration!),
                                style: TextStyle(
                                    fontSize: 11,
                                    height: 1.1,
                                    color: scheme.onSurfaceVariant)),
                          ]),
                    SizedBox(
                        height: narrow && small
                            ? 10
                            : small
                                ? 13
                                : 22,
                        child: SliderTheme(
                            data: SliderTheme.of(context).copyWith(
                                trackHeight: 3,
                                thumbShape: const RoundSliderThumbShape(
                                    enabledThumbRadius: 0),
                                overlayShape: SliderComponentShape.noOverlay),
                            child: Slider(
                                value: progress,
                                onChanged: length > 0 && onSeek != null
                                    ? (v) => onSeek!(Duration(
                                        milliseconds: (v * length).round()))
                                    : null,
                                activeColor: scheme.primary,
                                inactiveColor:
                                    scheme.primary.withValues(alpha: .18)))),
                  ]),
                  SizedBox(height: small ? 2 : 10),
                  Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        if (!compact)
                          control(
                              favorite
                                  ? Icons.favorite_rounded
                                  : Icons.favorite_border_rounded,
                              favorite ? '즐겨찾기 해제' : '즐겨찾기',
                              onFavorite),
                        control(Icons.fast_rewind_rounded, '이전 곡', onPrevious),
                        control(
                            playing
                                ? Icons.pause_rounded
                                : Icons.play_arrow_rounded,
                            playing ? '일시정지' : '재생',
                            onToggle,
                            main: true),
                        control(Icons.fast_forward_rounded, '다음 곡', onNext),
                        if (!compact)
                          control(Icons.volume_up_outlined, '볼륨', onVolume),
                      ]),
                ]));
      });
}
