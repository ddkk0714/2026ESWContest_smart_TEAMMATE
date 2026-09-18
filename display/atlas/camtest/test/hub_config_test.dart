import 'dart:io';

import 'package:camtest/hub_config.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('normalizeIp', () {
    test('IPv4 만 받는다', () {
      expect(normalizeIp('172.16.34.198'), '172.16.34.198');
      expect(normalizeIp('  10.0.0.1  '), '10.0.0.1');
      // 키패드로 0 을 먼저 찍는 일이 흔하다. 거절하지 말고 정규화한다.
      expect(normalizeIp('01.2.3.004'), '1.2.3.4');
    });

    test('주소가 아닌 것은 돌려보낸다', () {
      expect(normalizeIp('172.16.34'), isNull);
      expect(normalizeIp('172.16.34.'), isNull);
      expect(normalizeIp('999.1.1.1'), isNull);
      expect(normalizeIp('1.2.3.4.5'), isNull);
      expect(normalizeIp(''), isNull);
      // 호스트 이름은 이 화면으로 못 친다(키보드가 없다).
      expect(normalizeIp('deskmate.local'), isNull);
    });
  });

  test('주소와 URL 은 서로 되돌릴 수 있다', () {
    expect(hubUrlFor('172.16.34.198'), 'http://172.16.34.198:8765');
    expect(ipFromUrl('http://172.16.34.198:8765'), '172.16.34.198');
    expect(ipFromUrl(null), isNull);
    expect(ipFromUrl(''), isNull);
    expect(ipFromUrl('http://deskmate.local:8765'), isNull);
  });

  group('HubStore', () {
    late Directory dir;

    setUp(() => dir = Directory.systemTemp.createTempSync('camtest-hub'));
    tearDown(() => dir.deleteSync(recursive: true));

    test('적고 · 읽고 · 지운다', () {
      final store = HubStore(candidates: ['${dir.path}/cfg/hub.txt']);

      expect(store.read(), isNull);
      expect(store.save('http://172.16.34.198:8765'), isTrue);
      expect(store.read(), 'http://172.16.34.198:8765');
      expect(store.clear(), isTrue);
      expect(store.read(), isNull);
    });

    test('못 쓰는 경로는 건너뛰고 다음 후보에 적는다', () {
      // 부모가 파일이면 디렉터리를 못 만든다. 보드에서 설치 경로가 읽기 전용인
      // 경우와 같은 실패다.
      final blocked = File('${dir.path}/blocked')..writeAsStringSync('x');
      final store = HubStore(candidates: [
        '${blocked.path}/hub.txt',
        '${dir.path}/ok/hub.txt',
      ]);

      expect(store.save('http://10.0.0.5:8765'), isTrue);
      expect(File('${dir.path}/ok/hub.txt').existsSync(), isTrue);
      expect(store.read(), 'http://10.0.0.5:8765');
    });

    test('아무 데도 못 적으면 false — 조용히 성공한 척하지 않는다', () {
      final blocked = File('${dir.path}/blocked2')..writeAsStringSync('x');
      final store = HubStore(candidates: ['${blocked.path}/hub.txt']);

      expect(store.save('http://10.0.0.5:8765'), isFalse);
      expect(store.read(), isNull);
    });

    test('빈 줄만 있는 파일은 없는 것으로 친다', () {
      final path = '${dir.path}/empty/hub.txt';
      File(path)
        ..createSync(recursive: true)
        ..writeAsStringSync('\n\n');

      expect(HubStore(candidates: [path]).read(), isNull);
    });
  });
}
