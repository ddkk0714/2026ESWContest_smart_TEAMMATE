import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:deskmate_display/state_source.dart';

void main() {
  test('hub 의 UTF-8 한글 payload 를 깨지지 않게 읽는다 (10-24 실기: 개인화 설명이 Latin-1 로 깨짐)', () {
    const text = '{"explanation":"조도 10 lx 어두움 · 졸음 2회 → 기본 정책"}';
    final bytes = utf8.encode(text);
    expect(mqttPayloadText(bytes), text);
    expect(jsonDecode(mqttPayloadText(bytes))['explanation'], '조도 10 lx 어두움 · 졸음 2회 → 기본 정책');
  });

  test('깨진 바이트가 섞여도 예외 없이 대체 문자로 읽는다', () {
    expect(mqttPayloadText([0x7b, 0xff, 0x7d]), '{�}');
  });

  test('보내는 payload 도 UTF-8 이다 (addUTF8String)', () {
    final builder = MqttClientPayloadBuilder()..addUTF8String('{"note":"졸려요"}');
    expect(utf8.decode(builder.payload!.toList()), '{"note":"졸려요"}');
  });
}
