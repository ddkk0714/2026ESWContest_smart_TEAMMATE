/// 장치 위치 화면 — "장치를 보통 어디에 두나요?" 왼쪽 · 가운데 · 오른쪽.
///
/// 위에서 내려다본 책상 그림(아래 사용자 자리, 위 모니터, 세 위치)과 큰 버튼 셋.
/// 옆 위치는 모니터 옆 정도까지만 그린다 — PC 시뮬은 기기 방위각 ±50° 까지만 학습했고,
/// 왼쪽은 **앉은 사용자 기준** 왼쪽이다. 디자인은 단순하게.
///
/// 고르면 [onPick] 이 저장·Pi 4 적용을 하고 결과 문구([PlacementApplyResult])를 돌려준다.
/// 실패하면 이유와 '다시 시도' 를 보인다. [onSkip] 이 있으면 '건너뛰기'(첫 실행 질문).
library;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'placement_client.dart';
import 'placement_controller.dart';

class PlacementPage extends StatefulWidget {
  const PlacementPage({
    super.key,
    required this.current,
    required this.onPick,
    this.applied,
    this.onSkip,
    this.onDone,
  });

  /// 지금 저장된 값(left|center|right). 없으면 null.
  final String? current;

  /// Pi 4 에 적용된 값 설명(연결 탭의 appliedLabel). 없으면 안 그린다.
  final String? applied;
  final Future<PlacementApplyResult> Function(String placement) onPick;

  /// 첫 실행 질문일 때 '건너뛰기'. null 이면 버튼 없음.
  final VoidCallback? onSkip;

  /// 적용이 끝난 뒤 '닫기'. null 이면 버튼 없음(탭 안에 박혀 있을 때).
  final VoidCallback? onDone;

  @override
  State<PlacementPage> createState() => _PlacementPageState();
}

class _PlacementPageState extends State<PlacementPage> {
  String? _picked;
  bool _busy = false;
  PlacementApplyResult? _result;

  static const _options = [
    ('left', '1  왼쪽'),
    ('center', '2  가운데'),
    ('right', '3  오른쪽'),
  ];

  Future<void> _pick(String placement) async {
    if (_busy) return;
    setState(() {
      _picked = placement;
      _busy = true;
      _result = null;
    });
    final result = await widget.onPick(placement);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _result = result;
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final selected = _picked ?? widget.current;
    final result = _result;
    return Padding(
      key: const ValueKey('placement-page'),
      padding: const EdgeInsets.all(20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('장치를 보통 어디에 두나요?',
            key: const ValueKey('placement-title'),
            style: theme.textTheme.headlineSmall
                ?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 2),
        Text('앉은 자리에서 봤을 때', style: theme.textTheme.bodyLarge),
        const SizedBox(height: 12),
        Expanded(
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Expanded(
              flex: 5,
              child: CustomPaint(
                key: const ValueKey('placement-desk'),
                painter: _DeskPainter(selected),
              ),
            ),
            const SizedBox(width: 20),
            Expanded(
              flex: 4,
              child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final (key, label) in _options) ...[
                      SizedBox(
                        height: 60,
                        child: selected == key
                            ? FilledButton(
                                key: ValueKey('placement-$key'),
                                onPressed: _busy ? null : () => _pick(key),
                                child: Text(label,
                                    style: const TextStyle(
                                        fontSize: 22,
                                        fontWeight: FontWeight.w800)))
                            : OutlinedButton(
                                key: ValueKey('placement-$key'),
                                onPressed: _busy ? null : () => _pick(key),
                                child: Text(label,
                                    style: const TextStyle(
                                        fontSize: 22,
                                        fontWeight: FontWeight.w600))),
                      ),
                      const SizedBox(height: 10),
                    ],
                    if (widget.applied != null)
                      Text('Pi 4: ${widget.applied}',
                          key: const ValueKey('placement-applied-note'),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelMedium),
                  ]),
            ),
          ]),
        ),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(
            child: Text(
              _busy
                  ? '${placementKo(_picked)} 적용 중…'
                  : result?.text ??
                      (widget.current == null
                          ? '아직 고르지 않았어요. 선택하면 Pi 4 자세 모델이 그 위치용으로 바뀌어요.'
                          : '지금: ${placementKo(widget.current)}'),
              key: const ValueKey('placement-status'),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium?.copyWith(
                  color: result != null && !result.ok
                      ? DeskmateColors.offline
                      : DeskmateColors.ink),
            ),
          ),
          if (result != null && !result.ok && _picked != null)
            TextButton(
              key: const ValueKey('placement-retry'),
              onPressed: _busy ? null : () => _pick(_picked!),
              child: const Text('다시 시도'),
            ),
          if (widget.onSkip != null)
            TextButton(
              key: const ValueKey('placement-skip'),
              onPressed: _busy ? null : widget.onSkip,
              child: const Text('건너뛰기'),
            ),
          if (widget.onDone != null)
            FilledButton(
              key: const ValueKey('placement-done'),
              onPressed: _busy ? null : widget.onDone,
              child: const Text('닫기'),
            ),
        ]),
      ]),
    );
  }
}

