/// 보드에 직접 물린 ESP32-CAM 에서 자세를 읽는다. 네트워크도 Pi 4 도 없다.
///
///     ESP32-CAM ──UART 921600──> Pi 5 GPIO15 ──PeripheralManager──> 이 파일
///                                                                    ├ 프레임 디코딩
///                                                                    ├ 판정
///                                                                    └ 화면
///
/// `pi/deskmate_posture/node.py` 가 Pi 4 에서 하던 일을 앱 안에서 한다. ATLAS 는
/// Python 을 앱 런타임으로 지원하지 않으므로 이 경로가 보드에서 유일하게 가능한
/// 방법이다.
library;

import 'dart:async';

import 'calibration.dart';
import '../diag.dart';
import 'posture_judge.dart';
import 'posture_source.dart';
import 'posture_state.dart';
import 'uart_link.dart';
import 'vision_frame.dart';

/// 마지막 프레임이 이보다 묵으면 링크가 끊긴 것으로 본다.
const double staleAfterS = 1.0;

/// 라벨 → 화면 enum. 판정은 문자열을 쓰고 화면은 enum 을 쓴다.
const Map<String, PostureLabel> _labels = {
  'UPRIGHT': PostureLabel.upright,
  'SLUMP': PostureLabel.slump,
  'RECLINE': PostureLabel.recline,
  'DROWSY': PostureLabel.drowsy,
  'ABSENT': PostureLabel.absent,
  'BASELINE': PostureLabel.baseline,
  'UNKNOWN': PostureLabel.unknown,
};

const Map<String, String> _labelText = {
  'UPRIGHT': '바른 자세',
  'SLUMP': '엎드림',
  'RECLINE': '뒤로 젖힘',
  'DROWSY': '졸음 (꾸벅임)',
  'ABSENT': '자리 비움',
  'BASELINE': '기준 측정 중',
  'UNKNOWN': '기준 없음',
};

/// 판정이 실제로 본 축만 적는다. `envelope.py` 의 `reasons_for()` 와 같다.
List<String> reasonsFor(PostureVerdict verdict) {
  final out = <String>['posture_only'];
  switch (verdict.label) {
    case 'SLUMP':
      out.add('head_dropped_and_held');
    case 'RECLINE':
      out.add('moved_away_from_sensor');
    case 'DROWSY':
      out.add('nods_per_min_${verdict.nodRate.toStringAsFixed(1)}');
    case 'ABSENT':
      out.add('desk_empty');
  }
  if (verdict.note.isNotEmpty) out.add(verdict.note);
  final ratio = verdict.parts['headw_ratio'] ?? 0.0;
  if (ratio != 0.0) out.add('head_width_ratio_${ratio.toStringAsFixed(2)}');
  return out;
}

class SerialPostureSource implements PostureSource {
  /// 기본은 장치 파일 직접 읽기다. ATLAS 의 UART D-Bus Read 는 실제 수신량과
  /// 무관한 바이트를 섞어 돌려줘서 프레임이 하나도 안 맞았다(실기 확인).
  SerialPostureSource({UartTransport? uart, int baud = visionBaud})
      : _uart = uart ?? FileUart(),
        _baud = baud {
    _tracker = PostureTracker();
    _calibrator = Calibrator(
      tracker: _tracker,
      sendLine: (line) => _uart.write([...line.codeUnits, 0x0A]),
      markCount: () => _decoder.bgMarks,
    );
  }

  final UartTransport _uart;
  final int _baud;
  final VisionDecoder _decoder = VisionDecoder();
  late final PostureTracker _tracker;
  late final Calibrator _calibrator;

  // 벽시계는 뒤로 갈 수 있다. 지속 시간(엎드림·꾸벅임)을 재는 축이라 단조 시계를 쓴다.
  final Stopwatch _clock = Stopwatch()..start();

