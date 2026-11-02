// 보드 연결 주소(MQTT 브로커 host·port)만 기기에 저장한다.
//
// 저장 위치 후보는 ui_env_app settings_store.dart 와 같은 순서지만 파일은 따로 쓴다
// (`deskmate_ui/connection.json`) — 두 앱이 서로의 설정을 덮지 않게.
// 프로필·답변은 여기에 쓰지 않는다(메모리에만 둔다).
library;

import 'dart:convert';
import 'dart:io';

/// 주소가 어디서 왔는지. 연결 상태 칸에 그대로 보여 준다.
enum LinkSource {
  /// `deskmate_ui/connection.json` 에 저장된 값(사용자가 연결하기를 누른 값).
  saved,

  /// connection.json 이 없거나 비어 있어 `deskmate/settings.json` 의 `mqtt_host` 를 썼다
  /// (블루투스 실행기·개발자 앱이 쓰는 파일 — 같은 보드를 가리킨다).
  settings,

  /// 둘 다 비어 있어 Pi 4 유선 주소(`wiredPi4Host`)를 썼다.
  wiredDefault,

  /// 사용자가 "연결 끊기"를 눌러 일부러 비운 상태.
  off,
}

/// Pi 4 ↔ Pi 5 이더넷 직결 주소. 핫스팟이 바뀌어도 그대로라 새 설치의 기본값으로 쓴다
/// (10-27: 핫스팟을 바꾸자 Pi 4 wlan0 가 172.20.10.3 → .6 으로 바뀌어 저장 주소로는 끊겼다).
const wiredPi4Host = '172.16.34.199';

class LinkSettings {
  const LinkSettings(
      {this.host = '',
      this.port = defaultPort,
      this.source = LinkSource.saved,
      this.manualOff = false});
  static const defaultPort = 1883;
  final String host;
  final int port;

  /// 어디서 읽은 값인지(저장하지 않는다).
  final LinkSource source;

  /// 사용자가 "연결 끊기"로 비웠는지. 이때는 대체 주소를 쓰지 않는다.
  final bool manualOff;

  Map<String, Object?> toJson() => {
        'mqtt_host': host,
        'mqtt_port': port,
        if (host.isEmpty) 'manual_off': true,
      };

  static LinkSettings fromJson(Map<Object?, Object?> json) {
    final host = json['mqtt_host'];
    final port = json['mqtt_port'];
    return LinkSettings(
        host: host is String ? normalizeHost(host) : '',
        port: port is int && port > 0 && port < 65536 ? port : defaultPort,
        manualOff: json['manual_off'] == true);
  }

  LinkSettings withSource(LinkSource value) =>
      LinkSettings(host: host, port: port, source: value, manualOff: manualOff);
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
  LinkSettingsStore(
      {List<String>? candidates,
      List<String>? settingsCandidates,
      this.fallbackHost = wiredPi4Host})
      : candidates = candidates ?? defaultCandidates(),
        settingsCandidates = settingsCandidates ?? defaultSettingsCandidates();

  final List<String> candidates;

  /// 블루투스 실행기·개발자 앱의 `deskmate/settings.json` 후보(settings_store.dart 와 같은 순서).
  final List<String> settingsCandidates;

  /// 두 파일 모두 비었을 때 쓸 주소. 빈 문자열이면 대체 주소 없이 연결하지 않는다.
  final String fallbackHost;

  static List<String> defaultSettingsCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    add(env['DESKMATE_CONFIG_DIR'], 'settings.json');
    add(env['XDG_CONFIG_HOME'], 'deskmate/settings.json');
    add(env['HOME'], '.config/deskmate/settings.json');
    add('/tmp', 'deskmate/settings.json');
    return paths;
  }

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

  /// 연결 주소를 정한다. 순서:
  /// 1. `deskmate_ui/connection.json` 에 주소가 있으면 그 값(saved).
  /// 2. 사용자가 "연결 끊기"로 비웠으면 연결 안 함(off).
  /// 3. `deskmate/settings.json` 의 `mqtt_host`(settings) — 개발자 앱에서 옮겨 온 설정.
  /// 4. Pi 4 유선 주소 `fallbackHost`(wiredDefault) — 새 설치에서 "Pi 4 주소 없음"을 막는다.
  LinkSettings load() {
    final saved = _readFirst(candidates);
    if (saved != null && saved.host.isNotEmpty) {
      return saved.withSource(LinkSource.saved);
    }
    if (saved != null && saved.manualOff) {
      return saved.withSource(LinkSource.off);
    }
    final other = _readFirst(settingsCandidates);
    if (other != null && other.host.isNotEmpty) {
      return LinkSettings(
          host: other.host, port: other.port, source: LinkSource.settings);
    }
    final fallback = normalizeHost(fallbackHost);
    if (fallback.isNotEmpty) {
      return LinkSettings(host: fallback, source: LinkSource.wiredDefault);
    }
    return const LinkSettings(source: LinkSource.off);
  }

  /// 읽을 수 있는 첫 파일. 없거나 깨졌으면 null.
  static LinkSettings? _readFirst(List<String> paths) {
    for (final path in paths) {
      try {
        final file = File(path);
        if (!file.existsSync()) continue;
        final decoded = jsonDecode(file.readAsStringSync());
        if (decoded is Map) return LinkSettings.fromJson(decoded);
      } on Object {
        continue;
      }
    }
    return null;
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
