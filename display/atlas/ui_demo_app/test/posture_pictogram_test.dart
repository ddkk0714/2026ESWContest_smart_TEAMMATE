import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/posture_pictogram.dart';
import 'package:deskmate_ui_demo/pr56/display_state.dart';
import 'live_test.dart' show FakeTransport, phase;

void main() {
  test('posture key and colors follow the hub posture', () {
    DisplayState s(Map<String, Object?> summary) =>
        DisplayState.fromEnvelope(phase('FOCUS_PC', summary: summary));
    expect(
        postureKey(s({
          'present': true,
          'tof': {'posture': 'recline'}
        })),
        'recline');
    // 자리에 없으면 자세보다 자리 비움
    expect(
        postureKey(s({
          'present': false,
          'tof': {'posture': 'upright'}
        })),
        'away');
    expect(postureColors('upright').ink, isNot(postureColors('head_down').ink));
    expect(postureColors('drowsy').ink, isNot(postureColors('head_down').ink));
    expect(postureColors('recline').ink, isNot(postureColors('upright').ink));
    expect(postureColors(null).ink, postureColors('away').ink);
  });

  testWidgets('posture card changes picture and color with the posture',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = DemoModel(DemoConfig(
        jsonDecode(File('assets/config/demo.json').readAsStringSync())
            as Map<String, dynamic>));
    late FakeTransport transport;
    final link = LiveLink(
        transportFactory: (h, p) => transport = FakeTransport(),
        retryDelay: const Duration(hours: 1));
    await tester.pumpWidget(DemoApp(model: model, ticking: false, live: link));
    link.configure('192.168.0.10', 1883);
    await tester.pump();
    Future<void> posture(String p) async {
      transport.deliver(
          LiveLink.stateTopic,
          phase('FOCUS_PC', summary: {
            'present': true,
            'tof': {'posture': p}
          }));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
    }

    await posture('upright');
    model.show(DemoPage.details);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('posture-pictogram-upright')),
        findsOneWidget);
    Color inkOf(String label) => tester
        .widget<Text>(find.descendant(
            of: find.byKey(const ValueKey('desk-posture-card')),
            matching: find.text(label)))
        .style!
        .color!;
    final upright = inkOf('바른 자세');
    await posture('head_down');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('posture-pictogram-head_down')),
        findsOneWidget);
    expect(
        find.byKey(const ValueKey('posture-pictogram-upright')), findsNothing);
    expect(find.text('고개 숙임'), findsOneWidget);
    expect(inkOf('고개 숙임'), isNot(upright));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });
}