/// 위에서 내려다본 책상: 위 모니터, 아래 사용자 자리, 세 위치(왼·가운데·오른). 고른 위치는 채운다.
class _DeskPainter extends CustomPainter {
  const _DeskPainter(this.selected);
  final String? selected;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    final line = Paint()
      ..color = DeskmateColors.inkMuted
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    final fill = Paint()..color = DeskmateColors.surfaceMuted;
    final pick = Paint()..color = DeskmateColors.accentStrong;
    final faint = Paint()
      ..color = DeskmateColors.inkFaint
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;

    // 책상
    final desk = Rect.fromLTWH(w * .08, h * .12, w * .84, h * .56);
    canvas.drawRRect(RRect.fromRectAndRadius(desk, const Radius.circular(10)), fill);
    canvas.drawRRect(RRect.fromRectAndRadius(desk, const Radius.circular(10)), line);
    // 모니터(위 가운데)
    final monitor = Rect.fromLTWH(w * .36, h * .16, w * .28, h * .08);
    canvas.drawRect(monitor, Paint()..color = DeskmateColors.ink);
    // 사용자 자리(아래)
    canvas.drawCircle(Offset(w * .5, h * .86), h * .07,
        Paint()..color = DeskmateColors.inkMuted);
    _text(canvas, '나', Offset(w * .5, h * .86), 12, Colors.white);
    _text(canvas, '모니터', Offset(w * .5, h * .07), 11, DeskmateColors.inkMuted);

    // 세 위치: 모니터 옆·앞(±50° 안쪽). 왼쪽 = 앉은 사용자 기준 왼쪽 = 그림의 왼쪽.
    final spots = <String, Offset>{
      'left': Offset(w * .22, h * .30),
      'center': Offset(w * .5, h * .34),
      'right': Offset(w * .78, h * .30),
    };
    const labels = {'left': '1', 'center': '2', 'right': '3'};
    for (final e in spots.entries) {
      final r = h * .075;
      final on = e.key == selected;
      canvas.drawCircle(e.value, r, on ? pick : (Paint()..color = DeskmateColors.surfaceRaised));
      canvas.drawCircle(e.value, r, on ? line : faint);
      _text(canvas, labels[e.key]!, e.value, 16,
          on ? Colors.white : DeskmateColors.inkMuted);
    }
  }

  void _text(Canvas canvas, String s, Offset center, double size, Color color) {
    final tp = TextPainter(
      text: TextSpan(
          text: s,
          style: TextStyle(fontSize: size, fontWeight: FontWeight.w700, color: color)),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, center - Offset(tp.width / 2, tp.height / 2));
  }

  @override
  bool shouldRepaint(_DeskPainter old) => old.selected != selected;
}

/// 첫 실행 질문·연결 탭 '바꾸기' 가 띄우는 대화상자. 적용 결과는 [PlacementController] 가 들고 있다.
Future<void> showPlacementDialog(
  BuildContext context,
  PlacementController controller, {
  bool allowSkip = false,
}) {
  return showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (context) => Dialog(
      insetPadding: const EdgeInsets.all(24),
      child: SizedBox(
        width: 900,
        height: 520,
        child: PlacementPage(
          current: controller.saved,
          applied: controller.appliedLabel,
          onPick: controller.apply,
          onSkip: allowSkip
              ? () {
                  controller.skip();
                  Navigator.of(context).pop();
                }
              : null,
          onDone: () => Navigator.of(context).pop(),
        ),
      ),
    ),
  );
}
