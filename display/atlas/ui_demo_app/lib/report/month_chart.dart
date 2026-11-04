// 마무리 화면 두 번째 탭 '30일 기록' — 날마다 hub 가 고른 기기(쌓인 막대)와 되돌린 횟수(점).
// 지금은 사람마다 정해진 시뮬레이션 데이터(sampleMonth)를 그리고, 화면 아래에 '시뮬레이션' 이라고 적는다.
// 실제 기록을 쌓게 되면 MonthDay 목록만 바꿔 넣으면 된다.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../demo_model.dart';

const _ink = Color(0xFF263B37);
const _muted = Color(0xFF7A8580);

/// 막대에 쌓는 도움 종류(아래부터). 앱의 도움 순서 말과 같은 이름을 쓴다.
const monthKinds = <(String, Color)>[
  ('안내 카드', Color(0xFFA99A8A)),
  ('알림', Color(0xFFEE7E62)),
  ('음악', Color(0xFF79B98C)),
  ('선풍기', Color(0xFF7AA6D6)),
  ('조명', Color(0xFFF4B23E)),
];

class MonthDay {
  const MonthDay(this.counts, this.undos);

  /// 종류 이름 → 그날 hub 가 고른 횟수.
  final Map<String, int> counts;
  final int undos;
  int get total => counts.values.fold(0, (a, b) => a + b);
}

/// 사람마다 같은 결과가 나오는 30일 시뮬레이션: 도움 순서 1순위를 많이 쓰고, 3순위는 초반에 자주 되돌려
/// 후반으로 갈수록 덜 고른다(되돌림을 배우는 모습).
List<MonthDay> sampleMonth(Persona persona) {
  final rand = math.Random(persona.profileId.hashCode);
  final order = persona.order.isEmpty ? ['조명', '선풍기', '알림'] : persona.order;
  final liked = order.first;
  final disliked = order.last;
  return [
    for (var day = 0; day < 30; day++)
      () {
        final learned = day >= 14;
        final counts = <String, int>{
          liked: 2 + rand.nextInt(3) + (learned ? 1 : 0),
          if (order.length > 1) order[1]: 1 + rand.nextInt(2),
          disliked:
              learned ? (rand.nextInt(5) == 0 ? 1 : 0) : 1 + rand.nextInt(2),
          '안내 카드': rand.nextInt(3),
        };
        if (rand.nextInt(4) == 0) counts['음악'] = (counts['음악'] ?? 0) + 1;
        final undos = learned
            ? (rand.nextInt(6) == 0 ? 1 : 0)
            : (counts[disliked]! > 0 && rand.nextInt(3) != 0
                ? 1 + rand.nextInt(2)
                : 0);
        return MonthDay(counts, undos);
      }(),
  ];
}

class MonthReport extends StatelessWidget {
  const MonthReport({super.key, required this.persona, required this.days});
  final Persona persona;
  final List<MonthDay> days;

