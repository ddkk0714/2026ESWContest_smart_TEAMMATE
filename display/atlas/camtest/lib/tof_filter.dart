/// 카메라 그림을 ToF 화면처럼 54x42 로 줄여 보여 준다.
///
/// `pico_esp32-cam_ftdi` 의 `tools/camera_source.py` 를 옮긴 것이다. 그쪽은
/// 보드 없이 자세 판정을 돌려 보려고 웹캠을 ESP32-CAM + RP2040 자리에 세운
/// 물건인데, 여기서는 **판정 서비스(camsvc)가 보내는 흑백 preview** 를 같은
/// 순서로 통과시켜 화면에만 쓴다.
///
///     흑백 → 시그마-델타 배경 → |현재-배경| → 54x42 로 박스 축약
///                                                  |
///              최대 블롭 ← 열림 ← 구멍 채움 ← 임계
///
/// 앞줄은 `esp32cam_sender.ino`, 뒷줄은 RP2040 의 `vision.c` 가 하던 일이다.
///
/// **이건 센서가 아니라 대역이다.** 카메라의 자동 노출·색 처리·해상도가 ESP 와
/// 달라서, 여기서 맞춘 임계값을 보드에 그대로 옮기면 안 된다. 재현되는 것은
/// 신호의 *구조* 다 — 밝기 차이는 피사체가 배경과 밝기로 다를 때만 잡힌다는
/// 것, 그게 실제로 문제가 되는 실패다.
///
/// 판정에는 쓰지 않는다. 판정은 camsvc 의 스켈레톤이 하고, 이 격자는 "지금
/// 잡히고 있나" 를 눈으로 보는 용도다.
library;

import 'dart:typed_data';

import 'posture_source.dart' show VisionSnapshot;
import 'vision_frame.dart' show maskToZone;
import 'zone_image.dart';

export 'zone_image.dart' show tofCols, tofRows;

/// 스케치가 잡는 해상도(QQVGA). 배경 모델을 여기서 돌린다.
const int _srcW = 160;
const int _srcH = 120;

const int _bgPeriod = 4;   // 시그마-델타 갱신 주기 [프레임] (스케치: 4)
const int _bgGuard = 24;   // 이만큼 벗어난 픽셀은 배경으로 흡수하지 않는다 (스케치: 24)
const int _gainQ4 = 16;    // 차분 이득. 16 = 1.0 (스케치: 16)
const int _settleFrames = 20;  // 배경을 심기 전에 버릴 프레임 수

/// 한 장씩 넣으면 54x42 격자를 돌려준다. 배경 모델을 들고 있으므로 프레임
/// 순서대로 넣어야 한다.
class TofFilter {
  TofFilter({
    this.threshold = 40,
    this.opening = 1,
    this.fill = true,
    this.largest = true,
  });

  /// coverage 가 이 값 이상이면 전경. 보드의 `t<숫자>` 와 같은 뜻이다.
  int threshold;

  final int opening;
  final bool fill;
  final bool largest;

  Int16List? _bg;
  int _settle = _settleFrames;
  int _frames = 0;

  /// 배경을 다음 프레임에서 다시 심는다. 보드에서는 이걸 `b` 라고 부른다.
  void resetBackground() {
    _bg = null;
    _settle = _settleFrames;
  }

  /// 아직 배경을 못 세웠으면 true. 화면이 "기준 잡는 중" 이라고 말할 수 있게.
  bool get warmingUp => _bg == null || _settle > 0;

  /// 흑백 한 장 → 54x42 격자. 배경을 세우는 동안에는 빈 격자를 돌려준다.
  VisionSnapshot add(int width, int height, Uint8List grey) {
    if (width <= 0 || height <= 0 || grey.length < width * height) {
      return _empty();
    }
    final small = areaResizeBytes(grey, width, height, _srcW, _srcH);

    _frames++;
    if (_settle > 0) {
      _settle--;
      return _empty();
    }
    var bg = _bg;
    if (bg == null) {
      bg = Int16List(_srcW * _srcH);
      for (var i = 0; i < bg.length; i++) {
        bg[i] = small[i].round();
      }
      _bg = bg;
      return _empty();
    }

    final absdiff = Float64List(_srcW * _srcH);
    final step = _frames % _bgPeriod == 0;
    for (var i = 0; i < absdiff.length; i++) {
      final diff = small[i] - bg[i];
      absdiff[i] = diff.abs();
      // 시그마-델타: 한 번에 한 단계만, 그리고 지금 전경으로 읽히는 픽셀 쪽으로는
      // 절대 가지 않는다 - 안 그러면 가만히 앉은 사람이 몇 초 만에 배경에 녹는다.
      if (step && diff.abs() <= _bgGuard && diff != 0) {
        bg[i] = (bg[i] + (diff > 0 ? 1 : -1)).clamp(0, 255);
      }
    }

    // 절댓값은 박스 평균 **앞** 에서 픽셀마다 취한다. box(|cur-bg|) 는
    // |box(cur)-box(bg)| 가 아니고, 뒤쪽은 한 칸 안에 피사체의 밝은 부분과
    // 어두운 부분이 같이 들어가면 상쇄된다 - 윤곽선이 대부분 그렇다.
    final scaled = areaResize(absdiff, _srcW, _srcH, tofCols, tofRows);
    final coverage = Uint8List(tofCols * tofRows);
    for (var i = 0; i < coverage.length; i++) {
      final v = (scaled[i] * _gainQ4 / 16.0).round();
      coverage[i] = v < 0 ? 0 : (v > 255 ? 255 : v);
    }

    var mask = List<bool>.generate(
        coverage.length, (i) => coverage[i] >= threshold,
        growable: false);
    // 구멍 채움이 열림보다 먼저다. 침식이 테두리까지 구멍을 열어 버리면
    // 그 뒤로는 아무것도 못 채운다.
    if (fill) mask = fillHoles(mask, tofRows, tofCols);
    for (var i = 0; i < opening; i++) {
      mask = _erode(mask, tofRows, tofCols);
    }
    for (var i = 0; i < opening; i++) {
      mask = _dilate(mask, tofRows, tofCols);
    }
    if (largest && mask.any((v) => v)) {
      mask = largestBlob(mask, tofRows, tofCols);
    }

    return VisionSnapshot(
      width: tofCols,
      height: tofRows,
      coverage: coverage,
      mask: [for (final v in mask) v ? 1 : 0],
      depthMm: maskToZone(mask, tofRows, tofCols).data,
    );
  }

