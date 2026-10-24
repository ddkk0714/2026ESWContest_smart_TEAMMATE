import 'workspace_test_helpers.dart';
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:deskmate_display/link_status.dart';
import 'package:deskmate_display/mqtt_probe.dart';
import 'package:deskmate_display/state_source.dart';
import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:deskmate_display/connection_page.dart';
import 'package:deskmate_display/main.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _short = Duration(milliseconds: 300);

Future<MqttProbeResult> _probe(_FakeBroker broker,
        {Duration health = _short,
        Duration state = _short,
        MqttProbeProgress? onStep}) =>
    probeMqtt('127.0.0.1', broker.port,
        tcpTimeout: _short,
        connackTimeout: _short,
        healthTimeout: health,
        firstStateTimeout: state,
        onStep: onStep);

void main() {
  testWidgets(
      'connection status follows PC topic without hub keystroke summary',
      (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final sample = {
      'schema_version': '1.0',
      'node': 'pc-collector',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000,
      'data': {'typing_active': false, 'window_s': 60},
    };
    final broker = await tester.runAsync(
        () => _FakeBroker.start(keystrokePayload: jsonEncode(sample)));
    final directory = Directory.systemTemp.createTempSync('pc-link-test-');
    final settings = SettingsStore(
        candidates: ['${directory.path}/settings.json'],
        legacyPostureCandidates: const []);
    settings
        .save(DeskmateSettings(mqttHost: '127.0.0.1', mqttPort: broker!.port));
    try {
      await pumpDeveloperApp(
          tester,
          DeskmateApp(
            settings: settings,
            prober: (host, port, {onStep}) async => const MqttProbeResult(
                ok: true, cause: LinkCause.none, elapsed: Duration.zero),
          ));
      for (var i = 0; i < 10; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 50)));
        await tester.pump(const Duration(seconds: 1));
        if (find.byTooltip('연결').evaluate().isNotEmpty) break;
      }
      await tester.tap(find.byTooltip('연결'));
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 150)));
      await tester.pump(const Duration(seconds: 1));
      final connected =
          tester.widget<ConnectionPage>(find.byType(ConnectionPage));
      expect(connected.snapshot[LinkId.keystroke]?.health, LinkHealth.ok,
          reason: 'An idle PC is connected even without hub summary metrics');

      // A board key must not hide a stale PC stream.
      await tester.sendKeyDownEvent(LogicalKeyboardKey.keyA);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.keyA);
      broker.publishKeystroke(jsonEncode({
        ...sample,
        'ts': DateTime.now()
                .subtract(const Duration(seconds: 11))
                .millisecondsSinceEpoch /
            1000,
      }));
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump(const Duration(seconds: 1));
      final stale = tester.widget<ConnectionPage>(find.byType(ConnectionPage));
      expect(stale.snapshot[LinkId.keystroke]?.cause, LinkCause.stale);
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.runAsync(broker.close);
      directory.deleteSync(recursive: true);
    }
  });

  test('display executes a hub command once and returns QoS 1 results',
      () async {
    final broker = await _FakeBroker.start();
    addTearDown(broker.close);
    final bluetooth = _CommandBluetooth();
    final coordinator = FeedbackCoordinator(
        music: _NoCommandMusic(),
        lamp: IlinkLampFeedback(bluetooth)..select('AA:BB:CC:DD:EE:01'),
        bluetooth: bluetooth)
      ..enabled = true;
    final source = MqttStateSource('127.0.0.1',
        port: broker.port, onControlCommand: coordinator.handleControlCommand);
    addTearDown(source.close);
    try {
      await source.fetch();
    } on StateError {/* Wait for subscriptions. */}
    for (var i = 0;
        i < 100 && !broker.subscriptions.contains('deskmate/control/cmd');
        i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    expect(broker.subscriptions, contains('deskmate/control/cmd'));
    final command = jsonEncode({
      'schema_version': '1.0',
      'node': 'hub',
      'data': {
        'command_id': 'lamp-1',
        'target_id': 'desk_lamp',
        'operation': 'set_brightness',
        'value': 70,
        'gate': 'suggest',
        'requires_confirmation': false,
        'expires_ts_ms': DateTime.now()
            .add(const Duration(seconds: 15))
            .millisecondsSinceEpoch,
      }
    });
    broker.publishControl(command);
    broker.publishControl(command);
    for (var i = 0; i < 100 && broker.controlResults.length < 2; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    expect(broker.controlResults, hasLength(2));
    expect(broker.controlResults.first['data']['command_id'], 'lamp-1');
    expect(broker.controlResults.first['data']['status'], 'succeeded');
    expect(bluetooth.frames, [
      ilinkFrame([1, 8, 5, 1]),
      ilinkFrame([1, 8, 1, 70])
    ]);
    broker.publishControl(command, retain: true);
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(broker.controlResults, hasLength(2),
        reason: 'Retained control must not replay');
    expect(bluetooth.frames, hasLength(2));
  });

  test('display receives PC samples independently of phase messages', () async {
    final sample = {
      'schema_version': '1.0',
      'node': 'pc-collector',
      'ts': DateTime.now().millisecondsSinceEpoch / 1000,
      'data': {'dwell_mean_ms': 92.4, 'typing_active': true, 'window_s': 60},
    };
    final broker =
        await _FakeBroker.start(keystrokePayload: jsonEncode(sample));
    addTearDown(broker.close);
    final source = MqttStateSource('127.0.0.1', port: broker.port);
    addTearDown(source.close);
    // Connecting can finish before the first publications arrive.
    try {
      await source.fetch();
    } on StateError {/* Wait below. */}
    for (var i = 0; i < 100 && source.pcKeystroke == null; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    expect(source.pcKeystroke?.node, 'pc-collector');
    expect(source.pcKeystroke?.dwellMeanMs, 92.4);
    expect(source.pcKeystroke?.timestamp?.millisecondsSinceEpoch,
        ((sample['ts'] as double) * 1000).round());
    final phase = await source.fetch();
    broker.publishKeystroke(jsonEncode({
      ...sample,
      'ts': (sample['ts'] as double) + 1,
      'data': {'dwell_mean_ms': 123.0, 'typing_active': false},
    }));
    for (var i = 0; i < 100 && source.pcKeystroke?.dwellMeanMs != 123; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    expect(source.pcKeystroke?.dwellMeanMs, 123);
    expect(source.pcKeystroke?.typingActive, isFalse);
    expect((await source.fetch()).sequence, phase.sequence);
  });

  group('probeMqtt', () {
    test('passes all four steps in order when broker, hub and state respond',
        () async {
      final broker = await _FakeBroker.start();
      addTearDown(broker.close);
      final steps = <MqttProbeStep>[];

      final result = await _probe(broker, onStep: steps.add);

      expect(result.ok, isTrue);
      expect(result.cause, LinkCause.none);
      expect(result.failedAt, isNull);
      expect(result.hubHealth, 'online');
      expect(steps, MqttProbeStep.values);
      await broker.expectAllClosed();
    });

    test('closed port is networkUnreachable at tcp', () async {
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final port = server.port;
      await server.close();

      final result = await probeMqtt('127.0.0.1', port, tcpTimeout: _short);

      expect(result.ok, isFalse);
      expect(result.cause, LinkCause.networkUnreachable);
      expect(result.failedAt, MqttProbeStep.tcp);
      expect(result.detail, isNotNull);
    });

    test('silent broker is brokerRefused at connack', () async {
      final broker = await _FakeBroker.start(respondToConnect: false);
      addTearDown(broker.close);

      final result = await _probe(broker);

      expect(result.cause, LinkCause.brokerRefused);
      expect(result.failedAt, MqttProbeStep.connack);
    });

    test('non-zero CONNACK return code is brokerRefused with the code',
        () async {
      final broker = await _FakeBroker.start(connackCode: 5);
      addTearDown(broker.close);

      final result = await _probe(broker);

      expect(result.cause, LinkCause.brokerRefused);
      expect(result.failedAt, MqttProbeStep.connack);
      expect(result.detail, contains('CONNACK'));
    });

    test('offline hub wins even if a retained state arrives', () async {
      final broker = await _FakeBroker.start(hubStatus: 'offline');
      addTearDown(broker.close);

      final result = await _probe(broker);

      expect(result.cause, LinkCause.hubOffline);
      expect(result.failedAt, MqttProbeStep.hubHealth);
      expect(result.hubHealth, 'offline');
      await broker.expectAllClosed();
    });

    test('missing health is not a failure when state arrives', () async {
      final broker = await _FakeBroker.start(hubStatus: null);
      addTearDown(broker.close);

      final result =
          await _probe(broker, health: const Duration(milliseconds: 80));

      expect(result.ok, isTrue);
      expect(result.hubHealth, isNull);
    });

    test('live hub without state is noState at firstState', () async {
      final broker = await _FakeBroker.start(sendState: false);
      addTearDown(broker.close);

      final result = await _probe(broker);

      expect(result.cause, LinkCause.noState);
      expect(result.failedAt, MqttProbeStep.firstState);
      expect(result.hubHealth, 'online');
      await broker.expectAllClosed();
    });

    test('accepts the envelope form of health', () async {
      final broker =
          await _FakeBroker.start(hubStatus: 'offline', envelopeHealth: true);
      addTearDown(broker.close);

      final result = await _probe(broker);

      expect(result.cause, LinkCause.hubOffline);
    });

    test('state payload is only counted, not validated', () async {
      final broker = await _FakeBroker.start(statePayload: 'not json');
      addTearDown(broker.close);

      final result = await _probe(broker);

      expect(result.ok, isTrue);
    });
  });
}

/// CONNECT·SUBSCRIBE 에 응답하고 retain PUBLISH 를 흉내 내는 가짜 브로커.
/// (Codex 가 만든 것을 바탕으로 CONNACK 코드·envelope health·연결 종료 확인을 더했다.)
class _FakeBroker {
  _FakeBroker(
    this._server, {
    required this.respondToConnect,
    required this.connackCode,
    required this.hubStatus,
    required this.envelopeHealth,
    required this.sendState,
    required this.statePayload,
    required this.keystrokePayload,
  }) {
    _server.listen((socket) {
      _sockets.add(socket);
      unawaited(_serve(socket));
    });
  }

  final ServerSocket _server;
  final bool respondToConnect;
  final int connackCode;
  final String? hubStatus;
  final bool envelopeHealth;
  final bool sendState;
  final String? statePayload;
  final String? keystrokePayload;
  final Set<Socket> _sockets = {};
  final subscriptions = <String>{};
  final controlResults = <Map<String, dynamic>>[];

  int get port => _server.port;

  static Future<_FakeBroker> start({
    bool respondToConnect = true,
    int connackCode = 0,
    String? hubStatus = 'online',
    bool envelopeHealth = false,
    bool sendState = true,
    String? statePayload,
    String? keystrokePayload,
  }) async =>
      _FakeBroker(
        await ServerSocket.bind(InternetAddress.loopbackIPv4, 0),
        respondToConnect: respondToConnect,
        connackCode: connackCode,
        hubStatus: hubStatus,
        envelopeHealth: envelopeHealth,
        sendState: sendState,
        statePayload: statePayload,
        keystrokePayload: keystrokePayload,
      );

  /// 진단이 끝나면 클라이언트가 연결을 모두 닫아야 한다(누수 없음).
  Future<void> expectAllClosed() async {
    for (var i = 0; i < 50 && _sockets.isNotEmpty; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    expect(_sockets, isEmpty, reason: '진단이 끝난 뒤에도 열린 연결이 남음');
  }

  Future<void> _serve(Socket socket) async {
    // 진단기가 먼저 끊은 뒤 쓰면 done 이 reset 오류로 끝난다. 테스트 실패가 아니다.
    unawaited(socket.done.then<void>((_) {}, onError: (Object _) {}));
    final input = StreamIterator<int>(socket.expand((bytes) => bytes));
    try {
      while (await input.moveNext()) {
        final header = input.current;
        final packet = await _readPacketBody(input);
        switch (header >> 4) {
          case 1: // CONNECT
            if (!respondToConnect) {
              await socket.done;
              return;
            }
            socket.add([0x20, 0x02, 0x00, connackCode]);
          case 8: // SUBSCRIBE
            if (packet.length < 2) continue;
            final id = packet.take(2).toList();
            final reasons = <int>[];
            final publications = <(String, String)>[];
            var offset = 2;
            while (offset + 2 <= packet.length) {
              final size = packet[offset] * 256 + packet[offset + 1];
              offset += 2;
              if (offset + size + 1 > packet.length) break;
              final topic = utf8.decode(packet.sublist(offset, offset + size));
              subscriptions.add(topic);
              offset += size + 1; // 필터 뒤 요청 QoS 1바이트
              reasons.add(0x00);
              if (topic == 'deskmate/health/hub' && hubStatus != null) {
                final flat = {'node': 'hub', 'status': hubStatus};
                publications.add((
                  topic,
                  jsonEncode(envelopeHealth
                      ? {'schema_version': '1.0', 'data': flat}
                      : flat)
                ));
              } else if (topic == 'deskmate/state/phase' && sendState) {
                publications
                    .add((topic, statePayload ?? jsonEncode(_stateEnvelope)));
              } else if (topic == 'deskmate/sensor/keystroke' &&
                  keystrokePayload != null) {
                publications.add((topic, keystrokePayload!));
              }
            }
            socket.add([0x90, id.length + reasons.length, ...id, ...reasons]);
            // SUBACK 을 클라이언트가 구독 목록에 반영한 다음 retain 을 보낸다.
            await Future<void>.delayed(const Duration(milliseconds: 10));
            for (final (topic, payload) in publications) {
              _publish(socket, topic, payload);
            }
          case 3: // PUBLISH from display
            final topicSize = packet[0] * 256 + packet[1];
            final topic = utf8.decode(packet.sublist(2, 2 + topicSize));
            var offset = 2 + topicSize;
            if ((header >> 1) & 3 == 1) {
              socket.add([0x40, 2, packet[offset], packet[offset + 1]]);
              offset += 2;
            }
            if (topic == 'deskmate/control/result') {
              expect((header >> 1) & 3, 1);
              expect(header & 1, 0);
              controlResults.add(jsonDecode(utf8.decode(packet.sublist(offset)))
                  as Map<String, dynamic>);
            }
          case 14: // DISCONNECT
            return;
        }
      }
    } on Object {
      // 진단 종료나 TCP 도달 확인용 빈 연결은 의도적으로 닫힐 수 있다.
    } finally {
      await input.cancel();
      socket.destroy();
      _sockets.remove(socket);
    }
  }

  Future<List<int>> _readPacketBody(StreamIterator<int> input) async {
    var multiplier = 1;
    var remaining = 0;
    while (true) {
      if (!await input.moveNext()) {
        throw const SocketException('socket closed');
      }
      final digit = input.current;
      remaining += (digit & 0x7f) * multiplier;
      if ((digit & 0x80) == 0) break;
      multiplier *= 128;
      if (multiplier > 128 * 128 * 128) {
        throw const FormatException('invalid MQTT remaining length');
      }
    }
    final body = <int>[];
    while (body.length < remaining) {
      if (!await input.moveNext()) {
        throw const SocketException('socket closed');
      }
      body.add(input.current);
    }
    return body;
  }

  void _publish(Socket socket, String topic, String payload,
      {bool retain = true}) {
    final topicBytes = utf8.encode(topic);
    final body = [
      topicBytes.length >> 8,
      topicBytes.length & 0xff,
      ...topicBytes,
      ...utf8.encode(payload),
    ];
    var remaining = body.length;
    final encodedLength = <int>[];
    do {
      var digit = remaining % 128;
      remaining ~/= 128;
      if (remaining > 0) digit |= 0x80;
      encodedLength.add(digit);
    } while (remaining > 0);
    socket.add([retain ? 0x31 : 0x30, ...encodedLength, ...body]); // QoS 0
  }

  void publishKeystroke(String payload) {
    for (final socket in _sockets) {
      _publish(socket, 'deskmate/sensor/keystroke', payload);
    }
  }

  void publishControl(String payload, {bool retain = false}) {
    for (final socket in _sockets) {
      _publish(socket, 'deskmate/control/cmd', payload, retain: retain);
    }
  }

  Future<void> close() async {
    for (final socket in _sockets.toList()) {
      socket.destroy();
    }
    await _server.close();
  }
}

class _CommandBluetooth implements AtlasBluetoothService {
  final frames = <List<int>>[];
  @override
  Future<GattConnection> connectLamp(String address) async =>
      const GattConnection(adapter: 'test', clientId: 'client');
  @override
  Future<void> writeCharacteristic(GattConnection connection,
      {required String serviceId,
      required String characteristicId,
      required List<int> value}) async {
    frames.add(List.of(value));
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnimplementedError();
}

class _NoCommandMusic implements MusicPlayback {
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnimplementedError();
}

const _stateEnvelope = {
  'schema_version': '1.0',
  'ts': 1769000002.0,
  'node': 'hub',
  'seq': 1,
  'data': {
    'fsm_state': 'FOCUS_PC',
    'phase': 'focus',
    'context': 'pc',
    'c_focus': 0.4,
    'c_fatigue': 0.2,
    'confidence': 0.8,
    'gate': 'none',
    'reasons': <String>[],
  },
};