  @override
  Widget build(BuildContext context) {
    final disliked = persona.order.isEmpty ? '알림' : persona.order.last;
    double avg(int from, int to) =>
        days.sublist(from, to).fold<int>(0, (a, d) => a + d.undos) /
        (to - from);
    return Row(key: const ValueKey('month-report'), children: [
      SizedBox(
          width: 300,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('묻지 않고, 매일 조금씩 배웁니다.',
                style: TextStyle(fontSize: 22, color: _ink)),
            const SizedBox(height: 4),
            const Text('카드에 답하지 않아도, 반응이 곧 답이에요.',
                style: TextStyle(fontSize: 13, color: _muted)),
            const SizedBox(height: 14),
            _rule(const Color(0xFFEE7E62), '되돌림',
                '켜 준 기기를 다시 원래대로 → 그 사람에겐 덜 씁니다'),
            _rule(const Color(0xFFE8A23A), '그래도 안 깸',
                '기기를 켰는데 계속 졸면 → 효과 없음으로 적습니다'),
            _rule(const Color(0xFF6DB38A), '그대로 두고 깸', '→ 다음에도 그 기기를 먼저 씁니다'),
          ])),
      const SizedBox(width: 24),
      Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _helpOrder(persona),
        const SizedBox(height: 8),
        Expanded(
            // 처음 뜰 때 1일부터 30일까지 차례로 막대가 자라 올라온다
            child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0, end: 1),
                duration: const Duration(milliseconds: 1200),
                curve: Curves.easeOutCubic,
                builder: (context, t, _) => CustomPaint(
                    key: const ValueKey('month-chart'),
                    painter: _MonthPainter(days, progress: t),
                    child: const SizedBox.expand()))),
        const SizedBox(height: 2),
        const Row(children: [
          Text('1일', style: TextStyle(fontSize: 10, color: _muted)),
          Spacer(),
          Text('30일', style: TextStyle(fontSize: 10, color: _muted)),
        ]),
        const SizedBox(height: 6),
        Text(
            "'$disliked' 되돌림 → 하루 ${avg(0, 15).toStringAsFixed(1)}회(1–15일) → "
            '${avg(15, 30).toStringAsFixed(1)}회(16–30일)',
            style: const TextStyle(fontSize: 13, color: _ink)),
        const SizedBox(height: 6),
        Wrap(spacing: 12, runSpacing: 4, children: [
          for (final (name, color) in monthKinds.reversed)
            _legend(color, name, square: true),
          _legend(const Color(0xFFE8674A), '되돌림', square: false),
        ]),
        const SizedBox(height: 4),
        const Text('시뮬레이션 · 같은 사람 30일 · 막대 = 그날 허브가 고른 도움 · 점 = 되돌림',
            key: ValueKey('month-footnote'),
            style: TextStyle(fontSize: 9, color: _muted)),
      ])),
    ]);
  }

  /// 이름 + 도움 순서 캡슐: ① 알림 › ② 조명 › ③ 선풍기.
  static Widget _helpOrder(Persona persona) => Row(children: [
        Text(persona.name, style: const TextStyle(fontSize: 18, color: _ink)),
        const SizedBox(width: 12),
        Container(
            key: const ValueKey('month-help-order'),
            padding: const EdgeInsets.fromLTRB(12, 5, 6, 5),
            decoration: BoxDecoration(
                color: const Color(0xFFFBEFE2),
                borderRadius: BorderRadius.circular(20)),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              const Text('먼저 받고 싶은 도움',
                  style: TextStyle(fontSize: 11, color: Color(0xFFB0662A))),
              const SizedBox(width: 8),
              for (final (i, help) in persona.order.indexed) ...[
                if (i > 0)
                  const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 3),
                      child: Icon(Icons.chevron_right_rounded,
                          size: 14, color: Color(0xFFCFA27C))),
                Container(
                    padding: const EdgeInsets.fromLTRB(4, 2, 9, 2),
                    decoration: BoxDecoration(
                        color: i == 0 ? const Color(0xFFE8912B) : Colors.white,
                        borderRadius: BorderRadius.circular(14)),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Container(
                          width: 16,
                          height: 16,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: i == 0
                                  ? Colors.white
                                  : const Color(0xFFF3E2D0)),
                          child: Text('${i + 1}',
                              style: const TextStyle(
                                  fontSize: 10, color: Color(0xFFB0662A)))),
                      const SizedBox(width: 5),
                      Text(help,
                          style: TextStyle(
                              fontSize: 12,
                              color: i == 0 ? Colors.white : _ink)),
                    ])),
              ],
            ])),
      ]);

  static Widget _rule(Color color, String title, String body) => Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.fromLTRB(14, 10, 12, 10),
      decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: .7),
          borderRadius: BorderRadius.circular(14),
          border: Border(left: BorderSide(color: color, width: 5))),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: const TextStyle(fontSize: 17, color: _ink)),
        const SizedBox(height: 3),
        Text(body, style: const TextStyle(fontSize: 12, color: _muted)),
      ]));

  static Widget _legend(Color color, String name, {required bool square}) =>
      Row(mainAxisSize: MainAxisSize.min, children: [
        Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
                color: color,
                shape: square ? BoxShape.rectangle : BoxShape.circle,
                borderRadius: square ? BorderRadius.circular(2) : null)),
        const SizedBox(width: 4),
        Text(name, style: const TextStyle(fontSize: 11, color: _ink)),
      ]);
}

class _MonthPainter extends CustomPainter {
  _MonthPainter(this.days, {this.progress = 1});
  final List<MonthDay> days;

  /// 0→1: 막대가 왼쪽부터 차례로 자란다(각 막대는 앞 막대보다 조금 늦게 시작).
  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    if (days.isEmpty) return;
    const dotR = 3.6;
    final maxUndo = days.map((d) => d.undos).fold<int>(0, math.max);
    final top = maxUndo * (dotR * 2 + 2) + 4;
    final maxTotal =
        math.max(1, days.map((d) => d.total).fold<int>(0, math.max));
    final unit = (size.height - top) / maxTotal;
    final slot = size.width / days.length;
    final barW = slot * .78;
    for (var i = 0; i < days.length; i++) {
      final d = days[i];
      final grow = ((progress * (days.length + 4) - i) / 4).clamp(0.0, 1.0);
      if (grow == 0) continue;
      final x = i * slot + (slot - barW) / 2;
      var y = size.height;
      for (final (name, color) in monthKinds) {
        final n = d.counts[name] ?? 0;
        if (n == 0) continue;
        final h = n * unit * grow;
        canvas.drawRect(
            Rect.fromLTWH(x, y - h, barW, h), Paint()..color = color);
        y -= h;
      }
      for (var k = 0; k < (grow == 1 ? d.undos : 0); k++) {
        canvas.drawCircle(
            Offset(x + barW / 2, y - 4 - dotR - k * (dotR * 2 + 2)),
            dotR,
            Paint()..color = const Color(0xFFE8674A));
      }
    }
  }

  @override
  bool shouldRepaint(_MonthPainter old) =>
      old.days != days || old.progress != progress;
}
