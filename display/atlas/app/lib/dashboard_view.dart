import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';

class DashboardView extends StatelessWidget {
  const DashboardView({
    super.key,
    required this.state,
    required this.keystroke,
    required this.keystrokeReference,
    required this.onFeedback,
    required this.showDemoControl,
    required this.demoCyclingEnabled,
    required this.onToggleDemoCycling,
    required this.showFocusDetail,
    required this.onShowFocusDetail,
    this.liveKeys,
  });

  final DisplayState state;
  final KeystrokeMetrics? keystroke;
  final DateTime keystrokeReference;
  final int? liveKeys;
  final ValueChanged<String> onFeedback;
  final bool showDemoControl;
  final bool demoCyclingEnabled;
  final VoidCallback onToggleDemoCycling;
  final bool showFocusDetail;
  final ValueChanged<bool> onShowFocusDetail;

  @override
  Widget build(BuildContext context) {
    if (showFocusDetail) {
      return FocusDetailView(
        state: state,
        onClose: () => onShowFocusDetail(false),
      );
    }
    if (state.phase == 'idle') {
      return AmbientView(state: state, onDetail: () => onShowFocusDetail(true));
    }
    if (state.phase == 'end') return SessionReportView(state: state);
    if (state.phase == 'fatigue' && state.gate != 'none') {
      return SuggestionView(state: state, onFeedback: onFeedback);
    }
    if (state.phase == 'recovery') return FocusAmbientView(state: state);
    return FocusStatusView(
      state: state,
      keystroke: keystroke,
      keystrokeReference: keystrokeReference,
      liveKeys: liveKeys,
      onDetail: () => onShowFocusDetail(true),
      showDemoControl: showDemoControl,
      demoCyclingEnabled: demoCyclingEnabled,
      onToggleDemoCycling: onToggleDemoCycling,
    );
  }
}

class AmbientView extends StatelessWidget {
  const AmbientView({super.key, required this.state, required this.onDetail});
  final DisplayState state;
  final VoidCallback onDetail;

  @override
  Widget build(BuildContext context) {
    final now = state.timestamp.millisecondsSinceEpoch == 0
        ? DateTime.now()
        : state.timestamp;
    return Padding(
      padding: const EdgeInsets.fromLTRB(22, 50, 22, 8),
      child: Row(children: [
        Expanded(
          flex: 6,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Spacer(),
            Text(_clock(now), style: Theme.of(context).textTheme.displayLarge),
            const SizedBox(height: 18),
            Text(_date(now), style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 6),
            const Text('오늘도 편안하게 시작해볼까요?'),
            const Spacer(flex: 2),
            Text('상세 상태는 필요할 때만 확인할 수 있어요.',
                style: Theme.of(context).textTheme.labelMedium),
          ]),
        ),
        const SizedBox(width: 46),
        Expanded(
          flex: 4,
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            SoftPanel(
              child: Row(children: [
                Expanded(
                    child: _AmbientMetric(
                        title: '방 안 상태', value: _comfort(state))),
                const _StatusDot(),
              ]),
            ),
            const SizedBox(height: 18),
            SoftPanel(
              child: Row(children: [
                Expanded(
                  child: _AmbientMetric(
                    title: '책상 상태',
                    value: state.present == false ? '자리 비움' : '재실 감지됨',
                  ),
                ),
                Text(_environmentInline(state),
                    style: Theme.of(context).textTheme.labelMedium),
              ]),
            ),
            const SizedBox(height: 26),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                  onPressed: onDetail, child: const Text('상세 보기  ›')),
            ),
          ]),
        ),
      ]),
    );
  }
}