  Timer? _pump;
  PostureVerdict? _verdict;
  // 화면 표시용. 판정은 마스크만 쓰지만 사람 눈에는 coverage 가 더 읽힌다.
  List<int>? _lastMask;
  List<int>? _lastCoverage;
  List<double>? _lastDepth;
  int _visionW = 0;
  int _visionH = 0;
  double _lastFrameAt = -1e9;
  int _frames = 0;
  int _sequence = 0;
  double _fps = 0.0;
  double _lastTick = 0.0;
  double _lastLogAt = 0.0;
  double _lastFeatureLogAt = 0.0;
  int _bytes = 0;
  String? _error;
  bool _opening = false;
  bool _busy = false;

  double get _now => _clock.elapsedMicroseconds / 1e6;
  bool get _stale => _now - _lastFrameAt > staleAfterS;

  @override
  String get label => 'ESP32-CAM · UART';

  @override
  bool get canCalibrate => true;

  /// 포트를 열고 읽기 루프를 돈다. 실패하면 `UartUnavailable`.
  Future<void> start() async {
    if (_opening) return;
    _opening = true;
    await _uart.open(_baud);
    // 센서는 12fps 지만 UART 는 조각으로 온다. 자주 긁어 버퍼가 넘치지 않게 한다.
    _pump = Timer.periodic(const Duration(milliseconds: 20), (_) => _drain());
  }

  Future<void> _drain() async {
    // D-Bus 왕복이 느려지면 호출이 겹친다. 겹치면 버퍼 순서가 꼬인다.
    if (_busy) return;
    _busy = true;
    try {
      final chunk = await _uart.read(4096);
      if (chunk.isNotEmpty) {
        _bytes += chunk.length;
        for (final frame in _decoder.feed(chunk)) {
          _visionW = frame.width;
          _visionH = frame.height;
          if (frame.type == typeCoverage) _lastCoverage = frame.payload;
          if (frame.type != typeMask) continue;
          _lastMask = frame.payload;
          _onFrame(frame);
        }
      }
      // 프레임이 없어도 캘리브레이션 시계는 돌아야 한다 - 사람이 비켜 있는
      // 동안에는 마스크가 비어 프레임이 와도 아무 일도 안 일어난다.
      _calibrator.step(_now);
      _error = null;
      _logProgress();
    } catch (error) {
      _error = '$error';
    } finally {
      _busy = false;
    }
  }

  /// 5초에 한 줄. 보드에는 앱 표준출력이 안 남아서 이 로그가 유일한 창구다.
  ///
  /// 링크가 이상할 때 무엇을 봐야 하는지 한 줄에 담는다 - 바이트는 오는데 프레임이
  /// 안 되면 속도(stty)나 CRC 문제고, 바이트 자체가 없으면 배선이나 권한 문제다.
  void _logProgress() {
    final now = _now;
    if (now - _lastLogAt < 5.0) return;
    _lastLogAt = now;
    final verdict = _verdict;
    diag.write('link: 바이트 $_bytes · 프레임 $_frames · ${_fps.toStringAsFixed(1)}fps'
        ' · 버린 ${_decoder.droppedBytes} · CRC오류 ${_decoder.badCrc}'
        ' · 단계 ${_calibrator.stage}'
        ' · 판정 ${verdict?.label ?? "없음"}');
  }

