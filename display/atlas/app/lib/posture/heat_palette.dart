/// 노트북 뷰어(`tools/palette.py`)와 같은 색으로 zone 거리 맵을 칠한다.
///
/// 같은 장면을 두 화면이 다른 색으로 그리면 시연 중에 서로 못 맞춘다. 그래서
/// 정지점과 거리 매핑을 그쪽에서 그대로 가져왔다.
///
/// **차가운 쪽이 짙은 파랑이 아니라 검정이다.** 비어 있는 zone 이 '차가운 값'이
/// 아니라 '아무것도 없음' 으로 읽히게 하려는 것이다.
library;

import 'dart:ui' show Color;

/// `posture_viewer.py` 의 NEAR_MM · FAR_MM.
const double heatNearMm = 450.0;
const double heatFarMm = 2600.0;

/// (위치, R, G, B). `palette.py` 의 RAINBOW_HC 와 같은 값이다.
const List<(double, int, int, int)> _stops = [
  (0.00, 0, 0, 0),
  (0.14, 10, 12, 90),
  (0.28, 0, 60, 190),
  (0.40, 0, 130, 150),
  (0.50, 25, 165, 60),
  (0.60, 130, 155, 20),
  (0.70, 205, 45, 25),
  (0.82, 255, 125, 0),
  (0.92, 255, 220, 40),
  (1.00, 255, 255, 205),
];

/// 0~1 을 팔레트 색으로. 1 이 제일 뜨겁다(가깝다).
Color heatColor(double t) {
  if (t.isNaN) return const Color(0xFF000000);
  final value = t < 0 ? 0.0 : (t > 1 ? 1.0 : t);
  for (var i = 1; i < _stops.length; i++) {
    final (pos, r, g, b) = _stops[i];
    if (value > pos) continue;
    final (prevPos, pr, pg, pb) = _stops[i - 1];
    final span = pos - prevPos;
    final f = span <= 0 ? 0.0 : (value - prevPos) / span;
    return Color.fromARGB(
      255,
      (pr + (r - pr) * f).round(),
      (pg + (g - pg) * f).round(),
      (pb + (b - pb) * f).round(),
    );
  }
  final (_, r, g, b) = _stops.last;
  return Color.fromARGB(255, r, g, b);
}

/// zone 거리(mm) -> 팔레트 색. 가까울수록 뜨겁다.
///
/// `render_zones()` 와 같은 식이다: 무효 zone 은 제일 먼 값으로 본다.
Color heatOfDepth(double mm) {
  final d = mm.isFinite ? mm : heatFarMm;
  final clamped = (d - heatNearMm) / (heatFarMm - heatNearMm);
  return heatColor(1.0 - (clamped < 0 ? 0.0 : (clamped > 1 ? 1.0 : clamped)));
}
