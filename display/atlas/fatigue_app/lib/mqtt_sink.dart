/// 피로 기록을 `deskmate/fatigue/label` 로 보내는 MQTT 연결과 브로커 주소 저장.
library;

import 'dart:convert';
import 'dart:io';

import 'package:camtest/hub_config.dart' show normalizeIp;
import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';

import 'fatigue_log.dart';

const fatigueTopic = 'deskmate/fatigue/label';

class BrokerAddress {
  const BrokerAddress(this.host, [this.port = 1883]);
  final String host;
  final int port;

  @override
  String toString() => '$host:$port';
}

/// 이 앱의 브로커 주소. 없으면 기본 DESKMATE 앱이 저장한 settings.json 의 주소를 쓴다
/// (같은 보드라면 한 번만 찍으면 된다).
class BrokerStore {
  BrokerStore({List<String>? dirs}) : dirs = dirs ?? defaultDirs();

  final List<String> dirs;

  static List<String> defaultDirs() {
    final env = Platform.environment;
    return [
      for (final (base, tail) in [
        (env['DESKMATE_CONFIG_DIR'], ''),
        (env['XDG_CONFIG_HOME'], '/deskmate'),
        (env['HOME'], '/.config/deskmate'),
        ('/tmp', '/deskmate'),
      ])
        if (base != null && base.trim().isNotEmpty) '${base.trim()}$tail',
    ];
  }

  BrokerAddress? load() {
    for (final name in ['fatigue_broker.json', 'settings.json']) {
      for (final dir in dirs) {
        try {
          final file = File('$dir/$name');
          if (!file.existsSync()) continue;
          final json = jsonDecode(file.readAsStringSync());
          if (json is! Map) continue;
          final host = json['mqtt_host'];
          final ip = host is String ? normalizeIp(host) : null;
          if (ip == null) continue;
          final port = json['mqtt_port'];
          return BrokerAddress(
              ip, port is int && port >= 1 && port <= 65535 ? port : 1883);
        } on Object {
          continue;
        }
      }
    }
    return null;
  }

  bool save(BrokerAddress address) {
    for (final dir in dirs) {
      try {
        final file = File('$dir/fatigue_broker.json');
        file.parent.createSync(recursive: true);
        file.writeAsStringSync(jsonEncode(
            {'mqtt_host': address.host, 'mqtt_port': address.port}));
        return true;
      } on Object {
        continue;
      }
    }
    return false;
  }
}

class MqttFatigueSink implements FatigueSink {
  MqttFatigueSink(this.address)
      : _bootId = 'fatigue-${DateTime.now().microsecondsSinceEpoch}',
        _client = MqttServerClient.withPort(address.host,
            'deskmate-fatigue-${DateTime.now().microsecondsSinceEpoch}', address.port) {
    _client.logging(on: false);
    _client.keepAlivePeriod = 30;
    _client.autoReconnect = true;
    _client.connectionMessage = MqttConnectMessage()
        .withClientIdentifier(_client.clientIdentifier)
        .startClean();
  }

  final BrokerAddress address;
  final String _bootId;
  final MqttServerClient _client;
  Future<void>? _connecting;
  int _seq = 0;

  bool get connected =>
      _client.connectionStatus?.state == MqttConnectionState.connected;

  Future<void> connect() {
    if (connected) return Future.value();
    return _connecting ??= () async {
      try {
        final status = await _client.connect();
        if (status?.state != MqttConnectionState.connected) {
          _client.disconnect();
          throw StateError('MQTT 연결 실패 ($address)');
        }
      } finally {
        _connecting = null;
      }
    }();
  }

  @override
  Future<void> publishFatigue(Map<String, Object?> data) async {
    await connect();
    final builder = MqttClientPayloadBuilder()
      ..addString(jsonEncode({
        'schema_version': '1.0',
        'ts': DateTime.now().millisecondsSinceEpoch / 1000,
        'node': 'display-fatigue',
        'boot_id': _bootId,
        'seq': ++_seq,
        'data': data,
      }));
    _client.publishMessage(fatigueTopic, MqttQos.atLeastOnce, builder.payload!);
  }

  void close() => _client.disconnect();
}
