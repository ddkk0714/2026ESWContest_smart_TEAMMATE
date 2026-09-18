/// 사람이 키를 누르지 않는 캘리브레이션.
///
/// PC 뷰어는 SPACE 두 번으로 두 단계를 몰았다. 보드에는 키보드가 없으므로 같은
/// 순서를 시간으로 돌린다. **순서가 핵심이고 그건 그대로 지킨다.**
///
///     clear -> settle -> background -> sit -> baseline -> live
///     (비켜)   (센서 b)  (zone 기준)  (앉기)  (자세 기준)
///
/// 1단계가 두 겹인 이유: 센서 자신의 배경(ESP 는 `b` 한 번에 AEC 재실행 + 배경
/// 재캡처)이 먼저 끝나야 zone 기준 거리를 뜰 수 있다. 사람이 섞인 배경 위에 기준이
/// 굳으면 그 뒤로는 무엇을 해도 사람을 못 찾는다.
///
/// `espcam_with_decision` 의 `pi/deskmate_posture/calibration.py` 를 옮긴 것이다.
library;

import 'posture_judge.dart';

const String stageClear = 'clear';
const String stageSettle = 'settle';
const String stageBackground = 'background';
const String stageSit = 'sit';
const String stageBaseline = 'baseline';
const String stageLive = 'live';

const String bgDoneMark = 'bg captured'; // ESP 가 끝났다고 알리는 문구
const double settleMinS = 1.5; // 'b' 를 보낸 뒤 최소 대기
const double settleMaxS = 10.0; // 완료 신호가 없어도 이만큼이면 넘어간다

class Calibrator {
  Calibrator({
    required this.tracker,
    required this.sendLine,
    required this.markCount,
    this.clearS = 8.0,
    this.sitS = 10.0,
    this.bgFrames = 30,
    this.baselineFrames = 60,
    this.baselineWarnS = 30.0,
  });

  final PostureTracker tracker;

  /// 센서에 한 줄 보낸다(`b`). 못 보내도 캘리브레이션은 시간으로 넘어간다.
  final void Function(String line) sendLine;

  /// 지금까지 본 `bg captured` 문구 개수. 안 보내주는 센서도 있어 상한을 둔다.
  final int Function() markCount;

  final double clearS;
  final double sitS;
  final int bgFrames;
  final int baselineFrames;

  /// 사람이 안 앉으면 baseline 은 영원히 안 찬다. 포기하지는 않되 왜 안 끝나는지
  /// 화면에 말해 준다.
  final double baselineWarnS;

  String stage = stageClear;
  double _at = 0.0;
  int _marks = 0;
  bool _started = false;

  bool get live => stage == stageLive;

  /// 처음부터 다시. 이전 기준은 버린다 - 남겨 두면 새 배경을 뜨는 동안에도 옛
  /// baseline 으로 판정이 계속 나와서, 화면은 멀쩡한데 값이 틀린 상태가 된다.
  void restart(double now) {
    tracker.background.refMm = null;
    tracker.background.captured = false;
    tracker.baseline = null;
    tracker.nods.reset();
    stage = stageClear;
    _at = now;
    _marks = 0;
    _started = true;
  }

  /// 루프에서 `tracker.update()` **다음에** 부른다. `startBackground()` 와
  /// `startBaseline()` 은 다음 프레임부터 효력이 생기므로 순서가 뒤집히면 한
  /// 프레임이 엉뚱한 단계에 들어간다.
  void step(double now) {
    if (!_started) {
      restart(now);
      return;
    }
    final elapsed = now - _at;

    switch (stage) {
      case stageClear:
        if (elapsed >= clearS) {
          _marks = markCount();
          sendLine('b');
          _enter(stageSettle, now);
        }
      case stageSettle:
        // 완료 문구를 기다리되, 안 보내주는 센서도 있으므로 상한을 둔다.
        final done = markCount() > _marks;
        if (elapsed >= settleMinS && (done || elapsed >= settleMaxS)) {
          tracker.startBackground(samples: bgFrames);
          _enter(stageBackground, now);
        }
      case stageBackground:
        if (tracker.background.captured) _enter(stageSit, now);
      case stageSit:
        if (elapsed >= sitS) {
          tracker.startBaseline(samples: baselineFrames);
          _enter(stageBaseline, now);
        }
      case stageBaseline:
        if (tracker.baseline != null) _enter(stageLive, now);
    }
  }

  /// 화면에 띄울 한 줄. 남은 초를 세어 준다. live 면 빈 문자열.
  String hint(double now) {
    double left(double span) {
      final rest = span - (now - _at);
      return rest < 0 ? 0 : rest;
    }

    switch (stage) {
      case stageClear:
        return '1/2 배경 측정 — 자리에서 비켜 주세요 (${left(clearS).toStringAsFixed(0)}초)';
      case stageSettle:
        return '1/2 센서가 노출을 다시 잡는 중입니다';
      case stageBackground:
        return '1/2 빈 책상의 기준 거리를 재는 중입니다';
      case stageSit:
        return '2/2 바른 자세로 앉아 주세요 (${left(sitS).toStringAsFixed(0)}초)';
      case stageBaseline:
        if (now - _at >= baselineWarnS) {
          // 앉은 프레임만 세므로, 안 차면 사람이 안 보인다는 뜻이다.
          return '2/2 자세 기준을 못 채우는 중 — 화각 안에 앉아 있는지 확인하세요';
        }
        return '2/2 바른 자세 기준을 재는 중입니다';
      default:
        return '';
    }
  }

  void _enter(String next, double now) {
    stage = next;
    _at = now;
  }
}
