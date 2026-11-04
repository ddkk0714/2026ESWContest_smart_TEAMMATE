import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/cards/card_look.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/developer/pr74/voice_alert.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'auto_action_test.dart' show cmd;
import 'live_test.dart' show FakeTransport, phase, request;

ScreenNotice notice(String text, {String? id, int at = 0}) => ScreenNotice(
    text: text,
    shownAt: DateTime(2026, 11, 5, 10, 0, at),
    showFor: const Duration(minutes: 5),
    requestId: id);

void main() {
  test('notices stack and only the top one closes', () {
    final now = DateTime(2026, 11, 5, 10, 1);
    final board = NoticeBoard()
      ..show(notice('쉬어 가요', id: 'a'))
      ..show(notice('자세를 바로 해 볼까요?', id: 'b', at: 5));
    expect(board.all(now).map((n) => n.text), ['쉬어 가요', '자세를 바로 해 볼까요?']);
    expect(board.current(now)?.requestId, 'b');
    board.show(notice('자세를 다시 볼까요?', id: 'b', at: 9)); // 같은 id 는 바꾼다
    expect(board.all(now), hasLength(2));
    expect(board.dismiss(now)?.text, '자세를 다시 볼까요?');
    expect(board.all(now).single.requestId, 'a');
  });

  test('card pictures follow what the card is about', () {
    final posture = lookFor(arm: 'posture_card', posture: 'forward_head');
    expect(posture.isPosture, isTrue);
    expect(posture.label, '자세 안내');
    expect(lookFor(kind: 'rest_notice').icon, Icons.self_improvement_rounded);
    expect(lookFor(kind: 'vent_suggest').icon, Icons.window_rounded);
    expect(lookForTarget('desk_lamp').icon, Icons.lightbulb_rounded);
    expect(lookForTarget('vent_fan').icon, Icons.wind_power_rounded);
    expect(lookForTarget('music').icon, Icons.music_note_rounded);
    expect(lookForHelp('알림').label, '알림');
  });

  testWidgets('cards stack on top of each other and close one by one',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final m = DemoModel(DemoConfig(
        jsonDecode(File('assets/config/demo.json').readAsStringSync())
            as Map<String, dynamic>));
    late FakeTransport transport;
    final link = LiveLink(
        transportFactory: (h, p) => transport = FakeTransport(),
        retryDelay: const Duration(hours: 1));
    await tester.pumpWidget(DemoApp(model: m, ticking: false, live: link));
    link.configure('192.168.0.10', 1883);
    await tester.pump();
    transport.deliver(
        LiveLink.stateTopic,
        phase('ACTION_POSTURE', fatigue: .7, summary: {
          'present': true,
          'tof': {'posture': 'forward_head'}
        }));
    transport.deliver(
        LiveLink.requestTopic,
        request('posture_suggest', {
          'request_id': 'card-1',
          'auto': true,
          'options': ['dismiss'],
          'arm': 'posture_card',
          'arm_label': '자세 안내 카드',
          'text': '자세를 바로 해 볼까요?',
          'expires_in_s': 60,
        }));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.byKey(const ValueKey('card-deck')), findsOneWidget);
    expect(find.text('자세를 바로 해 볼까요?'), findsOneWidget);
    expect(find.text('지금 자세 · 고개가 앞으로 나옴'), findsOneWidget);
    expect(find.text('턱을 살짝 당기고 화면을 눈높이로 올려 보세요.'), findsOneWidget);
    expect(find.byKey(const ValueKey('card-deck-count')), findsNothing);
    // 거의 화면을 덮는다
    expect(tester.getSize(find.byKey(const ValueKey('notice-banner'))).width,
        greaterThan(800));

    transport.deliver(LiveLink.commandTopic,
        cmd('desk_lamp', 'adjust_brightness', {'percent': 60}));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('카드 2장'), findsOneWidget);
    // 새 카드가 맨 위 — 맨 위만 누를 수 있다
    await tester.tap(find.byKey(const ValueKey('auto-action-dismiss')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const ValueKey('auto-action-card')), findsNothing);
    expect(find.byKey(const ValueKey('card-deck-count')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('notice-dismiss')));
    await tester.pump();
    expect(find.text('자세를 바로 해 볼까요?'), findsNothing);
    expect(find.byKey(const ValueKey('card-deck')), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });
}
