// 마무리 화면 '신호 넷 → 피로 점수 → 개입' 그래프. 집중 동안 모은 SessionTimeline 을 그대로 그린다.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'session_timeline.dart';

const _ink = Color(0xFF263B37);
const _muted = Color(0xFF7A8580);
const _grid = Color(0xFFE1E5DC);
const _event = Color(0xFFE8912B);
const _keyColor = Color(0xFF4F7FC0);
const _tofColor = Color(0xFFD9782F);
const _motionColor = Color(0xFF3E8E57);
const _co2Color = Color(0xFF8B63B8);
const _focusColor = Color(0xFF356C59);

class SessionChart extends StatelessWidget {
  const SessionChart(
      {super.key,
      required this.timeline,
      this.suggestGate = .45,
      this.autoGate = .75});
  final SessionTimeline timeline;

  /// hub fsm.yaml gate(제안 0.45 · 자동 0.75). hub 가 보내지 않아 기본값을 쓴다.
  final double suggestGate;
  final double autoGate;

  static const labelWidth = 104.0;
  static const rightWidth = 74.0;

  @override
  Widget build(BuildContext context) {
    final s = timeline.samples;
    if (s.length < 2) {
      return const Center(
          key: ValueKey('session-chart-empty'),
          child: Text('보드와 이어진 채 집중한 흐름이 아직 없어요.',
              style: TextStyle(color: _muted, fontSize: 14)));
    }
    final t0 = s.first.at;
    var t1 = s.last.at;
    for (final e in timeline.events) {
      if (e.at.isAfter(t1)) t1 = e.at;
    }
    final span = math.max(1, t1.difference(t0).inMilliseconds).toDouble();
    final xs = [for (final p in s) p.at.difference(t0).inMilliseconds / span];
    final ev = [
      for (final e in timeline.events)
        (e.at.difference(t0).inMilliseconds / span).clamp(0.0, 1.0)
    ];

    final co2 = [for (final p in s) p.co2?.toDouble()];
    final co2Known = co2.whereType<double>();
    final co2Min = co2Known.isEmpty ? 0.0 : co2Known.reduce(math.min);
    final co2Max = co2Known.isEmpty ? 0.0 : co2Known.reduce(math.max);
    final motionMax = math.max(
        1, s.map((p) => p.motion ?? 0).fold<int>(0, (a, b) => math.max(a, b)));

    // 처음 뜰 때 왼쪽에서 오른쪽으로 선이 그어진다(progress 0→1). 개입 세모는 선이 닿으면 나타난다.
    return TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: 1),
        duration: const Duration(milliseconds: 1400),
        curve: Curves.easeInOutCubic,
        builder: (context, progress, _) => _chart(xs, ev, s, co2, co2Known,
            co2Min, co2Max, motionMax, t0, span, progress));
  }

  Widget _chart(
      List<double> xs,
      List<double> ev,
      List<TimelineSample> s,
      List<double?> co2,
      Iterable<double> co2Known,
      double co2Min,
      double co2Max,
      int motionMax,
      DateTime t0,
      double span,
      double progress) {
    Widget lane(String title, String sub, List<double?> values, Color color,
            {bool step = false}) =>
        Expanded(
            flex: 10,
            child: Row(children: [
              _Label(title: title, sub: sub),
              Expanded(
                  child: CustomPaint(
                      painter: _LanePainter(xs, values, ev, color,
                          step: step, progress: progress),
                      child: const SizedBox.expand())),
              const SizedBox(width: rightWidth),
            ]));

    return Column(key: const ValueKey('session-chart'), children: [
      lane(
          '키 입력', '최근 60 s 중 멈춘 비율', [for (final p in s) p.keyIdle], _keyColor),
      lane(
          'ToF 자세',
          '보드 모델의 졸음 증거',
          [
            for (final p in s)
              p.drowsyEvidence == null ? null : (p.drowsyEvidence! ? 1.0 : 0.0)
          ],
          _tofColor,
          step: true),
      lane(
          'mmWave',
          '몸 움직임',
          [for (final p in s) p.motion == null ? null : p.motion! / motionMax],
          _motionColor),
      lane(
          'CO₂',
          co2Known.isEmpty ? 'ppm' : '${co2Min.round()}~${co2Max.round()} ppm',
          [
            for (final v in co2)
              v == null
                  ? null
                  : co2Max - co2Min < 1
                      ? .5
                      : (v - co2Min) / (co2Max - co2Min)
          ],
          _co2Color),
      const SizedBox(height: 6),
      Expanded(
          flex: 34,
          child: Row(children: [
            const _Label(title: '피로 점수', sub: '허브 계산 · 초록 = 집중'),
            Expanded(
                child: CustomPaint(
                    key: const ValueKey('session-chart-score'),
                    painter: _ScorePainter(xs, [for (final p in s) p.fatigue],
                        [for (final p in s) p.focus], ev, suggestGate, autoGate,
                        progress: progress),
                    child: const SizedBox.expand())),
            SizedBox(
                width: rightWidth,
                child: LayoutBuilder(
                    builder: (context, c) => Stack(children: [
                          _gateLabel(c.maxHeight, autoGate,
                              '자동 ${_fmt(autoGate)}', const Color(0xFFC0583A)),
                          _gateLabel(
                              c.maxHeight,
                              suggestGate,
                              '개입 ${_fmt(suggestGate)}',
                              const Color(0xFFB7891F)),
                        ]))),
          ])),
      SizedBox(
          height: 18,
          child: Row(children: [
            const SizedBox(width: labelWidth),
            Expanded(
                child: LayoutBuilder(
                    builder: (context, c) => Stack(children: [
                          for (final f in [0.0, .25, .5, .75, 1.0])
                            Positioned(
                                left: (c.maxWidth * f - 18)
                                    .clamp(0.0, c.maxWidth - 36),
                                top: 2,
                                child: Text(
                                    _clock(t0.add(Duration(
                                        milliseconds: (span * f).round()))),
                                    style: const TextStyle(
                                        fontSize: 10, color: _muted))),
                        ]))),
            const SizedBox(width: rightWidth),
          ])),
    ]);
  }

  static Widget _gateLabel(double h, double v, String text, Color color) =>
      Positioned(
          left: 6,
          top: (_scoreTop + (1 - v) * (h - _scoreTop - _scoreBottom)) - 7,
          child: Text(text, style: TextStyle(fontSize: 10, color: color)));

  static String _fmt(double v) => v.toStringAsFixed(2);
  static String _clock(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

/// 점수 칸 위아래 여백(세모 표시 자리).
const _scoreTop = 12.0;
const _scoreBottom = 2.0;

class _Label extends StatelessWidget {
  const _Label({required this.title, required this.sub});
  final String title;
  final String sub;
  @override
  Widget build(BuildContext context) => SizedBox(
      width: SessionChart.labelWidth,
      child: Padding(
          padding: const EdgeInsets.only(right: 10),
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(title,
                    style: const TextStyle(
                        fontSize: 13, color: _ink, height: 1.1)),
                Text(sub,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 9, color: _muted, height: 1.2)),
              ])));
}

