import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';

DemoModel model() => DemoModel(DemoConfig(
    jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>));

void main() {
  test('same work input auto-starts trained persona but asks new persona', () {
    final trained = model()..beginScenario();
    final fresh = model()
      ..selectProfile(2)
      ..beginScenario();
    for (int i = 0; i < 9; i++) {
      trained.tick();
      fresh.tick();
    }
    expect(trained.active, false);
    trained.tick();
    fresh.tick();
    expect(trained.page, DemoPage.focus);
    expect(trained.active, true);
    expect(fresh.proposal, true);
    expect(fresh.active, false);
    fresh.start();
    expect(fresh.persona.rounds, 1);
    expect(fresh.active, true);
  });

  test('focus notice lasts five seconds and detail and widget views expire',
      () {
    final m = model()..start();
    for (int i = 0; i < 4; i++) {
      m.tick();
    }
    expect(m.notice, isNotEmpty);
    m.tick();
    expect(m.notice, isEmpty);
    m.show(DemoPage.details);
    for (int i = 0; i < 10; i++) {
      m.tick();
    }
    expect(m.page, DemoPage.focus);
    m.show(DemoPage.widgets);
    for (int i = 0; i < 29; i++) {
      m.tick();
    }
    expect(m.page, DemoPage.widgets);
    m.touch();
    m.tick();
    expect(m.page, DemoPage.widgets);
    for (int i = 0; i < 29; i++) {
      m.tick();
    }
    expect(m.page, DemoPage.details);
  });

  test(
      'profile order drives intervention and new profile asks before changing light',
      () {
    final student = model()..start();
    student.fatigue();
    expect(student.brightness, .95);
    expect(student.fanOn, false);
    student.fatigue();
    expect(student.fanOn, true);
    final worker = model()
      ..selectProfile(1)
      ..start();
    worker.fatigue();
    expect(worker.brightness, .45);
    expect(worker.events.last, contains('알림'));
    final fresh = model()
      ..selectProfile(2)
      ..start();
    fresh.fatigue();
    expect(fresh.brightness, .85);
    fresh.feedback(true);
    expect(fresh.brightness, .95);
  });

  test('absence freezes time, allows resume and finishes with devices off', () {
    final m = model()
      ..start()
      ..tick();
    m.away();
    m.tick();
    expect(m.sessionSeconds, 60);
    m.resume();
    m.tick();
    expect(m.sessionSeconds, 120);
    m.away();
    for (int i = 0; i < 5; i++) {
      m.tick();
    }
    expect(m.page, DemoPage.report);
    expect(m.ledOn, false);
    expect(m.fanOn, false);
    expect(m.completedSeconds, 120);
    expect(m.completedSessions, 1);
    m.finish();
    expect(m.completedSessions, 1);
  });

  testWidgets(
      'home, focus, details, widgets, report and profile fit Pi display',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false));
    await tester.pumpAndSettle();
    expect(find.text('DESKMATE'), findsNothing);
    expect(find.text('오늘도, 당신의 속도로.'), findsOneWidget);
    expect(find.textContaining('UI DEMO V2'), findsNothing);
    final clockCenter =
        tester.getCenter(find.byKey(const ValueKey('home-clock-start')));
    expect(clockCenter.dx, closeTo(512, 1));
    expect(clockCenter.dy, closeTo(300, 3));
    expect(tester.getCenter(find.byKey(const ValueKey('home-message'))).dy,
        closeTo(194, 5));
    expect(tester.getTopLeft(find.byKey(const ValueKey('home-date'))).dy,
        greaterThan(clockCenter.dy + 70));
    expect(find.text('허리 피자, 인생 피자. 웃음꽃 피자.'), findsNothing);
    expect(find.text('집중할 때는 조용히, 필요할 때는 곁에.'), findsNothing);
    expect(find.text('집중 시작'), findsNothing);
    expect(find.text('내 책상'), findsNothing);
    await tester.tap(find.text('720 ppm'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    expect(m.page, DemoPage.widgets);
    expect(find.byKey(const ValueKey('home-fixed-grid')), findsOneWidget);
    m.show(DemoPage.home);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('home-clock-start')));
    await tester.pumpAndSettle();
    expect(m.page, DemoPage.focus);
    await tester.tapAt(const Offset(500, 350));
    await tester.pumpAndSettle();
    expect(find.text('작업 종료'), findsOneWidget);
    expect(
        (tester.getCenter(find.byKey(const ValueKey('focus-elapsed-clock'))) -
                tester.getCenter(find.byKey(const ValueKey('focus-time-card'))))
            .distance,
        lessThan(.01));
    final environment =
        tester.getSize(find.byKey(const ValueKey('desk-environment-card')));
    final posture =
        tester.getSize(find.byKey(const ValueKey('desk-posture-card')));
    expect(environment, posture);
    expect(
        find.descendant(
            of: find.byKey(const ValueKey('desk-environment-card')),
            matching: find.byIcon(Icons.eco_outlined)),
        findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('멀티 위젯'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.byKey(const ValueKey('home-fixed-grid')), findsOneWidget);
    expect(tester.takeException(), isNull);
    m.show(DemoPage.details);
    m.finish();
    await tester.pumpAndSettle();
    expect(find.text('오늘 피곤했나요?'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('괜찮았어요'));
    await tester.pumpAndSettle();
    expect(m.feeling, '괜찮았어요');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('proposal accept and reject and demo controls are reachable',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model()
      ..selectProfile(2)
      ..beginScenario();
    for (int i = 0; i < 10; i++) {
      m.tick();
    }
    await tester.pumpWidget(DemoApp(model: m, ticking: false));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();
    expect(m.active, false);
    await tester.tap(find.byTooltip('발표 조작창'));
    await tester.pumpAndSettle();
    expect(find.text('발표 조작창'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('재실·작업 상황 재생'));
    await tester.pumpAndSettle();
    for (int i = 0; i < 10; i++) {
      m.tick();
    }
    await tester.pumpAndSettle();
    await tester.tap(find.text('시작할게요'));
    await tester.pumpAndSettle();
    expect(m.active, true);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('top right exit closes home and focused views', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    var exits = 0;
    await tester
        .pumpWidget(DemoApp(model: m, ticking: false, onExit: () => exits++));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('앱 종료'));
    expect(exits, 1);
    m.start();
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('앱 종료'));
    expect(exits, 2);
    await tester.pumpWidget(const SizedBox());
  });
  test('profile slots grow by two and keep an available final add slot', () {
    final m = model();
    expect(m.profileSlotCount, 4);
    m.profiles.removeLast();
    m.profiles.removeLast();
    expect(m.profileSlotCount, 2);
    for (int count = 2; count <= 7; count++) {
      m.saveProfile(Persona(name: '사용자$count', job: '수험생', rounds: 0));
      expect(m.profileSlotCount, ((count + 2) ~/ 2) * 2);
      expect(m.profileSlotCount, greaterThan(m.profiles.length));
    }
  });

  testWidgets(
      'profile selection, emoji editing and last-slot registration work',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false));
    await tester.pumpAndSettle();
    await tester.tap(find.text('지우 · 수험생'));
    await tester.pumpAndSettle();
    expect(m.page, DemoPage.home);
    expect(find.byKey(const ValueKey('home-clock-start')), findsOneWidget);
    final popupSize =
        tester.getSize(find.byKey(const ValueKey('profile-picker')));
    expect(popupSize.width, lessThan(1024));
    expect(popupSize.height, lessThan(600));
    expect(find.text('수험생'), findsNothing);
    final firstAvatar = find.descendant(
        of: find.byKey(const ValueKey('profile-slot-0')),
        matching: find.byType(Image));
    expect(tester.getTopLeft(find.text('지우')).dy,
        greaterThan(tester.getBottomRight(firstAvatar).dy));
    expect(find.byKey(const ValueKey('profile-slot-2')), findsOneWidget);
    expect(tester.getCenter(find.byKey(const ValueKey('new-profile-slot'))).dx,
        greaterThan(512));
    expect(tester.getCenter(find.byKey(const ValueKey('new-profile-slot'))).dy,
        greaterThan(300));
    await tester.tap(find.text('프로필 관리'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('지우 이미지 변경'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('avatar-rabbit')));
    await tester.pumpAndSettle();
    expect(m.profiles[0].avatar, 'rabbit');
    await tester.tap(find.text('완료'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('profile-slot-1')));
    await tester.pumpAndSettle();
    expect(m.profileIndex, 1);
    expect(m.page, DemoPage.home);
    expect(find.byKey(const ValueKey('profile-picker')), findsNothing);
    await tester.tap(find.text('민준 · 재택 직장인'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('new-profile-slot')));
    await tester.pumpAndSettle();
    expect(m.page, DemoPage.home);
    expect(find.byKey(const ValueKey('home-clock-start')), findsOneWidget);
    expect(find.byKey(const ValueKey('profile-creation')), findsOneWidget);
    expect(find.byKey(const ValueKey('profile-question')), findsOneWidget);
    final initialName = tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text;
    expect(initialName, isNotEmpty);
    await tester.tap(find.byKey(const ValueKey('profile-random-name')));
    await tester.pump();
    expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('profile-name')))
            .controller!
            .text,
        isNot(initialName));
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '';
    await tester.tap(find.byKey(const ValueKey('profile-random-name')));
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '';
    await tester.pump();
    expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('profile-next')))
            .onPressed,
        isNull);
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '꽃님';
    await tester.tap(find.byKey(const ValueKey('profile-random-name')));
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '꽃님';
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('profile-next')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('creation-avatar-flower')));
    for (int i = 1; i < 9; i++) {
      if (i == 2) await tester.tap(find.text('창작·독서'));
      if (i == 3) await tester.tap(find.byTooltip('늘리기'));
      if (i == 4) await tester.tap(find.byTooltip('줄이기'));
      if (i == 5) await tester.tap(find.byTooltip('선풍기 우선순위 올리기'));
      if (i == 6) await tester.tap(find.text('무음'));
      if (i == 7) await tester.tap(find.text('피곤'));
      if (i == 8) {
        await tester.tap(find.byKey(const ValueKey('placement-right')));
      }
      await tester.pump();
      expect(find.byKey(const ValueKey('profile-question')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('profile-next')));
      await tester.pumpAndSettle();
    }
    expect(find.byKey(const ValueKey('profile-welcome')), findsOneWidget);
    expect(find.textContaining('꽃님,'), findsOneWidget);
    await tester.tap(find.text('내 책상으로 가기'));
    await tester.pumpAndSettle();
    expect(m.persona.name, '꽃님');
    expect(m.persona.avatar, 'flower');
    expect(m.persona.job, '창작·독서');
    expect(m.persona.age, 21);
    expect(m.persona.sleep, 6);
    expect(m.persona.order.first, '선풍기');
    expect(m.persona.sound, '무음');
    expect(m.persona.mood, '피곤');
    expect(m.persona.placement, 'right');
    expect(m.profiles.length, 4);
    expect(m.profileSlotCount, 6);
    await tester.tap(find.text('꽃님 · 창작·독서'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('empty-profile-4')), findsOneWidget);
    expect(find.byKey(const ValueKey('new-profile-slot')), findsOneWidget);
    await tester.tap(find.byTooltip('프로필 선택 닫기'));
    await tester.pumpAndSettle();
    expect(m.page, DemoPage.home);
    expect(find.byKey(const ValueKey('profile-picker')), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('profile conversation supports back and cancel without saving',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false));
    await tester.tap(find.text('지우 · 수험생'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('new-profile-slot')));
    await tester.pumpAndSettle();
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '봄';
    await tester.tap(find.byKey(const ValueKey('profile-random-name')));
    tester
        .widget<TextField>(find.byKey(const ValueKey('profile-name')))
        .controller!
        .text = '봄';
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('profile-next')));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('이전 질문'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('profile-name')))
            .controller!
            .text,
        '봄');
    await tester.tap(find.byTooltip('이전 질문'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('profile-picker')), findsOneWidget);
    expect(m.profiles.length, 3);
    await tester.tap(find.byKey(const ValueKey('new-profile-slot')));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('프로필 등록 닫기'));
    await tester.pumpAndSettle();
    expect(m.profiles.length, 3);
    expect(m.persona.name, '지우');
    expect(m.page, DemoPage.home);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'presentation fatigue and absence buttons work from idle or active session',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    for (final active in [false, true]) {
      for (final fatigue in [true, false]) {
        final m = model();
        if (active) {
          m.start();
          m.tick();
          m.tick();
          m.show(DemoPage.details);
        }
        final elapsed = m.sessionSeconds;
        await tester
            .pumpWidget(DemoApp(key: UniqueKey(), model: m, ticking: false));
        await tester.pumpAndSettle();
        await tester.tap(find.byTooltip('발표 조작창'));
        await tester.pumpAndSettle();
        expect(find.text('발표 조작창'), findsOneWidget);
        expect(find.text('같은 상황, 다른 반응'), findsNothing);
        final button = find.widgetWithText(
            OutlinedButton, fatigue ? '피로·졸음 상황' : '부재 → 일시정지·종료');
        expect(tester.widget<OutlinedButton>(button).onPressed, isNotNull);
        await tester.tap(button);
        await tester.pumpAndSettle();
        expect(m.active, true);
        expect(m.sessionSeconds, elapsed);
        if (fatigue) {
          expect(m.fatigueEvents, 1);
          expect(m.page, DemoPage.focus);
        } else {
          expect(m.absent, true);
          expect(m.page, DemoPage.details);
          for (int i = 0; i < 5; i++) {
            m.tick();
          }
          expect(m.page, DemoPage.report);
        }
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
      }
    }
  });
  testWidgets('fatigue card closes after either feedback answer',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    for (final answer in ['다른 방법', '좋아요']) {
      final m = model()
        ..selectProfile(2)
        ..start();
      await tester
          .pumpWidget(DemoApp(key: UniqueKey(), model: m, ticking: false));
      await tester.pumpAndSettle();
      m.fatigue();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('suggestion-card')), findsOneWidget);
      await tester.tap(find.text(answer));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('suggestion-card')), findsNothing);
      expect(find.text(m.notice), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    }
  });
}
