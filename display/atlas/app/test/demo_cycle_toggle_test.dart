import 'package:deskmate_display/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('demo cycles by default and can be paused', (tester) async {
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    expect(find.text('#1'), findsOneWidget);

    await tester.pump(const Duration(seconds: 1));
    await tester.pump();
    expect(find.text('#2'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('demo-cycle-toggle')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    expect(find.text('#2'), findsOneWidget);
  });
}