class FocusStatusView extends StatelessWidget {
  const FocusStatusView({
    super.key,
    required this.state,
    required this.keystroke,
    required this.keystrokeReference,
    required this.onDetail,
    required this.showDemoControl,
    required this.demoCyclingEnabled,
    required this.onToggleDemoCycling,
    this.liveKeys,
  });
  final DisplayState state;
  final KeystrokeMetrics? keystroke;
  final DateTime keystrokeReference;
  final int? liveKeys;
  final VoidCallback onDetail;
  final bool showDemoControl;
  final bool demoCyclingEnabled;
  final VoidCallback onToggleDemoCycling;

  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        final compact = constraints.maxWidth < 700;
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 18),
              Text(_focusHeadline(state),
                  style: Theme.of(context).textTheme.headlineLarge),
              const SizedBox(height: 5),
              Text(_focusSubline(state),
                  style: Theme.of(context).textTheme.bodyMedium),
              const SizedBox(height: 32),
              Expanded(
                child: Flex(
                  direction: compact ? Axis.vertical : Axis.horizontal,
                  children: [
                    Expanded(
                        child: _CurrentStatePanel(
                            state: state, onDetail: onDetail)),
                    SizedBox(width: compact ? 0 : 28, height: compact ? 16 : 0),
                    Expanded(child: _EnvironmentPanel(state: state)),
                  ],
                ),
              ),
              const SizedBox(height: 26),
              _MetricStrip(state: state),
              if (keystroke != null) ...[
                const SizedBox(height: 10),
                KeystrokeSummary(
                  metrics: keystroke,
                  reference: keystrokeReference,
                  liveKeys: liveKeys,
                ),
              ],
              if (showDemoControl) ...[
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerRight,
                  child: OutlinedButton.icon(
                    key: const ValueKey('demo-cycle-toggle'),
                    onPressed: onToggleDemoCycling,
                    icon: Icon(demoCyclingEnabled
                        ? Icons.pause_rounded
                        : Icons.play_arrow_rounded),
                    label: Text('자동 순환: ${demoCyclingEnabled ? 'ON' : 'OFF'}'),
                  ),
                ),
              ],
            ]);
      });
}

class SuggestionView extends StatelessWidget {
  const SuggestionView(
      {super.key, required this.state, required this.onFeedback});
  final DisplayState state;
  final ValueChanged<String> onFeedback;

  @override
  Widget build(BuildContext context) {
    return Stack(children: [
      Positioned.fill(
        child: Row(children: [
          Expanded(
              child: Opacity(
                  opacity: .25, child: _CurrentStatePanel(state: state))),
          const SizedBox(width: 30),
          const Expanded(child: SizedBox()),
          const SizedBox(width: 30),
          Expanded(
              child: Opacity(
                  opacity: .25, child: _EnvironmentPanel(state: state))),
        ]),
      ),
      Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 430, maxHeight: 430),
          child: SoftPanel(
            raised: true,
            padding: const EdgeInsets.all(34),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(children: [
                    const _StatusDot(),
                    const SizedBox(width: 8),
                    Text('지금의 제안',
                        style: Theme.of(context).textTheme.labelMedium),
                  ]),
                  const SizedBox(height: 14),
                  Text(_suggestionTitle(state),
                      style: Theme.of(context).textTheme.headlineMedium),
                  const Spacer(),
                  Text(_suggestionBody(state),
                      style: Theme.of(context).textTheme.bodyLarge),
                  const Spacer(),
                  Text('변경은 사용자가 선택할 때만 적용돼요.',
                      style: Theme.of(context).textTheme.labelMedium),
                  const SizedBox(height: 15),
                  Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                    OutlinedButton(
                      onPressed: () => onFeedback('reject'),
                      child: const Text('괜찮아요'),
                    ),
                    const SizedBox(width: 10),
                    FilledButton(
                      onPressed: () => onFeedback('accept'),
                      child: const Text('적용할게요'),
                    ),
                  ]),
                ]),
          ),
        ),
      ),
    ]);
  }
}

