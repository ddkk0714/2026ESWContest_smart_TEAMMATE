import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/page_transition.dart';

Widget pages(ValueNotifier<String> page) => MaterialApp(
    home: ValueListenableBuilder<String>(
        valueListenable: page,
        builder: (_, value, __) => PageTransitionView(
            pageKey: value, child: SizedBox(key: ValueKey(value)))));

void main() {
  double opacity(WidgetTester tester, String key) => tester
      .widget<FadeTransition>(find
          .ancestor(
              of: find.byKey(ValueKey(key)),
              matching: find.byType(FadeTransition))
          .first)
      .opacity
      .value;
  testWidgets(
      'page transition overlaps briefly and blocks outgoing interaction',
      (tester) async {
    final page = ValueNotifier('a');
    await tester.pumpWidget(pages(page));
    await tester.pumpAndSettle();
    page.value = 'b';
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.byKey(const ValueKey('a')), findsOneWidget);
    expect(find.byKey(const ValueKey('b')), findsOneWidget);
    final blockers = tester.widgetList<IgnorePointer>(find.ancestor(
        of: find.byKey(const ValueKey('a')),
        matching: find.byType(IgnorePointer)));
    expect(blockers.any((b) => b.ignoring), isTrue);
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump();
    expect(find.byKey(const ValueKey('a')), findsNothing);
    expect(find.byKey(const ValueKey('b')), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    page.dispose();
  });
  testWidgets('pages dissolve without moving or scaling', (tester) async {
    final page = ValueNotifier('a');
    await tester.pumpWidget(pages(page));
    await tester.pumpAndSettle();
    page.value = 'b';
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 250));
    expect(opacity(tester, 'a'), closeTo(.5, .01));
    expect(opacity(tester, 'b'), closeTo(.5, .01));
    expect(find.byType(SlideTransition), findsNothing);
    expect(find.byType(ScaleTransition), findsNothing);
    expect(find.byType(FractionalTranslation), findsNothing);
    await tester.pumpAndSettle();
    expect(opacity(tester, 'b'), 1);
    await tester.pumpWidget(const SizedBox());
    page.dispose();
  });
  testWidgets('reduced motion does not animate between pages', (tester) async {
    final page = ValueNotifier('a');
    await tester.pumpWidget(MaterialApp(
        home: MediaQuery(
            data: const MediaQueryData(disableAnimations: true),
            child: ValueListenableBuilder<String>(
                valueListenable: page,
                builder: (_, value, __) => PageTransitionView(
                    pageKey: value, child: SizedBox(key: ValueKey(value)))))));
    await tester.pumpAndSettle();
    page.value = 'b';
    await tester.pump();
    await tester.pump();
    expect(find.byKey(const ValueKey('a')), findsNothing);
    expect(find.byKey(const ValueKey('b')), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    page.dispose();
  });
  testWidgets('product screens and controls contain no presentation labels',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = DemoModel(
        DemoConfig(
            jsonDecode(File('assets/config/demo.json').readAsStringSync())
                as Map<String, dynamic>),
        presentationEnabled: true);
    await tester.pumpWidget(DemoApp(model: model, ticking: false));
    Future<void> check() async {
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      for (final text in tester.widgetList<Text>(find.byType(Text))) {
        expect(
            text.data ?? text.textSpan?.toPlainText() ?? '',
            isNot(matches(
                RegExp('시연|데모|모의|예시|\\bdemo\\b', caseSensitive: false))));
      }
      for (final tip in tester.widgetList<Tooltip>(find.byType(Tooltip))) {
        expect(
            tip.message ?? '',
            isNot(matches(
                RegExp('시연|데모|모의|예시|\\bdemo\\b', caseSensitive: false))));
      }
      expect(tester.takeException(), isNull);
    }

    await check();
    await tester.tap(find.byTooltip('나의 책상 설정'));
    await check();
    await tester.tap(find.byTooltip('설정 닫기'));
    await check();
    await tester.tap(find.byTooltip('발표 조작창'));
    await check();
    await tester.tap(find.text('닫기'));
    await check();
    model.start();
    await check();
    model.show(DemoPage.details);
    await check();
    model.show(DemoPage.widgets);
    await check();
    model.finish();
    await check();
    await tester.pumpWidget(const SizedBox());
  });
}
