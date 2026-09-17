/// 54x42 zone 거리 배열 -> 자세 판정.
///
/// `espcam_with_decision` 의 `tools/posture.py` 를 그대로 옮긴 것이다. ATLAS 는
/// Python 을 앱 런타임으로 지원하지 않으므로 판정이 앱 안에 있어야 한다.
///
/// **임계값과 판정 순서를 임의로 고치지 말 것.** 전부 실측 로그로 맞춘 값이고,
/// 왜 그 값인지는 Python 원본의 주석에 남아 있다. 이 포팅이 원본과 같은 숫자를
/// 내는지는 `test/posture_judge_test.dart` 가 골든 벡터로 채점한다 —
/// 벡터는 `tools/gen_posture_golden.py` 가 원본을 돌려 만든 것이다.
///
/// 판정하는 자세 (UPRIGHT 기준 대비):
///   ABSENT   자리비움    - 점유 zone 이 거의 없다
///   SLUMP    엎드림      - 이탈하면서 센서에 가까워진다
///   RECLINE  뒤로 젖힘   - 이탈하면서 센서에서 멀어진다
///   DROWSY   졸음        - 머리가 내려갔다 올라오기를 반복한다
library;

import 'dart:collection';
import 'dart:math' as math;

const int zoneCols = 54;
const int zoneRows = 42;

const double maxRangeMm = 4000.0; // 이보다 먼 값은 무효
const double intrusionMm = 150.0; // 배경보다 이만큼 가까우면 사람 zone
const double bgDecayMm = 1.0; // 자동 학습 시 배경이 프레임당 내려오는 폭
const double presentMinOcc = 0.04; // 재실로 볼 최소 점유율

const double slumpSpan = 0.22;
const double slumpHoldS = 3.0; // 엎드림은 '내려가서 머무는' 것
const double slumpArmAt = 0.25;
const double reclineHoldS = 2.5;
const double reclineArmAt = 0.25;
const double reclineRelDeadband = 0.25;
const double reclineRelSpan = 0.25;

const double distSmoothS = 1.2; // 거리 축만 평활한다
const double baselineClipAt = 0.02;
const double nodWindowS = 30.0;
const double restWindowS = 8.0;
const double nodAmplitudeRel = 0.06;
const double nodAmplitudeMin = 0.03;
const double nodMinDownS = 0.18;
const double nodMaxDownS = 2.5;
const double nodRefractoryS = 0.8;
const double dipLookbackS = 3.0;
const double nodRateFull = 8.0; // 분당 이 횟수면 졸음 기여도 최대
const double nodMinSpanS = 18.0;
const double nodQuietS = 8.0;
const double nodFadeS = 6.0;
const double motionSpan = 0.06;

const int headMinWidth = 4; // 머리로 인정할 최소 가로 zone 수
const int headBandRows = 4; // 머리 거리 산출에 쓸 행 수
const double torsoHalfSpan = 0.22;

const double slumpLabelAt = 0.70;
const double reclineLabelAt = 0.55;
const double drowsyLabelAt = 0.50;

double clipValue(double value, double lo, double hi) =>
    value < lo ? lo : (value > hi ? hi : value);

/// `np.median` 의 1차원판. 짝수 개면 가운데 두 값의 평균.
double medianOf(List<double> values) {
  if (values.isEmpty) {
    throw StateError('중앙값을 낼 값이 없다');
  }
  final ordered = List<double>.from(values)..sort();
  final mid = ordered.length ~/ 2;
  if (ordered.length.isOdd) return ordered[mid];
  return (ordered[mid - 1] + ordered[mid]) / 2.0;
}

/// 행 우선 1차원 리스트로 담은 2차원 격자. 거리(double)와 마스크(bool)를 같이 담는다.
class Grid<T> {
  Grid(this.data, this.rows, this.cols);

  final List<T> data;
  final int rows;
  final int cols;

  int get size => rows * cols;

