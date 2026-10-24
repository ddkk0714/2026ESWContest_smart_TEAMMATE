import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/main.dart';
import 'package:deskmate_ui_demo/live/device_board.dart';
import 'package:deskmate_ui_demo/live/home_items.dart';
import 'package:deskmate_ui_demo/live/link_settings.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/live/live_request.dart';
import 'package:deskmate_ui_demo/live/payload.dart';
import 'package:deskmate_ui_demo/live/placement_client.dart';
import 'package:deskmate_ui_demo/live/profile_payload.dart';
import 'package:deskmate_ui_demo/live/transport.dart';
import 'package:deskmate_ui_demo/pr56/co2_grade.dart';
import 'package:deskmate_ui_demo/pr56/display_state.dart';

DemoModel model() => DemoModel(DemoConfig(
    jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>));

/// MQTT 대신 쓰는 가짜 연결. 받은 메시지를 [deliver] 로 넣고, 보낸 것은 [published] 에 쌓인다.
class FakeTransport implements LiveTransport {
  FakeTransport({this.succeed = true});
  final bool succeed;
  final _messages = StreamController<LiveMessage>.broadcast(sync: true);
  final _connection = StreamController<bool>.broadcast(sync: true);
  bool connected = false;
  final subscribed = <String>[];
  final published = <({String topic, String text, bool retain})>[];

  @override
  Stream<LiveMessage> get messages => _messages.stream;
  @override
  Stream<bool> get connection => _connection.stream;
  @override
  bool get isConnected => connected;
  @override
  Future<bool> connect() async => connected = succeed;
  @override
  void subscribe(String topic) => subscribed.add(topic);
  @override
  void publish(String topic, String text, {bool retain = false}) =>
      published.add((topic: topic, text: text, retain: retain));
  @override
  void close() => connected = false;

  void deliver(String topic, Object? json, {bool retained = false}) =>
      _messages.add(LiveMessage(topic, json is String ? json : jsonEncode(json),
          retained: retained));

  void drop() => _connection.add(false);

  List<Map<String, dynamic>> sent(String topic) => [
        for (final p in published)
          if (p.topic == topic) jsonDecode(p.text) as Map<String, dynamic>
      ];
}

Map<String, Object?> phase(String fsm,
        {Map<String, Object?> summary = const {},
        double fatigue = .1,
        double focus = .1,
        String phaseName = 'focus',
        List<String> envFlags = const []}) =>
    {
      'schema_version': '1.0',
      'ts': 1769000002.0,
      'node': 'hub',
      'boot_id': 'a8021bf0',
      'seq': 1,
      'data': {
        'fsm_state': fsm,
        'phase': phaseName,
        'context': 'pc',
        'c_focus': focus,
        'c_fatigue': fatigue,
        'confidence': fatigue,
        'gate': 'none',
        'sensor_summary': {...summary, 'env_flags': envFlags},
      },
    };

Map<String, Object?> request(String kind, Map<String, Object?> extra) => {
      'schema_version': '1.0',
      'ts': 1769000003.0,
      'node': 'hub',
      'seq': 2,
      'data': {
        'request_id': 'req-$kind',
        'kind': kind,
        'options': ['accept', 'reject'],
        ...extra,
      },
    };

Future<(LiveLink, FakeTransport)> connected(
    {bool succeed = true,
    DateTime Function()? clock,
    PlacementClient Function(String host)? placement}) async {
  late FakeTransport transport;
  final link = LiveLink(
      transportFactory: (host, port) => transport = FakeTransport(succeed: succeed),
      clock: clock,
      placementFactory: placement,
      retryDelay: const Duration(hours: 1));
  link.configure('192.168.0.10', 1883);
  await Future<void>.delayed(Duration.zero);
  return (link, transport);
}

