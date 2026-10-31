// hub 자동 실행 카드·되돌리기·빠른 시연(2026-10-31).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/live/auto_action.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';

import 'live_test.dart' show FakeTransport, connected, model, phase;

Map<String, Object?> cmd(String target, String operation, Object? value,
        {String gate = 'auto',
        String origin = 'ACTION_POSTURE',
        String cause = 'posture',
        String id = 'c1'}) =>
    {
      'schema_version': '1.0',
      'ts': 1769000010.0,
      'node': 'hub',
      'data': {
        'command_id': id,
        'target_id': target,
        'operation': operation,
        'value': value,
        'origin_state': origin,
        'cause': cause,
        'gate': gate,
        'requires_confirmation': false,
      },
    };

Map<String, Object?> result(String id, String status, {String? error}) => {
      'schema_version': '1.0',
      'ts': 1769000011.0,
      'node': 'pi5-ui',
      'data': {
        'command_id': id,
        'status': status,
        if (error != null) 'error_code': error,
      },
    };

const sleepy = {
  'tof': {'posture': 'face_down'},
  'personalize': {
    'arm': 'wake_light',
    'arm_label': '무드등 밝혀 깨우기',
    'source': 'bandit',
    'n_personal': 0,
  },
};

void main() {
  group('문구표', () {
    test('기기 × 원인', () {
      const up = {'percent': 60, 'min_raw': 153};
      expect(autoActionTitle('desk_lamp', 'adjust_brightness', up, 'posture'),
          '졸음이 내려앉기 전에, 불빛을 밝혀 둘게요');
      expect(
          autoActionTitle('desk_lamp', 'adjust_brightness', up, 'environment'),
          '주변이 어둑해져 불빛을 조금 더했어요');
      expect(autoActionTitle('desk_lamp', 'adjust_brightness', up, 'cognitive'),
          '머리가 무거워 보여 환한 빛으로 바꿨어요');
      expect(
          autoActionTitle(
              'desk_lamp', 'adjust_brightness', {'percent': -50}, 'cognitive'),
          '눈이 쉴 수 있게 불빛을 낮췄어요');
      expect(autoActionTitle('vent_fan', 'set_power', 'on', 'environment'),
          '공기가 무거워 바람을 조금 들였어요');
      expect(autoActionTitle('vent_fan', 'set_power', 'off', 'environment'),
          isNull);
      expect(autoActionTitle('air_purifier', 'set_mode', 'high', 'environment'),
          '숨 쉬기 편하게 공기를 맑게 하고 있어요');
      // 알림음·음성·휴식 안내는 hub 정보 카드·rest_notice 가 맡는다 — 겹치지 않게 카드 없음
      expect(autoActionTitle('speaker', 'chime', {}, 'posture'), isNull);
      expect(autoActionTitle('speaker', 'speak', {'text': '쉬어요'}, 'cognitive'),
          isNull);
    });
  });

  group('LiveLink 자동 실행 카드', () {
    test('무드등 자동 실행 → 카드, 결과, 되돌리기 → undo', () async {
      var now = DateTime(2026, 11, 6, 14);
      final (link, transport) = await connected(clock: () => now);
      transport.deliver(LiveLink.stateTopic,
          phase('ACTION_POSTURE', fatigue: .82, summary: sleepy));
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'adjust_brightness', {'percent': 60, 'min_raw': 153}));
      var card = link.autoActions.current(now)!;
      expect(card.title, '졸음이 내려앉기 전에, 불빛을 밝혀 둘게요');
      expect(card.reason, '책상에 엎드린 채 한참이 지났어요'); // 숫자(피로 %)는 제품 화면에 안 낸다
      expect(card.personalNote, '처음에 알려 준 대로, 빛으로 깨워 드려요');
      transport.deliver(LiveLink.resultTopic, result('c1', 'succeeded'));
      expect(link.autoActions.current(now)!.status, AutoActionStatus.done);

      now = now.add(const Duration(seconds: 20));
      expect(link.undoAutoAction(), isTrue);
      final fb = transport.sent(LiveLink.feedbackTopic).last['data'];
      expect(fb['request_id'], 'atlas-display');
      expect(fb['verdict'], 'reject');
      expect(fb['response_ms'], 20000);
      expect(link.autoActions.current(now)!.status, AutoActionStatus.undoing);
      expect(link.undoAutoAction(), isFalse); // 두 번 보내지 않는다
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'set_brightness', 120, gate: 'undo', id: 'u1'));
      card = link.autoActions.current(now)!;
      expect(card.status, AutoActionStatus.undone);
      expect(card.statusTextAt(now), '원래대로 돌려 뒀어요. 다음엔 다른 방법을 찾아볼게요');
      link.dispose();
    });

    test('되돌리기는 60 s 안에서만, 카드는 90 s 뒤 사라진다', () async {
      var now = DateTime(2026, 11, 6, 14);
      final (link, transport) = await connected(clock: () => now);
      transport.deliver(LiveLink.commandTopic,
          cmd('vent_fan', 'set_power', 'on', cause: 'environment', origin: 'ACTION_ENV'));
      expect(link.autoActions.current(now)!.personalNote, isNull); // 정책 결정 없음 = FSM 기본
      now = now.add(const Duration(seconds: 61));
      expect(link.autoActions.current(now)!.canUndo(now), isFalse);
      expect(link.undoAutoAction(), isFalse);
      expect(transport.sent(LiveLink.feedbackTopic), isEmpty);
      now = now.add(const Duration(seconds: 30));
      expect(link.autoActions.current(now), isNull);
      link.dispose();
    });

    test('실패 결과는 이유를 붙이고 되돌리기를 끈다', () async {
      final now = DateTime(2026, 11, 6, 14);
      final (link, transport) = await connected(clock: () => now);
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'adjust_brightness', {'percent': 60}));
      transport.deliver(LiveLink.resultTopic,
          result('c1', 'failed', error: 'lamp_not_configured'));
      final card = link.autoActions.current(now)!;
      expect(card.statusTextAt(now), '잠시 기기에 닿지 못했어요'); // 오류 코드는 개발자 화면에만
      expect(card.canUndo(now), isFalse);
      link.dispose();
    });

    test('제안(suggest)·되돌리기만·알림음·ACTION 밖 명령은 카드 없음', () async {
      final now = DateTime(2026, 11, 6, 14);
      final (link, transport) = await connected(clock: () => now);
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'adjust_brightness', {'percent': 60}, gate: 'suggest'));
      transport.deliver(LiveLink.commandTopic,
          cmd('speaker', 'chime', {'pattern': 'double'}));
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'set_brightness', 255, origin: 'REST'));
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'set_brightness', 100, gate: 'undo'));
      expect(link.autoActions.current(now), isNull);
      link.dispose();
    });

    test('반응이 쌓인 뒤의 개인화 문구', () async {
      final now = DateTime(2026, 11, 6, 14);
      final (link, transport) = await connected(clock: () => now);
      transport.deliver(
          LiveLink.stateTopic,
          phase('ACTION_POSTURE', summary: {
            'personalize': {'arm': 'wake_light', 'source': 'bandit', 'n_personal': 3},
          }));
      transport.deliver(LiveLink.commandTopic,
          cmd('desk_lamp', 'adjust_brightness', {'percent': 60}));
      expect(link.autoActions.current(now)!.personalNote,
          '그동안의 반응을 기억해서 골랐어요');
      link.dispose();
    });
  });

  test('되돌리기를 보냈는데 hub 되돌리기 명령이 없으면 — 기기는 그대로, 학습만', () async {
    var now = DateTime(2026, 11, 6, 14);
    final (link, transport) = await connected(clock: () => now);
    transport.deliver(LiveLink.commandTopic,
        cmd('desk_lamp', 'adjust_brightness', {'percent': 60}));
    now = now.add(const Duration(seconds: 50));
    expect(link.undoAutoAction(), isTrue);
    expect(link.autoActions.current(now)!.statusTextAt(now), '원래대로 돌려 두는 중이에요…');
    now = now.add(const Duration(seconds: 10));
    expect(link.autoActions.current(now)!.statusTextAt(now),
        '알겠어요. 다음엔 다른 방법으로 다가갈게요');
    link.dispose();
  });

  group('빠른 시연', () {
    test('토글은 deskmate/demo/mode 로, 지금 모드는 state/phase 로', () async {
      final (link, transport) = await connected();
      expect(link.setDemoSpeed(true), isTrue);
      expect(transport.sent(LiveLink.demoModeTopic).single['data'],
          {'speed': 'fast'});
      transport.deliver(
          LiveLink.stateTopic,
          phase('FOCUS_PC', summary: {
            'demo_mode': {'speed': 'fast', 'enabled': true},
          }));
      expect(link.state!.demoFast, isTrue);
      link.setDemoSpeed(false);
      expect(transport.sent(LiveLink.demoModeTopic).last['data'],
          {'speed': 'normal'});
      transport.deliver(LiveLink.stateTopic, phase('FOCUS_PC', seq: 2));
      expect(link.state!.demoFast, isFalse); // 옛 hub(필드 없음)도 꺼짐으로
      link.dispose();
    });
  });

  testWidgets('화면: 자동 실행 카드·되돌리기 버튼·빠른 시연 배지', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    late FakeTransport transport;
    final link = LiveLink(
        transportFactory: (h, p) => transport = FakeTransport(),
        retryDelay: const Duration(hours: 1));
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false, live: link));
    link.configure('192.168.0.10', 1883);
    await tester.pump();
    transport.deliver(
        LiveLink.stateTopic,
        phase('ACTION_POSTURE', fatigue: .82, summary: {
          ...sleepy,
          'demo_mode': {'speed': 'fast', 'enabled': true},
        }));
    transport.deliver(LiveLink.commandTopic,
        cmd('desk_lamp', 'adjust_brightness', {'percent': 60, 'min_raw': 153}));
    await tester.pump();
    expect(find.byKey(const ValueKey('auto-action-card')), findsOneWidget);
    expect(find.text('졸음이 내려앉기 전에, 불빛을 밝혀 둘게요'), findsOneWidget);
    expect(find.text('처음에 알려 준 대로, 빛으로 깨워 드려요'), findsOneWidget);
    expect(find.byKey(const ValueKey('demo-fast-badge')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('auto-action-undo')));
    await tester.pump();
    expect(transport.sent(LiveLink.feedbackTopic).last['data']['verdict'],
        'reject');
    expect(find.text('원래대로 돌려 두는 중이에요…'), findsOneWidget);
    expect(find.byKey(const ValueKey('auto-action-undo')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('auto-action-dismiss')));
    await tester.pump();
    expect(find.byKey(const ValueKey('auto-action-card')), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });
}
