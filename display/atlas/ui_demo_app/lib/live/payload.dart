// V18 보드 연결의 공통 메시지 모양.
//
// 규칙은 ui_env_app(com.atlas.app.deskmate_display)의 state_source.dart 에서 복사했다.
// 앱끼리 import 하지 않고 복사하는 이유: 두 앱이 서로 다른 pubspec·빌드로 따로 설치되기 때문이다.
library;

import 'dart:convert';
import 'dart:math';

/// MQTT payload 바이트 → 문자열. hub 는 UTF-8 JSON 을 보낸다.
/// 바이트 하나를 글자 하나로 읽으면(옛 `bytesToStringAsString`) 한글이 `ì¡°ëª…` 처럼 깨진다(10-24 실기).
/// 깨진 바이트가 섞여 있어도 화면은 멈추지 않게 대체 문자로 둔다.
String mqttPayloadText(List<int> bytes) =>
    utf8.decode(bytes, allowMalformed: true);

/// JSON 객체를 `Map<String, dynamic>` 으로. 객체가 아니면 빈 맵.
Map<String, dynamic> jsonMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map((key, value) => MapEntry(key.toString(), value));
  }
  return const {};
}

/// 공통 envelope(`schema_version`·`ts`·`node`·`boot_id`·`seq`·`data`)를 만든다.
/// seq 는 노드별로 1 씩 오른다.
class EnvelopeWriter {
  EnvelopeWriter(
      {required this.node, String? bootId, DateTime Function()? clock})
      : bootId = bootId ?? 'v18-${DateTime.now().microsecondsSinceEpoch}',
        _clock = clock ?? DateTime.now;

  final String node;
  final String bootId;
  final DateTime Function() _clock;
  int _seq = 0;

  Map<String, Object?> wrap(Map<String, Object?> data) => {
        'schema_version': '1.0',
        'ts': _clock().millisecondsSinceEpoch / 1000,
        'node': node,
        'boot_id': bootId,
        'seq': ++_seq,
        'data': data,
      };

  String encode(Map<String, Object?> data) => jsonEncode(wrap(data));
}

/// `deskmate/feedback/user` 의 `data`. ui_env_app `buildFeedbackData` 와 같은 키에
/// 카드 종류(`kind`)를 하나 더 싣는다. hub 는 `kind == personalization` 만 따로 다루므로
/// 제안 카드 종류(…_suggest)는 기존 처리와 부딪히지 않는다.
Map<String, Object> buildFeedbackData({
  required String requestId,
  required String verdict,
  int? responseMs,
  String? kind,
}) =>
    {
      'request_id': requestId,
      'verdict': verdict,
      'response_ms': responseMs ?? 0,
      // personalization 은 hub 가 동의 처리로 읽는 값이라 카드 응답에는 싣지 않는다.
      if (kind != null && kind.isNotEmpty && kind != 'personalization')
        'kind': kind,
    };

final _random = Random.secure();

/// 이름과 무관한 프로필 식별자. 별명·이미지·순서로 거꾸로 알아낼 수 없게 난수만 쓴다.
String newProfileId() {
  final buffer = StringBuffer('p');
  for (var i = 0; i < 8; i++) {
    buffer.write(_random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}
