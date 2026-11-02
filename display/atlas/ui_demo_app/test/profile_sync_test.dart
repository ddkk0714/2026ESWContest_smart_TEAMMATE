// V34: 다른 화면(devview)이 같은 profile_id 로 고친 deskmate/profile/user → 이 앱의 저장된 프로필.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:deskmate_ui_demo/demo_model.dart';
import 'package:deskmate_ui_demo/live/live_link.dart';
import 'package:deskmate_ui_demo/live/profile_payload.dart';
import 'package:deskmate_ui_demo/live/profile_sync.dart';
import 'package:deskmate_ui_demo/live/transport.dart';
import 'package:deskmate_ui_demo/profile_store.dart';
import 'package:flutter_test/flutter_test.dart';

DemoConfig _config() => DemoConfig(
    jsonDecode(File('assets/config/demo.json').readAsStringSync())
        as Map<String, dynamic>);

class _Transport implements LiveTransport {
  final _messages = StreamController<LiveMessage>.broadcast(sync: true);
  final _connection = StreamController<bool>.broadcast(sync: true);
  bool connected = false;
  final published = <({String topic, String text, bool retain})>[];
  @override
  Stream<LiveMessage> get messages => _messages.stream;
  @override
  Stream<bool> get connection => _connection.stream;
  @override
  bool get isConnected => connected;
  @override
  Future<bool> connect() async => connected = true;
  @override
  void subscribe(String topic) {}
  @override
  void publish(String topic, String text, {bool retain = false}) =>
      published.add((topic: topic, text: text, retain: retain));
  @override
  void close() => connected = false;
  void deliver(String topic, Object? json, {bool retained = false}) =>
      _messages.add(LiveMessage(topic, jsonEncode(json), retained: retained));
  int sent(String topic) => published.where((p) => p.topic == topic).length;
}

Map<String, Object?> _envelope(Map<String, Object?> data,
        {String node = 'laptop-devview'}) =>
    {'schema_version': '1.0', 'ts': 1.0, 'node': node, 'data': data};

