import 'package:deskmate_display/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('demo cycling is OFF by default and can be enabled',
      (tester) async {
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();
    expect(find.text('#1'), findsOneWidget);
    expect(find.textContaining('OFF'), findsWidgets);
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.text('#1'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('demo-cycle-toggle')));
    await tester.pump();
    expect(find.textContaining('ON'), findsWidgets);
  });
}
