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

/// 도움 순서 항목 → hub 채널 이름(personalize.yaml 의 channel 과 맞춤, 알림은 소리·음성·카드를 묶은 notify).
String helpId(String item) => switch (item) {
      '조명' => 'lamp',
      '선풍기' => 'fan',
      '알림' => 'notify',
      _ => item,
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
      'focus_help': [for (final item in persona.order) helpId(item)],
      'placement': persona.placement,
    };
