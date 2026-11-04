// 화면을 거의 덮는 큰 카드와, 여러 장이 쌓이는 카드 덱.
// 안내(알림)·자동 실행·제안 카드를 한 곳에 모아 최근 것을 맨 위에 둔다. 새 카드는 아래에서 툭 떨어지듯 올라와
// 얹히고, 뒤 카드는 살짝 비스듬히 위로 삐져나온다. 맨 위 카드를 닫으면 다음 카드가 앞으로 나온다.
library;

import 'package:flutter/material.dart';

import 'card_look.dart';

const _ink = Color(0xFF263B37);
const _muted = Color(0xFF7A8580);

/// 큰 카드 한 장: 왼쪽 그림판 · 오른쪽 꼬리표 · 제목 · 설명 · 버튼.
class BigCard extends StatelessWidget {
  const BigCard({
    super.key,
    required this.look,
    required this.title,
    this.titleKey,
    this.lines = const [],
    this.lineKeyPrefix,
    this.tag,
    this.tagKey,
    this.trailing,
    this.extra,
    this.actions = const [],
    this.onClose,
    this.closeKey,
  });

  final CardLook look;
  final String title;
  final Key? titleKey;

  /// 제목 아래 설명 줄들(이유 · 개인화 · 상태).
  final List<String> lines;
  final String? lineKeyPrefix;

  /// 제목 위 작은 꼬리표(예: hub 가 고른 방법 이름). 없으면 그림판 꼬리표만.
  final String? tag;
  final Key? tagKey;

  /// 오른쪽 위(예: 쉬는 시간 카운트다운).
  final Widget? trailing;

  /// 설명 아래 넓은 칸(제안 카드의 이유·바뀔 것).
  final Widget? extra;
  final List<Widget> actions;
  final VoidCallback? onClose;
  final Key? closeKey;

  static const width = 880.0;
  static const height = 450.0;

  @override
  Widget build(BuildContext context) => Container(
      width: width,
      height: height,
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
          color: const Color(0xFFFFFEF8),
          borderRadius: BorderRadius.circular(34),
          border: Border.all(color: const Color(0xFFD5DFCF)),
          boxShadow: [
            BoxShadow(
                color: _ink.withValues(alpha: .14),
                blurRadius: 24,
                offset: const Offset(0, 6))
          ]),
      child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        CardPicturePanel(look: look, size: 140),
        const SizedBox(width: 26),
        Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            if (tag != null)
              Container(
                  key: tagKey,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                  decoration: BoxDecoration(
                      color: look.tile,
                      borderRadius: BorderRadius.circular(20)),
                  child: Text(tag!,
                      style: TextStyle(fontSize: 14, color: look.ink))),
            const Spacer(),
            if (trailing != null) trailing!,
            if (onClose != null)
              IconButton(
                  key: closeKey,
                  tooltip: '닫기',
                  iconSize: 30,
                  onPressed: onClose,
                  icon: const Icon(Icons.close_rounded)),
          ]),
          const SizedBox(height: 10),
          Text(title,
              key: titleKey,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 34, height: 1.3, color: _ink)),
          const SizedBox(height: 12),
          for (final (i, line) in lines.indexed)
            Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(line,
                    key: lineKeyPrefix == null
                        ? null
                        : ValueKey('$lineKeyPrefix$i'),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 18, height: 1.45, color: _muted))),
          if (extra != null)
            Expanded(child: SingleChildScrollView(child: extra))
          else
            const Spacer(),
          if (actions.isNotEmpty) ...[
            const SizedBox(height: 14),
            // 큰 카드 버튼은 터치 화면에서 누르기 쉽게 조금 크게(높이 56, 글자 19)
            Theme(
                data: Theme.of(context).copyWith(
                    filledButtonTheme: FilledButtonThemeData(
                        style: _bigButton(context)
                            .merge(Theme.of(context).filledButtonTheme.style)),
                    outlinedButtonTheme: OutlinedButtonThemeData(
                        style: _bigButton(context).merge(
                            Theme.of(context).outlinedButtonTheme.style))),
                child: Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                  for (final (i, a) in actions.indexed) ...[
                    if (i > 0) const SizedBox(width: 14),
                    a
                  ]
                ])),
          ],
        ])),
      ]));
}

