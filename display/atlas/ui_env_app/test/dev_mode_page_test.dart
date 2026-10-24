import 'dart:convert';
import 'dart:io';

import 'package:deskmate_display/command_log.dart';
import 'package:deskmate_display/dev_mode_page.dart';
import 'package:deskmate_display/device_state_board.dart';
import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/judgment_panel.dart';
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
    final board = DeviceStateBoard();
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
            board: board,
            deviceAvailable: const {'desk_lamp': false, 'vent_fan': false},
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
    // 기기 현재 상태 줄: 아직 명령 없음
    expect(
        tester
            .widget<Text>(find.byKey(const ValueKey('devmode-device-state')))
            .data,
        startsWith('현재: 선풍기 - (기기 없음 · 제어 못함) / 램프 - (기기 없음 · 제어 못함) / 공기청정기 -'));
    final fanCmd = {
      'schema_version': '1.0',
      'node': 'hub',
      'data': {
        'command_id': 'fan001',
        'target_id': 'vent_fan',
        'operation': 'set_power',
        'value': 'on',
        'gate': 'auto',
      }
    };
    log.addMessage(CommandLog.topicCmd, fanCmd);
    board.addMessage(CommandLog.topicCmd, fanCmd);
    final lampCmd = {
      'schema_version': '1.0',
      'node': 'hub',
      'data': {
        'command_id': 'ab12cd',
        'target_id': 'desk_lamp',
        'operation': 'set_brightness',
        'value': 230,
        'gate': 'auto',
      }
    };
    log.addMessage(CommandLog.topicCmd, lampCmd);
    board.addMessage(CommandLog.topicCmd, lampCmd);
    log.addMessage(CommandLog.topicResult, {
      'schema_version': '1.0',
      'node': 'display-control',
      'data': {'command_id': 'ab12cd', 'status': 'failed', 'error_code': 'lamp_not_configured'}
    });
    board.addMessage(CommandLog.topicResult, {
      'schema_version': '1.0',
      'node': 'display-control',
      'data': {'command_id': 'ab12cd', 'status': 'failed', 'error_code': 'lamp_not_configured'}
    });
    await tester.pump();
    await tester.pump();
    // 블루투스가 없어도 "현재: 선풍기 ON" 으로 표기되고, 제어 못함이 뒤에 붙는다.
    expect(
        tester
            .widget<Text>(find.byKey(const ValueKey('devmode-device-state')))
            .data,
        startsWith('현재: 선풍기 ON … 결과 없음 (기기 없음 · 제어 못함) / '
            '램프 밝기 230 ✗ 제어 못함(lamp_not_configured) (기기 없음 · 제어 못함) / 공기청정기 -'));
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

  testWidgets('info panel shows bluetooth, sensors, keystroke, personalization and placement',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final state = DisplayState.fromEnvelope({
      'schema_version': '1.0',
      'ts': 1769000002.0,
      'seq': 3,
      'data': {
        'fsm_state': 'FOCUS_PC',
        'phase': 'focus',
        'context': 'pc',
        'c_fatigue': 0.2,
        'c_focus': 0.1,
        'confidence': 0.5,
        'gate': 'none',
        'reasons': [],
        'sensor_summary': {
          'present': true,
          'co2_ppm': 812,
          'temp_c': 24.6,
          'humidity_pct': 41,
          'lux': 380,
          'env_flags': ['co2_rising'],
          'mmwave': {'motion_state': 'still', 'motion_level': 12, 'distance_cm': 64},
          'keystroke': {'typing_active': true, 'event_count': 214, 'flight_mean_ms': 131.4},
          'personalization': {
            'mode': 'observe',
            'status': 'predicted',
            'label': 'FATIGUE',
            'confidence': 0.71,
          },
          'personalize': {'explanation': '최근 수락률이 높은 자세 제안'},
        },
      },
    });
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Padding(
          padding: const EdgeInsets.fromLTRB(44, 24, 44, 28),
          child: DevModePage(
            state: state,
            host: null,
            onHostChanged: (_) {},
            log: CommandLog(),
            board: DeviceStateBoard(),
            defaultOnStart: false,
            onDefaultOnStartChanged: (_) {},
            bluetoothLine: '램프 정상 · 선풍기 미사용 · 스피커 미사용 · 자동피드백 ON',
            placement: 'left',
          ),
        ),
      ),
    ));
    await tester.pump();
    String text(String key) => tester.widget<Text>(find.byKey(ValueKey(key))).data!;
    expect(text('info-bt'), '램프 정상 · 선풍기 미사용 · 스피커 미사용 · 자동피드백 ON');
    expect(text('info-mmwave'), '재실 · still 12 · 64 cm');
    expect(text('info-env'), 'CO₂ 812 ppm · 24.6°C · 41% · 380 lx · co2_rising');
    expect(text('info-keys'), '타이핑 · 214회 · flight 131ms');
    expect(text('info-personalization'), 'predicted FATIGUE 0.71');
    expect(text('info-explanation'), '최근 수락률이 높은 자세 제안');
    // 위치: 서비스 응답이 없으니 설정값(left)을 세 칸으로 강조
    expect(text('judge-placement'), 'left ■□□');
    expect(JudgmentPanel.placementText('center'), 'center □■□');
    expect(JudgmentPanel.placementText('right'), 'right □□■');
    expect(JudgmentPanel.placementText('unknown'), 'unknown □□□');
    expect(JudgmentPanel.placementText(null), '-');
    expect(tester.takeException(), isNull);
  });

  testWidgets('info panel without state shows "-" everywhere', (tester) async {
    await tester.pumpWidget(const MaterialApp(
        home: Scaffold(body: SizedBox(width: 400, height: 200, child: DevInfoPanel(state: null)))));
    for (final key in ['info-bt', 'info-mmwave', 'info-env', 'info-keys', 'info-personalization', 'info-explanation']) {
      expect(tester.widget<Text>(find.byKey(ValueKey(key))).data, '-', reason: key);
    }
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
