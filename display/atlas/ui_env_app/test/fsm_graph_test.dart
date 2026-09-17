import 'package:deskmate_display/fsm_graph.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('FSM graph exposes three deterministic zoom stages',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(1400, 900));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: FsmGraphPage(currentState: 'FOCUS_PC')),
    ));
    await tester.pump();

    expect(find.byKey(const ValueKey('fsm-zoom-selector')), findsOneWidget);
    expect(find.byKey(const ValueKey('fsm-zoom-selected-1')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('fsm-zoom-3')));
    await tester.pump();
    expect(find.byKey(const ValueKey('fsm-zoom-selected-3')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('fsm-zoom-2')));
    await tester.pump();
    expect(find.byKey(const ValueKey('fsm-zoom-selected-2')), findsOneWidget);
  });

  testWidgets('mouse wheel advances only one zoom stage per event',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(1400, 900));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: FsmGraphPage(currentState: 'START')),
    ));
    await tester.pump();

    await tester.sendEventToBinding(const PointerScrollEvent(
      position: Offset(700, 450),
      scrollDelta: Offset(0, -80),
    ));
    await tester.pump();

    expect(find.byKey(const ValueKey('fsm-zoom-selected-2')), findsOneWidget);
    expect(find.byKey(const ValueKey('fsm-zoom-selected-3')), findsNothing);
  });
}
