import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';

/// PC aggregates only. Board keyboard events must never replace this source.
class KeystrokePage extends StatefulWidget {
  const KeystrokePage({
    super.key,
    required this.metrics,
    required this.now,
    required this.demo,
    required this.sourceLabel,
    required this.onConnection,
  });

  final KeystrokeMetrics? metrics;
  final DateTime now;
  final bool demo;
  final String sourceLabel;
  final VoidCallback onConnection;

  @override
  State<KeystrokePage> createState() => _KeystrokePageState();
}

class _KeystrokePageState extends State<KeystrokePage> {
  final _history = <KeystrokeMetrics>[];

  @override
  void initState() {
    super.initState();
    _record();
  }

  @override
  void didUpdateWidget(KeystrokePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.sourceLabel != widget.sourceLabel ||
        oldWidget.demo != widget.demo ||
        oldWidget.metrics?.node != widget.metrics?.node) {
      _history.clear();
    }
    _record();
  }

  void _record() {
    final sample = widget.metrics;
    if (sample == null || sample.timestamp == null || sample.valid == false) {
      return;
    }
    final age = sample.ageFrom(widget.now);
    if (!widget.demo && (age == null || age > const Duration(seconds: 5))) {
      return;
    }
    if (_history.isNotEmpty &&
        !sample.timestamp!.isAfter(_history.last.timestamp!)) {
      return;
    }
    _history.add(sample);
    if (_history.length > 60) _history.removeAt(0);
  }

  @override
  Widget build(BuildContext context) {
    final ks = widget.metrics;
    final age = ks?.ageFrom(widget.now);
    final stale =
        !widget.demo && (age == null || age > const Duration(seconds: 5));
    final usable = ks != null && !stale && ks.valid != false;
    final status = widget.demo
        ? 'PC 수집기 대기'
        : ks == null
            ? 'PC 수집기 대기'
            : stale
                ? '수신 끊김'
                : ks.valid == false
                    ? '유효하지 않은 표본'
                    : ks.typingActive == true
                        ? '타이핑 중'
                        : ks.typingActive == false
                            ? '입력 없음 · 수신 중'
                            : '수신 중';
    return ListView(
      key: const ValueKey('pc-keystroke-page'),
      children: [
        Wrap(
          spacing: 16,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text('PC 키스트로크', style: Theme.of(context).textTheme.headlineMedium),
            Chip(label: Text(status)),
            OutlinedButton.icon(
              onPressed: widget.onConnection,
              icon: const Icon(Icons.lan_outlined),
              label: const Text('연결 설정'),
            ),
          ],
        ),
        Text('${widget.sourceLabel} · ${ks?.node ?? "PC 수집기 미수신"}'
            '${ks?.windowS == null ? "" : " · ${ks!.windowS}초 집계"}'
            '${widget.demo || age == null ? "" : " · ${age.inSeconds}초 전 표본"}'),
        const SizedBox(height: 12),
        LayoutBuilder(builder: (context, constraints) {
          final columns = constraints.maxWidth >= 650 ? 3 : 2;
          final width = (constraints.maxWidth - (columns - 1) * 12) / columns;
          return Wrap(spacing: 12, runSpacing: 12, children: [
            _metric(width, '키 누름 유지', ks?.dwellMeanMs, 'ms', usable,
                detail: '표준편차 ${_number(ks?.dwellStdMs)} ms'),
            _metric(width, '키 입력 간격', ks?.flightMeanMs, 'ms', usable,
                detail: '표준편차 ${_number(ks?.flightStdMs)} ms'),
            _metric(width, '리듬 변동계수', ks?.flightCv, '', usable,
                decimals: 2, detail: '간격 표준편차 / 평균'),
            _metric(width, '입력 공백', ks?.idleRatio, '%', usable, ratio: true),
            _metric(width, '교정 비율', ks?.correctionRate, '%', usable,
                ratio: true, detail: '백스페이스 빈도'),
            _metric(width, '마우스 활동', ks?.mouseEventRate, '/min', usable,
                decimals: 1, detail: '좌표·버튼 내용 미수집'),
          ]);
        }),
        const SizedBox(height: 12),
        _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('최근 수신 추이 · 키 누름 유지 / 키 입력 간격 (ms)'),
          Text('이 탭에서 받은 최근 ${_history.length}개 표본 · 각 표본은 슬라이딩 윈도 통계'),
          const SizedBox(height: 8),
          if (_history.length < 2)
            const SizedBox(
                height: 100,
                child: Center(child: Text('표본 2개 이상 수신하면 그래프가 표시됩니다.')))
          else ...[
            Text('민트: 유지 · 갈색: 간격 · 세로축 0~${_chartMax.round()} ms'),
            SizedBox(
              height: 100,
              width: double.infinity,
              child: CustomPaint(
                key: const ValueKey('keystroke-trend'),
                painter: _TimingPainter(List.of(_history), _chartMax),
              ),
            ),
          ],
        ])),
        const SizedBox(height: 12),
        _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('PC 연결 방법', style: TextStyle(fontWeight: FontWeight.w600)),
          const Text(
              'PC와 Pi 4·Pi 5를 같은 네트워크에 연결하고, 연결 설정에서 Pi 4 MQTT 주소를 지정하세요.'),
          const Text('PC 저장소에서 의존성을 설치한 뒤 수집기를 실행하세요.'),
          const SelectableText(
              'python -m pip install -r collector/requirements.txt\npython -m collector --broker <Pi4-IP> --port <MQTT-port>'),
          const Text('1Hz로 수신합니다. Bluetooth 페어링만으로 PC 입력이 전송되지는 않습니다.'),
          const Text('입력한 문자·키 내용은 전송하지 않습니다. 그래프는 메모리에만 유지되며 탭을 나가면 초기화됩니다.'),
        ])),
      ],
    );
  }

  double get _chartMax {
    var max = 1.0;
    for (final sample in _history) {
      for (final value in [sample.dwellMeanMs, sample.flightMeanMs]) {
        if (value != null && value > max) max = value;
      }
    }
    return max;
  }

  Widget _metric(
      double width, String label, double? value, String unit, bool usable,
      {int decimals = 0, bool ratio = false, String? detail}) {
    final display =
        value == null ? '--' : _number(ratio ? value * 100 : value, decimals);
    return SizedBox(
        width: width,
        child: _card(Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label),
            Text('$display${unit.isEmpty ? "" : " $unit"}',
                style: TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.w600,
                    color:
                        usable ? DeskmateColors.ink : DeskmateColors.inkFaint)),
            if (ratio) ...[
              const SizedBox(height: 6),
              LinearProgressIndicator(
                value: value ?? 0,
                color: usable
                    ? DeskmateColors.accentStrong
                    : DeskmateColors.inkFaint,
                backgroundColor: DeskmateColors.surfaceMuted,
              ),
            ],
            if (detail != null) Text(detail),
          ],
        )));
  }

  static String _number(double? value, [int decimals = 0]) =>
      value == null ? '--' : value.toStringAsFixed(decimals);

  static Widget _card(Widget child) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: DeskmateColors.surfaceRaised,
          borderRadius: BorderRadius.circular(DeskmateRadius.panel),
          border: Border.all(color: DeskmateColors.line),
        ),
        child: child,
      );
}

