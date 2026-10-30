import 'package:flutter/material.dart';

import 'fatigue_log.dart';

/// KSS 1~9 버튼 한 줄. 1·3·5·7·9 아래에 뜻을 붙인다.
class KssPicker extends StatelessWidget {
  const KssPicker({super.key, required this.onSelected, this.selected});

  final ValueChanged<int> onSelected;
  final int? selected;

  static const _pad = ButtonStyle(
      padding: WidgetStatePropertyAll(EdgeInsets.symmetric(vertical: 14)));

  @override
  Widget build(BuildContext context) => Row(children: [
        for (var k = 1; k <= 9; k++)
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 2),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                SizedBox(
                  width: double.infinity,
                  child: selected == k
                      ? FilledButton(
                          key: ValueKey('kss-$k'),
                          onPressed: () => onSelected(k),
                          style: _pad,
                          child: Text('$k'))
                      : OutlinedButton(
                          key: ValueKey('kss-$k'),
                          onPressed: () => onSelected(k),
                          style: _pad,
                          child: Text('$k')),
                ),
                SizedBox(
                  height: 32,
                  child: Text(kssAnchors[k] ?? '',
                      textAlign: TextAlign.center,
                      maxLines: 2,
                      style: Theme.of(context).textTheme.labelSmall),
                ),
              ]),
            ),
          ),
      ]);
}

/// 어느 화면에 있든 질문 시각이 되면 위에 뜨는 카드.
class FatiguePromptCard extends StatelessWidget {
  const FatiguePromptCard({super.key, required this.onAnswer});

  final ValueChanged<int> onAnswer;

  @override
  Widget build(BuildContext context) => Card(
        key: const ValueKey('fatigue-prompt'),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('지금 얼마나 졸리세요?',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                KssPicker(onSelected: onAnswer),
              ]),
        ),
      );
}

/// 피로 기록 탭: 프로필 고르기·만들기 → 작업 시작 → 진행 중(졸려요·종료).
class FatiguePage extends StatefulWidget {
  const FatiguePage({
    super.key,
    required this.session,
    required this.store,
    required this.canSend,
    required this.onChanged,
  });

  final FatigueSession session;
  final FatigueProfileStore store;

  /// hub 로 보낼 통로(MQTT)가 있는지. 없으면 시작을 막는다 — 기록이 조용히 사라지지 않게.
  final bool canSend;
  final VoidCallback onChanged;

  @override
  State<FatiguePage> createState() => _FatiguePageState();
}

class _FatiguePageState extends State<FatiguePage> {
  late List<FatigueProfile> _profiles = widget.store.load();
  String? _selectedId;
  double _sleep = 7;
  int? _kss;
  String? _message;

  FatigueProfile? get _selected =>
      _profiles.where((p) => p.id == _selectedId).firstOrNull;

  Future<void> _editProfile(String slot) async {
    final old = _profiles.where((p) => p.slot == slot).firstOrNull;
    final made = await showDialog<FatigueProfile>(
        context: context,
        builder: (_) => _ProfileDialog(slot: slot, existing: old));
    if (made == null) return;
    final next = [..._profiles.where((p) => p.slot != slot), made]
      ..sort((a, b) => a.slot.compareTo(b.slot));
    final saved = widget.store.save(next);
    setState(() {
      _profiles = next;
      _selectedId = made.id;
      _message = saved ? null : '프로필을 저장하지 못했습니다(앱을 끄면 사라집니다).';
    });
  }

  Future<void> _start() async {
    final who = _selected, kss = _kss;
    if (who == null || kss == null) return;
    await widget.session.start(who, sleepHours: _sleep, kss: kss);
    setState(() => _kss = null);
    widget.onChanged();
  }