void _eventLines(Canvas canvas, Size size, List<double> events) {
  final paint = Paint()
    ..color = _event.withValues(alpha: .55)
    ..strokeWidth = 1.2;
  for (final e in events) {
    canvas.drawLine(
        Offset(e * size.width, 0), Offset(e * size.width, size.height), paint);
  }
}

/// 한 줄 신호. 값이 없는 칸(null)은 선을 끊는다.
class _LanePainter extends CustomPainter {
  _LanePainter(this.xs, this.values, this.events, this.color,
      {this.step = false, this.progress = 1});
  final double progress;
  final List<double> xs;
  final List<double?> values;
  final List<double> events;
  final Color color;
  final bool step;

  @override
  void paint(Canvas canvas, Size size) {
    final pad = size.height * .15;
    double y(double v) =>
        pad + (1 - v.clamp(0.0, 1.0)) * (size.height - pad * 2);
    canvas.clipRect(Rect.fromLTWH(0, 0, size.width * progress, size.height));
    _eventLines(canvas, size, events);
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6
      ..strokeJoin = StrokeJoin.round;
    Path? path;
    double? prevY;
    for (var i = 0; i < values.length; i++) {
      final v = values[i];
      if (v == null) {
        if (path != null) canvas.drawPath(path, paint);
        path = null;
        continue;
      }
      final x = xs[i] * size.width;
      if (path == null) {
        path = Path()..moveTo(x, y(v));
      } else {
        if (step && prevY != null) path.lineTo(x, prevY);
        path.lineTo(x, y(v));
      }
      prevY = y(v);
    }
    if (path != null) canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_LanePainter old) =>
      old.values != values || old.events != events || old.progress != progress;
}

