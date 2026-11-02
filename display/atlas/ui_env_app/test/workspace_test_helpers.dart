import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Existing diagnostic tests enter those tools through the new settings route.
Future<void> pumpDeveloperApp(WidgetTester tester, Widget app) async {
  await tester.pumpWidget(app);
  await tester.pump();
  await tester
      .ensureVisible(find.byKey(const ValueKey('workspace-nav-settings')));
  await tester.tap(find.byKey(const ValueKey('workspace-nav-settings')));
  await tester.pump();
  await tester.ensureVisible(find.byKey(const ValueKey('workspace-developer')));
  await tester.tap(find.byKey(const ValueKey('workspace-developer')));
  await tester.pump();
}
