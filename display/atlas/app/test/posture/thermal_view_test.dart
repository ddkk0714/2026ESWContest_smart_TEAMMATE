// 열화상 화면(`thermal_pose.py` 이식)의 계산 쪽.
//
// 원본에 골든 벡터가 없어 비트 단위로 맞출 수는 없다. 대신 그 도구가 왜 전역
// 평활화 대신 CLAHE 를, 왜 타일 LUT 를 섞는지 설명해 둔 성질을 붙잡는다.
import 'dart:typed_data';

import 'package:deskmate_display/posture/thermal_view.dart';
import 'package:deskmate_display/posture/zone_image.dart';
import 'package:flutter_test/flutter_test.dart';

Uint8List _flat(int w, int h, int value) =>
    Uint8List(w * h)..fillRange(0, w * h, value);

/// 왼쪽에서 오른쪽으로 좁은 폭만 변하는 그림. 전역 평활화로는 잘 안 펴진다.
Uint8List _lowContrastGradient(int w, int h, {int lo = 100, int hi = 130}) {
  final out = Uint8List(w * h);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      out[y * w + x] = lo + ((hi - lo) * x / (w - 1)).round();
    }
  }
  return out;
}

void main() {
  group('CLAHE', () {
    test('평평한 그림은 평평하게 남는다', () {
      final out = clahe(_flat(54, 42, 100), 54, 42);
      expect(out.length, 54 * 42);
      expect(out.every((v) => v == out.first), isTrue);
    });

    test('좁은 대비를 넓힌다', () {
      const w = 54;
      const h = 42;
      final src = _lowContrastGradient(w, h);
      final out = clahe(src, w, h);

      int spread(List<int> v) =>
          v.reduce((a, b) => a > b ? a : b) - v.reduce((a, b) => a < b ? a : b);
      expect(spread(out), greaterThan(spread(src)));
    });

    test('타일 수로 안 나누어떨어지는 크기도 그대로 돌려준다', () {
      // 54/8 · 42/8 은 둘 다 정수가 아니다. 이 프로젝트가 쓰는 크기가 그렇다.
      final out = clahe(_lowContrastGradient(54, 42), 54, 42);
      expect(out.length, 54 * 42);
      expect(out.every((v) => v >= 0 && v <= 255), isTrue);
    });

    test('타일 경계에서 값이 튀지 않는다', () {
      // 타일 LUT 를 안 섞으면 여기서 계단이 생긴다. 원본이 이중선형으로 섞는 이유다.
      const w = 64;
      const h = 64;
      final out = clahe(_lowContrastGradient(w, h, lo: 40, hi: 210), w, h);

      var worst = 0;
      for (var y = 0; y < h; y++) {
        for (var x = 1; x < w; x++) {
          final step = (out[y * w + x] - out[y * w + x - 1]).abs();
          if (step > worst) worst = step;
        }
      }
      // 입력이 한 칸에 약 2.7 씩 오르는 매끄러운 경사다. 경계에서 계단이 지면
      // 이 값이 수십으로 뛴다.
      expect(worst, lessThan(24));
    });

    test('clipLimit 을 낮추면 덜 편다', () {
      const w = 54;
      const h = 42;
      final src = _lowContrastGradient(w, h);
      int spread(List<int> v) =>
          v.reduce((a, b) => a > b ? a : b) - v.reduce((a, b) => a < b ? a : b);

      final tight = clahe(src, w, h, clipLimit: 1.0);
      final loose = clahe(src, w, h, clipLimit: 40.0);
      expect(spread(loose), greaterThanOrEqualTo(spread(tight)));
    });
  });

  group('thermalZones', () {
    test('입력 크기와 무관하게 54x42 격자를 낸다', () {
      final frame = thermalZones(_lowContrastGradient(320, 240), 320, 240);
      expect(frame.cols, tofCols);
      expect(frame.rows, tofRows);
      expect(frame.heat.length, tofCols * tofRows);
      expect(frame.heat.every((v) => v >= 0.0 && v <= 1.0), isTrue);
    });

    test('원본 분포를 그대로 보고한다 — 이 셋이 그림이 쓸 만한지 말해 준다', () {
      const w = 40;
      const h = 30;
      final src = _flat(w, h, 100);
      src[0] = 10;
      src[1] = 240;

      final frame = thermalZones(src, w, h);
      expect(frame.rawMin, 10);
      expect(frame.rawMax, 240);
      expect(frame.spread, 230);
      expect(frame.rawMean, closeTo(100, 2));
    });

    test('가려진 렌즈처럼 평평하면 spread 가 0 이다', () {
      final frame = thermalZones(_flat(160, 120, 8), 160, 120);
      expect(frame.spread, 0);
    });

    test('망가진 프레임은 던지지 않는다', () {
      final frame = thermalZones(Uint8List(10), 160, 120);
      expect(frame.heat.length, tofCols * tofRows);
      expect(frame.heat.every((v) => v == 0.0), isTrue);
    });
  });

  group('zoneBoundaries', () {
    // 한 칸이 정수 픽셀이 아닐 때(800/54 = 14.81) 고정 간격으로 선을 그으면
    // 오른쪽 끝에서 세 칸이나 밀린다.
    test('첫 선과 마지막 선이 그림의 양 끝에 붙는다', () {
      final xs = zoneBoundaries(800, 54);
      expect(xs.first, 0);
      expect(xs.last, 800);
    });

    test('선이 단조 증가하고 칸 수만큼 있다', () {
      final xs = zoneBoundaries(800, 54);
      expect(xs.length, 55);
      for (var i = 1; i < xs.length; i++) {
        expect(xs[i], greaterThan(xs[i - 1]));
      }
    });

    test('딱 떨어지는 크기에서 한 픽셀 밀리지 않는다', () {
      final xs = zoneBoundaries(108, 54);
      expect(xs[1], 2);
      expect(xs[27], 54);
    });
  });
}