class SessionReportView extends StatelessWidget {
  const SessionReportView({super.key, required this.state});
  final DisplayState state;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: 8),
          Text('오늘의 집중 리포트', style: Theme.of(context).textTheme.headlineLarge),
          const SizedBox(height: 4),
          Text('${_date(state.timestamp)} · 세션 완료',
              style: Theme.of(context).textTheme.bodyMedium),
          const SizedBox(height: 22),
          Row(children: [
            Expanded(
                child:
                    _ReportMetric(label: '종료 상태', value: _stateLabel(state))),
            const SizedBox(width: 18),
            Expanded(
                child: _ReportMetric(
                    label: '집중 저하', value: _percent(state.focus))),
            const SizedBox(width: 18),
            Expanded(
                child: _ReportMetric(
                    label: '판정 신뢰도', value: _percent(state.confidence))),
          ]),
          const SizedBox(height: 20),
          Expanded(
            child: Row(children: [
              Expanded(flex: 7, child: _ReportChart(state: state)),
              const SizedBox(width: 20),
              Expanded(flex: 3, child: _ReportEvents(state: state)),
            ]),
          ),
          const SizedBox(height: 14),
          SoftPanel(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 11),
            child: Text('환경 변화    ${_environmentInline(state)}',
                style: Theme.of(context).textTheme.bodyMedium),
          ),
        ],
      );
}

class FocusAmbientView extends StatelessWidget {
  const FocusAmbientView({super.key, required this.state});
  final DisplayState state;

  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text('•  DESKMATE FOCUS',
              style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 54),
          SizedBox(
            width: 220,
            height: 220,
            child: CustomPaint(
              painter: _FocusRingPainter(value: state.confidence),
              child: Center(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(_elapsedLabel(state),
                      style: Theme.of(context).textTheme.headlineLarge),
                  const SizedBox(height: 6),
                  Text('STATUS',
                      style: Theme.of(context).textTheme.labelMedium),
                ]),
              ),
            ),
          ),
          const SizedBox(height: 70),
          Text('DESKMATE', style: Theme.of(context).textTheme.labelMedium),
        ]),
      );
}

class FocusDetailView extends StatelessWidget {
  const FocusDetailView(
      {super.key, required this.state, required this.onClose});
  final DisplayState state;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) => Row(children: [
        Expanded(
          flex: 6,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Spacer(),
            Text(_elapsedLabel(state),
                style: Theme.of(context).textTheme.displayLarge),
            const SizedBox(height: 4),
            Text('남은 집중 시간', style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 26),
            SizedBox(height: 180, child: _CurrentStatePanel(state: state)),
            const Spacer(),
          ]),
        ),
        const SizedBox(width: 38),
        Expanded(
          flex: 4,
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            SoftPanel(
              raised: true,
              child: Row(children: [
                Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('FOCUS MODE',
                            style: Theme.of(context).textTheme.labelMedium),
                        const SizedBox(height: 10),
                        Text('Deep Focus',
                            style: Theme.of(context).textTheme.titleLarge),
                      ]),
                ),
                const OutlinedButton(onPressed: null, child: Text('변경')),
              ]),
            ),
            const SizedBox(height: 18),
            Row(children: const [
              Expanded(
                  child: FilledButton(onPressed: null, child: Text('Pause'))),
              SizedBox(width: 12),
              Expanded(
                  child:
                      OutlinedButton(onPressed: null, child: Text('Focus 종료'))),
            ]),
            const SizedBox(height: 12),
            TextButton(onPressed: onClose, child: const Text('상태 화면으로 돌아가기')),
            const SizedBox(height: 58),
            Text('추가 입력이 없으면 곧 조용한 화면으로 돌아갑니다.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.labelMedium),
          ]),
        ),
      ]);
}

class SoftPanel extends StatelessWidget {
  const SoftPanel(
      {super.key, required this.child, this.padding, this.raised = false});
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final bool raised;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding ?? const EdgeInsets.all(22),
        decoration: BoxDecoration(
          color: raised ? DeskmateColors.surfaceRaised : DeskmateColors.surface,
          borderRadius: BorderRadius.circular(DeskmateRadius.panel),
          border: Border.all(color: Colors.white.withValues(alpha: .36)),
          boxShadow: [
            BoxShadow(
              color: Colors.white.withValues(alpha: raised ? .7 : .42),
              offset: const Offset(-2, -2),
              blurRadius: raised ? 8 : 5,
            ),
            BoxShadow(
              color: Colors.black.withValues(alpha: raised ? .12 : .065),
              offset: const Offset(3, 4),
              blurRadius: raised ? 13 : 8,
            ),
          ],
        ),
        child: child,
      );
}