/// 앱 글꼴은 그대로 두고 크기만 키운다(textStyle 을 새로 주면 글꼴이 빠져 한글이 깨진다).
ButtonStyle _bigButton(BuildContext context) => ButtonStyle(
    minimumSize: const WidgetStatePropertyAll(Size(140, 56)),
    padding: const WidgetStatePropertyAll(EdgeInsets.symmetric(horizontal: 28)),
    textStyle: WidgetStatePropertyAll(
        (Theme.of(context).textTheme.labelLarge ?? const TextStyle())
            .copyWith(fontSize: 19)));

class DeckEntry {
  const DeckEntry({required this.id, required this.at, required this.card});

  /// 같은 카드를 알아보는 이름. 바뀌면 새 카드로 보고 다시 떨어뜨린다.
  final String id;

  /// 처음 뜬 때. 최근 것이 맨 위.
  final DateTime at;
  final Widget card;
}

/// 쌓인 카드. 맨 위만 누를 수 있고, 뒤로는 3장까지 보인다.
class CardDeck extends StatelessWidget {
  const CardDeck({super.key, required this.entries});
  final List<DeckEntry> entries;

  static const visible = 3;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) return const SizedBox.shrink();
    final sorted = [...entries]..sort((a, b) => a.at.compareTo(b.at));
    final shown = sorted.length > visible
        ? sorted.sublist(sorted.length - visible)
        : sorted;
    final reduce = MediaQuery.disableAnimationsOf(context);
    return Stack(key: const ValueKey('card-deck'), children: [
      Positioned.fill(
          child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () {},
              child: ColoredBox(color: Colors.black.withValues(alpha: .30)))),
      Center(
          child: FittedBox(
              fit: BoxFit.scaleDown,
              child: Padding(
                  padding: const EdgeInsets.only(top: 48),
                  child: SizedBox(
                      width: BigCard.width,
                      height: BigCard.height,
                      child: Stack(clipBehavior: Clip.none, children: [
                        for (final (i, e) in shown.indexed)
                          _DeckCard(
                              key: ValueKey('deck-${e.id}'),
                              depth: shown.length - 1 - i,
                              reduceMotion: reduce,
                              child: e.card),
                        if (sorted.length > 1)
                          Positioned(
                              right: 18,
                              top: -30,
                              child: Container(
                                  key: const ValueKey('card-deck-count'),
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 12, vertical: 4),
                                  decoration: BoxDecoration(
                                      color: _ink,
                                      borderRadius: BorderRadius.circular(20)),
                                  child: Text('카드 ${sorted.length}장',
                                      style: const TextStyle(
                                          color: Colors.white, fontSize: 13)))),
                      ]))))),
    ]);
  }
}

/// 덱 속 한 장. 처음엔 아래에서 기울어진 채 떨어지듯 올라오고(챱), 뒤로 밀리면 작아지며 위로 비켜 선다.
class _DeckCard extends StatelessWidget {
  const _DeckCard(
      {super.key,
      required this.depth,
      required this.child,
      this.reduceMotion = false});
  final int depth;
  final Widget child;
  final bool reduceMotion;

  @override
  Widget build(BuildContext context) {
    final move = Duration(milliseconds: reduceMotion ? 0 : 320);
    return IgnorePointer(
        ignoring: depth > 0,
        child: AnimatedSlide(
            duration: move,
            curve: Curves.easeOutCubic,
            offset: Offset(0, -.085 * depth),
            child: AnimatedScale(
                duration: move,
                curve: Curves.easeOutCubic,
                scale: 1 - .04 * depth,
                child: AnimatedRotation(
                    duration: move,
                    curve: Curves.easeOutCubic,
                    turns: depth == 0 ? 0 : (depth.isOdd ? -.010 : .008),
                    child: TweenAnimationBuilder<double>(
                        tween: Tween(begin: reduceMotion ? 1 : 0, end: 1),
                        duration: const Duration(milliseconds: 460),
                        curve: Curves.easeOutBack,
                        builder: (context, t, card) => Transform.translate(
                            offset: Offset(0, 420 * (1 - t)),
                            child: Transform.rotate(
                                angle: .14 * (1 - t), child: card)),
                        child: child)))));
  }
}
