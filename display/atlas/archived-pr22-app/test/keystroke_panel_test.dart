import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('demo dashboard shows live keystroke metrics', (tester) async {
    // 7인치 화면(1024x600)을 가정한다. 기본 800x600 보다 가로가 넉넉하다.
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    expect(find.text('키스트로크'), findsOneWidget);
    expect(find.text('타이핑 중'), findsOneWidget);

    // 데모 첫 국면(START)의 원지표가 그대로 보인다.
    expect(find.text('96 ms'), findsOneWidget);   // dwell_mean_ms
    expect(find.text('152 ms'), findsOneWidget);  // flight_mean_ms
    expect(find.text('24%'), findsOneWidget);     // idle_ratio
    expect(find.text('5%'), findsOneWidget);      // correction_rate
    expect(find.text('0.36'), findsOneWidget);    // 54.0 / 152.0
    expect(find.textContaining('60초 윈도'), findsOneWidget);
  });

  testWidgets('marks the panel stale when the collector sample is old', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final now = DateTime.now();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: KeystrokePanelForTest(
            reference: now,
            metrics: KeystrokeMetrics(
              node: 'pc-collector',
              windowS: 60,
              dwellMeanMs: 92.4,
              // hub 는 국면을 계속 내보내는데 collector 표본만 30초 묵었다.
              timestamp: now.subtract(const Duration(seconds: 30)),
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    expect(find.text('수신 끊김'), findsOneWidget);
    expect(find.textContaining('30초 전'), findsOneWidget);
  });

  testWidgets('shows the board keyboard node and live key count', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final now = DateTime.now();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: KeystrokePanelForTest(
            reference: now,
            liveKeys: 137,
            metrics: KeystrokeMetrics(
              node: 'pi-keyboard',
              windowS: 60,
              dwellMeanMs: 88.0,
              flightMeanMs: 131.0,
              flightStdMs: 38.0,
              idleRatio: 0.11,
              correctionRate: 0.03,
              typingActive: true,
              valid: true,
              timestamp: now,
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    expect(find.text('타이핑 중'), findsOneWidget);
    expect(find.textContaining('pi-keyboard'), findsOneWidget);
    expect(find.textContaining('137타'), findsOneWidget);
    expect(find.text('88 ms'), findsOneWidget);
  });
}
