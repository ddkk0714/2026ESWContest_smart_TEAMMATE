// 보드에 직접 꽂힌 키보드를 앱이 캡처해 키스트로크 특징을 뽑는다.
//
// collector/capture.py + collector/features.py 를 Dart 로 옮긴 것이고,
// display/web/js/keycap.js 와 같은 계산이다. 상수(flight 상한 2.0s · idle 간격 3.0s ·
// dwell 상한 2000ms)와 반올림 자릿수를 맞춰 세 노드의 값을 그대로 비교할 수 있게 했다.
//
// 왜 앱 안에서 하나:
//   ATLAS 는 Yocto 최소 이미지라 Python 런타임이 없다. 보드 키보드로 실기 확인을 하려면
//   앱이 직접 key 이벤트를 받아 계산하는 수밖에 없다.
//
// 프라이버시 원칙(타협 불가):
//   - 이벤트 목록에는 시각과 '종류'(char/space/backspace/enter/other)만 남는다.
//   - 문자 값(`KeyEvent.character`)은 종류를 정할 때 한 번 보고 버린다. 저장하지 않는다.
//   - 물리 키 식별자는 '지금 눌려 있는 키' 맵에만 잠깐 있다가 뗄 때 지운다.
//     dwell 을 정확히 짝지으려면 필요하고, 시퀀스로 남지 않으므로 입력 내용을 복원할 수 없다.
import 'dart:math' as math;

import 'package:flutter/services.dart';

import 'display_state.dart';

/// collector/capture.py 의 KeyKind 와 같은 집합.
enum KeyKind { char, space, backspace, enter, other }

/// KeyEvent → 종류. 문자 값은 반환하지도 저장하지도 않는다.
KeyKind classifyKeyEvent(KeyEvent event) {
  final logical = event.logicalKey;
  if (logical == LogicalKeyboardKey.space) return KeyKind.space;
  if (logical == LogicalKeyboardKey.backspace) return KeyKind.backspace;
  if (logical == LogicalKeyboardKey.enter ||
      logical == LogicalKeyboardKey.numpadEnter) {
    return KeyKind.enter;
  }
  // 출력되는 문자 하나면 char. pynput 이 KeyCode 를 CHAR 로 보는 것과 같은 기준이다.
  final character = event.character;
  if (character != null && character.isNotEmpty) {
    if (character == ' ') return KeyKind.space;
    if (character.runes.length == 1) return KeyKind.char;
  }
  return KeyKind.other;
}

class _KeyEventRecord {
  const _KeyEventRecord(this.t, this.kind, this.down);
  final double t; // 초
  final KeyKind kind;
  final bool down;
}

/// 키 이벤트를 모아 [windowS] 초 창의 특징을 만든다.
class KeystrokeCapture {
  KeystrokeCapture({
    this.node = 'pi-keyboard',
    this.windowS = 60,
    this.flightGapMaxS = 2.0,
    this.idleGapS = 3.0,
    this.maxEvents = 20000,
  });

  final String node;
  final int windowS;

  /// 이보다 큰 간격은 리듬 통계에서 뺀다. 잠깐 멈춘 걸 '느린 타이핑' 으로 읽지 않기 위해서다.
  final double flightGapMaxS;

  /// 이 이상 공백이면 idle 로 센다.
  final double idleGapS;
  final int maxEvents;

  final List<_KeyEventRecord> _events = [];

  /// 지금 눌려 있는 물리 키 → 종류. auto-repeat 억제와 dwell 짝짓기에 쓰고 뗄 때 지운다.
  final Map<PhysicalKeyboardKey, KeyKind> _held = {};

  int _pressTotal = 0;
  double? _lastPressT;

  int get pressTotal => _pressTotal;
  bool get hasData => _pressTotal > 0;
  double? get lastPressT => _lastPressT;

  /// 눌림. 이미 눌려 있는 키면 auto-repeat 로 보고 무시한다(1타건 처리).
  bool press(PhysicalKeyboardKey key, KeyKind kind, double tS) {
    if (_held.containsKey(key)) return false;
    _held[key] = kind;
    _events.add(_KeyEventRecord(tS, kind, true));
    _pressTotal += 1;
    _lastPressT = tS;
    _trim(tS);
    return true;
  }

  /// 뗌. press 없이 들어온 release 는 dwell 짝짓기에서 자연스럽게 버려진다.
  void release(PhysicalKeyboardKey key, double tS) {
    final kind = _held.remove(key);
    if (kind == null) return;
    _events.add(_KeyEventRecord(tS, kind, false));
    _trim(tS);
  }

