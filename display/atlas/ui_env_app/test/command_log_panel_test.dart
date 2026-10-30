import 'package:deskmate_display/command_log.dart';
import 'package:deskmate_display/command_log_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _wrap(CommandLog log) => MaterialApp(
      home: Scaffold(
        body: SizedBox(height: 200, child: CommandLogPanel(log: log)),
      ),
    );

void main() {
  testWidgets('empty log says so, then shows each line and the count',
      (tester) async {
    final log = CommandLog();
    await tester.pumpWidget(_wrap(log));
    expect(find.byKey(const ValueKey('cmdlog-empty')), findsOneWidget);
    expect(find.text('0/200'), findsOneWidget);

    final at = DateTime(2026, 10, 24, 12, 5, 10);
    log.add(CommandLogEntry(at, CommandLogKind.cmd,
        'desk_lamp set_brightness 230 (auto, id ab12)'));
    log.add(CommandLogEntry(at, CommandLogKind.result,
        'ab12 failed lamp_not_configured (display-control)',
        ok: false));
    await tester.pump();
    expect(find.byKey(const ValueKey('cmdlog-empty')), findsNothing);
    expect(
        find.text('12:05:10 CMD desk_lamp set_brightness 230 (auto, id ab12)'),
        findsOneWidget);
    expect(
        find.text(
            '12:05:10 RESULT ab12 failed lamp_not_configured (display-control)'),
        findsOneWidget);
    expect(find.text('2/200'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('cmdlog-clear')));
    await tester.pump();
    expect(log.isEmpty, isTrue);
    expect(find.byKey(const ValueKey('cmdlog-empty')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('200 lines stay bounded and the list follows the newest line',
      (tester) async {
    final log = CommandLog();
    await tester.pumpWidget(_wrap(log));
    for (var i = 0; i < 230; i++) {
      log.add(CommandLogEntry(
          DateTime(2026, 10, 24, 12, 0, i % 60), CommandLogKind.request, 'r$i'));
    }
    await tester.pump();
    await tester.pump();
    expect(find.text('200/200'), findsOneWidget);
    final list = tester.widget<ListView>(find.byKey(const ValueKey('cmdlog-list')));
    expect(list.semanticChildCount, 200);
    // 자동 스크롤: 마지막 줄이 보인다.
    expect(find.textContaining('REQUEST r229'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
