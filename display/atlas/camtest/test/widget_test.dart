import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:camtest/main.dart';
import 'package:camtest/posture_source.dart';
import 'package:camtest/posture_state.dart';

PostureState state({
  PostureLabel label = PostureLabel.upright,
  bool present = true,
  bool valid = true,
  double? headDeltaMm = -4.2,
  double? nodPerMin = 0,
  String? scenario,
  List<String> reasons = const ['posture_only'],
}) =>
    PostureState(
      label: label,
      present: present,
      valid: valid,
      motion: 0.08,
      coverage: 0.21,
      focusDrop: 0.12,
      fatigue: 0.1,
      reasons: reasons,
      sequence: 7,
      timestamp: DateTime.now(),
      headDeltaMm: headDeltaMm,
      nodPerMin: nodPerMin,
      scenario: scenario,
      node: 'pi4-posture',
    );

/// 첫 프레임과, fetch 의 Future 가 풀린 뒤의 프레임까지 그린다.
Future<void> show(WidgetTester tester, PostureSource source) async {
  tester.view.physicalSize = const Size(1024, 600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(PostureApp(source: source));
  await tester.pump();
  await tester.pump();
  await tester.pump();
}

void main() {
  testWidgets('앉아 있고 자세가 바르면 그렇게 말한다', (tester) async {
    await show(tester, _FakeSource(state()));

    expect(find.text('바른 자세'), findsOneWidget);
    expect(find.text('착석 중'), findsOneWidget);
    expect(find.text('−4mm'), findsOneWidget);
    expect(find.text('자세 신호만 사용'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('자리가 비면 착석 배지도 같이 바뀐다', (tester) async {
    await show(
        tester,
        _FakeSource(state(
            label: PostureLabel.absent,
            present: false,
            headDeltaMm: null,
            nodPerMin: null,
            reasons: const ['posture_only', 'desk_empty'])));

    // 원 안의 라벨과 착석 배지 두 곳에 같은 말이 뜬다.
    expect(find.text('자리 비움'), findsNWidgets(2));
    expect(find.text('책상이 비어 있음'), findsOneWidget);
    expect(find.text('—'), findsNWidgets(2)); // 머리 거리 · 꾸벅임
  });

  testWidgets('엎드리면 근거까지 같이 보여준다', (tester) async {
    await show(
        tester,
        _FakeSource(state(
            label: PostureLabel.slump,
            headDeltaMm: -104.2,
            reasons: const ['posture_only', 'head_dropped_and_held'])));

    expect(find.text('엎드림'), findsOneWidget);
    expect(find.text('−104mm'), findsOneWidget);
    expect(find.text('머리가 내려간 채 유지'), findsOneWidget);
  });

  testWidgets('기준을 잡는 중이면 판정을 믿지 말라고 먼저 말한다', (tester) async {
    await show(
        tester,
        _FakeSource(state(
            label: PostureLabel.baseline,
            valid: false,
            scenario: '2/2 바른 자세 기준을 재는 중입니다')));

    expect(find.byKey(const ValueKey('note')), findsOneWidget);
    expect(find.text('2/2 바른 자세 기준을 재는 중입니다'), findsOneWidget);
    expect(find.text('기준 측정 중'), findsOneWidget);
  });

  testWidgets('기준 다시 잡기는 확인을 받고 나서 보낸다', (tester) async {
    final source = _FakeSource(state());
    await show(tester, source);

    await tester.tap(find.byKey(const ValueKey('calibrate')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(source.calibrateCalls, 0, reason: '확인 전에는 보내지 않는다');

    await tester.tap(find.byKey(const ValueKey('calibrate-confirm')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(source.calibrateCalls, 1);
  });

  testWidgets('데모 빌드에는 기준 다시 잡기 버튼이 없다', (tester) async {
    await show(tester, _FakeSource(state(), canCalibrate: false));

    expect(find.byKey(const ValueKey('calibrate')), findsNothing);
    expect(find.text('DEMO · 실데이터 아님'), findsOneWidget);
  });

  testWidgets('노드에 못 붙으면 그렇게 말한다', (tester) async {
    await show(tester, _FailingSource());

    expect(find.byKey(const ValueKey('note')), findsOneWidget);
    expect(find.textContaining('연결하지 못했습니다'), findsOneWidget);
  });

  testWidgets('센서 링크가 끊기면 판정이 멀쩡해 보여도 경고한다', (tester) async {
    // 마지막 프레임이 계속 재발행되므로 /api/state 만 보면 정상으로 보인다.
    await show(
        tester,
        _FakeSource(state(),
            link: const LinkHealth(
                stage: 'live', stale: true, fps: 0, frames: 812)));

    expect(find.textContaining('센서 프레임이 멈췄습니다'), findsOneWidget);
  });

  test('모르는 근거 코드는 버리지 않고 그대로 보여준다', () {
    expect(reasonLabel('posture_only'), '자세 신호만 사용');
    expect(reasonLabel('nods_per_min_4.5'), '꾸벅임 4.5회/분');
    expect(reasonLabel('posture 24/60'), '자세 기준 24/60');
    expect(reasonLabel('무슨 새 코드'), '무슨 새 코드');
  });
}

class _FakeSource implements PostureSource {
  _FakeSource(this.value, {this.canCalibrate = true, this.link});

  final PostureState value;
  final LinkHealth? link;
  int calibrateCalls = 0;

  @override
  final bool canCalibrate;

  @override
  String get label => 'fake';

  @override
  Future<PostureState> fetch() async => value;

  @override
  Future<LinkHealth?> health() async => link;

  @override
  Future<void> calibrate() async => calibrateCalls++;

  @override
  void close() {}
}

class _FailingSource implements PostureSource {
  @override
  bool get canCalibrate => true;

  @override
  String get label => '10.0.0.9:8765';

  @override
  Future<PostureState> fetch() async =>
      throw SocketException('연결이 거부되었습니다');

  @override
  Future<LinkHealth?> health() async => null;

  @override
  Future<void> calibrate() async {}

  @override
  void close() {}
}
