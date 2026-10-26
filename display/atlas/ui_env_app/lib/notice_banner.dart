import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'voice_alert.dart';

/// 화면 알림 한 장 — 쉬기 알림(rest_notice)·자동 정보 카드·TTS 대신 띄운 문구.
/// 버튼은 닫기 하나. 쉬는 시간이 있으면 남은 시간을 거꾸로 센다.
class NoticeBanner extends StatelessWidget {
  const NoticeBanner({
    super.key,
    required this.notice,
    required this.now,
    required this.onDismiss,
  });

  final ScreenNotice notice;
  final DateTime now;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final remaining = notice.remaining(now);
    final rest = notice.kind == 'rest_notice';
    return Material(
      key: const ValueKey('notice-banner'),
      color: DeskmateColors.accent.withValues(alpha: 0.28),
      borderRadius: BorderRadius.circular(DeskmateRadius.control),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 6, 8),
        child: Row(children: [
          Icon(
              rest
                  ? Icons.self_improvement_rounded
                  : Icons.notifications_active_rounded,
              size: 20,
              color: DeskmateColors.accentStrong),
          const SizedBox(width: 10),
          if (notice.label != null) ...[
            Container(
              key: const ValueKey('notice-label'),
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: DeskmateColors.surfaceRaised,
                borderRadius: BorderRadius.circular(DeskmateRadius.pill),
              ),
              child: Text(notice.label!,
                  style: Theme.of(context).textTheme.labelMedium),
            ),
            const SizedBox(width: 8),
          ],
          Expanded(
            child: Text(
              notice.text,
              key: const ValueKey('notice-text'),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context)
                  .textTheme
                  .bodyLarge
                  ?.copyWith(fontWeight: FontWeight.w600),
            ),
          ),
          if (remaining != null) ...[
            const SizedBox(width: 12),
            Text(
              formatCountdown(remaining),
              key: const ValueKey('notice-countdown'),
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  fontFeatures: const [FontFeature.tabularFigures()]),
            ),
          ],
          IconButton(
            key: const ValueKey('notice-dismiss'),
            tooltip: '닫기',
            onPressed: onDismiss,
            icon: const Icon(Icons.close_rounded),
          ),
        ]),
      ),
    );
  }
}

/// 1:05 처럼 분:초.
String formatCountdown(Duration value) {
  final seconds = value.inSeconds;
  return '${seconds ~/ 60}:${(seconds % 60).toString().padLeft(2, '0')}';
}
