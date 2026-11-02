// 프로필 → hub 의 `deskmate/profile/user` (retain). 대응표는 README "프로필 → hub" 절.
//
// 이름·이미지·직업 같은 식별 단서는 싣지 않는다. profile_id 는 난수다.
library;

import '../demo_model.dart';

const profileTopic = 'deskmate/profile/user';

/// 나이 → 연령대. 10 대 미만도 10s 로 묶는다(hub 는 대략적인 수면·리듬 prior 에만 쓴다).
String ageBand(int age) => age < 20
    ? '10s'
    : age < 30
        ? '20s'
        : age < 40
            ? '30s'
            : age < 50
                ? '40s'
                : '50s+';

/// 자정부터의 분 → `HH:MM`. 하루를 넘으면 접는다.
String clockText(int minutes) {
  final m = minutes % (24 * 60);
  return '${(m ~/ 60).toString().padLeft(2, '0')}:${(m % 60).toString().padLeft(2, '0')}';
}

/// 빛과 바람 탭 → 조명 선호. 시작할 때 조명을 끄도록 했으면 low.
String lightPref(DeskPreferences p) {
  if (!p.lightEnabled || p.brightness < .4) return 'low';
  return p.brightness < .7 ? 'mid' : 'high';
}

/// 소리 탭 → 소리 선호. 무음이거나 소리를 끄면 off.
String soundPref(Persona persona) {
  final p = persona.preferences;
  if (!p.musicEnabled || persona.sound == '무음') return 'off';
  if (p.volume < .34) return 'low';
  return p.volume < .67 ? 'mid' : 'high';
}

/// 빛과 바람 탭 → 바람 선호. 시작할 때 바람을 끄도록 했으면 off, 아니면 1~3 단계.
String windPref(DeskPreferences p) {
  if (!p.fanEnabled) return 'off';
  return switch (p.fanSpeed) { 1 => 'low', 2 => 'mid', _ => 'high' };
}

/// 도움 순서 항목 → hub 의 focus_help 어휘(light · wind · sound · voice · break, 10-25 총괄 확정).
/// 앱의 도움 순서는 조명·선풍기·알림 셋이라 light · wind · sound 만 나온다(알림 = 알림음).
/// 음성 안내(voice)·휴식(break)은 앱에 따로 고르는 항목이 생기면 여기에 더한다.
String? helpId(String item) => switch (item) {
      '조명' => 'light',
      '선풍기' || '바람' => 'wind',
      '알림' || '알림음' => 'sound',
      '음성 안내' => 'voice',
      '휴식' => 'break',
      _ => null,
    };

Map<String, Object?> profileData(Persona persona) => {
      'profile_id': persona.profileId,
      'age_band': ageBand(persona.age),
      'sleep_hours': persona.sleep,
      'bedtime': clockText(persona.bedtimeMinutes),
      'wake_time': clockText(persona.bedtimeMinutes + persona.sleep * 60),
      'light_pref': lightPref(persona.preferences),
      'sound_pref': soundPref(persona),
      'wind_pref': windPref(persona.preferences),
      'focus_help': [
        for (final item in persona.order)
          if (helpId(item) case final id?) id
      ],
      'placement': persona.placement,
    };

