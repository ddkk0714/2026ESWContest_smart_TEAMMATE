/// 센서가 지금 보고 있는 것을 그대로 보여준다.
///
/// 화면에 자세 라벨만 뜨면 "잘 되고 있는 건지" 를 사람이 확인할 방법이 없다.
/// 노트북에서 `tools/posture_viewer.py` 로 보던 것과 같은 역할이다 - 내가 화각
/// 안에 있는지, 몸이 잘렸는지, 배경이 잘못 잡혔는지는 눈으로 봐야 안다.
///
/// 세 가지를 번갈아 본다.
///   * **거리** - 판정에 들어간 zone 거리(mm)를 노트북 뷰어와 같은 팔레트로 칠한다.
///     가까울수록 뜨겁다(검정 → 파랑 → 초록 → 빨강 → 노랑 → 흰색).
///   * **차이값** - ESP 가 보내는 coverage(0~255). 배경과 얼마나 다른가.
///   * **마스크** - RP2040 의 `vision.c` 가 만든 이진 실루엣.
///     **판정이 실제로 먹는 것은 이쪽이다.**
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'heat_palette.dart';
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

enum VisionMode { heat, coverage, mask }

class _VisionViewPageState extends State<VisionViewPage> {
  Timer? _timer;
  VisionSnapshot? _snapshot;
  // 노트북 뷰어와 같은 그림을 기본으로 둔다.
  VisionMode _mode = VisionMode.heat;

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
                      child: VisionPanel(
                          snapshot: snapshot, mode: _mode, stale: stale),
                    ),
            ),
            const SizedBox(height: 8),
            Text(
              stale
                  ? '프레임이 멈췄습니다 — 배선과 전원을 확인하세요'
                  : switch (_mode) {
                      VisionMode.heat => '가까울수록 뜨겁다 — 노트북 뷰어와 같은 팔레트',
                      VisionMode.coverage => '배경과 다를수록 밝다 (ESP 원본 coverage)',
                      VisionMode.mask => '초록 = 판정이 사람으로 본 zone',
                    },
              style: TextStyle(fontSize: 12, color: stale ? kAmber : kDim),
            ),
          ]),
        ),
      ),
    );
  }

  static const Map<VisionMode, (String, IconData)> _modeLook = {
    VisionMode.heat: ('거리', Icons.thermostat),
    VisionMode.coverage: ('차이값', Icons.gradient),
    VisionMode.mask: ('마스크', Icons.person_outline),
  };

  Widget _toggle() {
    final (name, icon) = _modeLook[_mode]!;
    return TextButton.icon(
      key: const ValueKey('vision-toggle'),
      onPressed: () => setState(() {
        final next = (_mode.index + 1) % VisionMode.values.length;
        _mode = VisionMode.values[next];
      }),
      style:
          TextButton.styleFrom(foregroundColor: kInk, backgroundColor: kSurface),
      icon: Icon(icon, size: 18),
      label: Text(name),
    );
  }
}

/// 센서 화면 한 장. 캘리브레이션 중에는 본 화면에도 이 패널이 뜬다 - 기준을 잡는
/// 동안 내가 화각 안에 제대로 있는지 볼 수 있어야 한다.
class VisionPanel extends StatelessWidget {
  const VisionPanel({
    super.key,
    required this.snapshot,
    this.mode = VisionMode.heat,
    this.stale = false,
  });

  final VisionSnapshot snapshot;
  final VisionMode mode;
  final bool stale;

  @override
  Widget build(BuildContext context) => AspectRatio(
        aspectRatio: snapshot.width / snapshot.height,
        child: Container(
          decoration: BoxDecoration(
            color: kSurface,
            border: Border.all(color: stale ? kAmber : kLine, width: 2),
            borderRadius: BorderRadius.circular(8),
          ),
          child: CustomPaint(
            painter: _VisionPainter(snapshot: snapshot, mode: mode),
          ),
        ),
      );
}

class _VisionPainter extends CustomPainter {
  _VisionPainter({required this.snapshot, required this.mode});

  final VisionSnapshot snapshot;
  final VisionMode mode;

  @override
  void paint(Canvas canvas, Size size) {
    final cols = snapshot.width;
    final rows = snapshot.height;
    if (cols == 0 || rows == 0) return;
    final cellW = size.width / cols;
    final cellH = size.height / rows;
    final paint = Paint()..style = PaintingStyle.fill;

    void fill(int index, Color color) {
      final r = index ~/ cols;
      final c = index % cols;
      paint.color = color;
      canvas.drawRect(
          Rect.fromLTWH(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5), paint);
    }

    switch (mode) {
      case VisionMode.heat:
        // 노트북 뷰어와 같은 그림. 판정에 들어간 거리 격자를 그대로 칠한다.
        final depth = snapshot.depthMm;
        if (depth != null && depth.length >= cols * rows) {
          for (var i = 0; i < cols * rows; i++) {
            fill(i, heatOfDepth(depth[i]));
          }
        }
      case VisionMode.coverage:
        final coverage = snapshot.coverage;
        if (coverage != null && coverage.length >= cols * rows) {
          for (var i = 0; i < cols * rows; i++) {
            final v = coverage[i];
            if (v < 8) continue; // 배경과 같은 zone 은 안 그린다
            fill(i, Color.fromARGB(255, v, v, v));
          }
        }
      case VisionMode.mask:
        final mask = snapshot.mask;
        if (mask != null && mask.length >= cols * rows) {
          for (var i = 0; i < cols * rows; i++) {
            if (mask[i] != 0) fill(i, kGreen);
          }
        }
    }
  }

  @override
  bool shouldRepaint(_VisionPainter old) =>
      old.snapshot != snapshot || old.mode != mode;
}
