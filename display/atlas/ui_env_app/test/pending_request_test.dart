import 'package:deskmate_display/dashboard_view.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/state_source.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

DisplayState _state({String? cause = 'environment', List<String> flags = const ['co2_high']}) =>
    DisplayState(
      fsmState: 'ACTION_ENV',
      phase: 'fatigue',
      context: 'pc',
      focus: 0.3,
      fatigue: 0.6,
      confidence: 0.6,
      gate: 'suggest',
      cause: cause,
      reasons: const [],
      sequence: 1,
      timestamp: DateTime(2026, 10, 25, 12),
      co2Ppm: 1200,
      envFlags: flags,
    );

void main() {
  group('PendingRequest.fromData', () {
    test('reads kind, explanation, minutes and why; needs a request_id', () {
      final r = PendingRequest.fromData({
        'request_id': 'q-7',
        'kind': 'vent_suggest',
        'minutes': 5,
        'why': ['co2_high', 'outdoor_ok'],
        'expires_in_s': 60,
        'explanation': ' 창문 쪽이 시원해요 ',
      })!;
      expect(r.kind, 'vent_suggest');
      expect(r.minutes, 5);
      expect(r.why, ['co2_high', 'outdoor_ok']);
      expect(r.expiresInS, 60);
      expect(r.explanation, '창문 쪽이 시원해요');
      expect(PendingRequest.fromData({'kind': 'checkin_suggest'}), isNull);
      // 옛 이름 ventilate_minutes 도 읽는다
      expect(
          PendingRequest.fromData(
              {'request_id': 'q', 'kind': 'env_suggest', 'ventilate_minutes': 3})!.minutes,
          3);
    });
  });

  group('suggestion card text by kind', () {
    test('checkin_suggest asks "지금 피곤하세요?" with 예/아니오', () {
      const r = PendingRequest(requestId: 'q', kind: 'checkin_suggest');
      expect(suggestionTitle(_state(), r), '지금 피곤하세요?');
      expect(suggestionBody(_state(), r), contains('예/아니오'));
      expect(suggestionButtons(r), (accept: '예', reject: '아니오'));
      const withText = PendingRequest(
          requestId: 'q', kind: 'checkin_suggest', explanation: '움직임이 줄었어요');
      expect(suggestionBody(_state(), withText), '움직임이 줄었어요');
    });

    test('vent_suggest says how many minutes; body prefers explanation, then why codes', () {
      const r = PendingRequest(
          requestId: 'q',
          kind: 'vent_suggest',
          minutes: 15,
          why: ['co2_rule', 'co2_avg=1180', 'after_focus_wait', 'weather_unknown'],
          explanation: 'CO₂ 1180 ppm 이 5 분째 높아요 — 창문을 15 분만 열어 주세요 (가을)');
      expect(suggestionTitle(_state(), r), '창문을 15분 열어 주세요');
      expect(suggestionBody(_state(), r),
          'CO₂ 1180 ppm 이 5 분째 높아요 — 창문을 15 분만 열어 주세요 (가을)');
      expect(suggestionButtons(r), (accept: '열게요', reject: '괜찮아요'));
      // explanation 이 없으면 why 코드를 사람 말로
      const codes = PendingRequest(
          requestId: 'q',
          kind: 'vent_suggest',
          minutes: 5,
          why: ['co2_rule', 'co2_avg=1180', 'urgent', 'after_focus_wait', 'windy', 'weather_unknown', 'zzz']);
      expect(suggestionBody(_state(), codes),
          '이유: CO₂ 가 5분 넘게 높음 · 평균 1180 ppm · 1400 ppm 넘음 · 몰입이 끝나 지금 알림 · 바람이 불어 환기가 잘 됨 · 날씨 정보 없음 · zzz');
      const noMinutes = PendingRequest(requestId: 'q', kind: 'vent_suggest');
      expect(suggestionTitle(_state(), noMinutes), '창문을 잠깐 열어 주세요');
      expect(suggestionBody(_state(), noMinutes), startsWith('이유: '));
    });

    test('checkin and vent requests survive non-ACTION states; others do not', () {
      expect(MqttStateSource.keepsPendingAcrossStates('vent_suggest'), isTrue);
      expect(MqttStateSource.keepsPendingAcrossStates('checkin_suggest'), isTrue);
      expect(MqttStateSource.keepsPendingAcrossStates('env_suggest'), isFalse);
      expect(MqttStateSource.keepsPendingAcrossStates(null), isFalse);
    });

    test('other kinds and no request keep the cause-based wording', () {
      const r = PendingRequest(requestId: 'q', kind: 'env_suggest');
      expect(suggestionTitle(_state(), r), '잠깐 환기해 볼까요?');
      expect(suggestionTitle(_state(), null), '잠깐 환기해 볼까요?');
      expect(suggestionBody(_state(), null), startsWith('이유: CO₂가 높아요'));
      expect(suggestionButtons(null), (accept: '적용할게요', reject: '괜찮아요'));
    });
  });

  group('card in a FOCUS state', () {
    DisplayState focus() => DisplayState(
          fsmState: 'FOCUS_PC',
          phase: 'focus',
          context: 'pc',
          focus: 0.2,
          fatigue: 0.2,
          confidence: 0.5,
          gate: 'none',
          reasons: const [],
          sequence: 4,
          timestamp: DateTime(2026, 10, 25, 12),
        );

    Widget app(PendingRequest request, List<String> verdicts) => MaterialApp(
          home: Scaffold(
            body: DashboardView(
              state: focus(),
              hasPendingRequest: true,
              pendingRequest: request,
              // 자동 순환이 꺼져 시계 화면에 고정돼 있어도 질문 카드가 먼저다.
              pinAmbient: true,
              keystroke: null,
              keystrokeReference: DateTime(2026, 10, 25, 12),
              onFeedback: verdicts.add,
              showDemoControl: false,
              demoCyclingEnabled: false,
              onToggleDemoCycling: () {},
              showFocusDetail: false,
              onShowFocusDetail: (_) {},
            ),
          ),
        );

    testWidgets('vent_suggest card shows in FOCUS_PC and accept/reject answer it',
        (tester) async {
      tester.view.physicalSize = const Size(1024, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final verdicts = <String>[];
      await tester.pumpWidget(app(
          const PendingRequest(
              requestId: 'hub-77',
              kind: 'vent_suggest',
              promptCode: 'ventilate_window',
              minutes: 15,
              why: ['co2_rule'],
              explanation: 'CO₂ 1180 ppm 이 5 분째 높아요 — 창문을 15 분만 열어 주세요 (가을)',
              expiresInS: 180),
          verdicts));
      await tester.pumpAndSettle();
      expect(find.text('창문을 15분 열어 주세요'), findsOneWidget);
      expect(find.text('CO₂ 1180 ppm 이 5 분째 높아요 — 창문을 15 분만 열어 주세요 (가을)'),
          findsOneWidget);
      expect(find.text('열게요'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('suggestion-accept')));
      await tester.tap(find.byKey(const ValueKey('suggestion-reject')));
      expect(verdicts, ['accept', 'reject']);
      expect(tester.takeException(), isNull);
    });

    testWidgets('checkin_suggest asks yes/no in FOCUS_PC', (tester) async {
      tester.view.physicalSize = const Size(1024, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final verdicts = <String>[];
      await tester.pumpWidget(app(
          const PendingRequest(requestId: 'hub-78', kind: 'checkin_suggest', expiresInS: 30),
          verdicts));
      await tester.pumpAndSettle();
      expect(find.text('지금 피곤하세요?'), findsOneWidget);
      expect(find.text('예'), findsOneWidget);
      expect(find.text('아니오'), findsOneWidget);
      await tester.tap(find.text('아니오'));
      expect(verdicts, ['reject']);
      expect(tester.takeException(), isNull);
    });
  });

  group('buildManualAdjustData', () {
    test('has kind, target, from, to, ts; from is omitted when unknown', () {
      expect(
          buildManualAdjustData(targetId: 'desk_lamp', from: 110, to: 64, tsMs: 1769000000000),
          {'kind': 'manual_adjust', 'target_id': 'desk_lamp', 'from': 110, 'to': 64, 'ts': 1769000000000});
      final unknown =
          buildManualAdjustData(targetId: 'vent_fan', from: null, to: 3, tsMs: 1);
      expect(unknown.containsKey('from'), isFalse);
      expect(unknown['to'], 3);
    });
  });
}
