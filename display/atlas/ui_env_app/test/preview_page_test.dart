import 'dart:async';

import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/preview_page.dart';
import 'package:deskmate_display/tof_heatmap.dart';
import 'package:deskmate_display/tof_raw_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeTofSource implements PreviewTofSource {
  FakeTofSource(this.host);

  @override
  final String host;
  int rawCalls = 0;
  int postureCalls = 0;
  bool closed = false;
  Object? failWith;

  @override
  Future<TofRawSnapshot> fetchRaw() async {
    rawCalls++;
    if (failWith != null) throw failWith!;
    final dist = List<int>.generate(64, (i) => 300 + i * 10);
    dist[7] = -1;
    return TofRawSnapshot(frames: 42, sensors: [
      TofRawFrame(
          id: 0,
          have: true,
          distMm: dist,
          status: List<int>.filled(64, 5),
          ageMs: 100),
      TofRawFrame(
          id: 1,
          have: true,
          distMm: List<int>.filled(64, 900),
          status: List<int>.filled(64, 9),
          ageMs: 110),
    ]);
  }

  @override
  Future<TofPostureLabel> fetchPosture() async {
    postureCalls++;
    if (failWith != null) throw failWith!;
    return const TofPostureLabel(
      valid: true,
      posture: 'lean_forward',
      postureKo: '상체 앞으로',
      conf: 0.81,
      present: true,
      misplaced: false,
      onlineSensors: 2,
    );
  }

  @override
  void close() => closed = true;
}

DisplayState _state({String fsm = 'FOCUS_PC', String? explanation}) =>
    DisplayState(
      fsmState: fsm,
      phase: 'focus',
      context: 'pc',
      focus: 0.21,
      fatigue: 0.34,
      confidence: 0.5,
      gate: 'none',
      cause: null,
      reasons: const ['typing_steady'],
      sequence: 9,
      timestamp: DateTime(2026, 10, 21, 14),
      present: true,
      co2Ppm: 812,
      temperatureC: 24.6,
      humidityPct: 41,
      lux: 380,
      mmwaveMotionState: 'still',
      mmwaveMotionLevel: 12,
      mmwaveDistanceCm: 58,
      mmwaveRespBpm: 15,
      mmwaveDrowsyState: 'AWAKE',
      interventionExplanation: explanation,
      keystroke: KeystrokeMetrics(
        eventCount: 184,
        flightMeanMs: 148.2,
        flightStdMs: 63.5,
        correctionRate: 0.07,
        typingActive: true,
        valid: true,
        timestamp: DateTime(2026, 10, 21, 14),
      ),
    );

Future<FakeTofSource?> _pump(
  WidgetTester tester, {
  DisplayState? state,
  String? host = '172.20.10.3',
  List<PreviewFeedback>? sent,
  List<String>? hosts,
  bool feedbackEnabled = true,
  Completer<void>? sendGate,
}) async {
  tester.view.physicalSize = const Size(1024, 600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  FakeTofSource? source;
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: PreviewPage(
        state: state ?? _state(),
        host: host,
        onHostChanged: (h) => hosts?.add(h),
        onFeedback: (f) async {
          sent?.add(f);
          if (sendGate != null) await sendGate.future;
        },
        feedbackEnabled: feedbackEnabled,
        sourceFactory: (h) => source = FakeTofSource(h),
        pollInterval: const Duration(milliseconds: 300),
      ),
    ),
  ));
  await tester.pump();
  return source;
}

/// 긴 페이지라 아래쪽 버튼은 스크롤해서 보이게 한 뒤 누른다.
Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pump();
  await tester.tap(finder);
  await tester.pump();
}

