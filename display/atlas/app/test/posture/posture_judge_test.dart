import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:deskmate_display/posture/posture_judge.dart';
import 'package:deskmate_display/posture/vision_frame.dart';
import 'package:flutter_test/flutter_test.dart';

/// 판정 Dart 포팅이 Python 원본과 **같은 숫자**를 내는지 채점한다.
///
/// 골든 벡터는 `espcam_with_decision` 의 `tools/gen_posture_golden.py` 가 원본
/// 판정을 돌려 만든 것이다(`test/posture/golden/posture_golden.json`). 입력을 사각형
/// 목록으로 기술하므로 여기서 같은 마스크를 다시 만들 수 있다.
///
/// 이 테스트가 깨지면 포팅이 틀린 것이다. 골든 파일을 고쳐서 맞추지 말 것.
void main() {
  final file = File('test/posture/golden/posture_golden.json');
  if (!file.existsSync()) {
    // 파일 경로가 어긋나면 조용히 통과하는 것이 제일 나쁘다.
    fail('골든 벡터가 없다: ${file.absolute.path}');
  }
  final doc = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
  final meta = doc['meta'] as Map<String, dynamic>;
  final rows = meta['rows'] as int;
  final cols = meta['cols'] as int;
  final fps = (meta['fps'] as num).toDouble();
  final bgFrames = meta['bg_frames'] as int;
  final baselineFrames = meta['baseline_frames'] as int;
  final baselineRects = meta['baseline_rects'] as List;

  // 부동소수 차이 허용치. 골든이 소수 9자리로 반올림돼 있으므로 그 이하로 두면
  // 반올림 자체에 걸린다.
  const tolerance = 1e-8;

  List<bool> maskOf(List rects) {
    final mask = List<bool>.filled(rows * cols, false);
    for (final rect in rects) {
      final r0 = (rect[0] as num).toInt();
      final c0 = (rect[1] as num).toInt();
      final h = (rect[2] as num).toInt();
      final w = (rect[3] as num).toInt();
      for (var r = r0 < 0 ? 0 : r0; r < r0 + h && r < rows; r++) {
        for (var c = c0 < 0 ? 0 : c0; c < c0 + w && c < cols; c++) {
          mask[r * cols + c] = true;
        }
      }
    }
    return mask;
  }

  Grid<double> zoneOf(List rects) => maskToZone(maskOf(rects), rows, cols);

  void same(String where, double? got, Object? want) {
    if (want == null) {
      expect(got == null || got.isNaN, isTrue,
          reason: '$where: 원본은 NaN 인데 포팅은 $got');
      return;
    }
    final expected = (want as num).toDouble();
    expect(got, isNotNull, reason: '$where: 값이 없다');
    expect(got!.isNaN, isFalse, reason: '$where: 포팅이 NaN 을 냈다');
    expect((got - expected).abs() <= tolerance + tolerance * expected.abs(),
        isTrue,
        reason: '$where: 포팅 $got != 원본 $expected');
  }

  for (final entry in doc['cases'] as List) {
    final caseMap = entry as Map<String, dynamic>;
    final name = caseMap['name'] as String;

    test('판정이 원본과 같다 — $name', () {
      final tracker = PostureTracker();
      var t = 0.0;

      tracker.startBackground(samples: bgFrames);
      for (var i = 0; i < bgFrames; i++) {
        t += 1 / fps;
        tracker.update(zoneOf(const []), t);
      }
      tracker.startBaseline(samples: baselineFrames);
      for (var i = 0; i < baselineFrames; i++) {
        t += 1 / fps;
        tracker.update(zoneOf(baselineRects), t);
      }

      final base = tracker.baseline;
      expect(base, isNotNull, reason: 'baseline 을 못 잡았다');
      final wantBase = caseMap['baseline'] as Map<String, dynamic>;
      same('$name baseline.top_row', base!.topRow, wantBase['top_row']);
      same('$name baseline.spread', base.spread, wantBase['spread']);
      same('$name baseline.head_mm', base.headMm, wantBase['head_mm']);
      same('$name baseline.head_w', base.headW, wantBase['head_w']);
      expect(base.samples, wantBase['samples']);
      expect(base.clipped, wantBase['clipped']);

      final live = caseMap['live'] as List;
      final expected = caseMap['expect'] as List;
      expect(live.length, expected.length);

      for (var i = 0; i < live.length; i++) {
        t += 1 / fps;
        final got = tracker.update(zoneOf(live[i] as List), t);
        final want = expected[i] as Map<String, dynamic>;
        final where = '$name[$i]';

        expect(got.label, want['label'], reason: '$where 라벨');
        expect(got.present, want['present'], reason: '$where present');
        same('$where.phi', got.phi, want['phi']);
        same('$where.delta', got.delta, want['delta']);
        same('$where.nod_rate', got.nodRate, want['nod_rate']);
        same('$where.occupancy', got.features.occupancy, want['occupancy']);
        same('$where.top_row', got.features.topRow, want['top_row']);
        same('$where.centroid_row', got.features.centroidRow,
            want['centroid_row']);
        same('$where.spread', got.features.spread, want['spread']);
        same('$where.head_mm', got.features.headMm, want['head_mm']);
        same('$where.motion', got.features.motion, want['motion']);
        same('$where.head_w', got.features.headW, want['head_w']);

        final wantParts = want['parts'] as Map<String, dynamic>;
        for (final key in wantParts.keys) {
          same('$where.parts.$key', got.parts[key], wantParts[key]);
        }
        expect(got.parts.keys.toSet(), wantParts.keys.toSet(),
            reason: '$where parts 키가 다르다');
      }
    });
  }

  group('와이어 포맷', () {
    Uint8List framed(int type, int w, int h, Uint8List payload, {int? crc}) {
      final out = BytesBuilder();
      out.add(visionMagic);
      final head = ByteData(10)
        ..setUint8(0, type)
        ..setUint8(1, 0)
        ..setUint16(2, w, Endian.little)
        ..setUint16(4, h, Endian.little)
        ..setUint16(6, payload.length, Endian.little)
        ..setUint16(8, crc ?? crc16(payload), Endian.little);
      out.add(head.buffer.asUint8List());
      out.add(payload);
      return out.toBytes();
    }

    final payload =
        Uint8List.fromList([for (var i = 0; i < 54 * 42; i++) (i * 7) % 256]);

    test('온전한 프레임을 뜯는다', () {
      final decoder = VisionDecoder();
      final got = decoder.feed([
        ...utf8.encode('log text\n'),
        ...framed(typeMask, 54, 42, payload),
      ]);

      expect(got.length, 1);
      expect(got.first.type, typeMask);
      expect(got.first.width, 54);
      expect(got.first.height, 42);
      expect(got.first.payload, payload);
      expect(decoder.badCrc, 0);
    });

    test('CRC 가 틀린 프레임은 버린다', () {
      final decoder = VisionDecoder();
      final got = decoder.feed(framed(typeMask, 54, 42, payload, crc: 0x0000));

      expect(got, isEmpty);
      expect(decoder.badCrc, 1);
    });

    test('조각나서 들어와도 이어 붙인다', () {
      final decoder = VisionDecoder();
      final whole = framed(typeMask, 54, 42, payload);
      final got = <VisionFrame>[];
      for (var i = 0; i < whole.length; i += 97) {
        got.addAll(decoder.feed(
            whole.sublist(i, i + 97 > whole.length ? whole.length : i + 97)));
      }

      expect(got.length, 1);
      expect(got.first.payload, payload);
    });

    test('CRC-16/CCITT-FALSE 값이 Python 과 같다', () {
      // Python: binascii.crc_hqx(b"123456789", 0xFFFF) == 0x29B1
      expect(crc16(utf8.encode('123456789')), 0x29B1);
    });
  });
}
