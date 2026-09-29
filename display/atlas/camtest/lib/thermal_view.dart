/// 카메라 그림을 열화상처럼 칠하고, 54x42 zone 으로 줄이고, 그 위에 뼈대를 얹는다.
///
/// `pico_esp32-cam_ftdi` 의 `tools/thermal_pose.py`(`feat/thermal-pose-tee-zones`)
/// 를 옮긴 것이다. 노트북에서 그 도구로 보던 그림을 보드에서도 그대로 본다.
///
///     흑백 preview ──> 판정 서비스의 랜드마크 ──┐
///          │                                   │
///          ▼                                   ▼
///     54x42 축약 → CLAHE → 팔레트  ←────  뼈대를 그 위에 그린다
///
/// 순서가 중요하다. 색을 입힌 그림에 모델을 돌리면 검출이 무너지므로, 필터는
/// **표시 전용**이다. 여기서는 판정을 camsvc 가 이미 끝내서 랜드마크로 주므로
/// 앱은 색칠과 겹치기만 한다.
///
/// **이건 열화상 카메라가 아니다.** ESP32-CAM 은 빛을 재지 온도를 재지 않는다.
/// 밝기를 열화상 팔레트에 통과시킨 가짜 색이고, 어떤 픽셀값도 도(℃)가 아니다.
///
/// 팔레트는 앱의 `heat_palette.dart` 를 그대로 쓴다. 그쪽 정지점이 원본
/// `palette.py` 의 `RAINBOW_HC`(기본 팔레트)와 같은 값이라, 같은 장면을 노트북
/// 도구와 보드가 같은 색으로 그린다.
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'zone_image.dart';

/// 원본 `cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))`.
const double claheClipLimit = 2.5;
const int claheTilesX = 8;
const int claheTilesY = 8;

/// 한 장의 열화상 격자와, 그림이 쓸 만한지 말해 주는 원본 통계.
class ThermalFrame {
  const ThermalFrame({
    required this.heat,
    required this.cols,
    required this.rows,
    required this.rawMin,
    required this.rawMax,
    required this.rawMean,
  });

  /// 0~1. 1 이 제일 뜨겁다(밝다).
  final Float64List heat;
  final int cols;
  final int rows;

  /// 원본 흑백의 분포. **이 셋이 그림이 쓸 만한지 말해 주는 숫자다** — 렌즈가
  /// 가려진 프레임과 날아간 프레임은 둘 다 평평해서, 화면에서는 모델이 죽은
  /// 것과 구분이 안 된다.
  final int rawMin;
  final int rawMax;
  final int rawMean;

  int get spread => rawMax - rawMin;

  double at(int col, int row) => heat[row * cols + col];
}

