import 'workspace_test_helpers.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/keystroke_page.dart';
import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

final _t0 = DateTime(2026, 10, 12, 12);

KeystrokeMetrics _sample(int seconds,
        {bool active = true, bool valid = true}) =>
    KeystrokeMetrics(
      node: 'pc-collector',
      timestamp: _t0.add(Duration(seconds: seconds)),
      windowS: 60,
      dwellMeanMs: 92,
      flightMeanMs: 148,
      flightStdMs: 63,
      idleRatio: active ? .18 : 1,
      correctionRate: .07,
      mouseEventRate: 2,
      typingActive: active,
      valid: valid,
    );

Widget _page(KeystrokeMetrics? sample, DateTime now,
        {bool demo = false, VoidCallback? onConnection}) =>
    MaterialApp(
        home: Scaffold(
            body: KeystrokePage(
      metrics: sample,
      now: now,
      demo: demo,
      sourceLabel: 'MQTT test:1883',
      onConnection: onConnection ?? () {},
    )));

void main() {
  testWidgets('shows PC metrics, ratio bars and connection action',
      (tester) async {
    var opened = false;
    await tester
        .pumpWidget(_page(_sample(0), _t0, onConnection: () => opened = true));
    expect(find.text('타이핑 중'), findsOneWidget);
    expect(find.text('92 ms'), findsOneWidget);
    expect(find.text('148 ms'), findsOneWidget);
    expect(find.text('18 %'), findsOneWidget);
    expect(find.text('7 %'), findsOneWidget);
    expect(find.text('0.43'), findsOneWidget);
    expect(find.text('2.0 /min'), findsOneWidget);
    expect(
        tester
            .widgetList<LinearProgressIndicator>(
                find.byType(LinearProgressIndicator))
            .map((bar) => bar.value),
        [.18, .07]);
    await tester.tap(find.text('연결 설정'));
    expect(opened, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('distinguishes waiting, idle, invalid, stale and demo',
      (tester) async {
    await tester.pumpWidget(_page(null, _t0));
    expect(find.text('PC 수집기 대기'), findsOneWidget);
    expect(find.text('-- ms'), findsNWidgets(2));
    await tester.pumpWidget(_page(_sample(0, active: false), _t0));
    expect(find.text('입력 없음 · 수신 중'), findsOneWidget);
    expect(find.text('100 %'), findsOneWidget);
    await tester.pumpWidget(_page(_sample(0, valid: false), _t0));
    expect(find.text('유효하지 않은 표본'), findsOneWidget);
    await tester
        .pumpWidget(_page(_sample(0), _t0.add(const Duration(seconds: 5))));
    expect(find.text('타이핑 중'), findsOneWidget);
    await tester
        .pumpWidget(_page(_sample(0), _t0.add(const Duration(seconds: 6))));
    expect(find.text('수신 끊김'), findsOneWidget);
    await tester.pumpWidget(_page(_sample(0), _t0, demo: true));
    expect(find.text('데모 · 실제 PC 미연결'), findsOneWidget);
  });

  testWidgets('history skips repeated and invalid samples and caps at 60',
      (tester) async {
    await tester.pumpWidget(_page(_sample(0), _t0));
    await tester.pumpWidget(_page(_sample(0), _t0));
    expect(find.textContaining('최근 1개 표본'), findsOneWidget);
    await tester.pumpWidget(
        _page(_sample(1, valid: false), _t0.add(const Duration(seconds: 1))));
    expect(find.textContaining('최근 1개 표본'), findsOneWidget);
    for (var i = 1; i <= 65; i++) {
      await tester.pumpWidget(_page(_sample(i), _t0.add(Duration(seconds: i))));
    }
    expect(find.textContaining('최근 60개 표본'), findsOneWidget);
    await tester.drag(find.byType(ListView), const Offset(0, -250));
    await tester.pump();
    expect(find.byKey(const ValueKey('keystroke-trend')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('tab remains PC-specific after a board keyboard event',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await pumpDeveloperApp(
        tester,
        DeskmateApp(
            settings: SettingsStore(
                candidates: const [], legacyPostureCandidates: const [])));
    await tester.pump();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.keyA);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.keyA);
    await tester.pump(const Duration(seconds: 1));
    await tester.tap(find.byTooltip('PC 키스트로크'));
    await tester.pump();
    expect(find.byKey(const ValueKey('pc-keystroke-page')), findsOneWidget);
    expect(find.text('데모 · 실제 PC 미연결'), findsOneWidget);
    expect(find.textContaining('pc-collector'), findsOneWidget);
    expect(find.textContaining('pi-keyboard'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  test('PC envelope preserves sample time and missing fields', () {
    final metrics = KeystrokeMetrics.fromEnvelope({
      'schema_version': '1.0',
      'ts': _t0.millisecondsSinceEpoch / 1000,
      'node': 'pc-collector',
      'data': {'idle_ratio': 1, 'typing_active': false},
    })!;
    expect(metrics.timestamp, _t0);
    expect(metrics.node, 'pc-collector');
    expect(metrics.typingActive, isFalse);
    expect(metrics.dwellMeanMs, isNull);
    expect(() => KeystrokeMetrics.fromEnvelope({'schema_version': '2.0'}),
        throwsFormatException);
  });
}