  Future<void> _end() async {
    final kss = await showDialog<int>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('작업 종료 — 지금 얼마나 졸리세요?'),
              content: SizedBox(
                  width: 720,
                  child: KssPicker(
                      onSelected: (k) => Navigator.pop(context, k))),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('취소')),
              ],
            ));
    if (kss == null) return;
    await widget.session.end(kss: kss);
    widget.onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.session;
    final theme = Theme.of(context);
    if (s.active) {
      final now = DateTime.now();
      final elapsed = now.difference(s.startedAt!);
      final next = s.nextPromptAt!.difference(now);
      return ListView(children: [
        Text('작업 중 · 프로필 ${s.profile!.slot}',
            style: theme.textTheme.headlineSmall),
        const SizedBox(height: 8),
        Text('경과 ${elapsed.inMinutes}분 · '
            '${s.promptDue ? '질문에 답해 주세요' : '다음 질문까지 ${next.inMinutes + 1}분'}'),
        const SizedBox(height: 16),
        if (s.promptDue)
          FatiguePromptCard(onAnswer: (k) async {
            await s.answer(k);
            widget.onChanged();
          }),
        const SizedBox(height: 16),
        Row(children: [
          FilledButton.tonalIcon(
              key: const ValueKey('fatigue-sleepy'),
              onPressed: () async {
                await s.sleepy();
                setState(() => _message = '"졸려요" 기록됨');
              },
              icon: const Icon(Icons.bedtime_outlined),
              label: const Text('졸려요')),
          const SizedBox(width: 12),
          OutlinedButton.icon(
              key: const ValueKey('fatigue-end'),
              onPressed: _end,
              icon: const Icon(Icons.stop_circle_outlined),
              label: const Text('작업 종료')),
        ]),
        if (_message != null) ...[const SizedBox(height: 8), Text(_message!)],
      ]);
    }

    return ListView(children: [
      Text('피로 기록 — 프로필 선택', style: theme.textTheme.headlineSmall),
      const SizedBox(height: 8),
      Wrap(spacing: 8, runSpacing: 8, children: [
        for (final slot in profileSlots)
          _slotChip(slot, _profiles.where((p) => p.slot == slot).firstOrNull),
      ]),
      if (_selected != null) ...[
        const SizedBox(height: 20),
        Text('어젯밤 수면 ${_sleep.toStringAsFixed(1)}시간'),
        Slider(
            key: const ValueKey('fatigue-sleep'),
            value: _sleep,
            min: 0,
            max: 12,
            divisions: 24,
            onChanged: (v) => setState(() => _sleep = v)),
        const Text('지금 얼마나 졸리세요?'),
        const SizedBox(height: 8),
        KssPicker(selected: _kss, onSelected: (k) => setState(() => _kss = k)),
        const SizedBox(height: 12),
        Row(children: [
          FilledButton.icon(
              key: const ValueKey('fatigue-start'),
              onPressed: _kss == null || !widget.canSend ? null : _start,
              icon: const Icon(Icons.play_arrow_rounded),
              label: const Text('작업 시작')),
          const SizedBox(width: 12),
          TextButton(
              onPressed: () => _editProfile(_selected!.slot),
              child: const Text('프로필 다시 만들기')),
        ]),
        if (!widget.canSend)
          const Text('위쪽에서 MQTT 브로커 주소를 먼저 입력하세요.'),
      ],
      if (_message != null) ...[const SizedBox(height: 8), Text(_message!)],
    ]);
  }

  Widget _slotChip(String slot, FatigueProfile? profile) => ChoiceChip(
        key: ValueKey('fatigue-slot-$slot'),
        label: Text(profile == null
            ? '$slot · 새로 만들기'
            : '$slot · ${chronotypes[profile.chronotype]}'),
        selected: profile != null && profile.id == _selectedId,
        onSelected: (_) => profile == null
            ? _editProfile(slot)
            : setState(() => _selectedId = profile.id),
      );
}

/// 처음 한 번 받는 프로필 답변. 다시 만들면 새 id 가 된다(다른 사람으로 취급).
class _ProfileDialog extends StatefulWidget {
  const _ProfileDialog({required this.slot, this.existing});

  final String slot;
  final FatigueProfile? existing;

  @override
  State<_ProfileDialog> createState() => _ProfileDialogState();
}

class _ProfileDialogState extends State<_ProfileDialog> {
  late String _chrono = widget.existing?.chronotype ?? 'neither';
  late double _sleep = widget.existing?.usualSleepH ?? 7;
  late String _work = widget.existing?.workType ?? 'coding';

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text('프로필 ${widget.slot}'),
        content: SizedBox(
          width: 560,
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('아침형 / 저녁형'),
                SegmentedButton<String>(
                    segments: [
                      for (final e in chronotypes.entries)
                        ButtonSegment(value: e.key, label: Text(e.value)),
                    ],
                    selected: {_chrono},
                    onSelectionChanged: (v) =>
                        setState(() => _chrono = v.first)),
                const SizedBox(height: 12),
                Text('평소 수면 ${_sleep.toStringAsFixed(1)}시간'),
                Slider(
                    value: _sleep,
                    min: 3,
                    max: 11,
                    divisions: 16,
                    onChanged: (v) => setState(() => _sleep = v)),
                const Text('주로 하는 작업'),
                SegmentedButton<String>(
                    segments: [
                      for (final e in workTypes.entries)
                        ButtonSegment(value: e.key, label: Text(e.value)),
                    ],
                    selected: {_work},
                    onSelectionChanged: (v) =>
                        setState(() => _work = v.first)),
              ]),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('취소')),
          FilledButton(
              key: const ValueKey('fatigue-profile-save'),
              onPressed: () => Navigator.pop(
                  context,
                  FatigueProfile(
                      id: FatigueProfile.newId(widget.slot),
                      slot: widget.slot,
                      chronotype: _chrono,
                      usualSleepH: _sleep,
                      workType: _work)),
              child: const Text('저장')),
        ],
      );
}
