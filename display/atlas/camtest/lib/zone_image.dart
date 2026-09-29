/// zone 격자 하나를 다루는 데 공통으로 쓰는 것들.
///
/// 열화상 화면(`thermal_view.dart`)과 배경차분 화면(`tof_filter.dart`)이 둘 다
/// 같은 격자에 같은 방식으로 줄여야 두 그림을 겹쳐 놓고 비교할 수 있다.
library;

import 'dart:typed_data';

/// ToF(VL53L9CX) 가 내는 zone 격자. 이 프로젝트가 겨누는 해상도다.
const int tofCols = 54;
const int tofRows = 42;

/// 면적 평균으로 줄인다(OpenCV `INTER_AREA`). 배율이 정수가 아니라
/// (160/54 = 2.96) 가장자리 픽셀을 비율만큼만 세야 그림이 안 밀린다.
Float64List areaResizeBytes(Uint8List src, int sw, int sh, int dw, int dh) {
  final input = Float64List(sw * sh);
  for (var i = 0; i < input.length; i++) {
    input[i] = src[i].toDouble();
  }
  return areaResize(input, sw, sh, dw, dh);
}

Float64List areaResize(Float64List src, int sw, int sh, int dw, int dh) {
  if (sw == dw && sh == dh) return src;
  final out = Float64List(dw * dh);
  final xScale = sw / dw;
  final yScale = sh / dh;
  for (var dy = 0; dy < dh; dy++) {
    final y0 = dy * yScale;
    final y1 = y0 + yScale;
    final ry0 = y0.floor();
    final ry1 = (y1.ceil()).clamp(0, sh);
    for (var dx = 0; dx < dw; dx++) {
      final x0 = dx * xScale;
      final x1 = x0 + xScale;
      final rx0 = x0.floor();
      final rx1 = (x1.ceil()).clamp(0, sw);
      var sum = 0.0;
      var weight = 0.0;
      for (var sy = ry0; sy < ry1; sy++) {
        final wy = _overlap(sy.toDouble(), sy + 1.0, y0, y1);
        if (wy <= 0) continue;
        final row = sy * sw;
        for (var sx = rx0; sx < rx1; sx++) {
          final wx = _overlap(sx.toDouble(), sx + 1.0, x0, x1);
          if (wx <= 0) continue;
          final w = wx * wy;
          sum += src[row + sx] * w;
          weight += w;
        }
      }
      out[dy * dw + dx] = weight > 0 ? sum / weight : 0.0;
    }
  }
  return out;
}

double _overlap(double a0, double a1, double b0, double b1) {
  final lo = a0 > b0 ? a0 : b0;
  final hi = a1 < b1 ? a1 : b1;
  return hi > lo ? hi - lo : 0.0;
}

/// cols x rows 격자를 화면에 그렸을 때의 정확한 칸 경계 x 좌표.
///
/// 한 칸이 정수 픽셀이 아닐 때(800px / 54칸 = 14.81) 고정 간격으로 선을 그으면
/// 오른쪽 끝에서 세 칸이나 밀린다. `INTER_NEAREST` 가 목적지 x 를
/// `floor(x*cols/w)` 로 보내므로, 칸 i 는 `ceil(i*w/cols)` 에서 시작한다.
/// 나눗셈을 실수로 하면 몫이 딱 떨어지는 자리마다 선이 한 픽셀 늦으므로
/// 정수 올림으로 계산한다.
List<double> zoneBoundaries(double extent, int count) {
  final out = <double>[];
  for (var i = 0; i <= count; i++) {
    // 몫이 딱 떨어지는 자리에서 실수 오차로 올림이 한 픽셀 밀리지 않게 한다.
    final exact = i * extent / count;
    final v = (exact - 1e-9).ceilToDouble();
    if (out.isEmpty || out.last != v) out.add(v);
  }
  return out;
}
