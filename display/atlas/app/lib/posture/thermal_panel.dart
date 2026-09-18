/// 열화상 격자 + tee 뼈대 + HUD 한 장.
///
/// 그리는 규칙은 `thermal_pose.py` 의 `render_thermal()` · `draw_skeleton()` ·
/// `hud()` 에서 왔다. 계산은 [thermal_view] 가 하고 여기서는 칠하기만 한다.
library;

import 'package:flutter/material.dart';

import 'heat_palette.dart';
import 'palette.dart';
import 'thermal_view.dart';
import 'zone_image.dart';

/// 머리·어깨 가로대·가운데서 올라온 기둥·양쪽 팔.
///
/// `CHEST` 는 MediaPipe 랜드마크가 아니다 — 33개 중에 가슴은 없다. 그래서 번호를
/// 끝 너머로 주고 그릴 때 양 어깨의 중점으로 채운다. 머리를 어깨 둘에 각각 잇는
/// 대신 이 중점에 이으면 삼각 천막이 아니라 가로대에 기둥이 선 모양이 되는데,
/// 정면에서 본 상반신이 실제로 그렇다.
const int _nose = 0;
const int _lShoulder = 11;
const int _rShoulder = 12;
const int _lElbow = 13;
const int _rElbow = 14;
const int _lWrist = 15;
const int _rWrist = 16;
const int _chest = 33;

const List<List<int>> _teeConnections = [
  [_lShoulder, _rShoulder],
  [_chest, _nose],
  [_lShoulder, _lElbow],
  [_lElbow, _lWrist],
  [_rShoulder, _rElbow],
  [_rElbow, _rWrist],
];
const Set<int> _teePoints = {
  _nose, _lShoulder, _rShoulder, _lElbow, _rElbow, _lWrist, _rWrist,
};

class ThermalPanel extends StatelessWidget {
  const ThermalPanel({
    super.key,
    required this.frame,
    this.points = const [],
    this.showSkeleton = true,
    this.showGrid = false,
  });

  final ThermalFrame frame;

  /// 0~1 정규화 랜드마크. camsvc 가 판정에 쓴 그 점들이다.
  final List<Offset> points;
  final bool showSkeleton;
  final bool showGrid;

  @override
  Widget build(BuildContext context) => AspectRatio(
        aspectRatio: frame.cols / frame.rows,
        child: Container(
          decoration: BoxDecoration(
            color: Colors.black,
            border: Border.all(color: kLine, width: 2),
            borderRadius: BorderRadius.circular(8),
          ),
          child: CustomPaint(
            painter: _ThermalPainter(
              frame: frame,
              points: points,
              showSkeleton: showSkeleton,
              showGrid: showGrid,
            ),
          ),
        ),
      );
}

class _ThermalPainter extends CustomPainter {
  _ThermalPainter({
    required this.frame,
    required this.points,
    required this.showSkeleton,
    required this.showGrid,
  });

  final ThermalFrame frame;
  final List<Offset> points;
  final bool showSkeleton;
  final bool showGrid;

  @override
  void paint(Canvas canvas, Size size) {
    final cols = frame.cols;
    final rows = frame.rows;
    if (cols == 0 || rows == 0) return;

    // 칸을 통째로 칠한다. 원본이 INTER_NEAREST 로 늘리는 것과 같은 그림이다 —
    // 부드럽게 늘리면 센서 해상도가 어느 정도인지가 화면에서 사라진다.
    final cellW = size.width / cols;
    final cellH = size.height / rows;
    final paint = Paint()..style = PaintingStyle.fill;
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        paint.color = heatColor(frame.at(c, r));
        canvas.drawRect(
          Rect.fromLTWH(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5),
          paint,
        );
      }
    }

    if (showGrid) {
      // 칸 경계에 정확히 얹는다. 고정 간격으로 그으면 오른쪽 끝에서 몇 칸씩
      // 밀려 선과 블록이 어긋난다.
      final line = Paint()
        ..color = Colors.black.withValues(alpha: 0.55)
        ..strokeWidth = 1;
      for (final x in zoneBoundaries(size.width, cols)) {
        canvas.drawLine(Offset(x, 0), Offset(x, size.height), line);
      }
      for (final y in zoneBoundaries(size.height, rows)) {
        canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
      }
    }

    if (showSkeleton) _drawTee(canvas, size);
  }

  void _drawTee(Canvas canvas, Size size) {
    if (points.isEmpty) return;
    final at = <int, Offset>{};
    for (final index in _teePoints) {
      if (index >= points.length) continue;
      at[index] = Offset(
        points[index].dx * size.width,
        points[index].dy * size.height,
      );
    }
    final left = at[_lShoulder];
    final right = at[_rShoulder];
    if (left != null && right != null) {
      at[_chest] = Offset((left.dx + right.dx) / 2, (left.dy + right.dy) / 2);
    }

    final scale = (size.height / 720.0).clamp(0.5, 4.0);
    final bone = Paint()
      ..color = Colors.white
      ..strokeWidth = (8 * scale).clamp(2.0, 10.0)
      ..strokeCap = StrokeCap.round;
    for (final pair in _teeConnections) {
      final a = at[pair[0]];
      final b = at[pair[1]];
      if (a != null && b != null) canvas.drawLine(a, b, bone);
    }
    final joint = Paint()..color = Colors.white;
    final radius = (10 * scale).clamp(3.0, 12.0);
    for (final entry in at.entries) {
      if (entry.key == _chest) continue; // 모델의 점이 아니다. 선만 그린다.
      canvas.drawCircle(entry.value, radius, joint);
    }
  }

  @override
  bool shouldRepaint(_ThermalPainter old) =>
      old.frame != frame ||
      old.points != points ||
      old.showSkeleton != showSkeleton ||
      old.showGrid != showGrid;
}

/// 화면 위에 얹는 진단 줄. 원본 `hud()` 가 찍던 것 중 이 앱에서 뜻이 있는 것만.
class ThermalHud extends StatelessWidget {
  const ThermalHud({
    super.key,
    required this.frame,
    required this.previewWidth,
    required this.previewHeight,
    required this.joints,
    required this.fps,
  });

  final ThermalFrame frame;
  final int previewWidth;
  final int previewHeight;
  final int joints;
  final double fps;

  @override
  Widget build(BuildContext context) {
    // spread 가 그림이 쓸 만한지 말해 주는 숫자다. 가려진 렌즈와 날아간 프레임은
    // 둘 다 평평해서, 여기서 보면 모델이 죽은 것처럼 보인다.
    final flat = frame.spread < 24;
    return DefaultTextStyle(
      style: const TextStyle(fontSize: 12, color: kMuted, height: 1.5),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${frame.cols}x${frame.rows} zones · RAINBOW-HC · '
              'tee $joints점 · ${fps.toStringAsFixed(1)} fps'),
          Text(
            'camera $previewWidth x$previewHeight  '
            'min ${frame.rawMin}  mean ${frame.rawMean}  max ${frame.rawMax}  '
            'spread ${frame.spread}${flat ? '  ← 평평합니다 (렌즈 가림·노출)' : ''}',
            style: TextStyle(
                fontSize: 12, color: flat ? kAmber : kMuted, height: 1.5),
          ),
        ],
      ),
    );
  }
}
