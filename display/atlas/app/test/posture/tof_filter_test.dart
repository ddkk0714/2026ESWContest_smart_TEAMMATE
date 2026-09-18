// 카메라 → 54x42 ToF 화면 필터.
//
// 원본(`pico_esp32-cam_ftdi` 의 `tools/camera_source.py`)에는 골든 벡터가 없다.
// 그래서 숫자를 그대로 베끼는 대신, 그 파일이 왜 그 순서인지 설명해 둔 성질들을
// 붙잡는다 — 배경을 세우기 전에는 아무것도 내지 않는다, 가만히 있으면 비어
// 있다, 절댓값은 축약 전에 취한다, 구멍 채움이 열림보다 먼저다.
import 'dart:typed_data';

import 'package:deskmate_display/posture/tof_filter.dart';
import 'package:flutter_test/flutter_test.dart';

const _w = 160;
const _h = 120;

Uint8List _flat(int value) => Uint8List(_w * _h)..fillRange(0, _w * _h, value);

/// 가운데에 밝기가 다른 사각형 하나.
Uint8List _withBox(int background, int box,
    {int x0 = 40, int y0 = 30, int x1 = 120, int y1 = 90}) {
  final image = _flat(background);
  for (var y = y0; y < y1; y++) {
    for (var x = x0; x < x1; x++) {
      image[y * _w + x] = box;
    }
  }
  return image;
}

/// 배경 모델이 설 때까지 같은 장면을 넣어 준다.
void _settle(TofFilter filter, Uint8List scene) {
  for (var i = 0; i < 25; i++) {
    filter.add(_w, _h, scene);
  }
}