  /// 판정이 지금 무엇을 보고 그렇게 결정했는지 1초에 한 줄.
  ///
  /// 정확도가 낮을 때 임계값을 손대기 전에 **어느 축이 흔들리는지** 봐야 한다.
  /// 엎드림과 젖힘을 가르는 것은 거리(rel) 하나뿐이고, 그 값은 마스크의 겉보기
  /// 크기에서 되만든 것이라 이 경로에서 제일 약하다.
  void _logFeatures() {
    final verdict = _verdict;
    if (verdict == null) return;
    final now = _now;
    if (now - _lastFeatureLogAt < 1.0) return;
    _lastFeatureLogAt = now;
    final f = verdict.features;
    final p = verdict.parts;
    String n(double? v, {int digits = 3}) =>
        v == null || v.isNaN ? '—' : v.toStringAsFixed(digits);
    diag.write('feat: ${verdict.label}'
        ' occ=${n(f.occupancy)} top=${n(f.topRow)} spread=${n(f.spread)}'
        ' headW=${n(f.headW)} head=${n(f.headMm, digits: 0)}mm'
        ' rel=${n(p['dist_rel'])} dmm=${n(p['dist_mm'], digits: 0)}'
        ' | slump=${n(p['slump'], digits: 2)}/${n(p['slump_raw'], digits: 2)}'
        ' recl=${n(p['recline'], digits: 2)}/${n(p['recline_raw'], digits: 2)}'
        ' drow=${n(p['drowsy'], digits: 2)} nod=${n(verdict.nodRate, digits: 1)}'
        ' dip=${n(p['nod_dip'])} amp=${n(p['nod_amp'])}'
        ' | phi=${n(verdict.phi, digits: 2)} delta=${n(verdict.delta, digits: 2)}');
  }

  void _onFrame(VisionFrame frame) {
    final now = _now;
    final zone =
        maskToZone(maskFromBytes(frame.payload), frame.height, frame.width);
    _lastDepth = zone.data;
    _verdict = _tracker.update(zone, now);
    _frames++;
    if (_lastTick > 0) {
      // 지수 평활. 한 프레임이 늦어도 화면의 fps 가 튀지 않는다.
      final gap = now - _lastTick;
      if (gap > 0) _fps = 0.9 * _fps + 0.1 / gap;
    }
    _lastTick = now;
    _lastFrameAt = now;
    _logFeatures();
  }

  @override
  Future<PostureState> fetch() async {
    final verdict = _verdict;
    if (verdict == null) {
      throw StateError(_error ?? 'ESP32-CAM 프레임을 기다리고 있습니다');
    }
    final now = _now;
    final stale = _stale;
    final label = verdict.label;
    final calibrating = label == 'BASELINE' || label == 'UNKNOWN';
    final hint = _calibrator.hint(now);

    _sequence++;
    return PostureState(
      label: _labels[label] ?? PostureLabel.unknown,
      present: verdict.present,
      // 기준을 잡는 중이거나 프레임이 묵었으면 판정을 믿지 말라고 내린다.
      valid: !stale && !calibrating,
      motion: verdict.features.motion,
      coverage: verdict.features.occupancy,
      focusDrop: verdict.phi,
      fatigue: verdict.delta,
      reasons: reasonsFor(verdict),
      sequence: _sequence,
      timestamp: DateTime.now(),
      headDeltaMm: verdict.parts['dist_mm'],
      nodPerMin: verdict.nodRate,
      scenario: hint.isNotEmpty
          ? hint
          : stale
              ? '센서 프레임이 끊겼습니다 — ESP32-CAM 배선과 전원을 확인하세요'
              : '${_labelText[label] ?? label} · 보드에서 직접 판정 중',
      node: 'pi5-posture',
    );
  }

  /// 센서가 지금 보고 있는 것. 화면의 `센서 보기` 가 읽는다.
  VisionSnapshot? get vision {
    if (_visionW == 0 || _visionH == 0) return null;
    return VisionSnapshot(
      width: _visionW,
      height: _visionH,
      mask: _lastMask,
      coverage: _lastCoverage,
      depthMm: _lastDepth,
      maskAgeS: _lastFrameAt > 0 ? _now - _lastFrameAt : null,
    );
  }

  @override
  Future<LinkHealth?> health() async => LinkHealth(
        stage: _calibrator.stage,
        stale: _stale,
        fps: _fps,
        frames: _frames,
        error: _error,
      );

  /// 기준 다시 잡기. 센서 쪽 배경까지 다시 뜨므로 20초쯤 걸린다.
  @override
  Future<void> calibrate() async => _calibrator.restart(_now);

  @override
  void close() {
    _pump?.cancel();
    _pump = null;
    unawaited(_uart.close());
  }
}
