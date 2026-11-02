import 'package:deskmate_display/smooth_menu_scroll.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  for (final axis in [Axis.vertical, Axis.horizontal]) {
    testWidgets(
        'menu wheel eases, accumulates and reverses continuously on $axis',
        (tester) async {
      final items = [
        for (var i = 0; i < 12; i++)
          SizedBox(width: 73, height: 73, child: Text('Item $i'))
      ];
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: Center(
                  child: SizedBox(
                      width: axis == Axis.vertical ? 86 : 300,
                      height: axis == Axis.vertical ? 300 : 86,
                      child: SmoothMenuScroll(
                          axis: axis,
                          child: axis == Axis.vertical
                              ? Column(children: items)
                              : Row(children: items)))))));
      final menu = find.byType(SmoothMenuScroll);
      final scroll = tester.state<ScrollableState>(
          find.descendant(of: menu, matching: find.byType(Scrollable)));
      Future<void> wheel(double delta) =>
          tester.sendEventToBinding(PointerScrollEvent(
              position: tester.getCenter(menu),
              kind: PointerDeviceKind.mouse,
              scrollDelta: Offset(0, delta)));
      await wheel(90);
      await tester.pump();
      expect(scroll.position.pixels, closeTo(0, .01));
      await tester.pump(const Duration(milliseconds: 60));
      final first = scroll.position.pixels;
      expect(first, greaterThan(0));
      expect(first, lessThan(90));
      await wheel(70);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(scroll.position.pixels, greaterThan(first));
      expect(scroll.position.pixels, lessThan(160));
      await tester.pump(const Duration(milliseconds: 300));
      expect(scroll.position.pixels, closeTo(160, .01));
      await wheel(-60);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 70));
      expect(scroll.position.pixels, greaterThan(100));
      expect(scroll.position.pixels, lessThan(160));
      await tester.pump(const Duration(milliseconds: 300));
      expect(scroll.position.pixels, closeTo(100, .01));
      // Neither final offset aligns to the 73 px item size.
      expect(scroll.position.pixels % 73, closeTo(27, .01));
      await tester.pumpWidget(const SizedBox());
      expect(tester.takeException(), isNull);
    });
  }
}