  VisionSnapshot _empty() {
    final mask = List<bool>.filled(tofCols * tofRows, false);
    return VisionSnapshot(
      width: tofCols,
      height: tofRows,
      coverage: Uint8List(tofCols * tofRows),
      mask: List<int>.filled(tofCols * tofRows, 0),
      depthMm: maskToZone(mask, tofRows, tofCols).data,
    );
  }
}

/// 테두리에서 배경을 흘려 넣고, 물이 못 닿은 곳을 전경으로 올린다.
///
/// `vision.c` 가 모폴로지 닫힘 대신 이걸 쓰는 이유는, 54x42 에서는 사람의 두
/// 다리가 한 칸 간격이라 팽창 한 번에 붙어 버리기 때문이다. 흐름을 4-이웃으로
/// 두는 것도 일부러다 — 대각선으로 이어진 전경이 벽 노릇을 한다.
List<bool> fillHoles(List<bool> mask, int rows, int cols) {
  final outside = List<bool>.filled(rows * cols, false);
  final stack = <int>[];
  void seed(int index) {
    if (mask[index] || outside[index]) return;
    outside[index] = true;
    stack.add(index);
  }

  for (var c = 0; c < cols; c++) {
    seed(c);
    seed((rows - 1) * cols + c);
  }
  for (var r = 0; r < rows; r++) {
    seed(r * cols);
    seed(r * cols + cols - 1);
  }
  while (stack.isNotEmpty) {
    final index = stack.removeLast();
    final r = index ~/ cols;
    final c = index % cols;
    if (r > 0) seed(index - cols);
    if (r < rows - 1) seed(index + cols);
    if (c > 0) seed(index - 1);
    if (c < cols - 1) seed(index + 1);
  }
  return List<bool>.generate(mask.length, (i) => mask[i] || !outside[i],
      growable: false);
}

/// 제일 큰 연결 성분만 남긴다. 차분 화면은 나머지가 전부 반점이다. 8-이웃.
List<bool> largestBlob(List<bool> mask, int rows, int cols) {
  final label = List<int>.filled(mask.length, 0);
  var best = 0;
  var bestSize = 0;
  var current = 0;
  final stack = <int>[];
  for (var start = 0; start < mask.length; start++) {
    if (!mask[start] || label[start] != 0) continue;
    current++;
    var size = 0;
    stack.add(start);
    label[start] = current;
    while (stack.isNotEmpty) {
      final index = stack.removeLast();
      size++;
      final r = index ~/ cols;
      final c = index % cols;
      for (var dr = -1; dr <= 1; dr++) {
        for (var dc = -1; dc <= 1; dc++) {
          if (dr == 0 && dc == 0) continue;
          final nr = r + dr;
          final nc = c + dc;
          if (nr < 0 || nr >= rows || nc < 0 || nc >= cols) continue;
          final n = nr * cols + nc;
          if (!mask[n] || label[n] != 0) continue;
          label[n] = current;
          stack.add(n);
        }
      }
    }
    if (size > bestSize) {
      bestSize = size;
      best = current;
    }
  }
  if (best == 0) return mask;
  return List<bool>.generate(mask.length, (i) => label[i] == best,
      growable: false);
}

/// 3x3 침식. 테두리 밖은 가장자리 값으로 본다(`BORDER_REPLICATE`).
List<bool> _erode(List<bool> mask, int rows, int cols) =>
    _morph(mask, rows, cols, erode: true);

/// 3x3 팽창.
List<bool> _dilate(List<bool> mask, int rows, int cols) =>
    _morph(mask, rows, cols, erode: false);

List<bool> _morph(List<bool> mask, int rows, int cols, {required bool erode}) {
  final out = List<bool>.filled(mask.length, false);
  for (var r = 0; r < rows; r++) {
    for (var c = 0; c < cols; c++) {
      var value = erode;
      for (var dr = -1; dr <= 1 && value == erode; dr++) {
        for (var dc = -1; dc <= 1; dc++) {
          final nr = (r + dr).clamp(0, rows - 1);
          final nc = (c + dc).clamp(0, cols - 1);
          final n = mask[nr * cols + nc];
          if (erode ? !n : n) {
            value = !erode;
            break;
          }
        }
      }
      out[r * cols + c] = value;
    }
  }
  return out;
}