void main() {
  group('UTF-8', () {
    test('MQTT payload bytes decode as UTF-8, broken bytes do not throw', () {
      expect(mqttPayloadText(utf8.encode('{"text":"조명을 밝게"}')),
          '{"text":"조명을 밝게"}');
      // 옛 Latin-1 읽기라면 'ì¡°ëª…' 이 된다.
      expect(mqttPayloadText(utf8.encode('조명')), isNot(contains('ì')));
      expect(mqttPayloadText([0xEC, 0xA1, 0x41]), contains('A'));
    });

    test('Korean explanation survives the link into the card body', () async {
      final (link, transport) = await connected();
      transport.deliver(
          LiveLink.requestTopic,
          utf8.decode(utf8.encode(jsonEncode(request('vent_suggest', {
            'minutes': 15,
            'explanation': 'CO₂ 1180 ppm 이 5 분째 높아요 — 창문을 15 분만 열어 주세요 (가을)',
          })))));
      final card = link.request!.card(null);
      expect(card.title, '창문을 15분 열어 주세요');
      expect(card.body, startsWith('CO₂ 1180 ppm 이 5 분째 높아요'));
      link.dispose();
    });
  });

  group('state/phase → 홈 항목', () {
    late Co2Grades grades;
    setUp(() {
      grades = Co2Grades.fromJson(
          jsonDecode(File('assets/config/co2_bands.json').readAsStringSync())
              as Map);
    });

    test('full summary maps to calm Korean labels', () {
      final s = DisplayState.fromEnvelope(phase('FOCUS_PC',
          fatigue: .5,
          summary: {
            'co2_ppm': 1180,
            'temp_c': 24.36,
            'humidity_pct': 51.2,
            'lux': 320,
            'present': true,
            'tof': {'posture': 'lean_forward', 'posture_confidence': .8},
            'personalization': {
              'status': 'predicted',
              'label': 'fatigue',
              'confidence': .7
            },
          }));
      expect(airText(s, grades), '나쁨 · 1180 ppm');
      expect(roomText(s), '24.4° · 51% · 320 lx');
      expect(postureText(s), '앞으로 기울어짐');
      expect(conditionText(s), '조금 피곤해요 · 피로');
      expect(deskText(s).main, '24.4° · 51%');
      expect(deskText(s).sub, 'CO₂ 1180 ppm · 320 lx');
    });

    test('missing keys show 살펴보는 중 instead of invented numbers', () {
      final s = DisplayState.fromEnvelope(phase('IDLE', phaseName: 'idle'));
      expect(airText(s, grades), waitingText);
      expect(roomText(s), waitingText);
      expect(postureText(s), waitingText);
      expect(conditionText(s), '편안해요');
      expect(deskText(s).sub, '');
      final away = DisplayState.fromEnvelope(phase('FOCUS_PC', summary: {
        'present': false,
        'tof': {'posture': 'upright'}
      }));
      expect(postureText(away), '자리 비움');
      final tired = DisplayState.fromEnvelope(phase('FATIGUE', fatigue: .82));
      expect(conditionText(tired), '환기해요');
      final warming = DisplayState.fromEnvelope(phase('FOCUS_PC', summary: {
        'personalization': {'status': 'warming_up'}
      }));
      expect(conditionText(warming), '편안해요');
    });
  });

  group('제안 카드 → feedback/user', () {
    const kinds = {
      'posture_suggest': '자세를 가볍게 바로잡아 볼까요?',
      'env_suggest': '잠깐 환기해 볼까요?',
      'break_suggest': '잠깐 쉬어 가는 건 어떨까요?',
      'checkin_suggest': '지금 피곤하세요?',
      'vent_suggest': '창문을 15분 열어 주세요',
    };
    for (final entry in kinds.entries) {
      for (final verdict in ['accept', 'reject']) {
        test('${entry.key} $verdict', () async {
          final (link, transport) = await connected();
          transport.deliver(LiveLink.stateTopic,
              phase('ACTION_ENV', envFlags: ['co2_high']));
          transport.deliver(LiveLink.requestTopic,
              request(entry.key, {'expires_in_s': 180, 'minutes': 15}));
          expect(link.request!.card(link.state).title, entry.value);
          expect(await link.answer(verdict), isTrue);
          expect(link.request, isNull);
          final sent = transport.sent(LiveLink.feedbackTopic);
          expect(sent, hasLength(1));
          final envelope = sent.single;
          expect(envelope['schema_version'], '1.0');
          expect(envelope['node'], 'display');
          expect(envelope['seq'], 1);
          expect(envelope['ts'], isA<num>());
          expect(envelope['boot_id'], isA<String>());
          final data = envelope['data'] as Map<String, dynamic>;
          expect(data.keys.toSet(),
              {'request_id', 'verdict', 'response_ms', 'kind'});
          expect(data['request_id'], 'req-${entry.key}');
          expect(data['verdict'], verdict);
          expect(data['kind'], entry.key);
          expect(data['response_ms'], isA<int>());
          link.dispose();
        });
      }
    }

    test('checkin uses 예/아니오, vent keeps why reasons without codes', () {
      final now = DateTime(2026, 10, 25);
      final checkin = LiveRequest.fromData(
          request('checkin_suggest', {})['data'] as Map<String, dynamic>, now)!;
      expect(checkin.card(null).accept, '예');
      expect(checkin.card(null).reject, '아니오');
      final vent = LiveRequest.fromData(
          (request('vent_suggest', {
            'ventilate_minutes': 10,
            'why': ['co2_rule', 'co2_avg=1180'],
          })['data'] as Map<String, dynamic>),
          now)!;
      expect(vent.card(null).title, '창문을 10분 열어 주세요');
      expect(vent.card(null).body, '공기가 답답해졌어요');
    });

    test('state-independent cards survive FOCUS_*; posture card does not',
        () async {
      final (link, transport) = await connected();
      transport.deliver(LiveLink.requestTopic, request('vent_suggest', {}));
      transport.deliver(LiveLink.stateTopic, phase('FOCUS_PC'));
      expect(link.request?.kind, 'vent_suggest');
      transport.deliver(LiveLink.requestTopic, request('posture_suggest', {}));
      transport.deliver(LiveLink.stateTopic, phase('ACTION_POSTURE'));
      expect(link.request?.kind, 'posture_suggest');
      transport.deliver(LiveLink.stateTopic, phase('FOCUS_PC'));
      expect(link.request, isNull);
      // retain 으로 다시 온 지난 질문은 띄우지 않는다
      transport.deliver(LiveLink.requestTopic, request('break_suggest', {}),
          retained: true);
      expect(link.request, isNull);
      link.dispose();
    });

    test('expiry sends timeout with the same request_id', () async {
      final (link, transport) = await connected();
      transport.deliver(
          LiveLink.requestTopic, request('checkin_suggest', {'expires_in_s': 1}));
      await Future<void>.delayed(const Duration(milliseconds: 1100));
      expect(link.request, isNull);
      final data = transport.sent(LiveLink.feedbackTopic).single['data'];
      expect(data['verdict'], 'timeout');
      expect(data['request_id'], 'req-checkin_suggest');
      link.dispose();
    });
  });

  group('프로필 → deskmate/profile/user', () {
    test('retained envelope with the agreed schema and no name', () async {
      final persona = Persona(
          name: '꽃님',
          job: '수험생',
          rounds: 0,
          age: 27,
          sleep: 6,
          sound: '카페 음악',
          order: ['선풍기', '조명', '알림'],
          placement: 'left');
      persona.preferences
        ..brightness = .9
        ..fanEnabled = true
        ..fanSpeed = 2
        ..volume = .2;
      late FakeTransport transport;
      final linkWithTransport = LiveLink(
          transportFactory: (h, p) => transport = FakeTransport(),
          retryDelay: const Duration(hours: 1));
      linkWithTransport.publishProfile(persona); // 연결 전: 기억만
      linkWithTransport.configure('10.0.0.2', 1883);
      await Future<void>.delayed(Duration.zero);
      final sent = transport.published
          .where((p) => p.topic == profileTopic)
          .toList();
      expect(sent, hasLength(1));
      expect(sent.single.retain, isTrue);
      expect(sent.single.text, isNot(contains('꽃님')));
      expect(sent.single.text, isNot(contains('수험생')));
      final envelope = jsonDecode(sent.single.text) as Map<String, dynamic>;
      expect(envelope['node'], 'pi5-display');
      expect(envelope['ts'], isA<num>());
      expect(envelope['seq'], 1);
      final data = envelope['data'] as Map<String, dynamic>;
      expect(data, {
        'profile_id': persona.profileId,
        'age_band': '20s',
        'sleep_hours': 6,
        'bedtime': '23:30',
        'wake_time': '05:30',
        'light_pref': 'high',
        'sound_pref': 'low',
        'wind_pref': 'mid',
        'focus_help': ['fan', 'lamp', 'notify'],
        'placement': 'left',
      });
      expect(persona.profileId, matches(RegExp(r'^p[0-9a-f]{16}$')));
      expect(persona.copy().profileId, persona.profileId);
      expect(Persona(name: '꽃님', job: '수험생', rounds: 0).profileId,
          isNot(persona.profileId));
      linkWithTransport.dispose();
    });

    test('value bands and model hooks', () {
      expect([9, 19, 20, 35, 49, 50, 80].map(ageBand),
          ['10s', '10s', '20s', '30s', '40s', '50s+', '50s+']);
      final quiet = Persona(name: 'a', job: '수험생', rounds: 0, sound: '무음');
      quiet.preferences.lightEnabled = false;
      expect(soundPref(quiet), 'off');
      expect(lightPref(quiet.preferences), 'low');
      expect(windPref(quiet.preferences), 'off');
      final m = model();
      final seen = <String>[];
      m.onProfileChanged = (p) => seen.add(p.profileId);
      m.selectProfile(1);
      final draft = m.persona.copy()..placement = 'right';
      m.updateProfile(draft);
      m.saveProfile(Persona(name: 'b', job: '수험생', rounds: 0));
      expect(seen, hasLength(3));
      expect(seen[0], seen[1]);
      expect(m.persona.profileId, seen[2]);
    });
  });

  group('장치 위치 POST', () {
    late HttpServer server;
    late List<String> bodies;
    var status = 200;
    setUp(() async {
      // flutter_test 바인딩은 모든 HttpClient 를 400 을 주는 가짜로 바꾼다. 실제 소켓을 쓰게 되돌린다.
      HttpOverrides.global = null;
      bodies = [];
      status = 200;
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        bodies.add(await utf8.decodeStream(request));
        request.response.statusCode = status;
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode(status == 200
            ? {'placement': 'left', 'model': 'opt_l8_left.json'}
            : {'error': 'model'}));
        await request.response.close();
      });
    });
    tearDown(() => server.close(force: true));

    PlacementClient client(String host) =>
        HttpPlacementClient('127.0.0.1', port: server.port);

    test('success posts {"placement"} and returns a short note', () async {
      final (link, _) = await connected(placement: client);
      expect(await link.sendPlacement('left'), '장치 위치를 왼쪽으로 맞췄어요.');
      expect(jsonDecode(bodies.single), {'placement': 'left'});
      link.dispose();
    });

    test('server error and unreachable are brief notes, never throws',
        () async {
      status = 500;
      final (link, _) = await connected(placement: client);
      expect(await link.sendPlacement('right'), '위치를 보드에 맞추지 못해 이전 설정을 그대로 둘게요.');
      status = 404;
      expect(await link.sendPlacement('right'), '보드가 아직 위치 설정을 받지 않아요.');
      final closedPort = server.port;
      await server.close(force: true);
      final dead = LiveLink(
          transportFactory: (h, p) => FakeTransport(),
          placementFactory: (h) => HttpPlacementClient('127.0.0.1',
              port: closedPort, timeout: const Duration(milliseconds: 500)),
          retryDelay: const Duration(hours: 1))
        ..configure('127.0.0.1', 1883);
      expect(await dead.sendPlacement('center'),
          '보드에 닿지 않아 위치는 이 화면에만 기억해 둘게요.');
      final none = LiveLink(transportFactory: (h, p) => FakeTransport());
      expect(await none.sendPlacement('center'), isNull);
      link.dispose();
      dead.dispose();
      none.dispose();
    });
  });

  group('기기 상태(표시 전용)', () {
    test('cmd + succeeded result → 무드등 밝기 · 선풍기 단', () async {
      final (link, transport) = await connected();
      void cmd(String id, String target, String op, Object value) =>
          transport.deliver(LiveLink.commandTopic, {
            'schema_version': '1.0',
            'data': {
              'command_id': id,
              'target_id': target,
              'operation': op,
              'value': value
            }
          });
      void result(String id, String status, [Object? actual]) =>
          transport.deliver(LiveLink.resultTopic, {
            'schema_version': '1.0',
            'node': 'display-control',
            'data': {
              'command_id': id,
              'status': status,
              if (actual != null) 'actual_value': actual
            }
          });
      cmd('c1', 'desk_lamp', 'set_brightness', 200);
      result('c1', 'succeeded', 176);
      cmd('c2', 'vent_fan', 'set_level', 2);
      result('c2', 'succeeded');
      cmd('c3', 'vent_fan', 'set_power', 'off');
      result('c3', 'failed');
      expect(link.devices.text('desk_lamp'), '밝기 176');
      expect(link.devices.text('vent_fan'), '2단');
      expect(link.devices.summary, '무드등 밝기 176 · 선풍기 2단');
      // 재생된(retain) 명령은 무시하고, V18 은 control/cmd 를 보내지 않는다
      expect(transport.published.where((p) => p.topic.startsWith('deskmate/control')),
          isEmpty);
      expect(DeviceBoard.describe('vent_fan', 'set_mode', 'natural'), '자연풍');
      expect(DeviceBoard.describe('desk_lamp', 'set_power', 'on'), '켜짐');
      expect(DeviceBoard.describe('desk_lamp', 'blink', 1), isNull);
      link.dispose();
    });
  });

  group('연결 끊김 fallback', () {
    test('unreachable board keeps live false and retries', () async {
      final (link, transport) = await connected(succeed: false);
      expect(link.status, LinkStatus.connecting);
      expect(link.live, isFalse);
      expect(transport.connected, isFalse);
      link.dispose();
    });

    test('stale or dropped state hands the screen back to memory', () async {
      var now = DateTime(2026, 10, 25, 9);
      final (link, transport) = await connected(clock: () => now);
      final m = model();
      link.onState = (s) => m.followLive(s, postureLabel: postureText(s));
      transport.deliver(LiveLink.stateTopic, phase('IDLE', phaseName: 'idle'));
      expect(link.live, isTrue);
      expect(m.liveDriven, isTrue);
      now = now.add(const Duration(minutes: 1));
      expect(link.live, isFalse);
      m.leaveLive();
      expect(m.liveDriven, isFalse);
      now = now.add(const Duration(seconds: 1));
      transport.deliver(LiveLink.stateTopic, phase('IDLE', phaseName: 'idle'));
      expect(link.live, isTrue);
      transport.drop();
      expect(link.live, isFalse);
      link.dispose();
    });

    test('settings store round-trips only host and port', () {
      final dir = Directory.systemTemp.createTempSync('v18-link');
      addTearDown(() => dir.deleteSync(recursive: true));
      final store = LinkSettingsStore(candidates: ['${dir.path}/c.json']);
      expect(store.load().host, '');
      expect(store.save(LinkSettings(host: normalizeHost(' mqtt://10.0.0.5/ '), port: 1884)), isTrue);
      final loaded = store.load();
      expect(loaded.host, '10.0.0.5');
      expect(loaded.port, 1884);
      expect(File('${dir.path}/c.json').readAsStringSync(),
          '{"mqtt_host":"10.0.0.5","mqtt_port":1884}');
    });
  });

  group('FSM 흐름', () {
    test('focus starts, REST rests, absence pauses, END finishes', () {
      final m = model();
      DisplayState s(String fsm, {bool present = true}) =>
          DisplayState.fromEnvelope(phase(fsm, summary: {'present': present}));
      m.followLive(s('IDLE'));
      expect(m.active, isFalse);
      m.followLive(s('FOCUS_PC'));
      expect(m.active, isTrue);
      expect(m.liveSession, isTrue);
      m.tick();
      expect(m.sessionSeconds, 1); // 실제 시간
      m.followLive(s('FATIGUE'));
      m.followLive(s('FATIGUE'));
      expect(m.fatigueEvents, 1);
      m.followLive(s('REST'));
      expect(m.resting, isTrue);
      m.tick();
      expect(m.sessionSeconds, 1);
      m.followLive(s('FOCUS_PC', present: false));
      expect(m.resting, isFalse);
      expect(m.absent, isTrue);
      for (var i = 0; i < 10; i++) {
        m.tick();
      }
      expect(m.active, isTrue, reason: '보드를 따를 때 종료는 hub 가 정한다');
      m.followLive(s('END'));
      expect(m.active, isFalse);
      expect(m.page, DemoPage.report);
    });

    test('auto start off shows the start proposal instead', () {
      final m = model()..selectProfile(2);
      m.persona.preferences.autoStart = false;
      m.followLive(DisplayState.fromEnvelope(phase('FOCUS_PC')));
      expect(m.active, isFalse);
      expect(m.proposal, isTrue);
    });
  });

  testWidgets('live home strip, focus entry and card answer on screen',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    late FakeTransport transport;
    final link = LiveLink(
        transportFactory: (h, p) => transport = FakeTransport(),
        retryDelay: const Duration(hours: 1));
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false, live: link));
    link.configure('192.168.0.10', 1883);
    await tester.pump();
    // 연결 직후 지금 프로필을 retain 으로 알린다
    expect(transport.sent(profileTopic).single['data']['profile_id'],
        m.persona.profileId);
    transport.deliver(
        LiveLink.stateTopic,
        phase('IDLE', phaseName: 'idle', summary: {
          'co2_ppm': 1180,
          'temp_c': 24.4,
          'humidity_pct': 51,
          'lux': 320,
          'present': true,
          'tof': {'posture': 'upright'},
        }));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('link-dot')), findsOneWidget);
    expect(
        tester.widget<Text>(find.byKey(const ValueKey('status-공기'))).data,
        contains('1180 ppm'));
    expect(
        tester.widget<Text>(find.byKey(const ValueKey('status-실내'))).data,
        '24.4° · 51% · 320 lx');
    expect(
        tester.widget<Text>(find.byKey(const ValueKey('status-자세'))).data,
        '바른 자세');
    transport.deliver(LiveLink.stateTopic, phase('FOCUS_PC'));
    await tester.pumpAndSettle();
    expect(m.page, DemoPage.focus);
    transport.deliver(
        LiveLink.requestTopic,
        request('checkin_suggest',
            {'explanation': '입력 리듬이 느려졌어요'}));
    await tester.pumpAndSettle();
    expect(find.text('지금 피곤하세요?'), findsOneWidget);
    expect(find.text('입력 리듬이 느려졌어요'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('live-accept')));
    await tester.pumpAndSettle();
    expect(find.text('지금 피곤하세요?'), findsNothing);
    final feedback = transport.sent(LiveLink.feedbackTopic).single['data'];
    expect(feedback['verdict'], 'accept');
    expect(feedback['request_id'], 'req-checkin_suggest');
    expect(m.notice, '고마워요. 선호에 반영할게요');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });

  testWidgets('unreachable board keeps V17 in-memory screen', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final link = LiveLink(
        transportFactory: (h, p) => FakeTransport(succeed: false),
        retryDelay: const Duration(hours: 1));
    final m = model();
    await tester.pumpWidget(DemoApp(model: m, ticking: false, live: link));
    link.configure('192.168.0.10', 1883);
    await tester.pumpAndSettle();
    expect(
        tester.widget<Text>(find.byKey(const ValueKey('status-공기'))).data,
        '${m.config.seconds('co2')} ppm');
    expect(find.byTooltip('책상을 찾는 중이에요'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('home-clock-start')));
    await tester.pumpAndSettle();
    expect(m.active, isTrue);
    expect(m.liveSession, isFalse);
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });

  testWidgets('settings connection tab uses the in-app host keyboard',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final hosts = <String>[];
    final link = LiveLink(
        transportFactory: (h, p) {
          hosts.add('$h:$p');
          return FakeTransport(succeed: false);
        },
        retryDelay: const Duration(hours: 1));
    await tester.pumpWidget(DemoApp(model: model(), ticking: false, live: link));
    await tester.tap(find.byTooltip('나의 책상 설정'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('settings-tab-4')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('connection-host')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('host-keyboard')), findsOneWidget);
    for (final key in '10.0.0.7'.split('')) {
      await tester.tap(find.descendant(
          of: find.byKey(const ValueKey('host-keyboard')),
          matching: find.text(key)));
      await tester.pump();
    }
    await tester.tap(find.text('입력 완료'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('connection-apply')));
    await tester.pumpAndSettle();
    expect(hosts, ['10.0.0.7:1883']);
    expect(link.host, '10.0.0.7');
    await tester.tap(find.byTooltip('설정 닫기'));
    await tester.pumpAndSettle();
    await tester.pumpWidget(const SizedBox());
    link.dispose();
  });
}
