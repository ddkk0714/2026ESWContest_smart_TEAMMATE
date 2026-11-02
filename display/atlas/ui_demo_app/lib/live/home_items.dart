// `deskmate/state/phase` → 홈 하단 한 줄(공기·실내·자세·컨디션)과 집중 상세 카드 문구.
//
// 값이 없으면 숫자를 지어내지 않고 '살펴보는 중' 으로 둔다. 판정·임계값은 hub 가 정하고
// 여기서는 보이는 말만 고른다(CO₂ 등급은 assets/config/co2_bands.json, 표시 전용).
library;

import '../pr56/co2_grade.dart';
import '../pr56/display_state.dart';

const waitingText = '살펴보는 중';

/// ToF 자세 클래스(hub ingest.yaml posture_phi 의 키) → 화면 말.
String? postureKo(String? posture) => switch (posture) {
      'upright' => '바른 자세',
      'lean_forward' => '앞으로 기울어짐',
      'forward_head' => '고개가 앞으로 나옴',
      'head_down' => '고개 숙임',
      'recline' => '뒤로 기댐',
      'face_down' => '엎드림',
      'chin_rest' => '턱 괴기',
      'lean_left' => '왼쪽으로 기울어짐',
      'lean_right' => '오른쪽으로 기울어짐',
      'drowsy' => '졸린 자세',
      'away' => '자리 비움',
      'misplaced' => '장치 방향 확인',
      _ => null,
    };

/// 자세 항목. 자리에 없으면(present=false) 자세보다 자리 비움이 먼저다.
String postureText(DisplayState s) {
  if (s.present == false) return '자리 비움';
  return postureKo(s.tofPosture) ?? waitingText;
}

/// 공기 항목: CO₂ 등급 · ppm. 등급표를 아직 못 읽었으면 ppm 만.
String airText(DisplayState s, Co2Grades? grades) {
  final ppm = s.co2Ppm;
  if (ppm == null || ppm <= 0) return waitingText;
  final band = grades?.forValue(ppm);
  return band == null ? '$ppm ppm' : '${band.label} · $ppm ppm';
}

/// 실내 항목: 온도 · 습도 · 조도 중 받은 것만.
String roomText(DisplayState s) {
  final parts = [
    if (s.temperatureC != null) '${s.temperatureC!.toStringAsFixed(1)}°',
    if (s.humidityPct != null) '${s.humidityPct!.round()}%',
    if (s.lux != null) '${s.lux} lx',
  ];
  return parts.isEmpty ? waitingText : parts.join(' · ');
}

/// 컨디션 항목. 기존 문구(편안해요·환기해요)에 hub 의 피로 구간(제안 게이트 0.45, 확정 0.70)을 맞췄다.
/// C_focus 는 "큰 값 = 집중 저하 증거"(CLAUDE.md §5 기본값)라 0.70 이상이면 조금 지친 쪽으로 본다.
String conditionText(DisplayState s) {
  final base = s.fatigue >= .70
      ? '환기해요'
      : (s.fatigue >= .45 || s.focus >= .70)
          ? '조금 피곤해요'
          : '편안해요';
  final observed = observationText(s);
  return observed == null ? base : '$base · $observed';
}

/// 2 단계 관찰(`sensor_summary.personalization`, status=predicted 일 때만) → 한 단어.
/// 하단 한 줄에 들어가도록 짧게 둔다(관찰 모드라 판정이 아니라 참고 표시).
String? observationText(DisplayState s) {
  if (s.personalizationStatus != 'predicted') return null;
  return switch (s.personalizationLabel) {
    'focus' => '몰입',
    'fatigue' => '피로',
    'rest' => '휴식',
    'idle' => '쉼',
    _ => null,
  };
}

/// 집중 상세의 '책상 주변' 두 줄.
({String main, String sub}) deskText(DisplayState s) {
  final main = [
    if (s.temperatureC != null) '${s.temperatureC!.toStringAsFixed(1)}°',
    if (s.humidityPct != null) '${s.humidityPct!.round()}%',
  ];
  final sub = [
    if (s.co2Ppm != null && s.co2Ppm! > 0) 'CO₂ ${s.co2Ppm} ppm',
    if (s.lux != null) '${s.lux} lx',
  ];
  return (
    main: main.isEmpty ? waitingText : main.join(' · '),
    sub: sub.isEmpty ? '' : sub.join(' · ')
  );
}