/// 피로 점수(굵은 계단) · 집중 점수(가는 선) · 개입 기준선 · 개입 순간(주황 세모).
class _ScorePainter extends CustomPainter {
  _ScorePainter(
      this.xs, this.fatigue, this.focus, this.events, this.suggest, this.auto,
      {this.progress = 1});
  final double progress;
  final List<double> xs;
  final List<double> fatigue;
  final List<double> focus;
  final List<double> events;
  final double suggest;
  final double auto;

  @override
  void paint(Canvas canvas, Size size) {
    final h = size.height - _scoreTop - _scoreBottom;
    double y(double v) => _scoreTop + (1 - v.clamp(0.0, 1.0)) * h;
    final box = Rect.fromLTWH(0, _scoreTop, size.width, h);
    canvas.drawRect(box, Paint()..color = Colors.white.withValues(alpha: .6));
    canvas.drawRect(
        box,
        Paint()
          ..color = _grid
          ..style = PaintingStyle.stroke);
    canvas.drawLine(
        Offset(0, y(.5)), Offset(size.width, y(.5)), Paint()..color = _grid);
    void dashed(double v, Color c) {
      final p = Paint()
        ..color = c
        ..strokeWidth = 1.2;
      for (double x = 0; x < size.width; x += 8) {
        canvas.drawLine(
            Offset(x, y(v)), Offset(math.min(x + 4, size.width), y(v)), p);
      }
    }

    dashed(auto, const Color(0xFFC0583A));
    dashed(suggest, const Color(0xFFB7891F));
    // 칸·기준선은 처음부터, 선과 개입 표시는 왼쪽부터 그어진다
    canvas.clipRect(Rect.fromLTWH(0, 0, size.width * progress, size.height));
    _eventLines(canvas, size, events);

    Path line(List<double> v, {required bool step}) {
      final p = Path()..moveTo(xs.first * size.width, y(v.first));
      for (var i = 1; i < v.length; i++) {
        final x = xs[i] * size.width;
        if (step) p.lineTo(x, y(v[i - 1]));
        p.lineTo(x, y(v[i]));
      }
      return p;
    }

    canvas.drawPath(
        line(focus, step: false),
        Paint()
          ..color = _focusColor.withValues(alpha: .55)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.4);
    canvas.drawPath(
        line(fatigue, step: true),
        Paint()
          ..color = _ink
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.6
          ..strokeJoin = StrokeJoin.round);
    final tri = Paint()..color = _event;
    for (final e in events) {
      final x = e * size.width;
      canvas.drawPath(
          Path()
            ..moveTo(x - 6, 0)
            ..lineTo(x + 6, 0)
            ..lineTo(x, 10)
            ..close(),
          tri);
    }
  }

  @override
  bool shouldRepaint(_ScorePainter old) =>
      old.fatigue != fatigue ||
      old.events != events ||
      old.progress != progress;
}
