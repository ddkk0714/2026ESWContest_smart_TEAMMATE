import 'dart:convert';
import 'dart:io';

import 'package:deskmate_display/placement_client.dart';
import 'package:flutter_test/flutter_test.dart';

/// 가짜 자세 서비스. 상태 코드와 본문을 정해 두고 받은 요청을 기록한다.
class _FakeService {
  _FakeService(this.server) {
    server.listen(_handle);
  }

  final HttpServer server;
  final requests = <(String, String)>[]; // (method, body)
  int status = 200;
  String placement = 'center';
  bool bodyIsJson = true;

  int get port => server.port;

  Map<String, Object?> _info() => {
        'placement': placement,
        'model': 'models/opt_l8_c256_$placement.json',
        'options': ['left', 'center', 'right'],
        'models': {
          'left': 'opt_l8_c256_left.json',
          'center': 'opt_l8_c256.json',
          'right': 'opt_l8_c256_right.json',
        },
        'changed_ms': 1769000000000,
        'switches': 2,
      };

  Future<void> _handle(HttpRequest req) async {
    final body = await utf8.decodeStream(req);
    requests.add((req.method, body));
    if (req.method == 'POST' && status == 200) {
      final decoded = jsonDecode(body) as Map;
      placement = decoded['placement'] as String;
    }
    req.response.statusCode = status;
    if (status == 200) {
      req.response.write(bodyIsJson ? jsonEncode(_info()) : 'not json');
    } else if (status == 400) {
      req.response.write(jsonEncode({'error': 'unknown placement'}));
    } else if (status == 500) {
      req.response.write('model load failed');
    }
    await req.response.close();
  }

  Future<void> close() => server.close(force: true);
}

void main() {
  late _FakeService service;
  late HttpPlacementClient client;

  setUp(() async {
    service = _FakeService(await HttpServer.bind(InternetAddress.loopbackIPv4, 0));
    client = HttpPlacementClient('127.0.0.1',
        port: service.port, timeout: const Duration(seconds: 2));
  });

  tearDown(() async {
    client.close();
    await service.close();
  });

  test('GET parses placement, model, options and models', () async {
    final info = await client.fetch();
    expect(info.placement, 'center');
    expect(info.model, 'models/opt_l8_c256_center.json');
    expect(info.options, ['left', 'center', 'right']);
    expect(info.models['left'], 'opt_l8_c256_left.json');
    expect(info.switches, 2);
    expect(info.summary, 'center · models/opt_l8_c256_center.json');
    expect(service.requests.single.$1, 'GET');
  });

  test('POST sends {"placement":..} as JSON with Content-Length and returns the new state',
      () async {
    final info = await client.apply('left');
    expect(info.placement, 'left');
    expect(service.requests.single.$1, 'POST');
    expect(jsonDecode(service.requests.single.$2), {'placement': 'left'});
  });

  test('404 is unsupported (old service)', () async {
    service.status = 404;
    await expectLater(
        client.fetch(),
        throwsA(isA<PlacementException>()
            .having((e) => e.failure, 'failure', PlacementFailure.unsupported)));
  });

  test('400 is invalid with the server message', () async {
    service.status = 400;
    await expectLater(
        client.apply('left'),
        throwsA(isA<PlacementException>()
            .having((e) => e.failure, 'failure', PlacementFailure.invalid)
            .having((e) => e.message, 'message', 'unknown placement')));
  });

  test('500 is serverError and keeps the detail', () async {
    service.status = 500;
    await expectLater(
        client.apply('right'),
        throwsA(isA<PlacementException>()
            .having((e) => e.failure, 'failure', PlacementFailure.serverError)
            .having((e) => e.userText, 'userText', contains('500'))));
  });

  test('non-JSON 200 body is other', () async {
    service.bodyIsJson = false;
    await expectLater(
        client.fetch(),
        throwsA(isA<PlacementException>()
            .having((e) => e.failure, 'failure', PlacementFailure.other)));
  });

  test('connection refused is unreachable', () async {
    final port = service.port;
    await service.close();
    final dead = HttpPlacementClient('127.0.0.1',
        port: port, timeout: const Duration(seconds: 2));
    try {
      await expectLater(
          dead.fetch(),
          throwsA(isA<PlacementException>()
              .having((e) => e.failure, 'failure', PlacementFailure.unreachable)));
    } finally {
      dead.close();
    }
  });

  test('placementKo names the three positions and the empty states', () {
    expect(placementKo('left'), '왼쪽');
    expect(placementKo('center'), '가운데');
    expect(placementKo('right'), '오른쪽');
    expect(placementKo('unknown'), '미정');
    expect(placementKo(null), '미설정');
  });
}
