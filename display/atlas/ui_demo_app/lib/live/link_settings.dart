// 보드 연결 주소(MQTT 브로커 host·port)만 기기에 저장한다.
//
// 저장 위치 후보는 ui_env_app settings_store.dart 와 같은 순서지만 파일은 따로 쓴다
// (`deskmate_ui/connection.json`) — 두 앱이 서로의 설정을 덮지 않게.
// 프로필·답변은 여기에 쓰지 않는다(메모리에만 둔다).
library;

import 'dart:convert';
import 'dart:io';

class LinkSettings {
  const LinkSettings({this.host = '', this.port = defaultPort});
  static const defaultPort = 1883;
  final String host;
  final int port;

  Map<String, Object?> toJson() => {'mqtt_host': host, 'mqtt_port': port};

  static LinkSettings fromJson(Map<Object?, Object?> json) {
    final host = json['mqtt_host'];
    final port = json['mqtt_port'];
    return LinkSettings(
        host: host is String ? normalizeHost(host) : '',
        port: port is int && port > 0 && port < 65536 ? port : defaultPort);
  }
}

/// 앞뒤 공백·`mqtt://` 접두어를 걷어 낸다. 비면 연결하지 않는다.
String normalizeHost(String raw) {
  var host = raw.trim();
  for (final prefix in ['mqtt://', 'tcp://', 'http://']) {
    if (host.startsWith(prefix)) host = host.substring(prefix.length);
  }
  if (host.endsWith('/')) host = host.substring(0, host.length - 1);
  return host;
}

class LinkSettingsStore {
  LinkSettingsStore({List<String>? candidates})
      : candidates = candidates ?? defaultCandidates();

  final List<String> candidates;

  static List<String> defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    add(env['DESKMATE_CONFIG_DIR'], 'ui_connection.json');
    add(env['XDG_CONFIG_HOME'], 'deskmate_ui/connection.json');
    add(env['HOME'], '.config/deskmate_ui/connection.json');
    add('/tmp', 'deskmate_ui/connection.json');
    return paths;
  }

  /// 읽을 수 있는 첫 파일. 없거나 깨졌으면 빈 설정(연결 안 함).
  LinkSettings load() {
    for (final path in candidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) continue;
        final decoded = jsonDecode(file.readAsStringSync());
        if (decoded is Map) return LinkSettings.fromJson(decoded);
      } on Object {
        continue;
      }
    }
    return const LinkSettings();
  }

  /// 쓸 수 있는 첫 위치에 원자적으로 저장. 아무 데도 못 쓰면 false(이번 실행에만 적용).
  bool save(LinkSettings settings) {
    final contents = jsonEncode(settings.toJson());
    for (final path in candidates) {
      try {
        final file = File(path);
        file.parent.createSync(recursive: true);
        final temporary =
            File('$path.tmp.$pid.${DateTime.now().microsecondsSinceEpoch}');
        temporary.writeAsStringSync(contents, flush: true);
        temporary.renameSync(path);
        return true;
      } on Object {
        continue;
      }
    }
    return false;
  }
}
