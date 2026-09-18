/// ESP32-CAM 이 UART 로 보내는 프레임을 뜯고, 마스크를 zone 거리 배열로 바꾼다.
///
/// `espcam_with_decision` 의 `tools/esp_source.py` 를 옮긴 것이다. 와이어 포맷과
/// 상수는 그쪽과 **정확히** 같아야 한다 - 다르면 프레임이 통째로 버려지거나,
/// 거리 추정이 어긋나 판정이 조용히 틀린다.
///
/// 프레임 한 장:
///   A5 5A | type(u8) seq(u8) w(u16) h(u16) len(u16) crc(u16) | payload[len]
///   모든 수는 리틀엔디안. crc 는 payload 에 대한 CRC-16/CCITT-FALSE.
library;

import 'dart:collection';
import 'dart:math' as math;
import 'dart:typed_data';

import 'posture_judge.dart' show Grid, maxRangeMm, clipValue, medianOf;

const List<int> visionMagic = [0xA5, 0x5A];
const int headerLength = 12;
const int maxPayload = 64 * 1024;

const int typeCoverage = 1;
const int typeMask = 2;
const int typeSkeleton = 3;
const int typePreview = 4;
const int typeRaw = 6;
const int typeGraph = 7;
const Set<int> imageTypes = {
  typeCoverage,
  typeMask,
  typeSkeleton,
  typePreview,
  typeRaw
};

// 겉보기 크기 -> 거리 되만들기. 실센서(VL53L9CX)로 바꾸면 이 추정은 사라진다.
const double refScale = 0.24;
const double refMm = 700.0;
const double backgroundMm = 2400.0; // 벽
const double deskFarMm = 1300.0; // 책상 안쪽
const double deskNearMm = 950.0; // 책상 앞쪽
const double deskTop = 0.74; // 이 높이부터 아래가 책상
const double minScale = 0.02;

/// CRC-16/CCITT-FALSE. Python `binascii.crc_hqx(data, 0xFFFF)` 와 같은 값.
int crc16(List<int> data) {
  var crc = 0xFFFF;
  for (final byte in data) {
    crc ^= (byte & 0xFF) << 8;
    for (var bit = 0; bit < 8; bit++) {
      crc = (crc & 0x8000) != 0 ? ((crc << 1) ^ 0x1021) : (crc << 1);
      crc &= 0xFFFF;
    }
  }
  return crc;
}

class VisionFrame {
  const VisionFrame(this.type, this.width, this.height, this.payload);

  final int type;
  final int width;
  final int height;
  final Uint8List payload;
}

/// 바이트를 계속 넣으면 온전한 프레임만 돌려준다.
///
/// ESP 는 같은 포트로 로그 텍스트도 보낸다. 매직을 못 찾은 앞부분은 텍스트로 보고
/// 버린다 - 마지막 한 바이트가 0xA5 면 다음 조각과 이어질 수 있으니 남긴다.
class VisionDecoder {
  final List<int> _buf = [];

  /// 버린 바이트 수. 링크가 이상할 때 화면에 보여줄 수 있게 세어 둔다.
  int droppedBytes = 0;
  int badCrc = 0;

  /// ESP 가 같은 포트로 흘려보내는 로그 줄. 캘리브레이션이 `bg captured` 를
  /// 세어 센서 쪽 배경 재캡처가 끝났는지 본다. 원본(`esp_source._Link.log`)과
  /// 같은 길이로 둔다 - 세는 쪽이 직전 개수와 비교하는 방식이라 짧아도 된다.
  final ListQueue<String> log = ListQueue<String>();
  static const int _logLimit = 8;

  /// 로그에 찍힌 `bg captured` 개수.
  int get bgMarks {
    var n = 0;
    for (final line in log) {
      if (line.contains('bg captured')) n++;
    }
    return n;
  }

  void _text(List<int> raw) {
    if (raw.isEmpty) return;
    // 깨진 바이트가 섞여도 죽지 않게 - 프레임이 어긋나면 여기로 샌다.
    final text = String.fromCharCodes([
      for (final b in raw)
        if (b == 9 || b == 10 || (b >= 32 && b < 127)) b else 46
    ]);
    for (final line in text.split('\n')) {
      final trimmed = line.trim();
      if (trimmed.isEmpty) continue;
      log.addLast(trimmed);
      while (log.length > _logLimit) {
        log.removeFirst();
      }
    }
  }