void main() {
  testWidgets('shows the preview badge, privacy note and every sensor value',
      (tester) async {
    await _pump(tester, state: _state(explanation: 'CO₂ 높음 · 환기 제안'));
    expect(find.byKey(const ValueKey('preview-badge')), findsOneWidget);
    expect(find.text('프리뷰 · 정식 기능 아님'), findsOneWidget);
    expect(find.text('얼굴·영상 없음 · 64칸 깊이값만'), findsOneWidget);
    // mmWave · 환경 · 키스트로크 · FSM
    expect(find.text('감지'), findsOneWidget);
    expect(find.text('정지 · 12/100'), findsOneWidget);
    expect(find.text('깨어 있음'), findsOneWidget);
    expect(find.text('15 bpm'), findsOneWidget);
    expect(find.text('812 ppm'), findsOneWidget);
    expect(find.text('24.6 °C'), findsOneWidget);
    expect(find.text('380 lx'), findsOneWidget);
    expect(find.text('타이핑 중'), findsOneWidget);
    expect(find.text('184 회'), findsOneWidget);
    expect(find.text('0.34'), findsOneWidget); // C_fatigue
    expect(find.text('0.21'), findsOneWidget); // C_focus
    expect(find.text('FOCUS_PC'), findsWidgets);
    expect(find.text('CO₂ 높음 · 환기 제안'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('missing values are shown as — and no explanation is —',
      (tester) async {
    final empty = DisplayState(
      fsmState: 'IDLE',
      phase: 'idle',
      context: 'pc',
      focus: 0,
      fatigue: 0,
      confidence: 0,
      gate: 'none',
      reasons: const [],
      sequence: 1,
      timestamp: DateTime(2026, 10, 21),
    );
    await _pump(tester, state: empty, host: null);
    expect(
        tester
            .widget<Text>(find.byKey(const ValueKey('preview-explanation')))
            .data,
        '—');
    // 재실·CO₂·호흡·졸음 … 전부 —
    expect(find.text('—'), findsAtLeastNWidgets(10));
  });

  testWidgets('polls raw + posture from the host and paints both heatmaps',
      (tester) async {
    final source = await _pump(tester);
    expect(source, isNotNull);
    expect(source!.host, '172.20.10.3');
    await tester.pump();
    expect(source.rawCalls, 1);
    expect(source.postureCalls, 1);
    // 자세 라벨
    expect(find.text('상체 앞으로'), findsOneWidget);
    expect(find.textContaining('확신 81 %'), findsOneWidget);
    // 센서 A: 첫 칸 300 mm, 7번은 -1 → 회색. 센서 B: 전부 900 mm
    Color colorOf(String key) =>
        tester.widget<ColoredBox>(find.byKey(ValueKey(key))).color;
    expect(colorOf('tof-a-zone-0'), tofZoneColor(300, 5));
    expect(colorOf('tof-a-zone-7'), kTofInvalidColor);
    expect(colorOf('tof-b-zone-0'), tofZoneColor(900, 9));
    expect(colorOf('tof-b-zone-63'), tofZoneColor(900, 9));
    expect(find.textContaining('유효 63/64'), findsOneWidget);
    expect(find.textContaining('frames 42'), findsOneWidget);

    // 주기마다 다시 읽는다
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    expect(source.rawCalls, 2);

    // 연결 실패는 상태 줄에 보이고 화면은 안 죽는다
    source.failWith = Exception('boom');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump();
    expect(find.textContaining('raw 연결 실패'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('without a host it shows an input and normalizes what is typed',
      (tester) async {
    final hosts = <String>[];
    final source = await _pump(tester, host: null, hosts: hosts);
    expect(source, isNull); // 주소 없으면 폴링하지 않는다
    expect(find.byKey(const ValueKey('preview-host-input')), findsOneWidget);
    expect(find.text('주소 없음'), findsOneWidget);

    await tester.enterText(find.byKey(const ValueKey('preview-host-input')),
        'http://172.20.10.3:8771/');
    await tester.tap(find.byKey(const ValueKey('preview-host-apply')));
    await tester.pump();
    expect(hosts, ['172.20.10.3']);

    // 모양이 아니면 안 보내고 안내만
    await tester.enterText(
        find.byKey(const ValueKey('preview-host-input')), 'not a host');
    await tester.tap(find.byKey(const ValueKey('preview-host-apply')));
    await tester.pump();
    expect(hosts, ['172.20.10.3']);
    expect(find.textContaining('주소 모양이 아니에요'), findsOneWidget);
  });

  testWidgets('feedback buttons build the right payload', (tester) async {
    final sent = <PreviewFeedback>[];
    await _pump(tester, sent: sent);
    final send = find.byKey(const ValueKey('preview-send'));

    // 고르기 전엔 못 보낸다
    expect(tester.widget<FilledButton>(send).onPressed, isNull);

    // 맞아요 + 메모
    await _tap(tester, find.byKey(const ValueKey('preview-verdict-accept')));
    await tester.ensureVisible(find.byKey(const ValueKey('preview-note')));
    await tester.enterText(
        find.byKey(const ValueKey('preview-note')), '  전화 받는 중  ');
    await _tap(tester, send);
    expect(sent.length, 1);
    expect(sent[0].verdict, 'accept');
    expect(sent[0].correctedState, isNull);
    expect(sent[0].note, '전화 받는 중');
    // 보낸 뒤 선택·메모가 비워진다
    expect(tester.widget<FilledButton>(send).onPressed, isNull);
    expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('preview-note')))
            .controller!
            .text,
        isEmpty);

    // 아니에요, 메모 없음 → note 는 null
    await _tap(tester, find.byKey(const ValueKey('preview-verdict-reject')));
    await _tap(tester, send);
    expect(sent.length, 2);
    expect(sent[1].verdict, 'reject');
    expect(sent[1].note, isNull);

    // 정정은 상태를 골라야 보낼 수 있다
    await _tap(tester, find.byKey(const ValueKey('preview-verdict-correct')));
    expect(tester.widget<FilledButton>(send).onPressed, isNull);
    for (final s in ['FOCUS_PC', 'FATIGUE', 'REST', 'IDLE']) {
      expect(find.byKey(ValueKey('preview-correct-$s')), findsOneWidget);
    }
    await _tap(tester, find.byKey(const ValueKey('preview-correct-REST')));
    await _tap(tester, send);
    expect(sent.length, 3);
    expect(sent[2].verdict, 'correct');
    expect(sent[2].correctedState, 'REST');
    expect(sent[2].note, isNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('feedback is disabled while a test state is pinned',
      (tester) async {
    final sent = <PreviewFeedback>[];
    await _pump(tester, sent: sent, feedbackEnabled: false);
    final send = find.byKey(const ValueKey('preview-send'));
    expect(find.byKey(const ValueKey('preview-feedback-disabled')),
        findsOneWidget);
    await _tap(tester, find.byKey(const ValueKey('preview-verdict-accept')));
    expect(tester.widget<FilledButton>(send).onPressed, isNull);
    expect(sent, isEmpty);
  });

  testWidgets('closes the source on dispose', (tester) async {
    final source = await _pump(tester);
    await tester.pumpWidget(const SizedBox());
    expect(source!.closed, isTrue);
  });
}