  /// 행 하나의 시작 인덱스. 슬라이스를 뜨지 않고 인덱스로 읽는다(할당을 줄인다).
  int offset(int row) => row * cols;

  bool sameShape(Grid<Object?>? other) =>
      other != null && other.rows == rows && other.cols == cols;
}

Grid<double> _finite(Grid<double> depth) => Grid<double>(
      [for (final v in depth.data) v.isFinite ? v : maxRangeMm],
      depth.rows,
      depth.cols,
    );

/// zone 별 정적 배경 거리(책상 상판·벽).
class BackgroundModel {
  Grid<double>? refMm;
  bool captured = false;

  /// 책상을 비우고 부르는 것이 정확하다. zone 마다 프레임을 가로질러 중앙값.
  void capture(List<Grid<double>> frames) {
    if (frames.isEmpty) throw StateError('배경을 만들 프레임이 없다');
    final grids = [for (final f in frames) _finite(f)];
    final rows = grids.first.rows;
    final cols = grids.first.cols;
    final out = List<double>.filled(rows * cols, 0.0);
    final column = List<double>.filled(grids.length, 0.0);
    for (var i = 0; i < out.length; i++) {
      for (var g = 0; g < grids.length; g++) {
        column[g] = grids[g].data[i];
      }
      out[i] = medianOf(column);
    }
    refMm = Grid<double>(out, rows, cols);
    captured = true;
  }

  /// 못 비웠을 때를 위한 자동 학습 - zone 별 '천천히 감쇠하는 최댓값'.
  void update(Grid<double> depthMm) {
    if (captured) return;
    final d = _finite(depthMm);
    var ref = refMm;
    if (!d.sameShape(ref)) {
      ref = Grid<double>(List<double>.filled(d.size, maxRangeMm), d.rows, d.cols);
    }
    final next = List<double>.filled(d.size, 0.0);
    for (var i = 0; i < d.size; i++) {
      final decayed = ref!.data[i] - bgDecayMm;
      next[i] = decayed > d.data[i] ? decayed : d.data[i];
    }
    refMm = Grid<double>(next, d.rows, d.cols);
  }

  Grid<bool> occupied(Grid<double> depthMm) {
    final ref = refMm;
    if (ref == null) {
      return Grid<bool>(List<bool>.filled(depthMm.size, false),
          depthMm.rows, depthMm.cols);
    }
    final far = _finite(depthMm);
    return Grid<bool>([
      for (var i = 0; i < far.size; i++)
        (ref.data[i] - far.data[i]) >= intrusionMm
    ], depthMm.rows, depthMm.cols);
  }
}

class PostureFeatures {
  const PostureFeatures({
    required this.occupancy,
    required this.topRow,
    required this.centroidRow,
    required this.spread,
    required this.headMm,
    required this.motion,
    this.headW = 0.0,
  });

  final double occupancy; // 점유 zone 비율 [0,1]
  final double topRow; // 머리 높이 (0=위, 1=아래)
  final double centroidRow;
  final double spread; // 세로 점유 범위
  final double headMm; // 머리 zone 거리 중앙값
  final double motion; // 직전 프레임 대비 변화 비율
  final double headW; // 머리 밴드에서 가장 넓은 행 / 화면 폭
}

class PostureVerdict {
  PostureVerdict({
    required this.label,
    required this.present,
    required this.phi,
    required this.delta,
    required this.features,
    Map<String, double>? parts,
    this.nodRate = 0.0,
    this.note = '',
  }) : parts = parts ?? <String, double>{};

  final String label; // ABSENT/UPRIGHT/SLUMP/RECLINE/DROWSY/BASELINE/UNKNOWN
  final bool present;
  final double phi; // 집중 기여도
  final double delta; // 피로 기여도
  final PostureFeatures features;
  final Map<String, double> parts;
  final double nodRate; // 분당 꾸벅임
  final String note;
}

