/// 보드의 연결 설정을 앱 화면과 분리해 읽고 쓰는 로컬 저장소.
library;

import 'dart:convert';
import 'dart:io';

import 'hub_config.dart' show HubStore, normalizeIp;

class SavedBluetoothDevice {
  const SavedBluetoothDevice({required this.address, required this.name});

  final String address;
  final String name;

  static SavedBluetoothDevice? _from(Object? value) {
    if (value is! Map) {
      return null;
    }
    final rawAddress = value['address'];
    final rawName = value['name'];
    if (rawAddress is! String || rawName is! String || rawName.trim().isEmpty) {
      return null;
    }
    final address = rawAddress.trim();
    if (!RegExp(r'^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$').hasMatch(address)) {
      return null;
    }
    return SavedBluetoothDevice(
        address: address.toUpperCase(), name: rawName.trim());
  }

  Map<String, Object?> toJson() => {'address': address, 'name': name};
}

class DeskmateSettings {
  const DeskmateSettings({
    this.mqttHost,
    this.mqttPort,
    this.postureUrl,
    this.speaker,
    this.lamp,
    this.fan,
    this.previewHost,
    this.devModeDefault = false,
    this.placement,
    this.placementAsked = false,
    this.lampBrightnessFloor,
    this.feedbackEnabled,
  });

  final String? mqttHost;
  final int? mqttPort;
  final String? postureUrl;
  final SavedBluetoothDevice? speaker;
  final SavedBluetoothDevice? lamp;
  final SavedBluetoothDevice? fan;

  /// 개발자 화면의 프리뷰 탭이 ToF 원본·자세 라벨을 읽을 Pi 4 주소(IP 또는 호스트 이름). 비우면 MQTT 호스트를 쓴다.
  final String? previewHost;

  /// 앱을 켤 때 개발자 모드 화면(ToF 열지도·판정·명령 로그)을 바로 띄울지. 기본 false.
  final bool devModeDefault;

  /// 장치를 보통 두는 위치(앉은 자리 기준): left | center | right. 미설정이면 null.
  /// Pi 4 자세 서비스(:8771 `/api/placement`)에 같은 값을 보내 모델을 고른다.
  final String? placement;

  /// 첫 실행 질문("장치를 보통 어디에 두나요?")을 건너뛴 적이 있는지. true 면 다시 묻지 않는다.
  final bool placementAsked;

  static const placementOptions = ['left', 'center', 'right'];

  /// adjust_brightness 의 하한(원시 0~255). null 이면 lamp_defaults 의 기본값.
  final int? lampBrightnessFloor;

  /// 블루투스 탭의 "상태 기반 조명·음악 피드백" 스위치. null 이면 끈 채로 시작한다.
  final bool? feedbackEnabled;

  static const empty = DeskmateSettings();

  bool get isEmpty =>
      mqttHost == null &&
      mqttPort == null &&
      postureUrl == null &&
      speaker == null &&
      lamp == null &&
      fan == null &&
      previewHost == null &&
      !devModeDefault &&
      placement == null &&
      !placementAsked &&
      lampBrightnessFloor == null &&
      feedbackEnabled == null;

  DeskmateSettings copyWith({
    String? mqttHost,
    bool clearMqttHost = false,
    int? mqttPort,
    bool clearMqttPort = false,
    String? postureUrl,
    bool clearPostureUrl = false,
    SavedBluetoothDevice? speaker,
    bool clearSpeaker = false,
    SavedBluetoothDevice? lamp,
    bool clearLamp = false,
    SavedBluetoothDevice? fan,
    bool clearFan = false,
    String? previewHost,
    bool clearPreviewHost = false,
    bool? devModeDefault,
    String? placement,
    bool clearPlacement = false,
    bool? placementAsked,
    int? lampBrightnessFloor,
    bool clearLampBrightnessFloor = false,
    bool? feedbackEnabled,
  }) =>
      DeskmateSettings(
        mqttHost: clearMqttHost ? null : mqttHost ?? this.mqttHost,
        mqttPort: clearMqttPort ? null : mqttPort ?? this.mqttPort,
        postureUrl: clearPostureUrl ? null : postureUrl ?? this.postureUrl,
        speaker: clearSpeaker ? null : speaker ?? this.speaker,
        lamp: clearLamp ? null : lamp ?? this.lamp,
        fan: clearFan ? null : fan ?? this.fan,
        previewHost: clearPreviewHost ? null : previewHost ?? this.previewHost,
        devModeDefault: devModeDefault ?? this.devModeDefault,
        placement: clearPlacement ? null : placement ?? this.placement,
        placementAsked: placementAsked ?? this.placementAsked,
        lampBrightnessFloor: clearLampBrightnessFloor
            ? null
            : lampBrightnessFloor ?? this.lampBrightnessFloor,
        feedbackEnabled: feedbackEnabled ?? this.feedbackEnabled,
      );

