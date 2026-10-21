import 'dart:convert';

import 'package:deskmate_display/tof_raw_client.dart';
import 'package:flutter_test/flutter_test.dart';

/// 중계기 `:8772/raw` 응답 모양(`hub/atlas-tof/relay/uart_relay_board.cpp` rawJson).
String rawJson({List<int>? dist, List<int>? status, bool haveB = true}) =>
    jsonEncode({
      'dev': '/dev/ttyAMA0',
      'frames': 120,
      'type5': 118,
      'sent': 118,
      'bad': 2,
      'sensors': [
        {
          'id': 0,
          'have': true,
          'age_ms': 180,
          'uart_seq': 7,
          'ts_ms': 123456,
          'frame_id': 55,
          'frame_seq': 54,
          'mask': 3,
          'flags': 3,
          'flip_rows': true,
          'flip_cols': true,
          'w': 8,
          'h': 8,
          'dist_mm': dist ?? List<int>.generate(64, (i) => 300 + i * 20),
          'status': status ?? List<int>.filled(64, 5),
        },
        if (haveB)
          {
            'id': 1,
            'have': true,
            'age_ms': 190,
            'frame_id': 55,
            'frame_seq': 54,
            'flags': 3,
            'w': 8,
            'h': 8,
            'dist_mm': List<int>.filled(64, 700),
            'status': List<int>.filled(64, 9),
          }
        else
          {'id': 1, 'have': false},
      ],
    });

void main() {
  group('TofRawSnapshot', () {
    test('parses both sensors and keeps the array order as sent', () {
      final snap = TofRawSnapshot.fromJson(jsonDecode(rawJson()) as Map);
      expect(snap.frames, 120);
      expect(snap.bad, 2);
      expect(snap.sensors.length, 2);
      final a = snap.sensor(0)!;
      expect(a.have, isTrue);
      expect(a.zoneCount, 64);
      // 방향은 펌웨어가 적용한 것 — 받은 순서 그대로(첫 칸 300, 마지막 300+63*20)
      expect(a.distMm.first, 300);
      expect(a.distMm.last, 300 + 63 * 20);
      expect(a.flags, 3);
      expect(a.ageMs, 180);
      expect(a.frameId, 55);
      expect(a.validCount, 64);
      expect(a.minMm, 300);
      expect(a.medianMm, 300 + 32 * 20);
    });

    test('a sensor without a frame yet is have=false with empty arrays', () {
      final snap =
          TofRawSnapshot.fromJson(jsonDecode(rawJson(haveB: false)) as Map);
      final b = snap.sensor(1)!;
      expect(b.have, isFalse);
      expect(b.distMm, isEmpty);
      expect(b.isValidZone(0), isFalse);
      expect(b.zoneMm(0), -1);
      expect(b.validCount, 0);
      expect(b.minMm, isNull);
    });

    test('-1 distance and non 5/6/9/10 status are invalid zones', () {
      final dist = List<int>.filled(64, 500);
      dist[0] = -1;
      dist[1] = 0;
      final status = List<int>.filled(64, 5);
      status[2] = 4; // 경계/잡음
      status[3] = 255;
      status[4] = 6;
      status[5] = 9;
      status[6] = 10;
      final snap = TofRawSnapshot.fromJson(
          jsonDecode(rawJson(dist: dist, status: status)) as Map);
      final a = snap.sensor(0)!;
      expect(a.isValidZone(0), isFalse);
      expect(a.isValidZone(1), isFalse);
      expect(a.isValidZone(2), isFalse);
      expect(a.isValidZone(3), isFalse);
      expect(a.isValidZone(4), isTrue);
      expect(a.isValidZone(5), isTrue);
      expect(a.isValidZone(6), isTrue);
      expect(a.isValidZone(7), isTrue);
      expect(a.zoneMm(2), -1);
      expect(a.zoneMm(7), 500);
      expect(a.validCount, 60);
    });

    test('status array may be absent on an old relay — distance alone counts',
        () {
      final frame = TofRawFrame.fromJson({
        'id': 0,
        'have': true,
        'w': 8,
        'h': 8,
        'dist_mm': [for (var i = 0; i < 64; i++) i == 3 ? -1 : 400],
      });
      expect(frame.status, isEmpty);
      expect(frame.isValidZone(0), isTrue);
      expect(frame.isValidZone(3), isFalse);
      expect(frame.zoneStatus(0), -1);
    });
  });

  group('TofPostureLabel', () {
    test('uses posture_ko when given and the table otherwise', () {
      final withKo = TofPostureLabel.fromJson({
        'valid': true,
        'posture': 'lean_forward',
        'posture_ko': '앞으로 숙임',
        'conf': 0.83,
        'present': true,
        'misplaced': false,
        'online_sensors': 2,
      });
      expect(withKo.label, '앞으로 숙임');
      expect(withKo.conf, closeTo(0.83, 1e-9));
      expect(withKo.onlineSensors, 2);

      final noKo = TofPostureLabel.fromJson({'valid': false, 'posture': 'away'});
      expect(noKo.label, '자리비움');
      expect(noKo.valid, isFalse);

      final unknown = TofPostureLabel.fromJson({'posture': 'new_label'});
      expect(unknown.label, 'new_label');
    });
  });

  group('normalizePreviewHost', () {
    test('accepts ip or host name and strips scheme, port and path', () {
      expect(normalizePreviewHost('172.20.10.3'), '172.20.10.3');
      expect(normalizePreviewHost(' pi4-hub.local '), 'pi4-hub.local');
      expect(normalizePreviewHost('http://172.20.10.3:8771/'), '172.20.10.3');
      expect(normalizePreviewHost('172.20.10.3:8772'), '172.20.10.3');
      expect(normalizePreviewHost('172.20.10.3/raw'), '172.20.10.3');
    });

    test('rejects empty and non host text', () {
      expect(normalizePreviewHost(''), isNull);
      expect(normalizePreviewHost('   '), isNull);
      expect(normalizePreviewHost('not a host'), isNull);
      expect(normalizePreviewHost('ftp://'), isNull);
    });
  });

  test('HttpPreviewTofSource points at :8772/raw and :8771/api/state', () {
    final source = HttpPreviewTofSource('172.20.10.3');
    expect(source.rawUri.toString(), 'http://172.20.10.3:8772/raw');
    expect(source.postureUri.toString(), 'http://172.20.10.3:8771/api/state');
    source.close();
  });
}