  List<VisionFrame> feed(List<int> chunk) {
    _buf.addAll(chunk);
    final out = <VisionFrame>[];
    while (true) {
      final start = _indexOfMagic();
      if (start < 0) {
        // 프레임이 아닌 건 전부 로그 텍스트다. 헤더 앞부분일 수 있는 마지막 한
        // 바이트만 남긴다.
        final keep = (_buf.isNotEmpty && _buf.last == visionMagic[0]) ? 1 : 0;
        _text(_buf.sublist(0, _buf.length - keep));
        droppedBytes += _buf.length - keep;
        _buf.removeRange(0, _buf.length - keep);
        return out;
      }
      if (start > 0) {
        _text(_buf.sublist(0, start));
        droppedBytes += start;
        _buf.removeRange(0, start);
      }
      if (_buf.length < headerLength) return out;

      final type = _buf[2];
      final width = _buf[4] | (_buf[5] << 8);
      final height = _buf[6] | (_buf[7] << 8);
      final length = _buf[8] | (_buf[9] << 8);
      final crc = _buf[10] | (_buf[11] << 8);

      final known = imageTypes.contains(type) || type == typeGraph;
      if (!known ||
          length == 0 ||
          length > maxPayload ||
          (imageTypes.contains(type) && width * height != length)) {
        _buf.removeRange(0, 2); // 매직처럼 보였을 뿐이다
        droppedBytes += 2;
        continue;
      }
      if (_buf.length < headerLength + length) return out;

      final payload = Uint8List.fromList(
          _buf.sublist(headerLength, headerLength + length));
      if (crc16(payload) != crc) {
        _buf.removeRange(0, 2);
        badCrc++;
        continue;
      }
      _buf.removeRange(0, headerLength + length);
      if (imageTypes.contains(type)) {
        out.add(VisionFrame(type, width, height, payload));
      }
    }
  }

  int _indexOfMagic() {
    for (var i = 0; i + 1 < _buf.length; i++) {
      if (_buf[i] == visionMagic[0] && _buf[i + 1] == visionMagic[1]) return i;
    }
    return -1;
  }
}

/// 몸통 폭 / 화면 폭. 없으면 0.
double scaleOf(List<bool> mask, int rows, int cols) {
  final widths = List<int>.filled(rows, 0);
  var peak = 0;
  for (var r = 0; r < rows; r++) {
    var n = 0;
    final base = r * cols;
    for (var c = 0; c < cols; c++) {
      if (mask[base + c]) n++;
    }
    widths[r] = n;
    if (n > peak) peak = n;
  }
  if (peak == 0) return 0.0;
  final threshold = math.max(peak * 0.5, 1.0);
  final wide = <double>[
    for (final w in widths)
      if (w >= threshold) w.toDouble()
  ];
  return medianOf(wide) / cols;
}

final Map<int, Grid<double>> _bgCache = {};

/// 책상 + 벽 합성 기하. 사람이 아닌 zone 에 넣을 값이다.
///
/// 판정에는 영향이 없다 - 배경 캘리브레이션이 이 값을 그대로 기준으로 잡으므로
/// 침입량(ref - depth)은 달라지지 않는다. 화면에서 무엇을 보고 있는지 읽히게
/// 하려고 둔 것이다.
Grid<double> syntheticBackground(int rows, int cols) {
  final key = rows * 10000 + cols;
  final cached = _bgCache[key];
  if (cached != null) return cached;
  final data = List<double>.filled(rows * cols, backgroundMm);
  final top = (rows * deskTop).toInt();
  for (var r = top; r < rows; r++) {
    final f = (r - top) / math.max(rows - top - 1, 1);
    final value = deskFarMm - (deskFarMm - deskNearMm) * f;
    final base = r * cols;
    for (var c = 0; c < cols; c++) {
      data[base + c] = value;
    }
  }
  final grid = Grid<double>(data, rows, cols);
  _bgCache[key] = grid;
  return grid;
}

/// 이진 마스크 -> zone 거리 배열. 거리는 겉보기 크기에서 추정한다.
Grid<double> maskToZone(List<bool> mask, int rows, int cols) {
  final background = syntheticBackground(rows, cols);
  final depth = List<double>.from(background.data);
  var any = false;
  for (final on in mask) {
    if (on) {
      any = true;
      break;
    }
  }
  final scale = scaleOf(mask, rows, cols);
  if (any && scale >= minScale) {
    final value = clipValue(refMm * refScale / scale, 200.0, maxRangeMm);
    for (var i = 0; i < mask.length; i++) {
      if (mask[i]) depth[i] = value;
    }
  }
  return Grid<double>(depth, rows, cols);
}

/// 바이트 마스크(0/1)를 bool 리스트로. 프레임 payload 가 이 모양으로 온다.
List<bool> maskFromBytes(Uint8List payload) =>
    [for (final byte in payload) byte != 0];
