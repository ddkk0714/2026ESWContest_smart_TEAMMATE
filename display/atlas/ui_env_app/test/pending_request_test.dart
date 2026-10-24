import 'package:deskmate_display/dashboard_view.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/state_source.dart';
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

    test('vent_suggest says how many minutes and why', () {
      const r = PendingRequest(
          requestId: 'q', kind: 'vent_suggest', minutes: 5, why: ['co2_high', 'outdoor_ok']);
      expect(suggestionTitle(_state(), r), '창문을 5분 열어 주세요');
      expect(suggestionBody(_state(), r), '이유: CO₂가 높아요 · 바깥 날씨가 괜찮아요');
      expect(suggestionButtons(r), (accept: '열게요', reject: '괜찮아요'));
      const noMinutes = PendingRequest(requestId: 'q', kind: 'vent_suggest', why: ['zzz']);
      expect(suggestionTitle(_state(), noMinutes), '창문을 잠깐 열어 주세요');
      expect(suggestionBody(_state(), noMinutes), '이유: zzz');
    });

    test('other kinds and no request keep the cause-based wording', () {
      const r = PendingRequest(requestId: 'q', kind: 'env_suggest');
      expect(suggestionTitle(_state(), r), '잠깐 환기해 볼까요?');
      expect(suggestionTitle(_state(), null), '잠깐 환기해 볼까요?');
      expect(suggestionBody(_state(), null), startsWith('이유: CO₂가 높아요'));
      expect(suggestionButtons(null), (accept: '적용할게요', reject: '괜찮아요'));
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
