import 'package:camtest/posture_judge.dart';
import 'package:camtest/vision_frame.dart';
import 'package:flutter_test/flutter_test.dart';

/// 캡처 뒤의 배경 적응(`BackgroundModel.adapt`)을 채점한다.
///
/// 지켜야 할 것은 둘이다. 자리가 비어 있을 때 바뀐 배경(치운 의자, 올려 둔 물건)은
/// 따라가고, 가만히 앉은 사람은 아무리 오래 있어도 배경에 녹이지 않는다.
///
/// 마스크가 아니라 거리 배열을 직접 만든다. 의자처럼 사람이 아닌 것을 원하는 거리에
/// 놓아야 해서다.
void main() {
  const fps = 12.0;

  /// 합성 배경(벽·책상) 위에 블록을 얹는다. 블록은 (행, 열, 높이, 너비, 거리mm).
  Grid<double> scene([List<(int, int, int, int, double)> blocks = const []]) {
    final data = List<double>.from(syntheticBackground(zoneRows, zoneCols).data);
    for (final (r0, c0, h, w, mm) in blocks) {
      for (var r = r0; r < r0 + h; r++) {
        for (var c = c0; c < c0 + w; c++) {
          data[r * zoneCols + c] = mm;
        }
      }
    }
    return Grid<double>(data, zoneRows, zoneCols);
  }

  // 머리 + 몸통. 골든의 기준 자세(figure(4))와 같은 자리다.
  const person = [(4, 23, 5, 9, 700.0), (10, 14, 16, 26, 700.0)];
  // 등받이. 벽(2400mm) 앞 900mm, 264 zone(11.6%).
  const chair = (20, 16, 12, 22, 900.0);

  int zoneAt(int row, int col) => row * zoneCols + col;

  /// 빈 책상으로 배경을 뜨고 바른 자세로 기준까지 잡은 추적기와 그때의 시각.
  (PostureTracker, double) calibrated(
      [List<(int, int, int, int, double)> fixtures = const []]) {
    final tracker = PostureTracker();
    var t = 0.0;
    tracker.startBackground(samples: 20);
    for (var i = 0; i < 20; i++) {
      t += 1 / fps;
      tracker.update(scene(fixtures), t);
    }
    tracker.startBaseline(samples: 40);
    for (var i = 0; i < 40; i++) {
      t += 1 / fps;
      tracker.update(scene([...fixtures, ...person]), t);
    }
    expect(tracker.baseline, isNotNull, reason: 'baseline 을 못 잡았다');
    return (tracker, t);
  }

  /// 같은 장면을 [seconds] 동안 [rate] 로 넣는다. 반환 (마지막 판정, 시각).
  (PostureVerdict, double) hold(
      PostureTracker tracker, double t, Grid<double> frame, double seconds,
      {double rate = fps}) {
    PostureVerdict? last;
    final n = (seconds * rate).round();
    for (var i = 0; i < n; i++) {
      t += 1 / rate;
      last = tracker.update(frame, t);
    }
    return (last!, t);
  }

  test('가만히 앉은 사람은 10분을 있어도 배경에 녹지 않는다', () {
    final (tracker, t0) = calibrated();
    final before = List<double>.from(tracker.background.refMm!.data);

    final (verdict, _) = hold(tracker, t0, scene(person), 600);

    expect(verdict.present, isTrue);
    expect(verdict.label, 'UPRIGHT');
    expect(tracker.background.refMm!.data, before,
        reason: '사람이 있는 동안 배경이 움직였다');
  });

  test('비어 있는 동안 치운 의자는 배경에서 빠진다', () {
    final (tracker, t0) = calibrated([chair]);
    final seat = zoneAt(25, 26);
    expect(tracker.background.refMm!.data[seat], 900.0);

    // 사람이 떠나며 의자도 치웠다. 기다리는 동안에는 배경이 그대로다.
    var (verdict, t) = hold(tracker, t0, scene(), 2.0);
    expect(verdict.present, isFalse);
    expect(tracker.background.refMm!.data[seat], 900.0,
        reason: '비운 지 $bgEmptyHoldS 초가 안 됐는데 배경이 움직였다');

    (verdict, t) = hold(tracker, t, scene(), 28.0);
    expect(tracker.background.refMm!.data[seat], closeTo(backgroundMm, 50));

    // 의자가 있던 자리에 800mm 로 무엇이 들어온다. 배경이 900mm 에 고정돼 있었다면
    // 침입이 100mm 라 문턱(150mm)에 못 미쳐 자리비움으로 나온다.
    (verdict, t) = hold(tracker, t, scene([(20, 16, 12, 22, 800.0)]), 0.5);
    expect(verdict.present, isTrue, reason: '치운 의자 자리가 배경에 남아 있다');
  });

  test('비어 있을 때 올려 둔 작은 물건은 배경이 된다', () {
    final (tracker, t0) = calibrated();
    final cup = scene([(33, 30, 3, 4, 600.0)]); // 책상 위 12 zone

    var (verdict, t) = hold(tracker, t0, cup, 1.0);
    expect(verdict.features.occupancy, greaterThan(0.0));

    (verdict, t) = hold(tracker, t, cup, 14.0);
    expect(verdict.features.occupancy, 0.0, reason: '올려 둔 물건이 계속 침입으로 남는다');
  });

  test('반사가 잠깐 빠져도 배경을 멀리 끌지 않는다', () {
    final (tracker, t0) = calibrated();
    final hole = zoneAt(7, 7);
    final dropout = scene();
    for (var r = 5; r < 10; r++) {
      for (var c = 5; c < 10; c++) {
        dropout.data[zoneAt(r, c)] = double.nan;
      }
    }

    var (_, t) = hold(tracker, t0, dropout, 6.0);
    expect(tracker.background.refMm!.data[hole], backgroundMm);

    // 적응이 도는 동안 bgMissingS 를 넘게 계속 빠져 있으면 그때 '반사 없음'이다.
    (_, t) = hold(tracker, t, dropout, 10.0);
    expect(tracker.background.refMm!.data[hole], maxRangeMm);
  });

  test('적응 속도는 넣는 횟수가 아니라 흐른 시간으로 정해진다', () {
    final seat = zoneAt(25, 26);
    final (slow, s0) = calibrated([chair]);
    final (fast, f0) = calibrated([chair]);

    // 같은 8초를 12fps 와, 같은 프레임을 열 번씩 넣는 120fps 로 흘린다.
    hold(slow, s0, scene(), 8.0);
    hold(fast, f0, scene(), 8.0, rate: 120);

    final a = slow.background.refMm!.data[seat];
    final b = fast.background.refMm!.data[seat];
    expect(a, greaterThan(900.0 + 100), reason: '적응이 아예 안 돌았다');
    expect(a, lessThan(backgroundMm - 100), reason: '8초 만에 다 따라갔다 - 너무 빠르다');
    expect((a - b).abs(), lessThan(20.0), reason: '12fps $a vs 120fps $b');
  });
}
