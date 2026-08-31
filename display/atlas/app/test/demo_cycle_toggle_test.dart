import 'package:deskmate_display/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('demo cycle can be paused and resumed from the action bar', (
    tester,
  ) async {
    await tester.pumpWidget(const DeskmateApp());
    await tester.pump();

    expect(find.text('자동 순환: ON'), findsOneWidget);
    expect(find.textContaining('화면 내장 데모 · #1'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('demo-cycle-toggle')));
    await tester.pump();

    expect(find.text('자동 순환: OFF'), findsOneWidget);
    await tester.pump(const Duration(seconds: 2));
    expect(find.textContaining('화면 내장 데모 · #1'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('demo-cycle-toggle')));
    await tester.pump();
    await tester.pump();

    expect(find.text('자동 순환: ON'), findsOneWidget);
    expect(find.textContaining('화면 내장 데모 · #2'), findsOneWidget);
  });
}
