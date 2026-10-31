import 'dart:convert';

import 'package:flutter/material.dart';

import 'control_progress.dart';

/// 개발자 화면 '제어 진행' 탭 — 명령 하나당 한 줄(최신이 위): 시각 · 왜 · 기기·동작·값 · 결과·소요 · 되돌림.
/// 줄을 누르면 레코드 전체 필드를 펼친다. 화면이 떠 있는 동안만 hub 를 폴링한다.
class ControlProgressPage extends StatefulWidget {
  const ControlProgressPage(
      {super.key, required this.progress, required this.host});
  final ControlProgress progress;

  /// Pi 4 주소(hub :8765). null 이면 MQTT 추정만.
  final String? host;

  @override
  State<ControlProgressPage> createState() => _ControlProgressPageState();
}

class _ControlProgressPageState extends State<ControlProgressPage> {
  final _open = <String>{};

  static const _mono = TextStyle(
      fontFamily: 'LGText',
      fontSize: 12,
      fontFeatures: [FontFeature.tabularFigures()]);

  @override
  void initState() {
    super.initState();
    widget.progress.start(widget.host);
  }

  @override
  void didUpdateWidget(ControlProgressPage old) {
    super.didUpdateWidget(old);
    if (old.host != widget.host) widget.progress.start(widget.host);
  }

  @override
  void dispose() {
    widget.progress.stop();
    super.dispose();
  }

  static String _clock(DateTime? t) => t == null
      ? '--:--:--'
      : '${t.hour.toString().padLeft(2, '0')}:'
          '${t.minute.toString().padLeft(2, '0')}:'
          '${t.second.toString().padLeft(2, '0')}';

  static Color _statusColor(String status) => switch (status) {
        'succeeded' => const Color(0xFF2E7D4F),
        'failed' || 'timeout' => const Color(0xFFB3261E),
        'skipped' || 'cancelled' => const Color(0xFF7A7A7A),
        _ => const Color(0xFF9A6A00),
      };

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
      animation: widget.progress,
      builder: (context, _) {
        final p = widget.progress;
        final hub = p.source == ControlProgressSource.hub;
        final records = p.records;
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                const Text('제어 진행',
                    style:
                        TextStyle(fontSize: 15, fontWeight: FontWeight.w800)),
                const SizedBox(width: 10),
                Container(
                  key: const ValueKey('progress-source'),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                      color: hub
                          ? const Color(0xFF2E7D4F)
                          : const Color(0xFF9A6A00),
                      borderRadius: BorderRadius.circular(10)),
                  child: Text(hub ? 'hub 기록' : 'MQTT 추정',
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 12,
                          fontWeight: FontWeight.w700)),
                ),
                const SizedBox(width: 10),
                Text('${records.length}건 · 최신이 위', style: _mono),
                const Spacer(),
                Text(
                    hub
                        ? '${widget.host}:$controlLogPort · ${_clock(p.hubAt)}'
                        : 'control/cmd · control/result',
                    style: _mono),
              ]),
              if (p.hubProblem != null)
                Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(p.hubProblem!,
                        key: const ValueKey('progress-hub-problem'),
                        style: _mono.copyWith(color: const Color(0xFFB3261E)))),
              const SizedBox(height: 6),
              Expanded(
                  child: records.isEmpty
                      ? const Center(
                          child: Text('아직 나간 제어 명령이 없어요',
                              key: ValueKey('progress-empty')))
                      : ListView.separated(
                          itemCount: records.length,
                          separatorBuilder: (_, __) =>
                              const Divider(height: 1),
                          itemBuilder: (context, i) => _row(records[i]))),
            ]);
      });

  Widget _row(ControlRecord r) {
    final open = _open.contains(r.commandId);
    final latency = r.latencyMs;
    final undo = r.undoKo;
    return InkWell(
        key: ValueKey('progress-row-${r.commandId}'),
        onTap: () => setState(() =>
            open ? _open.remove(r.commandId) : _open.add(r.commandId)),
        child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(children: [
                    SizedBox(
                        width: 64, child: Text(_clock(r.ts), style: _mono)),
                    Expanded(
                        flex: 4,
                        child: Text(r.why.isEmpty ? '-' : r.why,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: _mono)),
                    Expanded(
                        flex: 5,
                        child: Text(r.what,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: _mono.copyWith(fontWeight: FontWeight.w700))),
                    SizedBox(
                        width: 150,
                        child: Text(r.statusKo,
                            key: ValueKey('progress-status-${r.commandId}'),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: _mono.copyWith(
                                color: _statusColor(r.status),
                                fontWeight: FontWeight.w700))),
                    SizedBox(
                        width: 64,
                        child: Text(latency == null ? '' : '$latency ms',
                            textAlign: TextAlign.right, style: _mono)),
                    SizedBox(
                        width: 120,
                        child: Text(undo ?? '',
                            textAlign: TextAlign.right,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: _mono)),
                  ]),
                  if (open)
                    Padding(
                        padding: const EdgeInsets.only(left: 64, top: 4),
                        child: SelectableText(
                            const JsonEncoder.withIndent('  ')
                                .convert(r.fields),
                            key: ValueKey('progress-detail-${r.commandId}'),
                            style: _mono.copyWith(fontSize: 11))),
                ])));
  }
}