class KeystrokeSummary extends StatelessWidget {
  const KeystrokeSummary({
    super.key,
    required this.metrics,
    required this.reference,
    this.liveKeys,
  });
  final KeystrokeMetrics? metrics;
  final DateTime reference;
  final int? liveKeys;

  @override
  Widget build(BuildContext context) {
    final age = metrics?.ageFrom(reference);
    final active = metrics != null && (metrics!.valid ?? true) && age != null;
    return Row(children: [
      Text('키스트로크', style: Theme.of(context).textTheme.labelMedium),
      const SizedBox(width: 14),
      Text(active
          ? (metrics!.typingActive == true ? '타이핑 중' : '입력 없음')
          : '수신 끊김'),
      const Spacer(),
      if (metrics?.dwellMeanMs != null) ...[
        Text('${metrics!.dwellMeanMs!.round()} ms'),
        const SizedBox(width: 12),
      ],
      if (metrics?.flightMeanMs != null) ...[
        Text('${metrics!.flightMeanMs!.round()} ms'),
        const SizedBox(width: 12),
      ],
      if (metrics?.flightCv != null) ...[
        Text(metrics!.flightCv!.toStringAsFixed(2)),
        const SizedBox(width: 12),
      ],
      if (metrics?.idleRatio != null) ...[
        Text(_percent(metrics!.idleRatio!)),
        const SizedBox(width: 12),
      ],
      if (metrics?.correctionRate != null)
        Text(_percent(metrics!.correctionRate!)),
      if (metrics?.windowS != null) ...[
        const SizedBox(width: 12),
        Text('${metrics!.windowS}초 윈도'),
      ],
      if (liveKeys != null) ...[
        const SizedBox(width: 12),
        Text('$liveKeys타'),
      ],
    ]);
  }
}

class KeystrokePanelForTest extends StatelessWidget {
  const KeystrokePanelForTest({
    super.key,
    required this.metrics,
    required this.reference,
    this.liveKeys,
  });
  final KeystrokeMetrics? metrics;
  final DateTime reference;
  final int? liveKeys;
  @override
  Widget build(BuildContext context) => SoftPanel(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const Text('키스트로크'),
          const Spacer(),
          KeystrokeSummary(
              metrics: metrics, reference: reference, liveKeys: liveKeys),
        ]),
      );
}

class _CurrentStatePanel extends StatelessWidget {
  const _CurrentStatePanel({required this.state, this.onDetail});
  final DisplayState state;
  final VoidCallback? onDetail;
  @override
  Widget build(BuildContext context) => SoftPanel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('현재 상태', style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 14),
          Text(_stateLabel(state),
              style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 7),
          Text(state.scenario ?? _focusSubline(state),
              style: Theme.of(context).textTheme.bodyMedium),
          const Spacer(),
          Row(children: [
            Text('세부 ${state.fsmState}',
                style: Theme.of(context).textTheme.labelMedium),
            const Spacer(),
            if (onDetail != null)
              TextButton(onPressed: onDetail, child: const Text('상세 보기')),
          ]),
        ]),
      );
}

class _EnvironmentPanel extends StatelessWidget {
  const _EnvironmentPanel({required this.state});
  final DisplayState state;
  @override
  Widget build(BuildContext context) => SoftPanel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('환경 요약', style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 20),
          Row(children: [
            Expanded(
                child: _DataPoint(
                    label: 'CO₂',
                    value: state.co2Ppm == null ? '--' : '${state.co2Ppm}',
                    unit: 'ppm')),
            Expanded(
                child: _DataPoint(
                    label: '조도',
                    value: state.lux == null ? '--' : '${state.lux}',
                    unit: 'lx')),
            Expanded(
                child: _DataPoint(
                    label: '재실',
                    value: state.present == null
                        ? '--'
                        : state.present!
                            ? '감지'
                            : '없음')),
          ]),
          const Spacer(),
          Text(_comfort(state), style: Theme.of(context).textTheme.bodyMedium),
        ]),
      );
}

