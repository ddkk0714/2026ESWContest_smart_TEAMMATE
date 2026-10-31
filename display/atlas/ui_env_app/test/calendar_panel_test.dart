import 'package:deskmate_display/calendar_panel.dart';
import 'package:deskmate_display/workspace_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('old events stay readable; new time ranges and places round-trip', () {
    final old = {
      'version': 1,
      'pinnedEvent': 'old',
      'events': [
        {
          'id': 'old',
          'title': '기존 일정',
          'date': '2026-10-18',
          'hour': 14,
          'minute': 0
        }
      ]
    };
    final migrated = WorkspacePreferences.fromJson(old);
    expect(migrated.events.single.title, '기존 일정');
    expect(migrated.events.single.startMinutes, 14 * 60);
    expect(migrated.events.single.endMinutes, isNull);
    expect(migrated.events.single.location, isEmpty);
    expect(migrated.toJson().containsKey('pinnedEvent'), isFalse);
    final event = DeskEvent(
        id: 'new',
        title: '새 일정',
        date: DateTime(2026, 10, 18),
        hour: 13,
        minute: 15,
        endHour: 14,
        endMinute: 45,
        location: '공학관');
    final parsed = DeskEvent.parse(event.toJson())!;
    expect(parsed.startMinutes, 13 * 60 + 15);
    expect(parsed.endMinutes, 14 * 60 + 45);
    expect(parsed.location, '공학관');
    for (final invalid in [
      {'endHour': 12, 'endMinute': 0},
      {'endHour': 13, 'endMinute': 15},
      {'endHour': 24, 'endMinute': 0},
      {'endHour': 14, 'endMinute': 60},
      {'endHour': 14, 'endMinute': null},
    ]) {
      expect(DeskEvent.parse({...event.toJson(), ...invalid}), isNull);
    }
  });

  for (final size in [
    const Size(800, 440),
    const Size(680, 380),
    const Size(244, 560)
  ]) {
    testWidgets('six-week month with long titles and crowded agenda fits $size',
        (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final events = List.generate(
          10,
          (i) => DeskEvent(
              id: '$i',
              title: '매우 긴 일정 이름과 프로젝트 발표 준비 $i',
              date: DateTime(2026, 8, 1),
              hour: 8 + i,
              minute: 0,
              endHour: 9 + i,
              endMinute: 0,
              location: '공학관 201호 팀 프로젝트 연구실'));
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: DeskCalendarPanel(
                  month: DateTime(2026, 8),
                  selectedDay: DateTime(2026, 8, 1),
                  today: DateTime(2026, 8, 1),
                  events: events,
                  onMonth: (_) {},
                  onDay: (_) {},
                  onAdd: () {},
                  onClose: () {},
                  onEdit: (_) {},
                  onDelete: (_) {}))));
      expect(
          find.byKey(const ValueKey('calendar-day-2026-8-31')), findsOneWidget);
      expect(find.text('+9'), findsOneWidget);
      expect(tester.takeException(), isNull);
      if (size.width >= 620) {
        await tester.scrollUntilVisible(
            find.byKey(const ValueKey('calendar-timeline-9')), 150,
            scrollable: find.descendant(
                of: find.byKey(const ValueKey('calendar-timeline')),
                matching: find.byType(Scrollable)));
        expect(
            find.byKey(const ValueKey('calendar-timeline-9')), findsOneWidget);
        expect(tester.takeException(), isNull);
      }
    });
  }
}