void main() {
  test('배경을 세우기 전에는 빈 격자만 낸다', () {
    final filter = TofFilter();
    final scene = _withBox(100, 220);

    expect(filter.warmingUp, isTrue);
    final first = filter.add(_w, _h, scene);
    expect(first.width, tofCols);
    expect(first.height, tofRows);
    // 배경이 없으면 무엇이 전경인지 말할 수 없다. 0 을 내는 게 맞다.
    expect(first.mask!.every((v) => v == 0), isTrue);
    expect(first.coverage!.every((v) => v == 0), isTrue);

    _settle(filter, scene);
    expect(filter.warmingUp, isFalse);
  });

  test('장면이 그대로면 전경이 없다', () {
    final filter = TofFilter();
    final scene = _withBox(100, 220);
    _settle(filter, scene);

    final snapshot = filter.add(_w, _h, scene);
    expect(snapshot.mask!.every((v) => v == 0), isTrue);
  });

  test('배경과 밝기가 다른 물체가 들어오면 그 자리가 전경이 된다', () {
    final filter = TofFilter();
    _settle(filter, _flat(100));

    final snapshot = filter.add(_w, _h, _withBox(100, 200));
    final mask = snapshot.mask!;
    expect(mask.any((v) => v != 0), isTrue);

    // 사각형 한가운데는 반드시 잡히고, 네 귀퉁이는 비어 있어야 한다.
    int at(int col, int row) => mask[row * tofCols + col];
    expect(at(tofCols ~/ 2, tofRows ~/ 2), 1);
    expect(at(0, 0), 0);
    expect(at(tofCols - 1, 0), 0);
    expect(at(0, tofRows - 1), 0);
    expect(at(tofCols - 1, tofRows - 1), 0);
  });

  // 원본 주석이 짚는 실패다. 밝기 차이는 피사체가 배경과 밝기로 다를 때만 잡힌다.
  test('배경과 밝기가 같으면 물체가 있어도 안 잡힌다', () {
    final filter = TofFilter();
    _settle(filter, _flat(100));

    final snapshot = filter.add(_w, _h, _flat(100));
    expect(snapshot.mask!.every((v) => v == 0), isTrue);
  });

  test('coverage 는 차이 크기를 따라간다', () {
    final near = TofFilter();
    _settle(near, _flat(100));
    final small = near.add(_w, _h, _withBox(100, 130)).coverage!;

    final far = TofFilter();
    _settle(far, _flat(100));
    final big = far.add(_w, _h, _withBox(100, 230)).coverage!;

    final middle = (tofRows ~/ 2) * tofCols + tofCols ~/ 2;
    expect(big[middle], greaterThan(small[middle]));
    expect(big[middle], lessThanOrEqualTo(255));
  });

  test('임계값을 올리면 약한 차이는 떨어져 나간다', () {
    final loose = TofFilter(threshold: 20);
    _settle(loose, _flat(100));
    final strict = TofFilter(threshold: 200);
    _settle(strict, _flat(100));

    final scene = _withBox(100, 150); // 차이 50
    expect(loose.add(_w, _h, scene).mask!.any((v) => v != 0), isTrue);
    expect(strict.add(_w, _h, scene).mask!.every((v) => v == 0), isTrue);
  });

  test('기준 다시 잡기는 배경을 처음부터 세운다', () {
    final filter = TofFilter();
    _settle(filter, _flat(100));
    expect(filter.warmingUp, isFalse);

    filter.resetBackground();
    expect(filter.warmingUp, isTrue);
    expect(filter.add(_w, _h, _withBox(100, 200)).mask!.every((v) => v == 0),
        isTrue);
  });

  test('구멍 채움은 테두리에 닿지 않는 빈 칸만 메운다', () {
    const rows = 5;
    const cols = 5;
    // 가운데가 뚫린 고리.
    final ring = <bool>[
      for (var r = 0; r < rows; r++)
        for (var c = 0; c < cols; c++)
          (r >= 1 && r <= 3 && c >= 1 && c <= 3) && !(r == 2 && c == 2),
    ];
    final filled = fillHoles(ring, rows, cols);
    expect(filled[2 * cols + 2], isTrue); // 구멍은 메워지고
    expect(filled[0], isFalse); // 바깥은 그대로
  });

  // 4-이웃으로 흘리는 것이 일부러라는 점. 대각선으로 이어진 전경은 벽이다.
  test('대각선으로 이어진 전경은 물을 막는다', () {
    const rows = 3;
    const cols = 3;
    final diagonal = <bool>[
      true, false, false, //
      false, true, false, //
      false, false, true, //
    ];
    final filled = fillHoles(diagonal, rows, cols);
    // 오른쪽 위·왼쪽 아래는 테두리에 닿아 있으므로 배경으로 남는다.
    expect(filled[2], isFalse);
    expect(filled[6], isFalse);
  });

  test('최대 블롭만 남기고 반점은 버린다', () {
    const rows = 4;
    const cols = 6;
    final speckled = List<bool>.filled(rows * cols, false);
    for (var r = 0; r < 3; r++) {
      for (var c = 0; c < 3; c++) {
        speckled[r * cols + c] = true; // 9칸짜리 덩어리
      }
    }
    speckled[3 * cols + 5] = true; // 외딴 반점

    final kept = largestBlob(speckled, rows, cols);
    expect(kept[0], isTrue);
    expect(kept[3 * cols + 5], isFalse);
    expect(kept.where((v) => v).length, 9);
  });

  test('입력 크기가 달라도 격자는 항상 54x42 다', () {
    final filter = TofFilter();
    final wide = Uint8List(320 * 240)..fillRange(0, 320 * 240, 100);
    for (var i = 0; i < 25; i++) {
      filter.add(320, 240, wide);
    }
    final snapshot = filter.add(320, 240, wide);
    expect(snapshot.width, 54);
    expect(snapshot.height, 42);
    expect(snapshot.coverage!.length, 54 * 42);
    expect(snapshot.depthMm!.length, 54 * 42);
  });

  test('망가진 프레임은 던지지 않고 빈 격자를 낸다', () {
    final filter = TofFilter();
    final snapshot = filter.add(_w, _h, Uint8List(10));
    expect(snapshot.mask!.every((v) => v == 0), isTrue);
  });
}