/// 머리 행과 몸통 중심 열 구간. 반환 (행, 열_시작, 열_끝) 또는 null.
///
/// 최상단 점유 행을 머리로 쓰면 손을 든 순간 그 손이 머리가 된다. 폭이 충분하고
/// 몸통 중심축 위에 있는 가장 높은 행을 고른다.
(int, int, int)? findHead(Grid<bool> occ) {
  final rows = occ.rows;
  final cols = occ.cols;
  final counts = List<int>.filled(rows, 0);
  var any = false;
  for (var r = 0; r < rows; r++) {
    final base = occ.offset(r);
    var n = 0;
    for (var c = 0; c < cols; c++) {
      if (occ.data[base + c]) n++;
    }
    counts[r] = n;
    if (n > 0) any = true;
  }
  if (!any) return null;

  // 몸통 = 가장 넓은 행들. 그 열 무게중심을 중심축으로 쓴다.
  var peak = 0;
  for (final n in counts) {
    if (n > peak) peak = n;
  }
  final wideEnough = math.max(peak * 0.5, 1.0);
  final colW = List<double>.filled(cols, 0.0);
  for (var r = 0; r < rows; r++) {
    if (counts[r] < wideEnough) continue;
    final base = occ.offset(r);
    for (var c = 0; c < cols; c++) {
      if (occ.data[base + c]) colW[c] += 1.0;
    }
  }
  var weight = 0.0;
  for (final w in colW) {
    weight += w;
  }
  if (weight <= 0) return null;
  var moment = 0.0;
  for (var c = 0; c < cols; c++) {
    moment += colW[c] * c;
  }
  final center = moment / weight;
  final half = math.max(torsoHalfSpan * cols, headMinWidth.toDouble());
  final lo = math.max(center - half, 0.0).toInt();
  final hi = math.min(center + half, cols.toDouble()).toInt();

  for (var r = 0; r < rows; r++) {
    final base = occ.offset(r);
    var band = 0;
    for (var c = lo; c < hi; c++) {
      if (occ.data[base + c]) band++;
    }
    if (band >= headMinWidth) return (r, lo, hi);
  }
  return null;
}

/// zone 배열에서 기하 특징을 뽑는다. 반환 (특징, 이번 점유 마스크).
(PostureFeatures, Grid<bool>) extract(
    Grid<double> depthGrid, BackgroundModel background,
    {Grid<bool>? prev}) {
  background.update(depthGrid);
  final occ = background.occupied(depthGrid);
  final rows = occ.rows;
  final cols = occ.cols;
  final total = occ.size.toDouble();

  final counts = List<int>.filled(rows, 0);
  var filled = 0;
  var lastRow = -1;
  for (var r = 0; r < rows; r++) {
    final base = occ.offset(r);
    var n = 0;
    for (var c = 0; c < cols; c++) {
      if (occ.data[base + c]) n++;
    }
    counts[r] = n;
    filled += n;
    if (n > 0) lastRow = r;
  }
  final occupancy = filled / total;
  final depth = _finite(depthGrid);

  final head = findHead(occ);
  double topRow, centroidRow, spread, headMm, headW;
  if (head != null && lastRow >= 0) {
    final (top, lo, hi) = head;
    topRow = top / (rows - 1);
    spread = math.max(lastRow - top, 0) / (rows - 1);
    var moment = 0.0;
    for (var r = 0; r < rows; r++) {
      moment += counts[r] * r;
    }
    centroidRow = (moment / filled) / (rows - 1);

    // 머리 거리 = 머리 행부터 몇 줄, 몸통 중심 열 구간 안쪽만. 들어올린 손이
    // 섞이지 않게 열도 같이 제한한다. 평균이 아니라 중앙값 - 팔뚝이 소수 zone 으로
    // 섞이면 평균은 따라가고 중앙값은 버린다.
    final end = math.min(top + headBandRows, rows);
    final vals = <double>[];
    var widest = 0;
    for (var r = top; r < end; r++) {
      final base = occ.offset(r);
      var inRow = 0;
      for (var c = lo; c < hi; c++) {
        if (occ.data[base + c]) {
          vals.add(depth.data[base + c]);
          inRow++;
        }
      }
      if (inRow > widest) widest = inRow;
    }
    headMm = vals.isEmpty ? double.nan : medianOf(vals);
    headW = (end > top && hi > lo) ? widest / cols : 0.0;
  } else {
    topRow = centroidRow = spread = 1.0;
    headMm = double.nan;
    headW = 0.0;
  }

  var motion = 0.0;
  if (prev != null && occ.sameShape(prev)) {
    var changed = 0;
    for (var i = 0; i < occ.size; i++) {
      if (occ.data[i] != prev.data[i]) changed++;
    }
    motion = changed / total;
  }

  return (
    PostureFeatures(
      occupancy: occupancy,
      topRow: topRow,
      centroidRow: centroidRow,
      spread: spread,
      headMm: headMm,
      motion: motion,
      headW: headW,
    ),
    occ
  );
}

