// V18: 프로필을 Pi 5 에 저장한다(`deskmate_ui/profiles.json`).
//
// hub 는 사람별 학습을 profile_id 로 묶는다. 앱을 다시 켤 때마다 id 가 바뀌면 학습이 처음부터 시작되므로
// 프로필·선택을 기기에 남긴다(사용자 지시 10-25 "모든 게 보드에서 개인화"). 별명·이미지는 이 파일에만 있고
// MQTT 로는 보내지 않는다. 오늘의 컨디션 같은 응답 기록·리포트 선택은 저장하지 않는다.
library;

import 'dart:convert';
import 'dart:io';

import 'demo_model.dart';

class SavedProfiles {
  const SavedProfiles(this.profiles, this.selected);
  final List<Persona> profiles;
  final int selected;
}

class ProfileStore {
  ProfileStore({List<String>? candidates})
      : candidates = candidates ?? defaultCandidates();

  final List<String> candidates;
  static const version = 1;

  static List<String> defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    add(env['DESKMATE_CONFIG_DIR'], 'ui_profiles.json');
    add(env['XDG_CONFIG_HOME'], 'deskmate_ui/profiles.json');
    add(env['HOME'], '.config/deskmate_ui/profiles.json');
    add('/tmp', 'deskmate_ui/profiles.json');
    return paths;
  }

  /// 읽을 수 있는 첫 파일. 없거나 깨졌거나 비었으면 null(기본 프로필로 시작).
  SavedProfiles? load() {
    for (final path in candidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) continue;
        final decoded = jsonDecode(file.readAsStringSync());
        if (decoded is! Map || decoded['profiles'] is! List) continue;
        final profiles = [
          for (final raw in decoded['profiles'] as List)
            if (raw is Map)
              if (personaFromJson(raw) case final persona?) persona
        ];
        if (profiles.isEmpty) continue;
        final selected = decoded['selected'];
        return SavedProfiles(
            profiles,
            selected is int && selected >= 0 && selected < profiles.length
                ? selected
                : 0);
      } on Object {
        continue;
      }
    }
    return null;
  }

  /// 쓸 수 있는 첫 위치에 원자적으로(임시 파일 → rename) 저장. 아무 데도 못 쓰면 false.
  bool save(List<Persona> profiles, int selected) {
    final contents = jsonEncode({
      'version': version,
      'selected': selected,
      'profiles': [for (final p in profiles) personaToJson(p)],
    });
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

Map<String, Object?> personaToJson(Persona p) {
  final d = p.preferences;
  return {
    'profile_id': p.profileId,
    'name': p.name,
    'avatar': p.avatar,
    'job': p.job,
    'rounds': p.rounds,
    'age': p.age,
    'sleep': p.sleep,
    'bedtime_min': p.bedtimeMinutes,
    'sound': p.sound,
    'order': p.order,
    'mood': p.mood,
    'placement': p.placement,
    'preferences': {
      'light_enabled': d.lightEnabled,
      'brightness': d.brightness,
      'warmth': d.warmth,
      'fan_enabled': d.fanEnabled,
      'fan_speed': d.fanSpeed,
      'music_enabled': d.musicEnabled,
      'volume': d.volume,
      'focus_minutes': d.focusMinutes,
      'auto_start': d.autoStart,
      'auto_adjust': d.autoAdjust,
      'start_notice': d.startNotice,
      'fatigue_brightness': d.fatigueBrightness,
      'fatigue_warmth': d.fatigueWarmth,
      'fatigue_fan_speed': d.fatigueFanSpeed,
    },
  };
}

/// 저장본 한 개 → Persona. id·이름이 없으면 null(그 항목만 버린다). 빠진 값은 기본값.
Persona? personaFromJson(Map<Object?, Object?> j) {
  final id = j['profile_id'];
  final name = j['name'];
  if (id is! String || !RegExp(r'^p[0-9a-f]{16}$').hasMatch(id)) return null;
  if (name is! String || name.trim().isEmpty) return null;
  T pick<T>(Object? v, T fallback) => v is T ? v : fallback;
  double unit(Object? v, double fallback) =>
      v is num ? v.toDouble().clamp(0.0, 1.0).toDouble() : fallback;
  int range(Object? v, int lo, int hi, int fallback) =>
      v is int ? (v < lo ? lo : (v > hi ? hi : v)) : fallback;
  final order = [
    for (final item in (j['order'] is List ? j['order'] as List : const []))
      if (item is String && const ['조명', '선풍기', '알림'].contains(item)) item
  ];
  final placement = j['placement'];
  final persona = Persona(
    profileId: id,
    name: name.trim(),
    job: pick<String>(j['job'], '수험생'),
    rounds: range(j['rounds'], 0, 1000, 0),
    age: range(j['age'], 1, 100, 19),
    sleep: range(j['sleep'], 1, 12, 7),
    bedtimeMinutes: range(j['bedtime_min'], 0, 24 * 60 - 1, Persona.defaultBedtime),
    sound: pick<String>(j['sound'], '백색 소음'),
    order: order.length == 3 ? order : const ['조명', '선풍기', '알림'],
    mood: pick<String>(j['mood'], '보통'),
    avatar: pick<String>(j['avatar'], 'cat'),
    placement: placement == 'left' || placement == 'right' ? placement as String : 'center',
  );
  final prefs = j['preferences'];
  if (prefs is Map) {
    persona.preferences
      ..lightEnabled = pick<bool>(prefs['light_enabled'], true)
      ..brightness = unit(prefs['brightness'], persona.preferences.brightness)
      ..warmth = unit(prefs['warmth'], .45)
      ..fanEnabled = pick<bool>(prefs['fan_enabled'], false)
      ..fanSpeed = range(prefs['fan_speed'], 1, 3, 1)
      ..musicEnabled = pick<bool>(prefs['music_enabled'], true)
      ..volume = unit(prefs['volume'], .3)
      ..focusMinutes = range(prefs['focus_minutes'], 5, 120, 25)
      ..autoStart = pick<bool>(prefs['auto_start'], true)
      ..autoAdjust = pick<bool>(prefs['auto_adjust'], true)
      ..startNotice = pick<bool>(prefs['start_notice'], true)
      ..fatigueBrightness = unit(prefs['fatigue_brightness'], .95)
      ..fatigueWarmth = unit(prefs['fatigue_warmth'], .25)
      ..fatigueFanSpeed = range(prefs['fatigue_fan_speed'], 1, 3, 3);
  }
  return persona;
}
