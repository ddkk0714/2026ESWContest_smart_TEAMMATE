import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'live_test.dart' show FakeTransport;

void main() {
  testWidgets('빠른 진행 switch turns hub demo fast mode on and off',
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
    List<String?> speeds() => [
          for (final p in transport.published)
            if (p.topic == LiveLink.demoModeTopic)
              ((jsonDecode(p.text) as Map)['data'] as Map)['speed'] as String?
        ];

    await tester.tap(find.byTooltip('발표 조작창'));
    await tester.pumpAndSettle();
    expect(find.text('빠른 진행'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('demo-fast')));
    await tester.pumpAndSettle();
    expect(model.presentationEnabled, isTrue);
    expect(speeds(), ['fast']);
    await tester.tap(find.byKey(const ValueKey('demo-fast')));
    await tester.pumpAndSettle();
    expect(model.presentationEnabled, isFalse);
    expect(speeds(), ['fast', 'normal']);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });
}
