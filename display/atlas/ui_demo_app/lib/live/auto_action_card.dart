import 'package:flutter/material.dart';

import '../developer/pr74/deskmate_theme.dart';

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