/// 머리 높이의 '내려갔다 올라오기'를 세어 분당 꾸벅임을 낸다.
///
/// 기준선은 창의 중앙값이라 자세가 통째로 바뀌어도 따라간다. 그래서 엎드려서
/// 머물면 꾸벅임으로 세지 않는다 - 엎드림과 졸음이 갈리는 지점.
class NodDetector {
  double windowS = nodWindowS;
  double amplitude = nodAmplitudeMin;
  final ListQueue<(double, double)> _hist = ListQueue<(double, double)>();
  final ListQueue<double> _events = ListQueue<double>();
  double? _downSince;
  double _lastEvent = -1e9;
  bool _abandoned = false;

  /// 진단용 - 꾸벅임이 안 세어질 때 문턱에 얼마나 모자랐는지 보이게.
  double rest = double.nan;
  double dip = 0.0;

  void reset() {
    _hist.clear();
    _events.clear();
    _downSince = null;
    _abandoned = false;
    rest = double.nan;
    dip = 0.0;
  }

  double update(double now, double topRow, bool present, {double scale = 1.0}) {
    // 사람이 없으면 top_row 가 1.0 과 잔여값 사이를 튀어 가짜 꾸벅임이 쌓인다.
    if (!present) {
      reset();
      return 0.0;
    }

    amplitude = math.max(nodAmplitudeRel * scale, nodAmplitudeMin);
    _hist.addLast((now, topRow));
    while (_hist.isNotEmpty && now - _hist.first.$1 > windowS) {
      _hist.removeFirst();
    }
    while (_events.isNotEmpty && now - _events.first > windowS) {
      _events.removeFirst();
    }

    if (_hist.length >= 8) {
      final recentRest = [
        for (final item in _hist)
          if (now - item.$1 <= restWindowS) item.$2
      ];
      final restValues = recentRest.isNotEmpty
          ? recentRest
          : [for (final item in _hist) item.$2];
      rest = medianOf(restValues);
      // 지금 값이 아니라 최근 창에서 가장 깊었던 하강을 보여준다.
      final recent = [
        for (final item in _hist)
          if (now - item.$1 <= dipLookbackS) item.$2
      ];
      dip = recent.isEmpty ? 0.0 : recent.reduce(math.max) - rest;

      final up = topRow < rest + amplitude * 0.3;
      if (_abandoned) {
        if (up) _abandoned = false; // 다시 올라와야 재무장
      } else if (_downSince == null) {
        if (topRow > rest + amplitude) _downSince = now; // 머리가 내려갔다
      } else if (up) {
        final held = now - _downSince!;
        _downSince = null; // 다시 올라왔다
        if (held >= nodMinDownS &&
            held <= nodMaxDownS &&
            now - _lastEvent >= nodRefractoryS) {
          _events.addLast(now); // 꾸벅 1회
          _lastEvent = now;
        }
      } else if (now - _downSince! > nodMaxDownS) {
        // 계속 내려가 있다 = 응시/엎드림. 올라올 때 뒤늦게 세지 않도록 무효화.
        _downSince = null;
        _abandoned = true;
      }
    }

    // 창이 아직 안 찼으면 관측 시간으로 나눠 초반 과대평가를 막는다.
    final span =
        _hist.isNotEmpty ? math.max(now - _hist.first.$1, 1.0) : windowS;
    final rate =
        _events.length * 60.0 / math.max(math.min(span, windowS), nodMinSpanS);

    // 조용해지면 창에서 빠지길 기다리지 않고 내린다.
    final quiet = now - _lastEvent;
    final fade = clipValue(1.0 - (quiet - nodQuietS) / nodFadeS, 0.0, 1.0);
    return rate * fade;
  }
}

