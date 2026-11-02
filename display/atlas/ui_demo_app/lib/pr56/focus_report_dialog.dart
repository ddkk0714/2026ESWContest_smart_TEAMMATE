import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'focus_report.dart';
import 'irumae.dart';

class FocusReportDialog extends StatelessWidget {
  const FocusReportDialog(
      {super.key,
      required this.report,
      required this.today,
      this.preview = false});
  final FocusReport report;
  final List<FocusReport> today;
  final bool preview;
  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final total = today.fold<int>(0, (sum, item) => sum + item.timerSeconds);
    String clock(DateTime date) =>
        '${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
    Widget card(IconData icon, String title, String value, String detail,
            double width) =>
        Container(
            width: width,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
                color: colors.primary.withValues(alpha: .055),
                borderRadius: BorderRadius.circular(20),
                border:
                    Border.all(color: colors.primary.withValues(alpha: .13))),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(icon, size: 21, color: colors.primary),
                const SizedBox(width: 7),
                Text(title,
                    style:
                        TextStyle(color: colors.onSurfaceVariant, fontSize: 15))
              ]),
              const SizedBox(height: 12),
              Text(value,
                  style: TextStyle(fontSize: 23, color: colors.primary)),
              const SizedBox(height: 7),
              Text(detail,
                  style: TextStyle(
                      fontSize: 13,
                      color: colors.onSurfaceVariant,
                      height: 1.5)),
            ]));
    return Dialog(
      key: const ValueKey('focus-report-dialog'),
      insetPadding: const EdgeInsets.all(20),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
      child: SizedBox(
          width: 740,
          height: math.min(500, MediaQuery.sizeOf(context).height - 40),
          child: Padding(
              padding: const EdgeInsets.fromLTRB(22, 18, 22, 14),
              child: Column(children: [
                Expanded(
                    child: SingleChildScrollView(
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                      Row(children: [
                        Expanded(
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                              Text(preview ? '집중 리포트 미리보기' : '이번 집중을 마쳤어요',
                                  style: TextStyle(
                                      fontSize: 23, color: colors.onSurface)),
                              const SizedBox(height: 7),
                              Text(
                                  '${report.endedAt.month}월 ${report.endedAt.day}일 · ${clock(report.startedAt)}–${clock(report.endedAt)}',
                                  style: TextStyle(
                                      fontSize: 13,
                                      color: colors.onSurfaceVariant)),
                            ])),
                        const SizedBox(width: 8),
                        const SizedBox(
                            width: 80,
                            height: 76,
                            child:
                                IrumaeAnimation(mood: IrumaeMood.celebrating)),
                      ]),
                      if (report.demo)
                        Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: Text('집중 비율과 자세 정보는 센서 연결 후 확인할 수 있어요.',
                                style: TextStyle(
                                    fontSize: 12,
                                    color: colors.onSurfaceVariant))),
                      Text('진행 시간 중 집중한 시간',
                          style: TextStyle(
                              fontSize: 15, color: colors.onSurfaceVariant)),
                      const SizedBox(height: 7),
                      FittedBox(
                          fit: BoxFit.scaleDown,
                          alignment: Alignment.centerLeft,
                          child: Text(
                              report.focusedSeconds == null
                                  ? '분석 연결 대기'
                                  : focusDuration(report.focusedSeconds!),
                              style: TextStyle(
                                  fontSize: 42,
                                  color: colors.primary,
                                  height: 1.15))),
                      const SizedBox(height: 7),
                      Text(
                          '타이머 ${focusDuration(report.timerSeconds)} · ${report.demo ? '집중 시간 기록' : '센서 리포트가 연결되면 집중 비율을 표시해요'}',
                          style: TextStyle(
                              fontSize: 14, color: colors.onSurfaceVariant)),
                      const SizedBox(height: 12),
                      ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: LinearProgressIndicator(
                              value: report.demo ? .84 : 0,
                              minHeight: 8,
                              backgroundColor:
                                  colors.primary.withValues(alpha: .1))),
                      const SizedBox(height: 20),
                      LayoutBuilder(builder: (context, bounds) {
                        final width = bounds.maxWidth >= 580
                            ? (bounds.maxWidth - 24) / 3
                            : bounds.maxWidth;
                        return Wrap(spacing: 12, runSpacing: 12, children: [
                          card(
                              Icons.accessibility_new_rounded,
                              '자세',
                              report.demo ? '바른 자세 78%' : '분석 연결 대기',
                              report.demo
                                  ? '자세 정보를 기다리고 있어요.'
                                  : '자세 데이터가 없어 평가하지 않았어요.',
                              width),
                          card(
                              Icons.local_fire_department_rounded,
                              '오늘 누적',
                              focusDuration(total),
                              '오늘 완료한 타이머 ${today.length}회${preview ? ' · 미리보기 제외' : ''}\n${report.demo ? '집중 기록' : '진행 시간 기준 · 집중 추정과 구분'}',
                              width),
                          card(
                              Icons.auto_awesome_rounded,
                              '집중의 질',
                              report.demo ? '안정적인 흐름' : '분석 연결 대기',
                              report.demo
                                  ? '집중 정보를 기다리고 있어요.'
                                  : '평가 기준과 분석 데이터가 연결되면 보여드려요.',
                              width),
                        ]);
                      }),
                      const SizedBox(height: 12),
                    ]))),
                const SizedBox(height: 8),
                Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                  TextButton(
                      key: const ValueKey('focus-report-close'),
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('닫기')),
                  const SizedBox(width: 10),
                  FilledButton(
                      key: const ValueKey('focus-report-today'),
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('오늘 기록 보기')),
                ]),
              ]))),
    );
  }
}
