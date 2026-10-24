import 'package:deskmate_display/irumae.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('all motions close smoothly without switching sprite silhouettes', () {
    for (final mood in IrumaeMood.values) {
      final start = irumaePoseAt(mood, 0);
      final nearEnd = irumaePoseAt(mood, .999999);
      expect(nearEnd.dyFraction, closeTo(start.dyFraction, .00001));
      expect(nearEnd.angle, closeTo(start.angle, .00001));
      expect(nearEnd.scaleY, closeTo(start.scaleY, .00001));
      var previous = start;
      for (var i = 1; i <= 1000; i++) {
        final current = irumaePoseAt(mood, i / 1000);
        expect(current.frame, start.frame);
        expect((current.angle - previous.angle).abs(), lessThan(.001));
        expect(
            (current.dyFraction - previous.dyFraction).abs(), lessThan(.001));
        expect((current.scaleY - previous.scaleY).abs(), lessThan(.001));
        previous = current;
      }
      final reduced = irumaePoseAt(mood, .4, reducedMotion: true);
      expect(reduced.frame, start.frame);
      expect(reduced.dyFraction, 0);
      expect(reduced.angle, 0);
      expect(reduced.scaleY, 1);
    }
  });

  testWidgets(
      'all five moods animate on paint; reduced motion and hidden tickers stop',
      (tester) async {
    Widget scene(IrumaeMood mood,
            {bool reduced = false, bool visible = true}) =>
        MaterialApp(
            home: MediaQuery(
                data: MediaQueryData(disableAnimations: reduced),
                child: TickerMode(
                    enabled: visible,
                    child: SizedBox(
                        width: 160,
                        height: 100,
                        child: IrumaeAnimation(mood: mood)))));
    await tester.pumpWidget(scene(IrumaeMood.greeting));
    final state = tester.state(find.byType(IrumaeAnimation));
    for (final mood in IrumaeMood.values) {
      await tester.pumpWidget(scene(mood));
      await tester.pump(const Duration(milliseconds: 320));
      expect(find.byKey(ValueKey('irumae-${mood.name}')), findsOneWidget);
      expect(tester.state(find.byType(IrumaeAnimation)), same(state));
      expect(tester.binding.hasScheduledFrame, isTrue);
      expect(tester.takeException(), isNull);
    }
    await tester.pumpWidget(scene(IrumaeMood.sleeping, reduced: true));
    await tester.pumpAndSettle();
    expect(tester.binding.hasScheduledFrame, isFalse);
    await tester.pumpWidget(scene(IrumaeMood.working, visible: false));
    await tester.pumpAndSettle();
    expect(tester.binding.hasScheduledFrame, isFalse);
    await tester.pumpWidget(scene(IrumaeMood.working));
    await tester.pump(const Duration(milliseconds: 16));
    expect(tester.binding.hasScheduledFrame, isTrue);
    await tester.pumpWidget(const SizedBox());
  });
}