  /// Flutter 의 하드웨어 키 이벤트를 그대로 받는다. 반환값은 '소비했는지' 가 아니라
  /// 새 타건으로 셌는지다. 이벤트는 소비하지 않는다.
  bool handle(KeyEvent event, double tS) {
    // KeyRepeatEvent 는 auto-repeat 다. 규약대로 1타건으로 처리하므로 무시한다.
    if (event is KeyRepeatEvent) return false;
    if (event is KeyDownEvent) {
      return press(event.physicalKey, classifyKeyEvent(event), tS);
    }
    if (event is KeyUpEvent) {
      release(event.physicalKey, tS);
    }
    return false;
  }

  void _trim(double nowS) {
    // 창의 두 배까지만 남긴다. 그보다 오래된 이벤트는 어떤 계산에도 안 쓰인다.
    final cut = nowS - windowS * 2;
    while (_events.isNotEmpty && _events.first.t < cut) {
      _events.removeAt(0);
    }
    while (_events.length > maxEvents) {
      _events.removeAt(0);
    }
  }

  /// `[nowS - windowS, nowS]` 구간의 특징. 반환 형태는 collector 의 to_payload() 와 같다.
  KeystrokeMetrics extract(double nowS, {DateTime? stampedAt}) {
    final startT = nowS - windowS;
    final inWindow =
        _events.where((e) => e.t >= startT && e.t <= nowS).toList(growable: false);
    final downs = inWindow.where((e) => e.down).toList(growable: false);
    final timestamp = stampedAt ?? DateTime.now();

    if (downs.isEmpty) {
      // 입력이 없으면 '공백 100%'. 나머지는 0 이 아니라 null 로 둔다.
      // 0 을 넣으면 화면이 '리듬 완벽' 으로 잘못 읽힌다.
      return KeystrokeMetrics(
        node: node,
        windowS: windowS,
        eventCount: 0,
        idleRatio: 1.0,
        typingActive: false,
        valid: true,
        timestamp: timestamp,
      );
    }

    final backspaces = downs.where((e) => e.kind == KeyKind.backspace).length;

    // dwell: 같은 종류의 press → 다음 release 를 FIFO 로 짝짓는다.
    final dwellMs = <double>[];
    final pending = <KeyKind, List<double>>{};
    for (final e in inWindow) {
      if (e.down) {
        (pending[e.kind] ??= <double>[]).add(e.t);
      } else {
        final queue = pending[e.kind];
        if (queue != null && queue.isNotEmpty) {
          final dt = (e.t - queue.removeAt(0)) * 1000.0;
          // 2초 넘게 눌려 있던 건 타건이 아니라 눌러둔 키로 본다.
          if (dt > 0 && dt < 2000) dwellMs.add(dt);
        }
      }
    }
    final dwell = _stat(dwellMs);

    // flight: 연속 keydown 간격. 멈춤(> flightGapMaxS)은 리듬 통계에서 뺀다.
    final downTimes = downs.map((e) => e.t).toList(growable: false);
    final flightMs = <double>[];
    for (var i = 1; i < downTimes.length; i++) {
      final gap = downTimes[i] - downTimes[i - 1];
      if (gap <= flightGapMaxS) flightMs.add(gap * 1000.0);
    }
    final flight = _stat(flightMs);

    // idle 비율: 창 앞뒤 여백 + 입력 사이 idleGapS 초과분.
    var idle = math.max(0.0, downTimes.first - startT) +
        math.max(0.0, nowS - downTimes.last);
    for (var i = 1; i < downTimes.length; i++) {
      final gap = downTimes[i] - downTimes[i - 1];
      if (gap > idleGapS) idle += gap;
    }

    return KeystrokeMetrics(
      node: node,
      windowS: windowS,
      eventCount: downs.length,
      dwellMeanMs: dwell?.mean,
      dwellStdMs: dwell?.std,
      flightMeanMs: flight?.mean,
      flightStdMs: flight?.std,
      flightCv: flight == null || flight.mean == 0
          ? null
          : _round(flight.std / flight.mean, 3),
      idleRatio: _round(math.min(1.0, idle / windowS), 4),
      correctionRate: _round(backspaces / downs.length, 4),
      typingActive: true,
      valid: true,
      timestamp: timestamp,
    );
  }
}

class _Stat {
  const _Stat(this.mean, this.std);
  final double mean;
  final double std;
}

/// statistics.fmean / statistics.pstdev(모집단 표준편차)와 같은 정의.
/// 표본이 없으면 0 이 아니라 null 이다 — 화면에서 '--' 로 나가야 한다.
_Stat? _stat(List<double> xs) {
  if (xs.isEmpty) return null;
  if (xs.length == 1) return _Stat(_round(xs.first, 2), 0);
  final mean = xs.reduce((a, b) => a + b) / xs.length;
  var acc = 0.0;
  for (final x in xs) {
    acc += (x - mean) * (x - mean);
  }
  return _Stat(_round(mean, 2), _round(math.sqrt(acc / xs.length), 2));
}

double _round(double v, int digits) {
  final m = math.pow(10, digits);
  return (v * m).round() / m;
}
