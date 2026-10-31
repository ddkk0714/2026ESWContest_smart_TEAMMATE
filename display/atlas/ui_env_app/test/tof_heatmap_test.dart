import 'package:deskmate_display/tof_heatmap.dart';
import 'package:deskmate_display/tof_raw_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Color zoneColorOf(WidgetTester tester, String prefix, int i) =>
    tester.widget<ColoredBox>(find.byKey(ValueKey('$prefix-zone-$i'))).color;

void main() {
  group('tofZoneColor', () {
    test('near is red, far is dark, in between moves along the scale', () {
      final near = tofZoneColor(200, 5);
      final far = tofZoneColor(2000, 5);
      final mid = tofZoneColor(1100, 5);
      expect(near, const Color(0xFFF06A6A));
      expect(far, const Color(0xFF282D3C));
      expect(mid, isNot(near));
      expect(mid, isNot(far));
      // 눈금 밖은 양 끝 색으로 고정
      expect(tofZoneColor(50, 5), near);
      expect(tofZoneColor(4000, 5), far);
    });

    test('missing distance or invalid status is grey', () {
      expect(tofZoneColor(-1, 5), kTofInvalidColor);
      expect(tofZoneColor(0, 5), kTofInvalidColor);
      expect(tofZoneColor(500, 4), kTofInvalidColor);
      expect(tofZoneColor(500, 255), kTofInvalidColor);
      // 상태값을 모르면(옛 중계기, -1) 거리만 본다
      expect(tofZoneColor(500, -1), isNot(kTofInvalidColor));
      for (final ok in kTofValidStatus) {
        expect(tofZoneColor(500, ok), isNot(kTofInvalidColor), reason: '$ok');
      }
    });
  });

  group('TofHeatmap', () {
    testWidgets('paints all 64 zones by distance and greys -1 / invalid status',
        (tester) async {
      final dist = List<int>.generate(64, (i) => 250 + i * 25);
      dist[10] = -1;
      final status = List<int>.filled(64, 5);
      status[20] = 4;
      final frame = TofRawFrame(
        id: 0,
        have: true,
        distMm: dist,
        status: status,
        ageMs: 120,
      );
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 240,
            child: TofHeatmap(frame: frame, title: 'A', keyPrefix: 'tof-a'),
          ),
        ),
      ));

      for (var i = 0; i < 64; i++) {
        expect(find.byKey(ValueKey('tof-a-zone-$i')), findsOneWidget,
            reason: 'zone $i');
      }
      expect(find.byKey(const ValueKey('tof-a-zone-64')), findsNothing);

      // 거리 순서대로 색이 다르다(0번 가장 가까움 = 붉음)
      expect(zoneColorOf(tester, 'tof-a', 0), tofZoneColor(250, 5));
      expect(zoneColorOf(tester, 'tof-a', 63), tofZoneColor(250 + 63 * 25, 5));
      expect(zoneColorOf(tester, 'tof-a', 0),
          isNot(zoneColorOf(tester, 'tof-a', 63)));
      // −1 과 무효 상태는 회색
      expect(zoneColorOf(tester, 'tof-a', 10), kTofInvalidColor);
      expect(zoneColorOf(tester, 'tof-a', 20), kTofInvalidColor);
      expect(zoneColorOf(tester, 'tof-a', 21), isNot(kTofInvalidColor));

      // 머리글에 유효 칸 수
      expect(find.textContaining('유효 62/64'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('no frame yet draws a grey 8x8 grid and says so',
        (tester) async {
      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 240,
            child: TofHeatmap(frame: null, title: 'B', keyPrefix: 'tof-b'),
          ),
        ),
      ));
      expect(find.text('응답 대기'), findsOneWidget);
      for (final i in [0, 31, 63]) {
        expect(zoneColorOf(tester, 'tof-b', i), kTofInvalidColor);
      }

      await tester.pumpWidget(const MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 240,
            child: TofHeatmap(
                frame: TofRawFrame(id: 1, have: false),
                title: 'B',
                keyPrefix: 'tof-b'),
          ),
        ),
      ));
      expect(find.text('아직 프레임 없음'), findsOneWidget);
      expect(zoneColorOf(tester, 'tof-b', 5), kTofInvalidColor);
    });
  });
}
