import 'package:deskmate_ui_demo/developer/pr74/notice_banner.dart';
import 'package:deskmate_ui_demo/developer/pr74/voice_alert.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _t0 = DateTime(2026, 10, 26, 18);

// hub 세션이 준 rest_notice 예시(10-26).
Map<String, dynamic> _rest() => {
      'request_id': 'b1-42-rest',
      'kind': 'rest_notice',
      'prompt_code': 'rest_now',
      'options': ['dismiss'],
      'expires_in_s': 60,
      'reason': 'drowsy',
      'cause': 'cognitive',
      'text': '졸음이 이어지고 있어요. 1분만 일어나서 쉬어 가세요.',
      'minutes': 1,
      'rest_min_sec': 60,
      'speak': true,
    };

void main() {
  test('chime pattern and volume map to a sound and a gain', () {
    expect(chimeAssetFor('single'), kChimeSingle);
    expect(chimeAssetFor('double'), kChimeSoft);
    expect(chimeAssetFor('triple'), kChimeAlert);
    expect(chimeAssetFor(' Warning '), kChimeAlert);
    expect(chimeAssetFor(null), kChimeSoft);
    expect(chimeVolumeFor('low'), 0.35);
    expect(chimeVolumeFor('mid'), 0.65);
    expect(chimeVolumeFor('high'), 1.0);
    expect(chimeVolumeFor(null), 1.0);
  });

  test('an auto info card keeps its arm label', () {
    final notice = ScreenNotice.fromRequest({
      'request_id': 'b1-50-arm',
      'kind': 'posture_suggest',
      'prompt_code': 'posture_reset',
      'auto': true,
      'options': ['dismiss'],
      'expires_in_s': 180,
      'arm': 'chime',
      'arm_label': '알림음',
      'channel': 'sound',
      'explanation': '고개가 오래 숙여져 있어요.',
    }, _t0);
    expect(notice.text, '고개가 오래 숙여져 있어요.');
    expect(notice.label, '알림음');
    expect(notice.requestId, 'b1-50-arm');
    expect(notice.showFor, const Duration(seconds: 180));
  });

  test('dismiss-only requests are notices, suggestions are not', () {
    expect(ScreenNotice.isNoticeRequest(_rest()), isTrue);
    expect(
        ScreenNotice.isNoticeRequest({
          'request_id': 'x',
          'kind': 'focus_info',
          'auto': true,
          'options': ['dismiss'],
        }),
        isTrue);
    expect(
        ScreenNotice.isNoticeRequest({
          'request_id': 'y',
          'kind': 'break_suggest',
          'options': ['accept', 'reject'],
        }),
        isFalse);
  });

  test('rest_notice counts down the rest time and then goes away', () {
    final notice = ScreenNotice.fromRequest(_rest(), _t0);
    expect(notice.text, '졸음이 이어지고 있어요. 1분만 일어나서 쉬어 가세요.');
    expect(notice.requestId, 'b1-42-rest');
    expect(notice.kind, 'rest_notice');
    expect(notice.remaining(_t0.add(const Duration(seconds: 15))),
        const Duration(seconds: 45));
    expect(notice.expired(_t0.add(const Duration(seconds: 59))), isFalse);
    expect(notice.expired(_t0.add(const Duration(seconds: 60))), isTrue);

    final board = NoticeBoard()..show(notice);
    expect(board.current(_t0.add(const Duration(seconds: 30))), same(notice));
    expect(board.current(_t0.add(const Duration(seconds: 61))), isNull);
    board.show(notice);
    expect(board.dismiss(_t0.add(const Duration(seconds: 5))), same(notice));
    expect(board.current(_t0.add(const Duration(seconds: 6))), isNull);
  });

  test('a notice without text or times still shows briefly', () {
    final notice = ScreenNotice.fromRequest({'auto': true}, _t0);
    expect(notice.text, '알림');
    expect(notice.requestId, isNull);
    expect(notice.countdown, isNull);
    expect(notice.showFor, const Duration(seconds: 60));
  });

  testWidgets('banner shows the text and countdown and closes', (tester) async {
    var closed = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: NoticeBanner(
          notice:
              ScreenNotice.fromRequest({..._rest(), 'arm_label': '화면 알림'}, _t0),
          now: _t0.add(const Duration(seconds: 5)),
          onDismiss: () => closed++,
        ),
      ),
    ));
    expect(find.text('졸음이 이어지고 있어요. 1분만 일어나서 쉬어 가세요.'), findsOneWidget);
    expect(find.text('화면 알림'), findsOneWidget);
    expect(
        tester
            .widget<Text>(find.byKey(const ValueKey('notice-countdown')))
            .data,
        '0:55');
    await tester.tap(find.byKey(const ValueKey('notice-dismiss')));
    expect(closed, 1);
    expect(formatCountdown(const Duration(seconds: 65)), '1:05');
  });
}
