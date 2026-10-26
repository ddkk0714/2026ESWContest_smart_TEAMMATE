/// 명령 로그 패널 — [CommandLog] 의 줄을 고정폭으로 쌓고, 새 줄이 오면 맨 아래로 따라간다.
///
/// 색은 세 가지만: 기본(CMD·REQUEST·진행 중 RESULT), 성공 RESULT, 실패 RESULT. FEEDBACK 은 흐리게.
library;

import 'package:flutter/material.dart';

import 'command_log.dart';
import 'deskmate_theme.dart';

class CommandLogPanel extends StatefulWidget {
  const CommandLogPanel({super.key, required this.log, this.onClear});

  final CommandLog log;

  /// 지우기 버튼. null 이면 [CommandLog.clear] 를 직접 부른다.
  final VoidCallback? onClear;

  @override
  State<CommandLogPanel> createState() => _CommandLogPanelState();
}

class _CommandLogPanelState extends State<CommandLogPanel> {
  final _scroll = ScrollController();
  int _seen = 0;

  static const _mono = TextStyle(
      fontFamily: 'LGText',
      fontFamilyFallback: ['Courier New', 'Menlo', 'DejaVu Sans Mono'],
      fontSize: 12.5,
      height: 1.3,
      color: DeskmateColors.ink);

  static const _ok = Color(0xFF2F7D4F);

  @override
  void initState() {
    super.initState();
    widget.log.addListener(_changed);
  }

  @override
  void didUpdateWidget(CommandLogPanel old) {
    super.didUpdateWidget(old);
    if (!identical(old.log, widget.log)) {
      old.log.removeListener(_changed);
      widget.log.addListener(_changed);
    }
  }

  @override
  void dispose() {
    widget.log.removeListener(_changed);
    _scroll.dispose();
    super.dispose();
  }

  void _changed() {
    if (!mounted) return;
    setState(() {});
    // 새 줄이 들어오면 맨 아래로. 레이아웃이 끝난 뒤에 움직여야 한다.
    final grew = widget.log.length > _seen;
    _seen = widget.log.length;
    if (grew) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted || !_scroll.hasClients) return;
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final entries = widget.log.entries;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(children: [
        Expanded(
          child: Text(
              '명령 로그 · control/cmd · control/result · interaction/request · feedback/user',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style:
                  _mono.copyWith(fontSize: 11, color: DeskmateColors.inkMuted)),
        ),
        Text('${entries.length}/${widget.log.maxEntries}',
            key: const ValueKey('cmdlog-count'),
            style:
                _mono.copyWith(fontSize: 11, color: DeskmateColors.inkMuted)),
        const SizedBox(width: 8),
        TextButton(
          key: const ValueKey('cmdlog-clear'),
          onPressed: widget.onClear ?? widget.log.clear,
          style: TextButton.styleFrom(
              visualDensity: VisualDensity.compact,
              padding: const EdgeInsets.symmetric(horizontal: 8)),
          child: const Text('지우기'),
        ),
      ]),
      Expanded(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
            color: DeskmateColors.surfaceMuted,
            borderRadius: BorderRadius.circular(6),
          ),
          child: entries.isEmpty
              ? Text('(아직 없음 — hub 가 명령·제안을 내면 여기에 쌓인다)',
                  key: const ValueKey('cmdlog-empty'),
                  style: _mono.copyWith(color: DeskmateColors.inkFaint))
              : ListView.builder(
                  key: const ValueKey('cmdlog-list'),
                  controller: _scroll,
                  itemCount: entries.length,
                  itemExtent: 17,
                  itemBuilder: (context, i) {
                    final e = entries[i];
                    return Text(e.line,
                        key: ValueKey('cmdlog-$i'),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: _mono.copyWith(color: _color(e)));
                  },
                ),
        ),
      ),
    ]);
  }

  static Color _color(CommandLogEntry e) {
    if (e.kind == CommandLogKind.result) {
      return switch (e.ok) {
        true => _ok,
        false => DeskmateColors.offline,
        null => DeskmateColors.ink,
      };
    }
    if (e.kind == CommandLogKind.feedback) return DeskmateColors.inkMuted;
    return DeskmateColors.ink;
  }
}
