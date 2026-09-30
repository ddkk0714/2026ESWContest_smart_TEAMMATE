import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:deskmate_display/settings_store.dart';

void main() {
  late Directory temp;

  setUp(() {
    temp = Directory.systemTemp.createTempSync('settings-store-test-');
  });

  tearDown(() {
    temp.deleteSync(recursive: true);
  });

  String path(String name) => '${temp.path}/$name/settings.json';

  test('saves all fields and loads them in a new store', () {
    final settings = DeskmateSettings(
      mqttHost: '192.168.1.20',
      mqttPort: 1884,
      postureUrl: 'http://192.168.1.30:8765',
      speaker: const SavedBluetoothDevice(
        address: 'AA:BB:CC:DD:EE:01',
        name: 'speaker',
      ),
      lamp: const SavedBluetoothDevice(
        address: 'AA:BB:CC:DD:EE:02',
        name: 'lamp',
      ),
    );
    expect(SettingsStore(candidates: [path('one')]).save(settings), isTrue);
    expect(SettingsStore(candidates: [path('one')]).load().toJson(),
        settings.toJson());
  });

  test('missing and malformed files return empty without throwing', () {
    final missing = SettingsStore(candidates: [path('missing')]);
    expect(missing.load().isEmpty, isTrue);
    File(path('broken'))
      ..createSync(recursive: true)
      ..writeAsStringSync('{broken');
    expect(SettingsStore(candidates: [path('broken')]).load().isEmpty, isTrue);
  });

  test('drops invalid fields while retaining valid fields', () {
    final file = File(path('partial'))
      ..createSync(recursive: true)
      ..writeAsStringSync(jsonEncode({
        'mqtt_host': '999.1.1.1',
        'mqtt_port': 70000,
        'posture_url': 'ftp://example.test',
        'speaker': {'address': 'bad', 'name': 'speaker'},
        'lamp': {'address': 'aa:bb:cc:dd:ee:ff', 'name': 'lamp'},
      }));
    final loaded = SettingsStore(candidates: [file.path]).load();
    expect(loaded.mqttHost, isNull);
    expect(loaded.mqttPort, isNull);
    expect(loaded.postureUrl, isNull);
    expect(loaded.speaker, isNull);
    expect(loaded.lamp?.address, 'AA:BB:CC:DD:EE:FF');
  });

  test('tries the next candidate if the first is not writable', () {
    final blocked = '${temp.path}/blocked';
    File(blocked).writeAsStringSync('a file blocks making a parent directory');
    final next = path('usable');
    final store = SettingsStore(candidates: ['$blocked/settings.json', next]);
    expect(store.save(const DeskmateSettings(mqttHost: '10.0.0.4')), isTrue);
    expect(store.lastPath, next);
    expect(File(next).existsSync(), isTrue);
  });

  test('returns false when every save candidate is unusable', () {
    final blocked = '${temp.path}/blocked';
    File(blocked).writeAsStringSync('file');
    expect(
      SettingsStore(candidates: ['$blocked/a.json', '$blocked/b.json'])
          .save(DeskmateSettings.empty),
      isFalse,
    );
  });

  test('clear removes settings and a subsequent load is empty', () {
    final candidate = path('clear');
    final store = SettingsStore(candidates: [candidate]);
    expect(store.save(const DeskmateSettings(mqttPort: 1883)), isTrue);
    expect(store.clear(), isTrue);
    expect(store.load().isEmpty, isTrue);
  });

  test('imports legacy hub URL and gives settings file precedence', () {
    final legacy = '${temp.path}/legacy/hub.txt';
    File(legacy)
      ..createSync(recursive: true)
      ..writeAsStringSync('http://192.168.1.9:8765\n');
    final candidate = path('migration');
    final store = SettingsStore(
      candidates: [candidate],
      legacyPostureCandidates: [legacy],
    );
    expect(store.load().postureUrl, 'http://192.168.1.9:8765');
    expect(File(legacy).readAsStringSync(), 'http://192.168.1.9:8765\n');

    File(candidate)
      ..createSync(recursive: true)
      ..writeAsStringSync(jsonEncode(
          {'schema_version': 1, 'posture_url': 'https://saved.test'}));
    expect(store.load().postureUrl, 'https://saved.test');

    File(candidate).writeAsStringSync(
      jsonEncode({'schema_version': 1, 'posture_url': 'not a URL'}),
    );
    expect(store.load().postureUrl, isNull);
  });

  test('copyWith clear flags remove selected values', () {
    final settings = const DeskmateSettings(
      mqttHost: '192.168.1.2',
      mqttPort: 1883,
      postureUrl: 'http://host.test',
      speaker: SavedBluetoothDevice(address: 'AA:BB:CC:DD:EE:FF', name: 'spk'),
      lamp: SavedBluetoothDevice(address: '11:22:33:44:55:66', name: 'lamp'),
    ).copyWith(
      clearMqttHost: true,
      clearMqttPort: true,
      clearPostureUrl: true,
      clearSpeaker: true,
      clearLamp: true,
    );
    expect(settings.isEmpty, isTrue);
  });

  test('effective MQTT values prefer saved, then valid build values', () {
    expect(
        effectiveMqttHost(
            const DeskmateSettings(mqttHost: '10.0.0.2'), '1.2.3.4'),
        '10.0.0.2');
    expect(effectiveMqttHost(DeskmateSettings.empty, '001.2.3.4'), '1.2.3.4');
    expect(effectiveMqttHost(DeskmateSettings.empty, ''), isNull);
    expect(
        effectiveMqttPort(const DeskmateSettings(mqttPort: 1234), 1883), 1234);
    expect(effectiveMqttPort(DeskmateSettings.empty, 1884), 1884);
    expect(effectiveMqttPort(DeskmateSettings.empty, 0), 1883);
  });

  test('normalizes valid Bluetooth addresses and rejects malformed ones', () {
    final loaded = DeskmateSettings.fromJson({
      'speaker': {'address': 'aa:bb:cc:dd:ee:ff', 'name': 'speaker'},
      'lamp': {'address': 'AA-BB-CC-DD-EE-FF', 'name': 'lamp'},
    });
    expect(loaded.speaker?.address, 'AA:BB:CC:DD:EE:FF');
    expect(loaded.lamp, isNull);
  });
}
