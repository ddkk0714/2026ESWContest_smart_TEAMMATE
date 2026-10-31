// 제어 진행 탭(V30): hub :8765/api/control-log, 없으면 MQTT 추정.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/developer/control_progress.dart';
import 'package:deskmate_ui_demo/developer/control_progress_page.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';

/// 가짜 :8765 — 레코드 배열(오래된 것 먼저)이나 오류를 돌려준다.
class FakeFetcher implements ControlLogFetcher {
  String host = '';
  List<Object?> records = const [];
  ControlLogUnavailable? error;
  int calls = 0;
  int? lastLimit;

  @override
  Future<List<Object?>> fetch(int limit) async {
    calls++;
    lastLimit = limit;
    final e = error;
    if (e != null) throw e;
    return records;
  }

  @override
  void close() {}
}

Map<String, Object?> hubRecord(String id, double created,
        {String status = 'succeeded', Map<String, Object?> extra = const {}}) =>
    {
      'v': 1,
      'command_id': id,
      'source': 'dispatcher',
      'status': status,
      'state': 'ACTION_POSTURE',
      'cause': 'posture',
      'gate': 'auto',
      'arm': 'wake_light',
      'target_id': 'desk_lamp',
      'operation': 'adjust_brightness',
      'value': {'percent': 60, 'min_raw': 153},
      'created_ts': created,
      'latency_ms': 412,
      ...extra,
    };

Map<String, dynamic> env(Map<String, Object?> data) =>
    {'schema_version': '1.0', 'ts': 1.0, 'node': 'hub', 'data': data};

/// start() 가 바로 한 번 묻는다 — 그 응답이 반영될 때까지.
Future<void> settle() => Future<void>.delayed(Duration.zero);

