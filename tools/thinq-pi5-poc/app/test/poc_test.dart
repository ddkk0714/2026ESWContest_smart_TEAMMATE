import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:thinq_poc/main.dart';
import 'package:thinq_poc/client.dart';

class Fake implements PocClient {
  final calls = <Map<String, dynamic>>[];
  Map<String, dynamic>? readResult;
  @override
  Future<Map<String, dynamic>> authStatus() async =>
      {'status': 'platform_binding_unverified', 'post_enabled': false};
  @override
  Future<void> close() async {}
  @override
  Future<Map<String, dynamic>> exchange(Map<String, dynamic> r) async {
    calls.add(r);
    if (r['backend'] == 'live') {
      return {
        'status': 'blocked',
        'error': 'live_credentials_and_policy_unverified',
        'post_enabled': false
      };
    }
    if (r['op'] == 'enable_fixture_post') {
      return {'status': 'fixture_post_enabled', 'post_enabled': true};
    }
    if (r['op'] == 'prepare') {
      return {
        'status': 'approval_required',
        'nonce': 'fixture-nonce',
        'value': r['value'],
        'post_enabled': true
      };
    }
    if (r['op'] == 'execute') {
      return {
        'status': 'state_match',
        'api_accepted': true,
        'state_match': true,
        'value': 'LOW',
        'post_enabled': true
      };
    }
    return readResult ??
        {
          'status': 'read_ok',
          'post_enabled': false,
          'fields': {
            'pm1': {'value': 15, 'valid': true, 'status': 'ok'}
          }
        };
  }
}

void main() {
  testWidgets(
      'default OFF, reads fixture without POST and labels unverified data',
      (tester) async {
    final c = Fake();
    await tester.pumpWidget(PocApp(client: c));
    expect(
        tester
            .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'LOW 승인 시험'))
            .onPressed,
        isNull);
    await tester.tap(find.text('Fixture 목록·Profile·State GET'));
    await tester.pumpAndSettle();
    expect(find.text('pm1: 15 · ok'), findsOneWidget);
    expect(c.calls.single['op'], 'read');
    expect(find.textContaining('실제 물리 동작 확인: 미검증'), findsOneWidget);
  });
  testWidgets(
      'cancel does not execute; checkbox and explicit approval required',
      (tester) async {
    final c = Fake();
    await tester.pumpWidget(PocApp(client: c));
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('LOW 승인 시험'));
    await tester.tap(find.text('LOW 승인 시험'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '승인'))
            .onPressed,
        isNull);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(c.calls.where((x) => x['op'] == 'execute'), isEmpty);
    await tester.tap(find.text('LOW 승인 시험'));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    await tester.tap(find.text('승인'));
    await tester.pumpAndSettle();
    expect(c.calls.where((x) => x['op'] == 'execute').length, 1);
    expect(c.calls.last['nonce'], 'fixture-nonce');
  });
  testWidgets('Live is explicitly blocked and never enables controls',
      (tester) async {
    final c = Fake();
    await tester.pumpWidget(PocApp(client: c));
    await tester.tap(find.text('Live 차단 사유 확인'));
    await tester.pumpAndSettle();
    expect(find.textContaining('live_credentials_and_policy_unverified'),
        findsOneWidget);
    expect(tester.widget<Switch>(find.byType(Switch)).value, isFalse);
  });
  testWidgets('renders actual Python fixture parser output contract',
      (tester) async {
    final c = Fake()
      ..readResult = Map<String, dynamic>.from(jsonDecode(
          File('test/fixtures/service_read.json').readAsStringSync()));
    await tester.pumpWidget(PocApp(client: c));
    await tester.tap(find.text('Fixture 목록·Profile·State GET'));
    await tester.pumpAndSettle();
    expect(find.text('pm1: 8 · ok'), findsOneWidget);
    expect(find.text('monitoring_mode: ON_WORKING · ok'), findsOneWidget);
    expect(find.textContaining('조회한 풍량: AUTO'), findsOneWidget);
  });
  testWidgets('Live read-only without lease never sends GET or POST',
      (tester) async {
    final c = Fake();
    await tester.pumpWidget(PocApp(client: c));
    await tester.tap(find.byType(DropdownButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('live_read_only').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Live 목록·Profile·State GET'));
    await tester.pumpAndSettle();
    expect(c.calls, isEmpty);
    expect(find.textContaining('credential_session_or_mode_unavailable'),
        findsOneWidget);
    expect(tester.widget<Switch>(find.byType(Switch)).onChanged, isNull);
  });
  testWidgets('Live manual selection alone cannot arm control', (tester) async {
    final c = Fake();
    await tester.pumpWidget(PocApp(client: c));
    await tester.tap(find.byType(DropdownButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('live_manual').last);
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'LOW 승인 시험'))
            .onPressed,
        isNull);
    expect(c.calls, isEmpty);
  });
}