/// V34: 다른 화면(devview 등)이 같은 profile_id 로 보낸 `deskmate/profile/user` → 이 프로필 값.
/// [profileData] 의 거꾸로다. 받은 대역이 지금 값의 대역과 같으면 그대로 두고(사람이 맞춘 값을 지키려고),
/// 다를 때만 그 대역의 가운데값으로 옮긴다. 모르는 값·없는 필드는 건드리지 않는다. 바꿨으면 true.
bool applyProfileData(Persona persona, Map<String, dynamic> data) {
  final p = persona.preferences;
  var changed = false;
  void mark(bool c) => changed = changed || c;

  final light = data['light_pref'];
  if (light is String && light != lightPref(p)) {
    final level = switch (light) {
      'low' => .3,
      'mid' => .55,
      'high' => .85,
      _ => null,
    };
    if (level != null) {
      p
        ..lightEnabled = true
        ..brightness = level;
      mark(true);
    }
  }

  final sound = data['sound_pref'];
  if (sound is String && sound != soundPref(persona)) {
    final volume = switch (sound) {
      'low' => .2,
      'mid' => .5,
      'high' => .85,
      _ => null,
    };
    if (sound == 'off') {
      p.musicEnabled = false;
      mark(true);
    } else if (volume != null) {
      p
        ..musicEnabled = true
        ..volume = volume;
      // '무음' 을 고른 채면 볼륨과 상관없이 off 로 읽히므로 기본 소리 종류로 돌린다.
      if (persona.sound == '무음') persona.sound = '백색 소음';
      mark(true);
    }
  }

  final wind = data['wind_pref'];
  if (wind is String && wind != windPref(p)) {
    final speed = switch (wind) {
      'low' => 1,
      'mid' => 2,
      'high' => 3,
      _ => null,
    };
    if (wind == 'off') {
      p.fanEnabled = false;
      mark(true);
    } else if (speed != null) {
      p
        ..fanEnabled = true
        ..fanSpeed = speed;
      mark(true);
    }
  }

  final sleep = data['sleep_hours'];
  if (sleep is num && sleep.isFinite && sleep >= 0 && sleep <= 24) {
    final hours = sleep.round();
    mark(hours != persona.sleep);
    persona.sleep = hours;
  }

  final bedtime = data['bedtime'];
  if (bedtime is String) {
    final m = RegExp(r'^(\d{1,2}):(\d{2})$').firstMatch(bedtime.trim());
    final h = m == null ? null : int.parse(m.group(1)!);
    final min = m == null ? null : int.parse(m.group(2)!);
    if (h != null && min != null && h < 24 && min < 60) {
      final minutes = h * 60 + min;
      mark(minutes != persona.bedtimeMinutes % (24 * 60));
      persona.bedtimeMinutes = minutes;
    }
  }

  final placement = data['placement'];
  if (placement is String &&
      const ['left', 'center', 'right'].contains(placement)) {
    mark(placement != persona.placement);
    persona.placement = placement;
  }

  final helps = data['focus_help'];
  if (helps is List) {
    const names = {'light': '조명', 'wind': '선풍기', 'sound': '알림'};
    final picked = <String>[
      for (final h in helps)
        if (names[h] case final item?) item,
    ];
    // 받은 순서대로, 앱에 없는 항목(voice·break)은 버리고, 빠진 앱 항목은 원래 순서로 뒤에.
    final order = picked.toSet().toList();
    for (final item in persona.order) {
      if (!order.contains(item)) order.add(item);
    }
    if (picked.isNotEmpty && order.join('|') != persona.order.join('|')) {
      persona.order = order;
      mark(true);
    }
  }

  final band = data['age_band'];
  if (band is String && band != ageBand(persona.age)) {
    final age = switch (band) {
      '10s' => 15,
      '20s' => 25,
      '30s' => 35,
      '40s' => 45,
      '50s+' => 55,
      _ => null,
    };
    if (age != null) {
      persona.age = age;
      mark(true);
    }
  }
  return changed;
}

/// 받은 값과 지금 프로필이 hub 에 보낼 값이 같은가(profile_id 제외). 같으면 다시 보내지 않는다.
bool profileMatches(Persona persona, Map<String, dynamic> data) {
  final mine = profileData(persona);
  for (final key in mine.keys) {
    if (key == 'profile_id') continue;
    final a = mine[key], b = data[key];
    if (a is List && b is List) {
      if (a.join('|') != b.join('|')) return false;
    } else if (a is num && b is num) {
      if (a.toDouble() != b.toDouble()) return false;
    } else if (a != b) {
      return false;
    }
  }
  return true;
}
