import 'dart:async';
import 'package:deskmate_display/privacy_page.dart';
import 'package:deskmate_display/privacy_state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class FakePrivacySource implements PrivacySource {
  @override
  bool isConnected = true;
  @override
  PrivacyState? privacyState = const PrivacyState(
      available: true,
      policyReady: true,
      policyVersion: 'test-v1',
      consented: false,
      hubBootId: 'hub-test');
  final actions = <String>[];
  Completer<PrivacyState>? completion;
  @override
  Future<PrivacyState> changePrivacy(String action) {
    actions.add(action);
    completion = Completer<PrivacyState>();
    return completion!.future;
  }
}

Future<void> mount(WidgetTester tester, FakePrivacySource? source) async {
  await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: PrivacyPage(source: source))));
}

void main() {
  testWidgets('pending policy blocks grant but permits configured deletion',
      (tester) async {
    final source = FakePrivacySource();
    source.privacyState = const PrivacyState(
        available: true,
        policyReady: false,
        policyVersion: 'pending',
        consented: false);
    await mount(tester, source);
    expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('privacy-grant')))
            .onPressed,
        isNull);
    expect(
        tester
            .widget<OutlinedButton>(
                find.byKey(const ValueKey('privacy-delete')))
            .onPressed,
        isNotNull);
  });

  testWidgets('cancelled confirmation never sends a grant', (tester) async {
    final source = FakePrivacySource();
    await mount(tester, source);
    await tester.ensureVisible(find.byKey(const ValueKey('privacy-grant')));
    await tester.tap(find.byKey(const ValueKey('privacy-grant')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('\uCDE8\uC18C'));
    await tester.pumpAndSettle();
    expect(source.actions, isEmpty);
  });

  testWidgets('confirmation waits for hub acknowledgement', (tester) async {
    final source = FakePrivacySource();
    await mount(tester, source);
    await tester.ensureVisible(find.byKey(const ValueKey('privacy-grant')));
    await tester.tap(find.byKey(const ValueKey('privacy-grant')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('\uB3D9\uC758'));
    await tester.pump();
    expect(source.actions, ['grant']);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    source.privacyState = const PrivacyState(
        available: true,
        policyReady: true,
        policyVersion: 'test-v1',
        consented: true,
        status: 'succeeded');
    source.completion!.complete(source.privacyState!);
    await tester.pumpAndSettle();
    expect(find.byType(LinearProgressIndicator), findsNothing);
    expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('privacy-grant')))
            .onPressed,
        isNull);
  });

  testWidgets('offline or unsupported source cannot change privacy',
      (tester) async {
    final source = FakePrivacySource()..isConnected = false;
    await mount(tester, source);
    expect(
        tester
            .widget<OutlinedButton>(
                find.byKey(const ValueKey('privacy-delete')))
            .onPressed,
        isNull);
    await mount(tester, null);
    expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('privacy-grant')))
            .onPressed,
        isNull);
  });

  final learningTitles = {
    'collecting': '정정 기록 수집 중',
    'training': '기기에서 개인 모델 학습 중',
    'paused': '개인 학습 잠시 멈춤',
    'accepted': '개인 모델 준비 완료',
    'rejected': '기존 모델 유지',
    'storage_error': '개인 학습 처리 실패',
    'head_load_failed': '개인 모델 복원 실패',
    'unavailable': '개인 학습 준비 필요',
    'future-status': '학습 상태 확인 중',
  };
  for (final entry in learningTitles.entries) {
    testWidgets('learning status ${entry.key} has accurate explanation',
        (tester) async {
      final source = FakePrivacySource();
      source.privacyState = PrivacyState(
          available: true,
          policyReady: true,
          policyVersion: 'test-v1',
          consented: true,
          learning: LearningState(status: entry.key, samples: 12, sessions: 2));
      await mount(tester, source);
      expect(find.text(entry.value), findsOneWidget);
      expect(find.text('정정 기록 12개 · 세션 2개'), findsOneWidget);
      expect(find.text('현재 개인 모델은 예측 비교용이며, 실제 상태 판단과 제어는 기존 방식으로 유지합니다.'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('offline and revoked consent hide cached personal model',
      (tester) async {
    final source = FakePrivacySource();
    source.privacyState = const PrivacyState(
        available: true,
        policyReady: true,
        policyVersion: 'test-v1',
        consented: true,
        learning: LearningState(
            status: 'accepted',
            headSource: 'personal',
            samples: 32,
            sessions: 4));
    await mount(tester, source);
    expect(find.text('비교 중인 모델: 저장된 개인 모델'), findsOneWidget);
    source.isConnected = false;
    await mount(tester, source);
    expect(find.text('학습 상태 확인 대기'), findsOneWidget);
    expect(find.text('비교 중인 모델: 저장된 개인 모델'), findsNothing);
    expect(find.text('정정 기록 32개 · 세션 4개'), findsNothing);
    source.isConnected = true;
    source.privacyState = const PrivacyState(
        available: true,
        policyReady: true,
        policyVersion: 'test-v1',
        consented: false,
        learning: LearningState(status: 'accepted', headSource: 'personal'));
    await mount(tester, source);
    expect(find.text('개인 학습 사용 중지'), findsOneWidget);
    expect(find.text('비교 중인 모델: 저장된 개인 모델'), findsNothing);
  });

  testWidgets('learning card follows a new hub snapshot', (tester) async {
    final source = FakePrivacySource();
    source.privacyState = const PrivacyState(
        available: true,
        policyReady: true,
        policyVersion: 'test-v1',
        consented: true,
        learning: LearningState(status: 'training'));
    await mount(tester, source);
    expect(find.text('기기에서 개인 모델 학습 중'), findsOneWidget);
    source.privacyState = const PrivacyState(
        available: true,
        policyReady: true,
        policyVersion: 'test-v1',
        consented: true,
        learning: LearningState(status: 'accepted', headSource: 'personal'));
    await mount(tester, source);
    expect(find.text('개인 모델 준비 완료'), findsOneWidget);
    expect(find.text('기기에서 개인 모델 학습 중'), findsNothing);
  });

  test('MQTT summary maps learning and clears absent diagnostics', () {
    final privacy = {
      'available': true,
      'policy_ready': true,
      'policy_version': 'test-v1',
      'consented': true
    };
    final state = PrivacyState.fromSummary({
      'privacy': privacy,
      'ondevice_learning': {
        'status': 'paused',
        'head_source': 'personal',
        'samples': 32,
        'sessions': 4
      }
    })!;
    expect(state.learning!.status, 'paused');
    expect(state.learning!.headSource, 'personal');
    expect(state.learning!.samples, 32);
    expect(PrivacyState.fromSummary({'privacy': privacy})!.learning, isNull);
    expect(PrivacyState.fromSummary(null), isNull);
    expect(PrivacyState.fromSummary({}), isNull);
    final malformed = LearningState.fromMap({
      'status': 9,
      'head_source': 7,
      'samples': double.infinity,
      'sessions': -1
    });
    expect(malformed.status, 'unknown');
    expect(malformed.headSource, 'common');
    expect(malformed.samples, 0);
    expect(malformed.sessions, 0);
  });
}