class _MetricStrip extends StatelessWidget {
  const _MetricStrip({required this.state});
  final DisplayState state;
  @override
  Widget build(BuildContext context) => Row(children: [
        Expanded(child: _SlimMetric(label: '집중 저하 / 낮음', value: state.focus)),
        const SizedBox(width: 22),
        Expanded(child: _SlimMetric(label: '피로 / 낮음', value: state.fatigue)),
        const SizedBox(width: 22),
        Expanded(
            child: _SlimMetric(label: '판정 신뢰도 / 높음', value: state.confidence)),
      ]);
}

class _SlimMetric extends StatelessWidget {
  const _SlimMetric({required this.label, required this.value});
  final String label;
  final double value;
  @override
  Widget build(BuildContext context) => ClipRRect(
        borderRadius: BorderRadius.circular(DeskmateRadius.control),
        child: Stack(children: [
          Container(height: 40, color: DeskmateColors.surfaceMuted),
          FractionallySizedBox(
            widthFactor: value.clamp(0, 1),
            child: Container(
                height: 40,
                color: DeskmateColors.accent.withValues(alpha: .48)),
          ),
          Positioned.fill(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 13),
              child: Row(children: [
                Text(label, style: Theme.of(context).textTheme.labelMedium),
                const Spacer(),
                Text(_percent(value),
                    style: const TextStyle(fontWeight: FontWeight.w700)),
              ]),
            ),
          ),
        ]),
      );
}

class _AmbientMetric extends StatelessWidget {
  const _AmbientMetric({required this.title, required this.value});
  final String title;
  final String value;
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 9),
        Text(value, style: Theme.of(context).textTheme.titleLarge),
      ]);
}

class _DataPoint extends StatelessWidget {
  const _DataPoint({required this.label, required this.value, this.unit});
  final String label;
  final String value;
  final String? unit;
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 7),
        RichText(
          text: TextSpan(
              style: Theme.of(context).textTheme.titleLarge,
              children: [
                TextSpan(text: value),
                if (unit != null)
                  TextSpan(
                      text: ' $unit',
                      style: Theme.of(context).textTheme.labelMedium),
              ]),
        ),
      ]);
}

class _ReportMetric extends StatelessWidget {
  const _ReportMetric({required this.label, required this.value});
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) => SoftPanel(
        padding: const EdgeInsets.all(18),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 8),
          Text(value, style: Theme.of(context).textTheme.titleLarge),
        ]),
      );
}

class _ReportChart extends StatelessWidget {
  const _ReportChart({required this.state});
  final DisplayState state;
  @override
  Widget build(BuildContext context) => SoftPanel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('집중 상태 변화', style: Theme.of(context).textTheme.labelMedium),
          const Spacer(),
          Expanded(
              child: CustomPaint(
                  painter: _ReportBarsPainter(state), size: Size.infinite)),
          const SizedBox(height: 10),
          Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: const [Text('시작'), Text('중간'), Text('완료')]),
        ]),
      );
}

class _ReportEvents extends StatelessWidget {
  const _ReportEvents({required this.state});
  final DisplayState state;
  @override
  Widget build(BuildContext context) => SoftPanel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('주요 이벤트', style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 16),
          const Text('세션 시작'),
          const SizedBox(height: 12),
          Text(_stateLabel(state)),
          if (state.reasons.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(state.reasons.take(2).join('\n'),
                style: Theme.of(context).textTheme.bodyMedium),
          ],
          const Spacer(),
          Text('상세 이력은 Hub 데이터 연결 후 표시됩니다.',
              style: Theme.of(context).textTheme.labelMedium),
        ]),
      );
}

class _StatusDot extends StatelessWidget {
  const _StatusDot();
  @override
  Widget build(BuildContext context) => Container(
        width: 8,
        height: 8,
        decoration: const BoxDecoration(
            color: DeskmateColors.accentStrong, shape: BoxShape.circle),
      );
}

