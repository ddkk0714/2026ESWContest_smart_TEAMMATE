// 카드 그림판 — 안내·자동 실행·제안 카드가 무엇에 관한 것인지 한눈에 보이게 하는 큰 픽토그램과 색.
// kind(interaction/request) · arm(hub 가 고른 방법) · target(기기)으로 고른다. 판정은 hub 가 하고 여기선 모습만.
library;

import 'package:flutter/material.dart';

import '../posture_pictogram.dart';

class CardLook {
  const CardLook(
      {required this.label,
      required this.ink,
      required this.tile,
      this.icon,
      this.posture});

  /// 카드 위 작은 꼬리표(예: 자세 안내 · 무드등).
  final String label;
  final Color ink;
  final Color tile;
  final IconData? icon;

  /// 자세 안내면 지금 자세(posture_pictogram 의 키). 그림을 아이콘 대신 자세로 그린다.
  final String? posture;
  bool get isPosture => icon == null;

  /// 큰 그림. 자세 안내는 사람 그림, 나머지는 아이콘.
  Widget picture(double size) => isPosture
      ? PosturePictogram(posture: posture, color: ink, size: size)
      : Icon(icon, size: size * .78, color: ink);
}

const _amber = (Color(0xFFB7791F), Color(0xFFF6ECD6));
const _blue = (Color(0xFF3D6FA8), Color(0xFFE3EDF8));
const _green = (Color(0xFF356C59), Color(0xFFE6EBDD));
const _purple = (Color(0xFF5D5F99), Color(0xFFE6E5F2));
const _coral = (Color(0xFFC0583A), Color(0xFFF6E2DA));
const _teal = (Color(0xFF2E7F7A), Color(0xFFDDEFED));

CardLook _look(String label, (Color, Color) c, IconData icon) =>
    CardLook(label: label, ink: c.$1, tile: c.$2, icon: icon);

/// 기기(target_id) 그림.
CardLook lookForTarget(String? target) => switch (target) {
      'desk_lamp' => _look('무드등', _amber, Icons.lightbulb_rounded),
      'vent_fan' || 'fan' => _look('선풍기', _blue, Icons.wind_power_rounded),
      'music' => _look('음악', _green, Icons.music_note_rounded),
      'speaker' => _look('스피커', _purple, Icons.record_voice_over_rounded),
      'air_purifier' => _look('공기청정기', _teal, Icons.air_rounded),
      _ => _look('자동으로 도왔어요', _green, Icons.auto_awesome_rounded),
    };

/// 자세 안내 카드에 덧붙이는 바로잡는 요령 한 줄. 모르는 자세면 null.
String? postureTip(String? posture) => switch (posture) {
      'forward_head' => '턱을 살짝 당기고 화면을 눈높이로 올려 보세요.',
      'chin_rest' => '팔을 내려놓고 등을 의자에 붙여 보세요.',
      'lean_forward' || 'head_down' => '허리를 세우고 어깨를 한 번 내려 보세요.',
      'recline' => '등받이에서 조금 일어나 화면과 거리를 맞춰 보세요.',
      'lean_left' || 'lean_right' => '양쪽 엉덩이에 무게를 고르게 실어 보세요.',
      'face_down' || 'drowsy' => '일어나 기지개를 켜고 물 한 잔 어떠세요?',
      _ => null,
    };

/// 집중 시작 제안.
final focusStartLook = _look('집중 시작', _green, Icons.timer_rounded);

/// 앱의 도움 순서 말(조명 · 선풍기 · 알림) 그림.
CardLook lookForHelp(String help) => switch (help) {
      '조명' => lookForTarget('desk_lamp'),
      '선풍기' => lookForTarget('vent_fan'),
      '알림' => _look('알림', _coral, Icons.notifications_active_rounded),
      _ => lookFor(),
    };

/// 안내·제안 카드 그림. [posture] 는 지금 자세(자세 안내일 때 그린다).
CardLook lookFor({String? kind, String? arm, String? posture}) {
  if (arm == 'posture_card' ||
      arm == 'posture_voice' ||
      kind == 'posture_suggest' ||
      kind == 'posture_card') {
    final colors = postureColors(posture);
    return CardLook(
        label: '자세 안내', ink: colors.ink, tile: colors.tile, posture: posture);
  }
  return switch ((kind, arm)) {
    ('rest_notice', _) ||
    ('break_suggest', _) ||
    (_, 'break_short' || 'break_long') =>
      _look('쉬어 가기', _purple, Icons.self_improvement_rounded),
    (_, 'sleep_suggest') => _look('잠자리 권유', _purple, Icons.bedtime_rounded),
    ('vent_suggest', _) ||
    (_, 'ventilate_suggest') =>
      _look('환기', _teal, Icons.window_rounded),
    ('checkin_suggest', _) =>
      _look('컨디션 확인', _coral, Icons.sentiment_satisfied_alt_rounded),
    (_, 'sound_cue') =>
      _look('알림음', _coral, Icons.notifications_active_rounded),
    (_, 'wake_light' || 'light_cool_bright' || 'light_warm_dim') =>
      lookForTarget('desk_lamp'),
    (_, 'fan_on') => lookForTarget('vent_fan'),
    (_, 'air_fan_up') => lookForTarget('air_purifier'),
    ('env_suggest', _) => _look('공기 · 환경', _teal, Icons.eco_rounded),
    _ => _look('작은 제안', _green, Icons.spa_rounded),
  };
}

/// 카드 왼쪽 그림판. 둥근 색 바탕 위에 큰 그림.
class CardPicturePanel extends StatelessWidget {
  const CardPicturePanel({super.key, required this.look, this.size = 150});
  final CardLook look;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
      key: const ValueKey('card-picture'),
      width: size * 1.45,
      decoration: BoxDecoration(
          color: look.tile, borderRadius: BorderRadius.circular(28)),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        look.picture(size),
        const SizedBox(height: 14),
        Text(look.label,
            key: const ValueKey('card-picture-label'),
            style: TextStyle(fontSize: 17, color: look.ink)),
      ]));
}