/// 바른 자세 기준값.
class PostureBaseline {
  PostureBaseline({
    required this.topRow,
    required this.spread,
    required this.headMm,
    this.headW = 0.0,
    this.samples = 0,
    this.clipped = false,
  });

  final double topRow;
  final double spread;
  final double headMm;
  final double headW;
  final int samples;
  final bool clipped; // 머리가 화각 위쪽에 잘린 채로 측정됐다

  factory PostureBaseline.fromFeatures(List<PostureFeatures> feats) {
    if (feats.isEmpty) throw StateError('baseline 을 만들 특징이 없다');
    // 머리를 못 찾은 프레임은 head_w 가 0 이다. 기준에 섞으면 통째로 낮아진다.
    final heads = [
      for (final f in feats)
        if (f.headMm.isFinite) f.headMm
    ];
    final widths = [
      for (final f in feats)
        if (f.headW > 0) f.headW
    ];
    final top = medianOf([for (final f in feats) f.topRow]);
    // top_row 가 0 에 붙으면 머리가 잘린 것이다. 그 baseline 을 쓰면 바른 자세도
    // '머리가 내려갔다'로 읽힌다.
    return PostureBaseline(
      topRow: top,
      spread: medianOf([for (final f in feats) f.spread]),
      headMm: heads.isEmpty ? double.nan : medianOf(heads),
      headW: widths.isEmpty ? 0.0 : medianOf(widths),
      samples: feats.length,
      clipped: top < baselineClipAt,
    );
  }
}