class _FocusRingPainter extends CustomPainter {
  const _FocusRingPainter({required this.value});
  final double value;
  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = math.min(size.width, size.height) / 2 - 16;
    canvas.drawCircle(center, radius + 12,
        Paint()..color = Colors.white.withValues(alpha: .18));
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..color = DeskmateColors.line
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3,
    );
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -math.pi / 2,
      math.pi * 2 * value.clamp(0, 1),
      false,
      Paint()
        ..color = DeskmateColors.inkMuted
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = 4,
    );
  }

  @override
  bool shouldRepaint(covariant _FocusRingPainter oldDelegate) =>
      oldDelegate.value != value;
}

class _ReportBarsPainter extends CustomPainter {
  const _ReportBarsPainter(this.state);
  final DisplayState state;
  @override
  void paint(Canvas canvas, Size size) {
    final values = <double>[
      state.focus,
      state.fatigue,
      state.confidence,
      state.focus,
      state.fatigue,
      state.confidence,
    ];
    final barWidth = math.min(34.0, size.width / (values.length * 2));
    final gap = barWidth * .65;
    final total = values.length * barWidth + (values.length - 1) * gap;
    var x = (size.width - total) / 2;
    for (var i = 0; i < values.length; i++) {
      final height = math.max(14.0, size.height * values[i].clamp(.08, .9));
      final rect = RRect.fromRectAndRadius(
        Rect.fromLTWH(x, size.height - height, barWidth, height),
        const Radius.circular(4),
      );
      canvas.drawRRect(
          rect,
          Paint()
            ..color =
                i.isEven ? DeskmateColors.inkFaint : DeskmateColors.accent);
      x += barWidth + gap;
    }
  }

  @override
  bool shouldRepaint(covariant _ReportBarsPainter oldDelegate) =>
      oldDelegate.state != state;
}

String _clock(DateTime time) =>
    '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';
String _date(DateTime time) => '${time.month}월 ${time.day}일';
String _percent(double value) => '${(value.clamp(0, 1) * 100).round()}%';
String _environmentInline(DisplayState state) => [
      if (state.co2Ppm != null) 'CO₂ ${state.co2Ppm} ppm',
      if (state.lux != null) '조도 ${state.lux} lx',
    ].join(' · ');
String _comfort(DisplayState state) =>
    state.present == false ? '조용히 대기 중이에요' : '쾌적해요';
String _elapsedLabel(DisplayState state) => '--:--';
String _focusHeadline(DisplayState state) =>
    state.phase == 'start' ? '집중을 준비하고 있어요' : '지금 잘 집중하고 있어요';
String _focusSubline(DisplayState state) => state.phase == 'start'
    ? '현재 상태를 살피며 기준을 만들고 있어요.'
    : '현재 상태를 방해하지 않도록 필요한 정보만 보여드려요.';
String _stateLabel(DisplayState state) =>
    const {
      'idle': '대기 중',
      'start': '집중 준비 중',
      'focus': '집중 유지 중',
      'fatigue': '잠시 조정이 필요해요',
      'recovery': '회복 중',
      'end': '세션 완료',
    }[state.phase] ??
    state.fsmState;
String _suggestionTitle(DisplayState state) => switch (state.cause) {
      'environment' => '조명을 조금 낮춰볼까요?',
      'posture' => '자세를 가볍게 바로잡아볼까요?',
      'cognitive' => '잠깐 쉬어가는 건 어떨까요?',
      _ => '환경을 잠시 조정해볼까요?',
    };
String _suggestionBody(DisplayState state) => switch (state.cause) {
      'environment' => '현재 환경 신호를 바탕으로 작은 조정을 제안드려요.',
      'posture' => '자세 변화가 이어지고 있어요. 가볍게 몸을 펴면 다시 편안해질 수 있어요.',
      'cognitive' => '피로 신호가 이어지고 있어요. 짧은 휴식이 다음 집중에 도움이 될 수 있어요.',
      _ => state.scenario ?? '현재 상태에 맞는 작은 변화를 제안드려요.',
    };
