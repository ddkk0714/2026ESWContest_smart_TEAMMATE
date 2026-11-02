import 'package:flutter/material.dart';
import 'live/live_request.dart';

class SuggestionCard extends StatelessWidget {
  const SuggestionCard(
      {super.key,
      required this.title,
      this.subtitle,
      this.details,
      required this.actions});
  final String title;
  final String? subtitle;
  final SuggestionDetails? details;
  final List<Widget> actions;

  static const ink = Color(0xFF263B37);
  static const green = Color(0xFF356C59);
  static const muted = Color(0xFF7A8580);

  @override
  Widget build(BuildContext context) => GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () {},
      child: ColoredBox(
        color: const Color(0xFFF4F2EB).withValues(alpha: .65),
        child: Center(
            child: Container(
          key: const ValueKey('suggestion-card'),
          width: details == null ? 600 : 860,
          constraints:
              BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height - 40),
          margin: const EdgeInsets.symmetric(horizontal: 24),
          padding: const EdgeInsets.all(28),
          decoration: BoxDecoration(
              color: const Color(0xFFFFFEF8),
              borderRadius: BorderRadius.circular(32),
              border: Border.all(color: const Color(0xFFD5DFCF)),
              boxShadow: [
                BoxShadow(
                    color: ink.withValues(alpha: .18),
                    blurRadius: 36,
                    offset: const Offset(0, 12))
              ]),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              Container(
                  padding: const EdgeInsets.all(9),
                  decoration: const BoxDecoration(
                      color: Color(0xFFE7EEDF), shape: BoxShape.circle),
                  child:
                      const Icon(Icons.spa_outlined, color: green, size: 24)),
              const SizedBox(width: 12),
              const Text('당신의 몰입을 위해, 작은 제안',
                  style: TextStyle(color: green, fontSize: 14)),
            ]),
            const SizedBox(height: 14),
            Text(title,
                textAlign: TextAlign.center,
                style: const TextStyle(color: ink, fontSize: 30, height: 1.35)),
            if (subtitle != null) ...[
              const SizedBox(height: 10),
              Text(subtitle!,
                  textAlign: TextAlign.center,
                  style:
                      const TextStyle(color: muted, fontSize: 15, height: 1.5)),
            ],
            if (details case final info?) ...[
              const SizedBox(height: 22),
              Flexible(child: SingleChildScrollView(
                  child: LayoutBuilder(builder: (context, bounds) {
                final reason =
                    _section(Icons.favorite_outline, '이 제안을 건네는 이유', [
                  Text(info.reason,
                      style: const TextStyle(
                          color: ink, fontSize: 17, height: 1.6)),
                  if (info.observations.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    const Text('책상에서 전해온 값',
                        style: TextStyle(color: muted, fontSize: 12)),
                    const SizedBox(height: 7),
                    Wrap(spacing: 6, runSpacing: 6, children: [
                      for (final value in info.observations)
                        Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 10, vertical: 7),
                            decoration: BoxDecoration(
                                color: const Color(0xFFFFFEF8),
                                borderRadius: BorderRadius.circular(12)),
                            child: Text(value,
                                style: const TextStyle(
                                    color: green, fontSize: 12))),
                    ]),
                  ],
                ]);
                final changes = _section(Icons.tune_rounded, '이렇게 도와드릴게요', [
                  for (final change in info.changes) ...[
                    Text(change.target,
                        style: const TextStyle(color: muted, fontSize: 13)),
                    const SizedBox(height: 5),
                    Text(change.amount,
                        style: const TextStyle(
                            color: ink, fontSize: 20, height: 1.4)),
                    const SizedBox(height: 14),
                  ],
                ]);
                return bounds.maxWidth < 640
                    ? Column(
                        children: [reason, const SizedBox(height: 12), changes])
                    : Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                            Expanded(child: reason),
                            const SizedBox(width: 16),
                            Expanded(child: changes)
                          ]);
              }))),
              const SizedBox(height: 16),
              Text(info.reassurance,
                  textAlign: TextAlign.center,
                  style:
                      const TextStyle(color: muted, fontSize: 12, height: 1.5)),
            ],
            if (actions.isNotEmpty) ...[
              const SizedBox(height: 22),
              Wrap(
                  alignment: WrapAlignment.center,
                  spacing: 10,
                  runSpacing: 10,
                  children: actions),
            ],
          ]),
        )),
      ));

  Widget _section(IconData icon, String title, List<Widget> content) =>
      Container(
          width: double.infinity,
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
              color: const Color(0xFFF0F3EA),
              borderRadius: BorderRadius.circular(22)),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(icon, size: 18, color: green),
              const SizedBox(width: 8),
              Expanded(
                  child: Text(title,
                      style: const TextStyle(color: green, fontSize: 14)))
            ]),
            const SizedBox(height: 12),
            ...content,
          ]));
}