void main() {
  group('applyProfileData', () {
    test('round trip: another persona takes the same hub values', () {
      final source = Persona(
          name: 'a',
          job: '수험생',
          rounds: 0,
          age: 33,
          sleep: 6,
          order: ['알림', '조명', '선풍기'],
          placement: 'right',
          bedtimeMinutes: 60);
      source.preferences
        ..brightness = .9
        ..fanEnabled = true
        ..fanSpeed = 2
        ..volume = .8;
      final target = Persona(name: 'b', job: '기타', rounds: 0);
      final data = Map<String, dynamic>.from(profileData(source));
      expect(applyProfileData(target, data), isTrue);
      expect(profileMatches(target, data), isTrue);
      expect(target.order, ['알림', '조명', '선풍기']);
      expect(target.placement, 'right');
      expect(target.bedtimeMinutes, 60);
      expect(target.sleep, 6);
      expect(ageBand(target.age), '30s');
      expect(applyProfileData(target, data), isFalse); // 두 번째는 바뀐 게 없다
    });

    test('a value already in the received band is kept as the user set it',
        () {
      final p = Persona(name: 'a', job: '기타', rounds: 0);
      p.preferences
        ..brightness = .62 // mid
        ..volume = .4; // mid
      applyProfileData(p, {'light_pref': 'mid', 'sound_pref': 'mid'});
      expect(p.preferences.brightness, .62);
      expect(p.preferences.volume, .4);
      applyProfileData(p, {'light_pref': 'high', 'sound_pref': 'low'});
      expect(p.preferences.brightness, .85);
      expect(p.preferences.volume, .2);
    });

    test('off, silent sound and fan speeds map back', () {
      final p = Persona(name: 'a', job: '기타', rounds: 0, sound: '무음');
      applyProfileData(p, {'sound_pref': 'high', 'wind_pref': 'high'});
      expect(soundPref(p), 'high'); // 무음은 기본 소리 종류로 — 안 그러면 off 로 읽힌다
      expect(p.sound, '백색 소음');
      expect(windPref(p.preferences), 'high');
      applyProfileData(
          p, {'light_pref': 'low', 'sound_pref': 'off', 'wind_pref': 'off'});
      expect(lightPref(p.preferences), 'low');
      expect(p.preferences.lightEnabled, isTrue); // 조명은 켠 채 어둡게
      expect(soundPref(p), 'off');
      expect(windPref(p.preferences), 'off');
    });

    test('focus order keeps app items only and appends the missing ones', () {
      final p = Persona(name: 'a', job: '기타', rounds: 0);
      applyProfileData(p, {
        'focus_help': ['voice', 'sound', 'break', 'light']
      });
      expect(p.order, ['알림', '조명', '선풍기']);
    });

    test('unknown values are ignored', () {
      final p = Persona(name: 'a', job: '기타', rounds: 0);
      final before = jsonEncode(profileData(p));
      expect(
          applyProfileData(p, {
            'light_pref': 'bright',
            'bedtime': '25:00',
            'placement': 'up',
            'age_band': '90s',
            'sleep_hours': 'eight',
          }),
          isFalse);
      expect(jsonEncode(profileData(p)), before);
    });
  });

  group('LiveLink + syncRemoteProfile', () {
    late Directory dir;
    late ProfileStore store;
    late DemoModel model;
    late _Transport transport;
    late LiveLink live;
    setUp(() async {
      dir = Directory.systemTemp.createTempSync('v34-profiles');
      store =
          ProfileStore(candidates: ['${dir.path}/deskmate_ui/profiles.json']);
      model = DemoModel(_config(), store: store, presentationEnabled: true);
      transport = _Transport();
      live = LiveLink(
          transportFactory: (_, __) => transport,
          retryDelay: const Duration(hours: 1));
      live.onRemoteProfile = (data) => syncRemoteProfile(model, live, data);
      live.publishProfile(model.persona);
      live.configure('10.0.0.2', 1883);
      await Future<void>.delayed(Duration.zero);
    });
    tearDown(() {
      live.dispose();
      dir.deleteSync(recursive: true);
    });

    test('the current person changed elsewhere is saved, not echoed back',
        () {
      final me = model.persona;
      final before = transport.sent(profileTopic);
      final data = Map<String, Object?>.from(profileData(me))
        ..['light_pref'] = 'low'
        ..['wind_pref'] = 'high';
      transport.deliver(profileTopic, _envelope(data), retained: true);
      expect(lightPref(model.persona.preferences), 'low');
      expect(windPref(model.persona.preferences), 'high');
      expect(transport.sent(profileTopic), before); // 같은 값 — 다시 안 보낸다
      final reloaded = DemoModel(_config(), store: store);
      expect(lightPref(reloaded.persona.preferences), 'low'); // profiles.json 에 남았다
    });

    test('values the app cannot hold are corrected once with the app value',
        () {
      final me = model.persona;
      final before = transport.sent(profileTopic);
      final data = Map<String, Object?>.from(profileData(me))
        ..['focus_help'] = ['voice', 'sound', 'light', 'wind'];
      transport.deliver(profileTopic, _envelope(data));
      expect(transport.sent(profileTopic), before + 1);
      final echoed = jsonDecode(transport.published.last.text)['data'] as Map;
      expect(echoed['focus_help'], ['sound', 'light', 'wind']);
      expect(echoed['profile_id'], me.profileId);
    });

    test('my own messages and unknown ids are ignored', () {
      final me = model.persona;
      final mine = Map<String, Object?>.from(profileData(me))
        ..['light_pref'] = 'low';
      transport.deliver(profileTopic, _envelope(mine, node: 'pi5-ui'));
      expect(lightPref(model.persona.preferences), isNot('low'));
      final stranger = Map<String, Object?>.from(mine)
        ..['profile_id'] = 'p0000000000000000';
      final count = model.profiles.length;
      transport.deliver(profileTopic, _envelope(stranger));
      expect(model.profiles.length, count); // 새 프로필을 만들지 않는다
      expect(lightPref(model.persona.preferences), isNot('low'));
    });

    test('another saved person is updated quietly without switching hub', () {
      final other = model.profiles[1];
      expect(identical(other, model.persona), isFalse);
      final before = transport.sent(profileTopic);
      final data = Map<String, Object?>.from(profileData(other))
        ..['wind_pref'] = 'mid'
        ..['focus_help'] = ['voice', 'wind'];
      transport.deliver(profileTopic, _envelope(data));
      expect(windPref(model.profiles[1].preferences), 'mid');
      expect(model.profiles[1].order.first, '선풍기');
      expect(transport.sent(profileTopic), before); // 다른 사람 것을 보내면 hub 의 지금 사람이 바뀐다
    });
  });
}
