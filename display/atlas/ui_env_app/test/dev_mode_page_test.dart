import 'dart:convert';
import 'dart:io';

import 'package:deskmate_display/command_log.dart';
import 'package:deskmate_display/dev_mode_page.dart';
import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:deskmate_display/tof_heatmap.dart';
import 'package:deskmate_display/tof_raw_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeTofSource implements PreviewTofSource {
  _FakeTofSource(this.host);

  @override
  final String host;
  bool closed = false;

  @override
  Future<TofRawSnapshot> fetchRaw() async => TofRawSnapshot(frames: 42, sensors: [
        TofRawFrame(
            id: 0,
            have: true,
            distMm: List<int>.generate(64, (i) => 300 + i * 10),
            status: List<int>.filled(64, 5),
            ageMs: 100),
        TofRawFrame(
            id: 1,
            have: true,
            distMm: List<int>.filled(64, 900),
            status: List<int>.filled(64, 9),
            ageMs: 110),
      ]);

  @override
  Future<TofPostureLabel> fetchPosture() async => const TofPostureLabel(
        valid: true,
        posture: 'head_down',
        postureKo: '고개 숙임',
        conf: 0.66,
        present: true,
        misplaced: false,
        onlineSensors: 2,
      );

  @override
  void close() => closed = true;
}

void main() {
  testWidgets(
      'dev mode page fits 1024x600: two heatmaps, judgment and the log in one screen',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final log = CommandLog();
    _FakeTofSource? source;
    bool? switched;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Padding(
          padding: const EdgeInsets.fromLTRB(44, 24, 44, 28),
          child: DevModePage(
            state: null,
            host: '172.20.10.3',
            onHostChanged: (_) {},
            log: log,
            defaultOnStart: false,
            onDefaultOnStartChanged: (v) => switched = v,
            connectionLabel: 'MQTT 연결됨',
            sourceFactory: (h) => source = _FakeTofSource(h),
            pollInterval: const Duration(seconds: 10),
          ),
        ),
      ),
    ));
    await tester.pump();
    expect(source?.host, '172.20.10.3');
    // 열지도 두 장이 칠해졌다(A 칸 0 = 300 mm 붉음, B = 900 mm).
    final a0 = tester.widget<ColoredBox>(find.byKey(const ValueKey('tof-a-zone-0')));
    expect(a0.color, tofZoneColor(300, 5));
    final b63 = tester.widget<ColoredBox>(find.byKey(const ValueKey('tof-b-zone-63')));
    expect(b63.color, tofZoneColor(900, 9));
    // 판정: 서비스 자세는 보이고 hub 상태는 아직 없어 "-".
    expect(tester.widget<Text>(find.byKey(const ValueKey('judge-posture-svc'))).data,
        'head_down 0.66 (센서 2)');
    expect(tester.widget<Text>(find.byKey(const ValueKey('judge-state'))).data, '-');
    // 로그 패널은 비어 있고, 줄이 오면 보인다 — 블루투스 연결과 무관.
    expect(find.byKey(const ValueKey('cmdlog-empty')), findsOneWidget);
    log.addMessage(CommandLog.topicCmd, {
      'schema_version': '1.0',
      'node': 'hub',
      'data': {
        'command_id': 'ab12cd',
        'target_id': 'desk_lamp',
        'operation': 'set_brightness',
        'value': 230,
        'gate': 'auto',
      }
    });
    log.addMessage(CommandLog.topicResult, {
      'schema_version': '1.0',
      'node': 'display-control',
      'data': {'command_id': 'ab12cd', 'status': 'failed', 'error_code': 'lamp_not_configured'}
    });
    await tester.pump();
    await tester.pump();
    expect(find.textContaining('CMD desk_lamp set_brightness 230 (auto, id ab12cd)'),
        findsOneWidget);
    expect(find.textContaining('RESULT ab12cd failed lamp_not_configured'),
        findsOneWidget);
    expect(find.textContaining('MQTT 연결됨'), findsOneWidget);
    // 시작 화면 스위치
    await tester.tap(find.byKey(const ValueKey('devmode-default-switch')));
    await tester.pump();
    expect(switched, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('without a host the page shows an address input and does not poll',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    String? applied;
    var polled = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: DevModePage(
          state: null,
          host: null,
          onHostChanged: (h) => applied = h,
          log: CommandLog(),
          defaultOnStart: false,
          onDefaultOnStartChanged: (_) {},
          sourceFactory: (h) {
            polled++;
            return _FakeTofSource(h);
          },
        ),
      ),
    ));
    await tester.pump();
    expect(polled, 0);
    expect(find.byKey(const ValueKey('devmode-host-input')), findsOneWidget);
    await tester.enterText(
        find.byKey(const ValueKey('devmode-host-input')), ' 172.20.10.3 ');
    await tester.tap(find.byKey(const ValueKey('devmode-host-apply')));
    await tester.pump();
    expect(applied, '172.20.10.3');
    expect(tester.takeException(), isNull);
  });

  testWidgets('settings dev_mode_default opens the app on the dev mode screen',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final temp = Directory.systemTemp.createTempSync('devmode-default-');
    addTearDown(() => temp.deleteSync(recursive: true));
    final path = '${temp.path}/settings.json';
    File(path).writeAsStringSync(
        jsonEncode({'schema_version': 1, 'dev_mode_default': true}));
    final store =
        SettingsStore(candidates: [path], legacyPostureCandidates: const []);
    expect(store.load().devModeDefault, isTrue);

    await tester.pumpWidget(DeskmateApp(settings: store));
    await tester.pump();
    expect(find.byKey(const ValueKey('devmode-page')), findsOneWidget);
    expect(find.byKey(const ValueKey('tof-a-zone-63')), findsOneWidget);
    expect(find.byKey(const ValueKey('tof-b-zone-63')), findsOneWidget);
    expect(find.byKey(const ValueKey('cmdlog-clear')), findsOneWidget);
    // 데모 소스의 첫 상태(START)가 판정에 보인다.
    expect(tester.widget<Text>(find.byKey(const ValueKey('judge-state'))).data,
        'START');
    final sw = tester.widget<Switch>(find.byKey(const ValueKey('devmode-default-switch')));
    expect(sw.value, isTrue);
    // 스위치를 끄면 설정 파일에서 사라진다.
    await tester.tap(find.byKey(const ValueKey('devmode-default-switch')));
    await tester.pump();
    expect(store.load().devModeDefault, isFalse);
    expect(tester.takeException(), isNull);
  });
}