void main() {
  late FakeFetcher fetcher;
  late DateTime now;
  late ControlProgress progress;

  setUp(() {
    now = DateTime(2026, 11, 6, 14);
    fetcher = FakeFetcher();
    progress = ControlProgress(
        fetcherFactory: (host) => fetcher..host = host,
        interval: const Duration(hours: 1),
        clock: () => now);
  });
  tearDown(() => progress.dispose());

  test('hub 기록: limit 50 으로 묻고 최신이 위, 필드로 한 줄', () async {
    fetcher.records = [
      hubRecord('a1', 100),
      hubRecord('b2', 200,
          status: 'failed', extra: {'error_code': 'lamp_not_configured'}),
      hubRecord('c3', 300,
          status: 'skipped', extra: {'reason': 'cooldown 40s/300s'}),
      {'no_id': true},
    ];
    progress.start('172.16.34.199');
    await settle();
    expect(fetcher.host, '172.16.34.199');
    expect(fetcher.lastLimit, 50);
    expect(progress.source, ControlProgressSource.hub);
    expect(progress.hubProblem, isNull);
    final rows = progress.records;
    expect(rows.map((r) => r.commandId), ['c3', 'b2', 'a1']);
    expect(rows[2].why, 'ACTION_POSTURE · posture · auto · wake_light');
    expect(rows[2].what,
        'desk_lamp adjust_brightness {"percent":60,"min_raw":153}');
    expect(rows[2].statusKo, '성공');
    expect(rows[2].latencyMs, 412);
    expect(rows[1].statusKo, '실패 · lamp_not_configured');
    expect(rows[0].statusKo, '생략 · cooldown 40s/300s');
  });

  test('404(옛 hub) → MQTT 추정으로, 상단에 IPK 갱신 필요', () async {
    fetcher.error = const ControlLogUnavailable('404', missing: true);
    progress.start('172.16.34.199');
    await settle();
    expect(progress.source, ControlProgressSource.mqtt);
    expect(progress.hubProblem, hubLogMissing);

    progress.addMessage(
        LiveLink.stateTopic,
        env({
          'sensor_summary': {
            'personalize': {'arm': 'wake_light', 'source': 'bandit'}
          }
        }));
    progress.addMessage(
        LiveLink.commandTopic,
        env({
          'command_id': 'm1',
          'target_id': 'desk_lamp',
          'operation': 'adjust_brightness',
          'value': {'percent': 60},
          'origin_state': 'ACTION_POSTURE',
          'cause': 'posture',
          'gate': 'auto',
        }));
    expect(progress.records.single.statusKo, '진행 중');
    expect(progress.records.single.why,
        'ACTION_POSTURE · posture · auto · wake_light');
    now = now.add(const Duration(milliseconds: 850));
    progress.addMessage(
        LiveLink.resultTopic,
        env({
          'command_id': 'm1',
          'status': 'succeeded',
          'previous': 96,
          'actual': 176
        }));
    final r = progress.records.single;
    expect(r.statusKo, '성공');
    expect(r.latencyMs, 850);
    expect(r.fields['actual'], 176);

    // 같은 기기 되돌리기(60 s 안) → 서로 묶는다
    now = now.add(const Duration(seconds: 20));
    progress.addMessage(
        LiveLink.commandTopic,
        env({
          'command_id': 'u1',
          'target_id': 'desk_lamp',
          'operation': 'set_brightness',
          'value': 96,
          'origin_state': 'ACTION_POSTURE',
          'cause': 'posture',
          'gate': 'undo',
        }));
    final rows = progress.records;
    expect(rows.first.commandId, 'u1');
    expect(rows.first.undoKo, '↩ 되돌리기 (m1)');
    expect(rows.first.fields.containsKey('arm'), isFalse);
    expect(rows.last.undoKo, '↩ 되돌려짐 (u1)');
  });

  test('연결 실패도 MQTT 추정, 결과가 15 s 없으면 시간 초과', () async {
    fetcher.error = const ControlLogUnavailable('Connection refused');
    progress.start('10.0.0.9');
    await settle();
    expect(progress.hubProblem, '$hubLogMissing · Connection refused');
    progress.addMessage(
        LiveLink.commandTopic,
        env({
          'command_id': 'f1',
          'target_id': 'vent_fan',
          'operation': 'set_level',
          'value': 3,
          'origin_state': 'ACTION_ENV',
          'cause': 'environment',
          'gate': 'suggest',
        }));
    // QoS 1 중복은 한 줄
    progress.addMessage(LiveLink.commandTopic,
        env({'command_id': 'f1', 'target_id': 'vent_fan', 'gate': 'suggest'}));
    expect(progress.records, hasLength(1));
    expect(progress.records.single.what, 'vent_fan set_level 3');
    now = now.add(const Duration(seconds: 16));
    expect(progress.records.single.statusKo, '시간 초과');
  });

  test('hub 가 다시 응답하면 hub 기록으로 돌아간다', () async {
    fetcher.error = const ControlLogUnavailable('404', missing: true);
    progress.start('172.16.34.199');
    await settle();
    expect(progress.source, ControlProgressSource.mqtt);
    fetcher
      ..error = null
      ..records = [hubRecord('h1', 1)];
    await progress.pollOnce();
    expect(progress.source, ControlProgressSource.hub);
    expect(progress.records.single.commandId, 'h1');
  });

  test('주소가 없으면 묻지 않고 MQTT 추정', () {
    progress.start(null);
    expect(progress.source, ControlProgressSource.mqtt);
    expect(progress.hubProblem, '$hubLogMissing · 책상 주소 미설정');
  });

  testWidgets('화면: 출처 배지·한 줄·누르면 전체 필드', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    fetcher.records = [
      hubRecord('a1', 1769000000, extra: {'undone_by': 'u9'})
    ];
    final p = ControlProgress(
        fetcherFactory: (host) => fetcher..host = host,
        interval: const Duration(hours: 1));
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ControlProgressPage(progress: p, host: '172.16.34.199'))));
    await tester.pump();
    await tester.pump();
    expect(find.text('hub 기록'), findsOneWidget);
    expect(find.text('desk_lamp adjust_brightness {"percent":60,"min_raw":153}'),
        findsOneWidget);
    expect(find.text('↩ 되돌려짐 (u9)'), findsOneWidget);
    expect(find.byKey(const ValueKey('progress-detail-a1')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('progress-row-a1')));
    await tester.pump();
    expect(find.byKey(const ValueKey('progress-detail-a1')), findsOneWidget);
    fetcher.error = const ControlLogUnavailable('404', missing: true);
    await p.pollOnce();
    await tester.pump();
    expect(find.text('MQTT 추정'), findsOneWidget);
    expect(find.text(hubLogMissing), findsOneWidget);
    expect(find.byKey(const ValueKey('progress-empty')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox()); // 탭이 닫히면 폴링 멈춤
    p.dispose();
  });
}