/// 특징 + baseline + 꾸벅임 빈도 -> 자세 라벨과 phi/delta.
PostureVerdict judge(
  PostureFeatures feats,
  PostureBaseline? base,
  double nodRate, {
  double slumpHeldS = slumpHoldS,
  double? headMm,
  double reclineHeldS = reclineHoldS,
  double? headW,
}) {
  final distMm = headMm ?? feats.headMm;
  final width = headW ?? feats.headW;
  if (feats.occupancy < presentMinOcc) {
    return PostureVerdict(
        label: 'ABSENT',
        present: false,
        phi: 0.0,
        delta: 0.0,
        features: feats,
        nodRate: nodRate,
        note: 'low occupancy');
  }
  if (base == null) {
    return PostureVerdict(
        label: 'UNKNOWN',
        present: true,
        phi: 0.0,
        delta: 0.0,
        features: feats,
        nodRate: nodRate,
        note: 'no baseline');
  }

  // 1) 이탈 '크기' - 머리가 내려간 정도 + 몸이 접힌 정도. 방향 정보는 없다.
  final headDrop = feats.topRow - base.topRow;
  final drop = clipValue(headDrop / slumpSpan, 0.0, 1.0);
  final collapse = clipValue(
      (base.spread - feats.spread) / math.max(base.spread, 1e-6), 0.0, 1.0);
  final magnitude = clipValue(0.8 * drop + 0.2 * collapse, 0.0, 1.0);

  // 2) 젖힘은 '멀어진 거리' 자체가 고유 축이다.
  final distDelta = (distMm.isFinite && base.headMm.isFinite)
      ? distMm - base.headMm
      : 0.0;
  double rel = 0.0;
  double reclineRaw = 0.0;
  if (distMm.isFinite && base.headMm.isFinite && base.headMm > 0.0) {
    rel = (distMm - base.headMm) / base.headMm;
    reclineRaw =
        clipValue((rel - reclineRelDeadband) / reclineRelSpan, 0.0, 1.0);
  }
  // 지속 조건을 못 채운 젖힘은 꾸벅임의 흔들림일 뿐이다.
  final recline = reclineRaw * clipValue(reclineHeldS / reclineHoldS, 0.0, 1.0);

  // 3) 엎드림은 머리 높이로 재되, 멀어지고 있으면 눌러 끈다. 게이트에는 순간값을
  //    쓴다 - '어느 쪽으로 가고 있나'는 지금 정보다.
  final slumpRaw = magnitude * (1.0 - reclineRaw);
  final slump = slumpRaw * clipValue(slumpHeldS / slumpHoldS, 0.0, 1.0);

  // 4) 졸음 - 꾸벅임 빈도. 자세가 아니라 시간 패턴이라 별도 축이다.
  final drowsy = clipValue(nodRate / nodRateFull, 0.0, 1.0);

  final parts = <String, double>{
    'slump': slump,
    'recline': recline,
    'drowsy': drowsy,
    'dist_mm': distDelta,
    'slump_raw': slumpRaw,
    'recline_raw': reclineRaw,
    'headw_ratio': base.headW > 0 ? width / base.headW : 0.0,
    'dist_rel': (distMm.isFinite && base.headMm > 0)
        ? distMm / base.headMm - 1.0
        : 0.0,
  };
  // 뒤로 젖힘은 자세 불량이지만 각성 상태일 수 있어 피로 기여를 낮게 잡는다.
  final delta = clipValue(
      math.max(0.95 * slump, math.max(0.85 * drowsy, 0.45 * recline)), 0.0, 1.0);
  final stability = 1.0 - clipValue(feats.motion / motionSpan, 0.0, 1.0);
  final phi = clipValue((1.0 - delta) * stability, 0.0, 1.0);

  // 젖힘을 먼저 본다. 반대로 두면 slump 가 먼저 문턱을 넘어 젖힘이 영영 안 뜬다.
  // 졸음이 엎드림보다 앞이다 - 꾸벅이며 조는 사람은 엎드림 조건도 같이 채우는데,
  // 꾸벅임은 세어서 얻은 적극적인 근거이고 엎드림은 '머리가 내려가 있다'일 뿐이다.
  String label;
  if (recline >= reclineLabelAt) {
    label = 'RECLINE';
  } else if (drowsy >= drowsyLabelAt) {
    label = 'DROWSY';
  } else if (slump >= slumpLabelAt) {
    label = 'SLUMP';
  } else {
    label = 'UPRIGHT';
  }
  return PostureVerdict(
    label: label,
    present: true,
    phi: phi,
    delta: delta,
    features: feats,
    parts: parts,
    nodRate: nodRate,
    note: base.clipped ? 'baseline clipped - press b' : '',
  );
}

/// 프레임을 계속 넣으면 배경·baseline 수집과 판정을 이어서 해준다.
class PostureTracker {
  final BackgroundModel background = BackgroundModel();
  PostureBaseline? baseline;
  final NodDetector nods = NodDetector();

  Grid<bool>? _prev;
  final List<PostureFeatures> _postureBuf = [];
  final List<Grid<double>> _bgBuf = [];
  final ListQueue<(double, double)> _headHist = ListQueue<(double, double)>();
  final ListQueue<(double, double)> _widthHist = ListQueue<(double, double)>();
  int _wantPosture = 0;
  int _wantBg = 0;
  double? _slumpSince;
  double? _reclineSince;

