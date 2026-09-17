import 'package:flutter/material.dart';

import 'deskmate_theme.dart';

class SessionReport {
  const SessionReport({
    required this.startedAt,
    required this.endedAt,
    required this.durationSeconds,
    required this.focusSeconds,
    required this.focusRatio,
    required this.fatigueEpisodes,
    required this.interventionTotal,
    required this.interventionRecovered,
    required this.breakAcceptRate,
    required this.stateDurations,
  });

  final DateTime startedAt;
  final DateTime endedAt;
  final double durationSeconds;
  final double focusSeconds;
  final double focusRatio;
  final int fatigueEpisodes;
  final int interventionTotal;
  final int interventionRecovered;
  final double? breakAcceptRate;
  final Map<String, double> stateDurations;

  factory SessionReport.fromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['schema_version'] != '1.0') {
      throw const FormatException('unsupported session report schema');
    }
    final data = envelope['data'];
    if (data is! Map<String, dynamic>) {
      throw const FormatException('session report data is missing');
    }
    final counts = data['intervention_counts'];
    final durations = data['state_durations_s'];
    final episodes = data['fatigue_episodes'];
    return SessionReport(
      startedAt: _epoch(data['t_start']),
      endedAt: _epoch(data['t_end']),
      durationSeconds: _number(data['duration_s']),
      focusSeconds: _number(data['focus_time_s']),
      focusRatio: _number(data['focus_ratio']).clamp(0.0, 1.0).toDouble(),
      fatigueEpisodes: episodes is List ? episodes.length : 0,
      interventionTotal:
          counts is Map ? (counts['total'] as num?)?.toInt() ?? 0 : 0,
      interventionRecovered:
          counts is Map ? (counts['recovered'] as num?)?.toInt() ?? 0 : 0,
      breakAcceptRate: data['break_accept_rate'] is num
          ? (data['break_accept_rate'] as num).toDouble().clamp(0.0, 1.0).toDouble()
          : null,
      stateDurations: durations is Map
          ? durations.map((key, value) =>
              MapEntry(key.toString(), value is num ? value.toDouble() : 0.0))
          : const {},
    );
  }

  static double _number(Object? value) =>
      value is num ? value.toDouble() : 0.0;

  static DateTime _epoch(Object? value) => DateTime.fromMillisecondsSinceEpoch(
        ((_number(value)) * 1000).round(),
      );
}

class SessionReportCard extends StatelessWidget {
  const SessionReportCard({super.key, required this.report});

  final SessionReport? report;

  @override
  Widget build(BuildContext context) {
    final value = report;
    if (value == null) {
      return const Center(child: Text('완료된 세션 리포트를 기다리고 있습니다.'));
    }
    final topStates = value.stateDurations.entries
        .where((entry) => entry.value > 0)
        .toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    return SingleChildScrollView(
      child: Container(
        key: const ValueKey('session-report-card'),
        padding: const EdgeInsets.all(24),
        decoration: BoxDecoration(
          color: DeskmateColors.surface,
          borderRadius: BorderRadius.circular(24),
          border: Border.all(color: Colors.white.withValues(alpha: .36)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('세션 리포트', style: Theme.of(context).textTheme.headlineLarge),
          const SizedBox(height: 6),
          Text('${_date(value.startedAt)} · ${_duration(value.durationSeconds)}',
              style: Theme.of(context).textTheme.bodyMedium),
          const SizedBox(height: 24),
          Wrap(spacing: 16, runSpacing: 16, children: [
            _Metric(label: '몰입 시간', value: _duration(value.focusSeconds)),
            _Metric(label: '몰입 비율', value: _percent(value.focusRatio)),
            _Metric(label: '피로 에피소드', value: '${value.fatigueEpisodes}회'),
            _Metric(label: '개입', value: '${value.interventionTotal}건'),
            _Metric(label: '회복 성공', value: '${value.interventionRecovered}건'),
            _Metric(
              label: '휴식 수락률',
              value: value.breakAcceptRate == null
                  ? '--'
                  : _percent(value.breakAcceptRate!),
            ),
          ]),
          if (topStates.isNotEmpty) ...[
            const SizedBox(height: 28),
            Text('상태별 체류', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 12),
            _StateDurationsChart(
                entries: topStates.take(6).toList(),
                sessionDurationSeconds: value.durationSeconds),
          ],
        ]),
      ),
    );
  }
}

class _StateDurationsChart extends StatelessWidget {
  const _StateDurationsChart({
    required this.entries,
    required this.sessionDurationSeconds,
  });

  final List<MapEntry<String, double>> entries;
  final double sessionDurationSeconds;

  @override
  Widget build(BuildContext context) {
    final recordedSeconds =
        entries.fold<double>(0, (sum, entry) => sum + entry.value);
    final denominator = sessionDurationSeconds > 0
        ? sessionDurationSeconds
        : recordedSeconds;

    return Semantics(
      key: const ValueKey('state-duration-chart'),
      label: 'State duration bar chart',
      child: Column(
        children: [
          for (final entry in entries)
            _StateDurationBar(
              state: entry.key,
              seconds: entry.value,
              ratio: denominator <= 0
                  ? 0
                  : (entry.value / denominator).clamp(0.0, 1.0).toDouble(),
            ),
        ],
      ),
    );
  }
}

class _StateDurationBar extends StatelessWidget {
  const _StateDurationBar({
    required this.state,
    required this.seconds,
    required this.ratio,
  });

  final String state;
  final double seconds;
  final double ratio;

  @override
  Widget build(BuildContext context) {
    final percentage = _percent(ratio);
    return Semantics(
      label: '$state $percentage, ${_duration(seconds)}',
      child: Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(state)),
            Text('$percentage (${_duration(seconds)})',
                style: Theme.of(context).textTheme.bodyMedium),
          ]),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(DeskmateRadius.pill),
            child: SizedBox(
              height: 10,
              child: Stack(children: [
                const Positioned.fill(
                    child: ColoredBox(color: DeskmateColors.surfaceMuted)),
                FractionallySizedBox(
                  widthFactor: ratio,
                  alignment: Alignment.centerLeft,
                  child: ColoredBox(color: _barColor(state)),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  static Color _barColor(String state) {
    final normalized = state.toUpperCase();
    if (normalized.contains('FATIGUE')) return DeskmateColors.warning;
    if (normalized.contains('RECOVERY') || normalized.contains('REST')) {
      return DeskmateColors.accent;
    }
    return DeskmateColors.accentStrong;
  }
}
class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: 190,
        child: Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: DeskmateColors.surfaceRaised,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label, style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 8),
            Text(value, style: Theme.of(context).textTheme.headlineMedium),
          ]),
        ),
      );
}

String _percent(double value) => '${(value * 100).round()}%';
String _duration(double seconds) {
  final minutes = (seconds / 60).round();
  if (minutes < 60) return '$minutes분';
  return '${minutes ~/ 60}시간 ${minutes % 60}분';
}

String _date(DateTime value) =>
    '${value.year}.${value.month.toString().padLeft(2, '0')}.${value.day.toString().padLeft(2, '0')}';