  Map<String, Object?> toJson() {
    final json = <String, Object?>{'schema_version': 1};
    if (mqttHost != null) {
      json['mqtt_host'] = mqttHost;
    }
    if (mqttPort != null) {
      json['mqtt_port'] = mqttPort;
    }
    if (postureUrl != null) {
      json['posture_url'] = postureUrl;
    }
    if (speaker != null) {
      json['speaker'] = speaker!.toJson();
    }
    if (lamp != null) {
      json['lamp'] = lamp!.toJson();
    }
    if (fan != null) {
      json['fan'] = fan!.toJson();
    }
    if (previewHost != null) {
      json['preview_host'] = previewHost;
    }
    if (devModeDefault) {
      json['dev_mode_default'] = true;
    }
    if (placement != null) {
      json['placement'] = placement;
    }
    if (placementAsked) {
      json['placement_asked'] = true;
    }
    if (lampBrightnessFloor != null) {
      json['lamp_brightness_floor'] = lampBrightnessFloor;
    }
    if (feedbackEnabled != null) {
      json['feedback_enabled'] = feedbackEnabled;
    }
    return json;
  }

  factory DeskmateSettings.fromJson(Map<String, Object?> json) {
    final rawHost = json['mqtt_host'];
    final host = rawHost is String ? normalizeIp(rawHost) : null;
    final rawPort = json['mqtt_port'];
    final port =
        rawPort is int && rawPort >= 1 && rawPort <= 65535 ? rawPort : null;
    final rawPosture = json['posture_url'];
    final posture = rawPosture is String ? _validHttpUrl(rawPosture) : null;
    final rawPreviewHost = json['preview_host'];
    return DeskmateSettings(
      mqttHost: host,
      mqttPort: port,
      postureUrl: posture,
      speaker: SavedBluetoothDevice._from(json['speaker']),
      lamp: SavedBluetoothDevice._from(json['lamp']),
      fan: SavedBluetoothDevice._from(json['fan']),
      previewHost:
          rawPreviewHost is String ? validHostName(rawPreviewHost) : null,
      devModeDefault: json['dev_mode_default'] == true,
      placement: validPlacement(json['placement']),
      placementAsked: json['placement_asked'] == true,
      lampBrightnessFloor: _byte(json['lamp_brightness_floor']),
      feedbackEnabled: json['feedback_enabled'] is bool
          ? json['feedback_enabled'] as bool
          : null,
    );
  }

  static int? _byte(Object? raw) =>
      raw is int && raw >= 0 && raw <= 255 ? raw : null;

  /// left | center | right 만 받는다(대소문자·공백 무시). 아니면 null.
  static String? validPlacement(Object? raw) {
    if (raw is! String) return null;
    final text = raw.trim().toLowerCase();
    return placementOptions.contains(text) ? text : null;
  }

  /// IP 나 호스트 이름만 받는다(스킴·경로 없이). 모양이 아니면 null.
  static String? validHostName(String raw) {
    final text = raw.trim();
    if (text.isEmpty || text.length > 253) return null;
    if (!RegExp(r'^[A-Za-z0-9.\-]+$').hasMatch(text)) return null;
    return text;
  }

  static String? _validHttpUrl(String raw) {
    final text = raw.trim();
    final uri = Uri.tryParse(text);
    if (uri == null ||
        !uri.hasAuthority ||
        uri.host.isEmpty ||
        (uri.scheme != 'http' && uri.scheme != 'https')) {
      return null;
    }
    return text;
  }
}

