import 'dart:async';

import 'package:flutter/material.dart';

import 'posture_source.dart';
import 'posture_state.dart';

/// PR 20의 자세 판정 결과만 보여주는 화면이다.
/// 원시 카메라 프레임·이미지·랜드마크는 이 앱에서 읽거나 표시하지 않는다.
class PostureStatusPage extends StatefulWidget {
  const PostureStatusPage({super.key, required this.hubUrl});

  final String hubUrl;

  @override
  State<PostureStatusPage> createState() => _PostureStatusPageState();
}

class _PostureStatusPageState extends State<PostureStatusPage> {
  late final PostureSource _source;
  Timer? _timer;
  PostureState? _state;
  LinkHealth? _health;
  String? _error;
  bool _busy = false;
  bool _calibrating = false;
  int _ticks = 0;

  @override
  void initState() {
    super.initState();
    _source = widget.hubUrl.trim().isEmpty
        ? DemoPostureSource()
        : HttpPostureSource(widget.hubUrl);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _source.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    if (_busy) return;
    _busy = true;
    try {
      final state = await _source.fetch();
      if (mounted) setState(() {
        _state = state;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      _busy = false;
    }
    if (_ticks++ % 5 == 0) {
      final health = await _source.health();
      if (mounted) setState(() => _health = health);
    }
  }

  Future<void> _calibrate() async {
    if (!_source.canCalibrate || _calibrating) return;
    final approved = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('자세 기준 다시 잡기'),
            content: const Text('바른 자세로 앉은 상태에서 새 기준을 잡습니다.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
              FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('시작')),
            ],
          ),
        ) ?? false;
    if (!approved) return;
    setState(() => _calibrating = true);
    try {
      await _source.calibrate();
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('자세 기준 측정을 시작했습니다.')),
      );
    } catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('기준 측정 실패: $error')),
      );
    } finally {
      if (mounted) setState(() => _calibrating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    final healthy = _health?.healthy ?? _error == null;
    final color = _color(state?.label);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(children: [
        Text('자세', style: Theme.of(context).textTheme.headlineSmall),
        const Spacer(),
        Icon(healthy ? Icons.link : Icons.link_off, color: healthy ? Colors.tealAccent : Colors.redAccent),
        const SizedBox(width: 6),
        Text(_source.label),
        if (_source.canCalibrate) ...[
          const SizedBox(width: 12),
          FilledButton.icon(
            key: const ValueKey('posture-calibrate'),
            onPressed: _calibrating ? null : _calibrate,
            icon: _calibrating
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.restart_alt),
            label: const Text('기준 다시 잡기'),
          ),
        ],
      ]),
      const SizedBox(height: 12),
      if (_error != null) _Notice(text: '자세 판정 연결 대기: $_error', color: Colors.redAccent),
      if (_health?.stale ?? false) const _Notice(text: '센서 프레임이 갱신되지 않았습니다.', color: Colors.amberAccent),
      const SizedBox(height: 12),
      Expanded(
        child: state == null
            ? const Center(child: CircularProgressIndicator())
            : Row(children: [
                Expanded(
                  flex: 4,
                  child: Card(
                    child: Center(
                      child: Column(mainAxisSize: MainAxisSize.min, children: [
                        Icon(_icon(state.label), size: 68, color: color),
                        const SizedBox(height: 16),
                        Text(_label(state.label), style: Theme.of(context).textTheme.headlineMedium),
                        const SizedBox(height: 8),
                        Text(state.scenario ?? (state.valid ? '자세 판정 정상' : '기준을 측정 중입니다'), textAlign: TextAlign.center),
                      ]),
                    ),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  flex: 5,
                  child: Card(
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('판정 지표', style: Theme.of(context).textTheme.titleLarge),
                        const SizedBox(height: 14),
                        _Metric('재실', state.present ? '감지' : '없음'),
                        _Metric('판정 상태', state.valid ? '신뢰 가능' : '기준 측정 중'),
                        _Metric('머리 거리 변화', state.headDeltaMm == null ? '--' : '${state.headDeltaMm!.toStringAsFixed(0)} mm'),
                        _Metric('꾸벅임', state.nodPerMin == null ? '--' : '${state.nodPerMin!.toStringAsFixed(1)}회/분'),
                        _Metric('움직임', '${(state.motion * 100).round()}%'),
                        _Metric('검출 범위', '${(state.coverage * 100).round()}%'),
                        _Metric('집중 저하 기여', '${(state.focusDrop * 100).round()}%'),
                        _Metric('피로 기여', '${(state.fatigue * 100).round()}%'),
                        const Spacer(),
                        Text(state.reasons.isEmpty ? '판정 근거 없음' : state.reasons.join(' · '),
                            style: Theme.of(context).textTheme.bodySmall),
                      ]),
                    ),
                  ),
                ),
              ]),
      ),
    ]);
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value);
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(children: [Text(label), const Spacer(), Text(value, style: const TextStyle(fontWeight: FontWeight.w700))]),
      );
}

class _Notice extends StatelessWidget {
  const _Notice({required this.text, required this.color});
  final String text;
  final Color color;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: color.withValues(alpha: .12), borderRadius: BorderRadius.circular(12)),
        child: Text(text, style: TextStyle(color: color)),
      );
}

String _label(PostureLabel label) => switch (label) {
      PostureLabel.upright => '바른 자세',
      PostureLabel.slump => '엎드림',
      PostureLabel.recline => '뒤로 젖힘',
      PostureLabel.drowsy => '졸음',
      PostureLabel.chinRest => '턱 괴기',
      PostureLabel.absent => '자리 비움',
      PostureLabel.baseline => '기준 측정 중',
      PostureLabel.unknown => '기준 없음',
    };

IconData _icon(PostureLabel label) => switch (label) {
      PostureLabel.drowsy => Icons.bedtime_outlined,
      PostureLabel.absent => Icons.person_off_outlined,
      PostureLabel.baseline || PostureLabel.unknown => Icons.straighten,
      _ => Icons.chair_alt,
    };

Color _color(PostureLabel? label) => switch (label) {
      PostureLabel.upright => Colors.tealAccent,
      PostureLabel.slump || PostureLabel.drowsy => Colors.redAccent,
      PostureLabel.recline || PostureLabel.chinRest => Colors.amberAccent,
      _ => Colors.blueGrey,
    };