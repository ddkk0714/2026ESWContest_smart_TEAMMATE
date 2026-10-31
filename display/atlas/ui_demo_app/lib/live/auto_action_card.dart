import 'package:flutter/material.dart';

import '../developer/pr74/deskmate_theme.dart';
import 'auto_action.dart';

/// hub 자동 실행 카드 — 제목(무엇을), 이유 한 줄, 개인화 한 줄, 되돌리기(60 s)·닫기.
class AutoActionCardView extends StatelessWidget {
  const AutoActionCardView({
    super.key,
    required this.card,
    required this.now,
    required this.onUndo,
    required this.onDismiss,
  });

  final AutoActionCard card;
  final DateTime now;
  final VoidCallback onUndo;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final canUndo = card.canUndo(now);
    final status = card.statusText;
    final lines = [
      if (card.reason != null) card.reason!,
      if (card.personalNote != null) card.personalNote!,
      if (status != null) status,
    ];
    return Material(
      key: const ValueKey('auto-action-card'),
      color: const Color(0xFFFFFEF8),
      borderRadius: BorderRadius.circular(22),
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 6, 10),
        child: Row(children: [
          const Icon(Icons.auto_awesome_rounded,
              size: 22, color: DeskmateColors.accentStrong),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(card.title,
                    key: const ValueKey('auto-action-title'),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: text.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700)),
                for (final (i, line) in lines.indexed)
                  Text(line,
                      key: ValueKey('auto-action-line-$i'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: text.bodyMedium),
              ],
            ),
          ),
          if (canUndo) ...[
            const SizedBox(width: 8),
            OutlinedButton(
              key: const ValueKey('auto-action-undo'),
              onPressed: onUndo,
              child: Text('되돌리기 ${card.undoSecondsLeft(now)}초'),
            ),
          ],
          IconButton(
            key: const ValueKey('auto-action-dismiss'),
            tooltip: '닫기',
            onPressed: onDismiss,
            icon: const Icon(Icons.close_rounded),
          ),
        ]),
      ),
    );
  }
}

/// hub 가 빠른 시연 모드면 화면 구석에 붙는 표시 — 심사위원에게 "확인 시간을 줄인 시연" 임을 숨기지 않는다.
class DemoFastBadge extends StatelessWidget {
  const DemoFastBadge({super.key});

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('demo-fast-badge'),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: DeskmateColors.accentStrong,
          borderRadius: BorderRadius.circular(DeskmateRadius.pill),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.fast_forward_rounded, size: 16, color: Colors.white),
          const SizedBox(width: 4),
          Text('빠른 시연',
              style: Theme.of(context)
                  .textTheme
                  .labelMedium
                  ?.copyWith(color: Colors.white, fontWeight: FontWeight.w700)),
        ]),
      );
}