class _TimingPainter extends CustomPainter {
  _TimingPainter(this.samples, this.max);
  final List<KeystrokeMetrics> samples;
  final double max;

  @override
  void paint(Canvas canvas, Size size) {
    final grid = Paint()..color = DeskmateColors.line;
    for (var i = 0; i <= 2; i++) {
      final y = size.height * i / 2;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    final start = samples.first.timestamp!;
    final span = samples.last.timestamp!.difference(start).inMilliseconds;
    if (span <= 0) return;
    void series(double? Function(KeystrokeMetrics) select, Color color) {
      final path = Path();
      var connected = false;
      DateTime? previous;
      for (final sample in samples) {
        final value = select(sample);
        if (value == null || sample.typingActive == false) {
          connected = false;
          continue;
        }
        final x = sample.timestamp!.difference(start).inMilliseconds /
            span *
            size.width;
        final y = size.height * (1 - value / max);
        if (previous != null &&
            sample.timestamp!.difference(previous) >
                const Duration(seconds: 5)) {
          connected = false;
        }
        if (connected) {
          path.lineTo(x, y);
        } else {
          path.moveTo(x, y);
        }
        canvas.drawCircle(Offset(x, y), 2, Paint()..color = color);
        connected = true;
        previous = sample.timestamp;
      }
      canvas.drawPath(
          path,
          Paint()
            ..color = color
            ..strokeWidth = 2
            ..style = PaintingStyle.stroke);
    }

    series((sample) => sample.dwellMeanMs, DeskmateColors.accentStrong);
    series((sample) => sample.flightMeanMs, DeskmateColors.warning);
  }

  @override
  bool shouldRepaint(_TimingPainter oldDelegate) => true;
}
