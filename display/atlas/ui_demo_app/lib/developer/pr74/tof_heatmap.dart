/// ToF 8×8 원본 격자를 색으로 그리는 열지도.
///
/// 발표의 키포인트 — "카메라 대신 이 64 칸이 전부" 를 눈으로 보여 준다. 얼굴도, 영상도
/// 없다. 칸 하나가 센서 한 zone 의 거리(mm)이고, 가까울수록 붉고 멀수록 푸르다.
/// 측정이 없거나(−1) 상태값이 무효인 칸은 회색.
///
/// 배열은 펌웨어가 이미 방향을 적용한 것이라 **여기서 뒤집지 않는다** — row-major 순서 그대로.
library;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'tof_raw_client.dart';

/// 무효·없음 칸의 색.
const kTofInvalidColor = Color(0xFFBDBCB7);

/// 색 눈금의 양 끝(mm). 20 cm 가 가장 붉고 2 m 부터는 가장 어둡다
/// (`hub/atlas-tof/pc/posture_view.html` 의 `distColor` 와 같은 눈금).
const kTofNearMm = 200;
const kTofFarMm = 2000;

const _stops = <Color>[
  Color(0xFFF06A6A),
  Color(0xFFF5A524),
  Color(0xFF4CC38A),
  Color(0xFF3B82F6),
  Color(0xFF282D3C),
];

/// 거리(mm)·상태값 → 칸 색. 거리 ≤ 0 이거나 상태값이 무효면 [kTofInvalidColor].
Color tofZoneColor(int distMm, int status) {
  if (distMm <= 0) return kTofInvalidColor;
  if (status >= 0 && !kTofValidStatus.contains(status)) return kTofInvalidColor;
  final t = ((distMm - kTofNearMm) / (kTofFarMm - kTofNearMm)).clamp(0.0, 1.0);
  final x = t * (_stops.length - 1);
  final i = x.floor().clamp(0, _stops.length - 2);
  final f = x - i;
  return Color.lerp(_stops[i], _stops[i + 1], f)!;
}

class TofHeatmap extends StatelessWidget {
  const TofHeatmap({
    super.key,
    required this.frame,
    required this.title,
    required this.keyPrefix,
    this.showNumbers = true,
  });

  /// null 이면 아직 응답이 없는 것, `have == false` 면 중계기가 이 센서 프레임을 못 받은 것.
  final TofRawFrame? frame;
  final String title;

  /// 칸 키 접두어. 칸 i 는 `ValueKey('$keyPrefix-zone-$i')`.
  final String keyPrefix;

  /// 칸 안에 cm 숫자를 쓸지. 작은 화면이면 끈다.
  final bool showNumbers;

  @override
  Widget build(BuildContext context) {
    final f = frame;
    final w = f?.width ?? 8;
    final h = f?.height ?? 8;
    final theme = Theme.of(context);
    final String sub;
    if (f == null) {
      sub = '응답 대기';
    } else if (!f.have) {
      sub = '아직 프레임 없음';
    } else {
      sub = '유효 ${f.validCount}/${f.zoneCount} · 최소 ${_mm(f.minMm)}'
          ' · 중앙값 ${_mm(f.medianMm)}'
          '${f.ageMs == null ? '' : ' · ${f.ageMs} ms 전'}';
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title,
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w800)),
      Text(sub,
          key: ValueKey('$keyPrefix-summary'),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.labelMedium),
      const SizedBox(height: 6),
      AspectRatio(
        aspectRatio: w / h,
        child: Container(
          padding: const EdgeInsets.all(3),
          decoration: BoxDecoration(
            color: DeskmateColors.surfaceMuted,
            borderRadius: BorderRadius.circular(10),
          ),
          child: Column(children: [
            for (var r = 0; r < h; r++)
              Expanded(
                child: Row(children: [
                  for (var c = 0; c < w; c++)
                    Expanded(child: _zone(f, r * w + c)),
                ]),
              ),
          ]),
        ),
      ),
    ]);
  }

  Widget _zone(TofRawFrame? f, int i) {
    final mm = f == null ? -1 : f.zoneMm(i);
    final status = f == null ? -1 : f.zoneStatus(i);
    final color = tofZoneColor(mm, status);
    final dark = color.computeLuminance() < 0.35;
    return Padding(
      padding: const EdgeInsets.all(1),
      child: ColoredBox(
        key: ValueKey('$keyPrefix-zone-$i'),
        color: color,
        child: showNumbers
            ? Center(
                child: FittedBox(
                  fit: BoxFit.scaleDown,
                  child: Text(
                    mm > 0 ? (mm / 10).round().toString() : '·',
                    style: TextStyle(
                      fontSize: 9,
                      fontWeight: FontWeight.w600,
                      color: dark ? Colors.white70 : DeskmateColors.ink,
                    ),
                  ),
                ),
              )
            : null,
      ),
    );
  }

  static String _mm(int? v) => v == null ? '—' : '$v mm';
}