class SettingsStore {
  SettingsStore(
      {List<String>? candidates, List<String>? legacyPostureCandidates})
      : candidates = candidates ?? defaultCandidates(),
        legacyPostureCandidates =
            legacyPostureCandidates ?? HubStore.defaultCandidates();

  final List<String> candidates;
  final List<String> legacyPostureCandidates;
  String? _lastPath;

  String? get lastPath => _lastPath;

  static List<String> defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) {
        return;
      }
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    // 보드마다 쓰기 가능한 설정 위치가 다르므로 후보를 순서대로 둔다.
    add(env['DESKMATE_CONFIG_DIR'], 'settings.json');
    add(env['XDG_CONFIG_HOME'], 'deskmate/settings.json');
    add(env['HOME'], '.config/deskmate/settings.json');
    add('/tmp', 'deskmate/settings.json');
    return paths;
  }

  /// 한 경로의 파일 오류가 보드 설정 전체를 막지 않도록 실패를 건너뛴다.
  DeskmateSettings load() {
    _lastPath = null;
    for (final path in candidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) {
          continue;
        }
        final decoded = jsonDecode(file.readAsStringSync());
        if (decoded is! Map) {
          continue;
        }
        final json = <String, Object?>{};
        decoded.forEach((key, value) {
          if (key is String) {
            json[key] = value;
          }
        });
        var settings = DeskmateSettings.fromJson(json);
        if (!json.containsKey('posture_url')) {
          settings = settings.copyWith(
            postureUrl: _readLegacyPosture(),
          );
        }
        _lastPath = path;
        return settings;
      } on Object {
        // 깨진 파일이나 읽기 권한 오류는 다음 후보를 시도하게 한다.
      }
    }
    final legacy = _readLegacyPosture();
    if (legacy != null) {
      return DeskmateSettings.empty.copyWith(postureUrl: legacy);
    }
    return DeskmateSettings.empty;
  }

  String? _readLegacyPosture() {
    for (final path in legacyPostureCandidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) {
          continue;
        }
        final lines = file.readAsLinesSync();
        for (final line in lines) {
          final valid = DeskmateSettings._validHttpUrl(line);
          if (valid != null) {
            return valid;
          }
        }
      } on Object {
        // 레거시 설정은 가져오기 전용이며 읽을 수 없어도 앱 시작을 막지 않는다.
      }
    }
    return null;
  }

  bool save(DeskmateSettings settings) {
    final contents = '${jsonEncode(settings.toJson())}\n';
    for (final path in candidates) {
      final file = File(path);
      File? temporary;
      try {
        file.parent.createSync(recursive: true);
        temporary =
            File('$path.tmp.$pid.${DateTime.now().microsecondsSinceEpoch}');
        temporary.writeAsStringSync(contents, flush: true);
        temporary.renameSync(path);
        _lastPath = path;
        return true;
      } on Object {
        try {
          if (temporary?.existsSync() ?? false) {
            temporary!.deleteSync();
          }
        } on Object {
          // 임시 파일 정리 실패는 다음 저장 후보 시도를 막지 않는다.
        }
      }
    }
    return false;
  }

  bool clear() {
    var removed = false;
    for (final path in candidates) {
      try {
        final file = File(path);
        if (file.existsSync()) {
          file.deleteSync();
          removed = true;
          _lastPath = path;
        }
      } on Object {
        // 읽기 전용 경로가 있어도 다른 후보의 파일은 지운다.
      }
    }
    return removed;
  }
}

/// 보드에 저장한 값을 우선하고, 없으면 빌드 설정을 쓴다.
String? effectiveMqttHost(DeskmateSettings settings, String buildHost) {
  final saved = settings.mqttHost;
  if (saved != null && saved.trim().isNotEmpty) return normalizeIp(saved);
  return normalizeIp(buildHost);
}

int effectiveMqttPort(DeskmateSettings settings, int buildPort) {
  final saved = settings.mqttPort;
  if (saved != null && saved >= 1 && saved <= 65535) {
    return saved;
  }
  return buildPort >= 1 && buildPort <= 65535 ? buildPort : 1883;
}
