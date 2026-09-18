/// 카메라가 지금 무엇을 보고 있는지, 그 위에 뼈대가 어디 찍히는지 보여 준다.
///
/// 판정만 보면 "왜 저렇게 나왔나" 를 알 수가 없다. 사람이 안 잡히는 건지, 잡히는데
/// 뼈대가 엉뚱한 데 찍히는 건지, 아예 렌즈가 가려진 건지는 그림을 봐야 갈린다.
///
/// 그림은 판정 서비스(`display/atlas/camsvc`)가 `127.0.0.1` 로만 내준다. 보드 밖으로
/// 나가지 않고, 앱도 받은 것을 저장하지 않는다.
library;

import 'dart:async';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import 'palette.dart';
import 'posture_source.dart';

/// 상반신 뼈대. MediaPipe 랜드마크 번호 쌍이다.
///
/// 33개를 다 잇지 않는다 - 책상 카메라는 다리를 못 보고, 판정도 위쪽 일곱 점만
/// 쓴다. 안 보이는 곳까지 그리면 모델이 짐작으로 찍은 선이 화면을 채운다.
const _bones = <List<int>>[
  [11, 12], // 어깨
  [11, 13], [13, 15], // 왼팔
  [12, 14], [14, 16], // 오른팔
  [11, 23], [12, 24], [23, 24], // 몸통
];

/// 판정이 실제로 쓰는 점. 나머지보다 크게 그린다.
const _judged = <int>{0, 11, 12, 13, 14, 15, 16};

class CameraViewPage extends StatefulWidget {
  const CameraViewPage({super.key, required this.source});

  final HttpPostureSource source;

  @override
  State<CameraViewPage> createState() => _CameraViewPageState();
}

class _CameraViewPageState extends State<CameraViewPage> {
  Timer? _timer;
  ui.Image? _image;
  List<Offset> _points = const [];
  String? _error;
  int _frames = 0;

  @override
  void initState() {
    super.initState();
    _tick();
    // 링크가 4fps 쯤이라 더 자주 물어도 같은 그림이 온다.
    _timer = Timer.periodic(const Duration(milliseconds: 350), (_) => _tick());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _image?.dispose();
    super.dispose();
  }

  Future<void> _tick() async {
    try {
      final frame = await widget.source.preview();
      if (!mounted) return;
      if (frame == null) {
        setState(() => _error = '아직 프레임이 없습니다');
        return;
      }
      final image = await frame.toImage();
      if (!mounted) {
        image.dispose();
        return;
      }
      setState(() {
        _image?.dispose();
        _image = image;
        _points = frame.points;
        _error = null;
        _frames++;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    final image = _image;
    return Scaffold(
      backgroundColor: kBg,
      appBar: AppBar(
        backgroundColor: kSurface,
        title: const Text('카메라 보기'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(
              child: Text(
                _points.isEmpty ? '뼈대 없음' : '뼈대 ${_points.length}점',
                style: TextStyle(
                    fontSize: 13,
                    color: _points.isEmpty ? kGray : kGreen),
              ),
            ),
          ),
        ],
      ),
      body: Center(
        child: image == null
            ? Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const CircularProgressIndicator(),
                  const SizedBox(height: 16),
                  Text(_error ?? '프레임을 기다리는 중…',
                      style: const TextStyle(color: kGray)),
                ],
              )
            : Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: AspectRatio(
                      aspectRatio: image.width / image.height,
                      child: CustomPaint(
                        painter: _CameraPainter(image: image, points: _points),
                      ),
                    ),
                  ),
                  Text(
                    _error ??
                        '${image.width}x${image.height} · $_frames장 받음',
                    style: const TextStyle(fontSize: 13, color: kGray),
                  ),
                ],
              ),
      ),
    );
  }
}

class _CameraPainter extends CustomPainter {
  _CameraPainter({required this.image, required this.points});

  final ui.Image image;
  final List<Offset> points;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawImageRect(
      image,
      Rect.fromLTWH(0, 0, image.width.toDouble(), image.height.toDouble()),
      Offset.zero & size,
      Paint()..filterQuality = FilterQuality.medium,
    );
    if (points.isEmpty) return;

    Offset at(int index) => Offset(
          points[index].dx * size.width,
          points[index].dy * size.height,
        );

    final bone = Paint()
      ..color = kGreen
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round;
    for (final pair in _bones) {
      if (pair[0] >= points.length || pair[1] >= points.length) continue;
      canvas.drawLine(at(pair[0]), at(pair[1]), bone);
    }

    for (var index = 0; index < points.length; index++) {
      final judged = _judged.contains(index);
      // 판정이 안 쓰는 점은 흐리게. 화면에서 무엇이 결과를 정하는지 보이게 한다.
      canvas.drawCircle(
        at(index),
        judged ? 4 : 2,
        Paint()..color = judged ? kAmber : kGray.withValues(alpha: 0.5),
      );
    }
  }

  @override
  bool shouldRepaint(_CameraPainter old) =>
      old.image != image || old.points != points;
}

/// 서비스가 보내는 preview 한 장 + 그 위의 랜드마크.
///
/// 바이트 규약은 `camsvc/src/service_api.h` 에 적혀 있다. 그림과 뼈대가 **같은
/// 프레임**이어야 해서 한 응답에 같이 온다.
class PreviewFrame {
  const PreviewFrame({
    required this.width,
    required this.height,
    required this.grey,
    required this.points,
  });

  final int width;
  final int height;
  final Uint8List grey;

  /// 0~1 정규화 좌표.
  final List<Offset> points;

  static PreviewFrame? parse(Uint8List body) {
    if (body.length < 6) return null;
    final view = ByteData.sublistView(body);
    final width = view.getUint16(0, Endian.little);
    final height = view.getUint16(2, Endian.little);
    final count = view.getUint16(4, Endian.little);
    if (width <= 0 || height <= 0) return null;

    final pixelsAt = 6 + count * 4;
    if (body.length < pixelsAt + width * height) return null;

    final points = <Offset>[];
    for (var i = 0; i < count; i++) {
      points.add(Offset(
        view.getUint16(6 + i * 4, Endian.little) / 65535.0,
        view.getUint16(6 + i * 4 + 2, Endian.little) / 65535.0,
      ));
    }
    return PreviewFrame(
      width: width,
      height: height,
      grey: Uint8List.sublistView(body, pixelsAt, pixelsAt + width * height),
      points: points,
    );
  }

  /// 흑백 바이트를 그릴 수 있는 이미지로. PNG 로 받지 않는 이유는 보드에 인코더를
  /// 하나 더 두지 않으려고다 - 19KB 라 그냥 보내는 편이 싸다.
  Future<ui.Image> toImage() {
    final rgba = Uint8List(width * height * 4);
    for (var i = 0; i < grey.length; i++) {
      final value = grey[i];
      final at = i * 4;
      rgba[at] = value;
      rgba[at + 1] = value;
      rgba[at + 2] = value;
      rgba[at + 3] = 255;
    }
    final done = Completer<ui.Image>();
    ui.decodeImageFromPixels(
      rgba,
      width,
      height,
      ui.PixelFormat.rgba8888,
      done.complete,
    );
    return done.future;
  }
}
