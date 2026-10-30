import 'dart:async';

import 'package:deskmate_display/placement_client.dart';
import 'package:deskmate_display/placement_controller.dart';
import 'package:deskmate_display/placement_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _wrap(Widget child) => MaterialApp(
      home: Scaffold(body: SizedBox(width: 900, height: 520, child: child)),
    );

String _status(WidgetTester tester) =>
    tester.widget<Text>(find.byKey(const ValueKey('placement-status'))).data!;

void main() {
  testWidgets('shows title, three buttons, the current pick highlighted, and applies',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final picks = <String>[];
    await tester.pumpWidget(_wrap(PlacementPage(
      current: 'center',
      applied: '가운데 · opt_l8_c256.json',
      onPick: (p) async {
        picks.add(p);
        return PlacementApplyResult.ok(
            PlacementInfo(placement: p, model: 'opt_l8_c256_$p.json'));
      },
    )));
    expect(find.text('장치를 보통 어디에 두나요?'), findsOneWidget);
    expect(find.text('앉은 자리에서 봤을 때'), findsOneWidget);
    expect(find.byKey(const ValueKey('placement-desk')), findsOneWidget);
    for (final k in ['left', 'center', 'right']) {
      expect(find.byKey(ValueKey('placement-$k')), findsOneWidget, reason: k);
    }
    // 현재값은 채운 버튼
    expect(tester.widget(find.byKey(const ValueKey('placement-center'))),
        isA<FilledButton>());
    expect(tester.widget(find.byKey(const ValueKey('placement-left'))),
        isA<OutlinedButton>());
    expect(_status(tester), '지금: 가운데');
    expect(find.textContaining('Pi 4: 가운데 · opt_l8_c256.json'), findsOneWidget);
    // 첫 실행이 아니면 건너뛰기 없음
    expect(find.byKey(const ValueKey('placement-skip')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('placement-left')));
    await tester.pump();
    await tester.pump();
    expect(picks, ['left']);
    expect(tester.widget(find.byKey(const ValueKey('placement-left'))),
        isA<FilledButton>());
    expect(_status(tester), '적용됨: opt_l8_c256_left.json');
    expect(find.byKey(const ValueKey('placement-retry')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('failure shows the reason and a retry button; skip calls back',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    var fail = true;
    var skipped = false;
    await tester.pumpWidget(_wrap(PlacementPage(
      current: null,
      onPick: (p) async {
        if (fail) {
          return const PlacementApplyResult.failed(
              PlacementException(PlacementFailure.unreachable, 'refused'),
              '저장했어요. Pi 4 자세 서비스에 연결되지 않아요 (refused)');
        }
        return PlacementApplyResult.ok(PlacementInfo(placement: p, model: 'm.json'));
      },
      onSkip: () => skipped = true,
    )));
    expect(_status(tester), contains('아직 고르지 않았어요'));
    expect(find.byKey(const ValueKey('placement-skip')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('placement-right')));
    await tester.pump();
    await tester.pump();
    expect(_status(tester), contains('연결되지 않아요'));
    expect(find.byKey(const ValueKey('placement-retry')), findsOneWidget);

    fail = false;
    await tester.tap(find.byKey(const ValueKey('placement-retry')));
    await tester.pump();
    await tester.pump();
    expect(_status(tester), '적용됨: m.json');
    expect(find.byKey(const ValueKey('placement-retry')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('placement-skip')));
    expect(skipped, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('buttons are disabled while applying', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final gate = Completer<PlacementApplyResult>();
    await tester.pumpWidget(_wrap(PlacementPage(
      current: null,
      onPick: (_) => gate.future,
    )));
    await tester.tap(find.byKey(const ValueKey('placement-center')));
    await tester.pump();
    expect(_status(tester), '가운데 적용 중…');
    expect(
        tester
            .widget<OutlinedButton>(find.byKey(const ValueKey('placement-left')))
            .onPressed,
        isNull);
    gate.complete(PlacementApplyResult.ok(
        const PlacementInfo(placement: 'center', model: 'opt_l8_c256.json')));
    await tester.pump();
    expect(_status(tester), '적용됨: opt_l8_c256.json');
    expect(tester.takeException(), isNull);
  });
}
