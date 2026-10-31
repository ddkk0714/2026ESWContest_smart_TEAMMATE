/// Local timer summaries. Sensor-derived evaluation is deliberately absent until
/// a matching session report is supplied by the hub. Demo values are explicit.
class FocusReport {
  const FocusReport(
      {required this.startedAt,
      required this.endedAt,
      required this.timerSeconds,
      required this.demo});
  final DateTime startedAt;
  final DateTime endedAt;
  final int timerSeconds;
  final bool demo;
  int? get focusedSeconds => demo ? (timerSeconds * .84).round() : null;
  int? get posturePercent => demo ? 78 : null;
  Map<String, Object> toJson() => {
        'startedAt': startedAt.toIso8601String(),
        'endedAt': endedAt.toIso8601String(),
        'timerSeconds': timerSeconds,
        'demo': demo,
      };
  static FocusReport? parse(Object? raw) {
    if (raw is! Map) return null;
    final start = DateTime.tryParse('${raw['startedAt']}');
    final end = DateTime.tryParse('${raw['endedAt']}');
    final seconds = raw['timerSeconds'];
    if (start == null ||
        end == null ||
        end.isBefore(start) ||
        seconds is! int ||
        seconds < 1 ||
        seconds > 10800 ||
        raw['demo'] is! bool) {
      return null;
    }
    return FocusReport(
        startedAt: start,
        endedAt: end,
        timerSeconds: seconds,
        demo: raw['demo']);
  }
}

bool sameFocusDay(DateTime a, DateTime b) =>
    a.year == b.year && a.month == b.month && a.day == b.day;
String focusDuration(int seconds) {
  final minutes = seconds ~/ 60;
  return minutes > 0
      ? '$minutes min${seconds % 60 == 0 ? '' : ' ${seconds % 60} sec'}'
      : '$seconds sec';
}