  /// 책상을 비운 상태에서 부를 것.
  void startBackground({int samples = 30}) {
    _bgBuf.clear();
    _wantBg = samples;
  }

  /// 바른 자세로 앉은 상태에서 부를 것.
  void startBaseline({int samples = 60}) {
    _postureBuf.clear();
    _wantPosture = samples;
  }

  bool get ready => baseline != null && _wantBg == 0 && _wantPosture == 0;

  PostureVerdict update(Grid<double> depth, double now) {
    if (_wantBg > 0) {
      _bgBuf.add(depth);
      final want = _wantBg;
      final done = _bgBuf.length;
      if (done >= want) {
        background.capture(_bgBuf);
        _wantBg = 0;
      }
      final (feats, occ) = extract(depth, background, prev: _prev);
      _prev = occ;
      return PostureVerdict(
          label: 'BASELINE',
          present: false,
          phi: 0.0,
          delta: 0.0,
          features: feats,
          note: 'background $done/$want');
    }

    final (feats, occ) = extract(depth, background, prev: _prev);
    _prev = occ;
    final present = feats.occupancy >= presentMinOcc;
    final scale = baseline?.spread ?? feats.spread;
    final nodRate = nods.update(now, feats.topRow, present, scale: scale);

    // 거리 축만 평활한다. 여기만 부호로 엎드림/젖힘을 가르므로 노이즈에 제일 약하고,
    // 자리를 비우면 남은 값이 의미를 잃으므로 같이 비운다.
    if (!present) {
      _headHist.clear();
      _widthHist.clear();
    } else {
      if (feats.headMm.isFinite) _headHist.addLast((now, feats.headMm));
      if (feats.headW > 0.0) _widthHist.addLast((now, feats.headW));
    }
    for (final hist in [_headHist, _widthHist]) {
      while (hist.isNotEmpty && now - hist.first.$1 > distSmoothS) {
        hist.removeFirst();
      }
    }
    final headMm = _headHist.isEmpty
        ? double.nan
        : medianOf([for (final item in _headHist) item.$2]);
    final headW = _widthHist.isEmpty
        ? 0.0
        : medianOf([for (final item in _widthHist) item.$2]);

    if (_wantPosture > 0) {
      final want = _wantPosture;
      if (feats.occupancy >= presentMinOcc) _postureBuf.add(feats);
      final done = _postureBuf.length;
      if (done >= want) {
        baseline = PostureBaseline.fromFeatures(_postureBuf);
        _wantPosture = 0;
      }
      return PostureVerdict(
          label: 'BASELINE',
          present: true,
          phi: 0.0,
          delta: 0.0,
          features: feats,
          nodRate: nodRate,
          note: 'posture $done/$want');
    }

    // 머리가 연속으로 내려가 있던 시간. 직전 프레임의 raw 값으로 재는 한 프레임
    // 지연이 있지만 무시할 수 있다.
    final held = _slumpSince == null ? 0.0 : now - _slumpSince!;
    final heldR = _reclineSince == null ? 0.0 : now - _reclineSince!;
    final verdict = judge(feats, baseline, nodRate,
        slumpHeldS: held, headMm: headMm, reclineHeldS: heldR, headW: headW);

    if ((verdict.parts['slump_raw'] ?? 0.0) >= slumpArmAt) {
      _slumpSince ??= now;
    } else {
      _slumpSince = null;
    }
    // 꾸벅임이 왜 안 세어지는지 보이게 같이 실어 보낸다.
    verdict.parts['nod_dip'] = nods.dip;
    verdict.parts['nod_amp'] = nods.amplitude;

    if ((verdict.parts['recline_raw'] ?? 0.0) >= reclineArmAt) {
      _reclineSince ??= now;
    } else {
      _reclineSince = null;
    }
    return verdict;
  }
}
