/// 센서가 지금 보고 있는 것을 그대로 보여준다.
///
/// 화면에 자세 라벨만 뜨면 "잘 되고 있는 건지" 를 사람이 확인할 방법이 없다.
/// 노트북에서 `tools/posture_viewer.py` 로 보던 것과 같은 역할이다 - 내가 화각
/// 안에 있는지, 몸이 잘렸는지, 배경이 잘못 잡혔는지는 눈으로 봐야 안다.
///
/// 두 가지를 겹쳐 보여준다.
///   * **coverage** - ESP 가 보내는 zone 별 배경 대비 차이값(0~255). 원본에 가깝다.
///   * **마스크** - RP2040 의 `vision.c` 가 임계값·모폴로지로 만든 이진 실루엣.
///     **판정이 실제로 먹는 것은 이쪽이다.** 초록 테두리로 그린다.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'palette.dart';
import 'posture_source.dart';

class VisionViewPage extends StatefulWidget {
  const VisionViewPage({super.key, required this.read, required this.title});

  /// 매 프레임 최신 스냅샷을 가져오는 함수.
  final VisionSnapshot? Function() read;
  final String title;

  @override
  State<VisionViewPage> createState() => _VisionViewPageState();
}

class _VisionViewPageState extends State<VisionViewPage> {
  Timer? _timer;
  VisionSnapshot? _snapshot;
  bool _showCoverage = true;

  @override
  void initState() {
    super.initState();
    _snapshot = widget.read();
    // 센서가 12fps 다. 그보다 자주 그려도 같은 그림이다.
    _timer = Timer.periodic(const Duration(milliseconds: 80), (_) {
      if (!mounted) return;
      setState(() => _snapshot = widget.read());
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = _snapshot;
    final stale = (snapshot?.maskAgeS ?? 0) > 1.0;
    return Scaffold(
      backgroundColor: kBg,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 14),
          child: Column(children: [
            Row(children: [
              IconButton(
                key: const ValueKey('vision-back'),
                onPressed: () => Navigator.pop(context),
                color: kInk,
                icon: const Icon(Icons.arrow_back),
              ),
              const SizedBox(width: 4),
              Text(widget.title,
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.w700, color: kInk)),
              const Spacer(),
              if (snapshot != null)
                Text('${snapshot.width}x${snapshot.height}',
                    style: const TextStyle(fontSize: 12, color: kDim)),
              const SizedBox(width: 12),
              _toggle(),
            ]),
            const SizedBox(height: 10),
            Expanded(
              child: snapshot == null
                  ? const Center(
                      child: Text('센서 프레임을 기다리고 있습니다',
                          style: TextStyle(color: kMuted)))
                  : Center(
                      child: AspectRatio(
                        aspectRatio: snapshot.width / snapshot.height,
                        child: Container(
                          decoration: BoxDecoration(
                            color: kSurface,
                            border: Border.all(
                                color: stale ? kAmber : kLine, width: 2),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: CustomPaint(
                            painter: _VisionPainter(
                              snapshot: snapshot,
                              showCoverage: _showCoverage,
                            ),
                          ),
                        ),
                      ),
                    ),
            ),
            const SizedBox(height: 8),
            Text(
              stale
                  ? '프레임이 멈췄습니다 — 배선과 전원을 확인하세요'
                  : '초록 = 판정이 사람으로 본 zone · 회색 = 배경 대비 차이',
              style: TextStyle(fontSize: 12, color: stale ? kAmber : kDim),
            ),
          ]),
        ),
      ),
    );
  }

  Widget _toggle() => TextButton.icon(
        key: const ValueKey('vision-toggle'),
        onPressed: () => setState(() => _showCoverage = !_showCoverage),
        style: TextButton.styleFrom(
            foregroundColor: kInk, backgroundColor: kSurface),
        icon: Icon(_showCoverage ? Icons.layers : Icons.layers_clear, size: 18),
        label: Text(_showCoverage ? '차이값 켬' : '마스크만'),
      );
}

class _VisionPainter extends CustomPainter {
  _VisionPainter({required this.snapshot, required this.showCoverage});

  final VisionSnapshot snapshot;
  final bool showCoverage;

  @override
  void paint(Canvas canvas, Size size) {
    final cols = snapshot.width;
    final rows = snapshot.height;
    if (cols == 0 || rows == 0) return;
    final cellW = size.width / cols;
    final cellH = size.height / rows;
    final paint = Paint()..style = PaintingStyle.fill;

    final coverage = snapshot.coverage;
    if (showCoverage && coverage != null && coverage.length >= cols * rows) {
      for (var r = 0; r < rows; r++) {
        for (var c = 0; c < cols; c++) {
          final v = coverage[r * cols + c];
          if (v < 8) continue; // 배경과 같은 zone 은 안 그린다
          paint.color = Color.fromARGB(255, v, v, v);
          canvas.drawRect(
              Rect.fromLTWH(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5),
              paint);
        }
      }
    }

    final mask = snapshot.mask;
    if (mask != null && mask.length >= cols * rows) {
      paint.color = kGreen.withValues(alpha: showCoverage ? 0.55 : 1.0);
      for (var r = 0; r < rows; r++) {
        for (var c = 0; c < cols; c++) {
          if (mask[r * cols + c] == 0) continue;
          canvas.drawRect(
              Rect.fromLTWH(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5),
              paint);
        }
      }
    }
  }

  @override
  bool shouldRepaint(_VisionPainter old) =>
      old.snapshot != snapshot || old.showCoverage != showCoverage;
}