/// 흑백 한 장 → 54x42 열화상 격자.
///
/// 축약을 먼저 하고 CLAHE 를 그 위에 돌린다. 원본 `to_heat()` 과 같은 순서다 —
/// 큰 그림에서 평활화한 뒤 줄이면 칸마다 다른 이웃을 본 결과가 섞인다.
ThermalFrame thermalZones(Uint8List grey, int width, int height,
    {int cols = tofCols, int rows = tofRows}) {
  if (width <= 0 || height <= 0 || grey.length < width * height) {
    return ThermalFrame(
      heat: Float64List(cols * rows),
      cols: cols,
      rows: rows,
      rawMin: 0,
      rawMax: 0,
      rawMean: 0,
    );
  }

  var lo = 255;
  var hi = 0;
  var sum = 0;
  final count = width * height;
  for (var i = 0; i < count; i++) {
    final v = grey[i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    sum += v;
  }

  final small = areaResizeBytes(grey, width, height, cols, rows);
  final bytes = Uint8List(cols * rows);
  for (var i = 0; i < bytes.length; i++) {
    final v = small[i].round();
    bytes[i] = v < 0 ? 0 : (v > 255 ? 255 : v);
  }

  final equalized = clahe(bytes, cols, rows);
  final heat = Float64List(cols * rows);
  for (var i = 0; i < heat.length; i++) {
    heat[i] = equalized[i] / 255.0;
  }
  return ThermalFrame(
    heat: heat,
    cols: cols,
    rows: rows,
    rawMin: lo,
    rawMax: hi,
    rawMean: sum ~/ count,
  );
}

/// CLAHE (Contrast Limited Adaptive Histogram Equalization).
///
/// OpenCV 의 `cv::CLAHE` 를 그대로 옮긴 구조다 — 타일별 히스토그램을 한도에서
/// 자르고 잘라낸 양을 전 구간에 고루 되돌린 뒤, 픽셀마다 이웃 타일 넷의 LUT 를
/// 이중선형으로 섞는다. 전역 평활화를 쓰면 배경이 넓은 프레임에서 사람이
/// 뭉개지고, 타일 LUT 를 섞지 않으면 타일 경계가 격자로 드러난다.
///
/// 골든 벡터가 없어 비트 단위로 같다고는 말하지 못한다. 보기용 필터라 그
/// 수준까지 맞출 이유도 없다고 봤다 — 구조와 상수를 맞췄다.
Uint8List clahe(Uint8List src, int width, int height,
    {double clipLimit = claheClipLimit,
    int tilesX = claheTilesX,
    int tilesY = claheTilesY}) {
  const bins = 256;
  if (width <= 0 || height <= 0) return Uint8List(0);

  // 타일 수로 안 나누어떨어지면 OpenCV 는 가장자리를 반사(BORDER_REFLECT_101)해
  // 늘린 뒤 계산한다. 안 그러면 마지막 타일만 작아져 그쪽 대비가 튄다.
  final padW = width % tilesX == 0 ? width : width + (tilesX - width % tilesX);
  final padH = height % tilesY == 0 ? height : height + (tilesY - height % tilesY);
  final padded = Uint8List(padW * padH);
  for (var y = 0; y < padH; y++) {
    final sy = _reflect101(y, height);
    for (var x = 0; x < padW; x++) {
      padded[y * padW + x] = src[sy * width + _reflect101(x, width)];
    }
  }

  final tileW = padW ~/ tilesX;
  final tileH = padH ~/ tilesY;
  final tileArea = tileW * tileH;
  if (tileArea == 0) return Uint8List.fromList(src);

  final limit = clipLimit > 0
      ? math.max((clipLimit * tileArea / bins).toInt(), 1)
      : 0;
  final lutScale = (bins - 1) / tileArea;

  // 타일마다 LUT 하나.
  final luts = List<Uint8List>.generate(tilesX * tilesY, (_) => Uint8List(bins),
      growable: false);
  final hist = Int32List(bins);
  for (var ty = 0; ty < tilesY; ty++) {
    for (var tx = 0; tx < tilesX; tx++) {
      hist.fillRange(0, bins, 0);
      for (var y = 0; y < tileH; y++) {
        final row = (ty * tileH + y) * padW + tx * tileW;
        for (var x = 0; x < tileW; x++) {
          hist[padded[row + x]]++;
        }
      }

      if (limit > 0) {
        var clipped = 0;
        for (var i = 0; i < bins; i++) {
          if (hist[i] > limit) {
            clipped += hist[i] - limit;
            hist[i] = limit;
          }
        }
        // 잘라낸 양을 전 구간에 고루 돌려준 뒤, 나머지를 일정 간격으로 흩는다.
        // 앞쪽 칸에 몰아주면 어두운 쪽만 들린다.
        final batch = clipped ~/ bins;
        var residual = clipped - batch * bins;
        for (var i = 0; i < bins; i++) {
          hist[i] += batch;
        }
        if (residual > 0) {
          final step = math.max(bins ~/ residual, 1);
          for (var i = 0; i < bins && residual > 0; i += step, residual--) {
            hist[i]++;
          }
        }
      }

      final lut = luts[ty * tilesX + tx];
      var sum = 0;
      for (var i = 0; i < bins; i++) {
        sum += hist[i];
        final v = (sum * lutScale).round();
        lut[i] = v < 0 ? 0 : (v > 255 ? 255 : v);
      }
    }
  }

  // 픽셀마다 이웃 타일 넷을 섞는다. 타일 중심을 기준으로 잡아야 경계가 안 보인다.
  final out = Uint8List(width * height);
  for (var y = 0; y < height; y++) {
    final tyf = y / tileH - 0.5;
    var ty1 = tyf.floor();
    final ya = tyf - ty1;
    var ty2 = ty1 + 1;
    ty1 = ty1.clamp(0, tilesY - 1);
    ty2 = ty2.clamp(0, tilesY - 1);
    for (var x = 0; x < width; x++) {
      final txf = x / tileW - 0.5;
      var tx1 = txf.floor();
      final xa = txf - tx1;
      var tx2 = tx1 + 1;
      tx1 = tx1.clamp(0, tilesX - 1);
      tx2 = tx2.clamp(0, tilesX - 1);

      final v = src[y * width + x];
      final a = luts[ty1 * tilesX + tx1][v] * (1 - xa) * (1 - ya);
      final b = luts[ty1 * tilesX + tx2][v] * xa * (1 - ya);
      final c = luts[ty2 * tilesX + tx1][v] * (1 - xa) * ya;
      final d = luts[ty2 * tilesX + tx2][v] * xa * ya;
      final mixed = (a + b + c + d).round();
      out[y * width + x] = mixed < 0 ? 0 : (mixed > 255 ? 255 : mixed);
    }
  }
  return out;
}

/// `BORDER_REFLECT_101`: 가장자리 픽셀은 겹치지 않고 반사한다(gfedcb|abcdefgh|gfedcba).
int _reflect101(int i, int n) {
  if (n == 1) return 0;
  var v = i;
  while (v < 0 || v >= n) {
    if (v < 0) {
      v = -v;
    } else {
      v = 2 * (n - 1) - v;
    }
  }
  return v;
}
