// MQTT 연결을 한 겹 감싼다. 테스트는 같은 인터페이스의 가짜를 넣는다.
//
// 클라이언트 설정(keepAlive 30 s·자동 재연결·재구독·UTF-8 송수신)은 ui_env_app
// state_source.dart 의 MqttStateSource 와 같다.
library;

import 'dart:async';

import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';

import 'payload.dart';

class LiveMessage {
  const LiveMessage(this.topic, this.text, {this.retained = false});
  final String topic;
  final String text;
  final bool retained;
}

abstract class LiveTransport {
  Stream<LiveMessage> get messages;

  /// 연결이 붙으면 true, 끊기면 false.
  Stream<bool> get connection;
  bool get isConnected;

  /// 연결에 성공하면 true. 실패해도 예외를 밖으로 내지 않는다.
  Future<bool> connect();
  void subscribe(String topic);
  void publish(String topic, String text, {bool retain = false});
  void close();
}

typedef LiveTransportFactory = LiveTransport Function(String host, int port);

class MqttLiveTransport implements LiveTransport {
  MqttLiveTransport(String host, int port)
      : _client = MqttServerClient.withPort(
            host, 'deskmate-ui-${DateTime.now().microsecondsSinceEpoch}', port) {
    _client.logging(on: false);
    _client.keepAlivePeriod = 30;
    _client.connectTimeoutPeriod = 4000;
    _client.autoReconnect = true;
    _client.resubscribeOnAutoReconnect = true;
    _client.onConnected = () => _emit(true);
    _client.onAutoReconnected = () => _emit(true);
    _client.onAutoReconnect = () => _emit(false);
    _client.onDisconnected = () => _emit(false);
    _client.connectionMessage = MqttConnectMessage()
        .withClientIdentifier(_client.clientIdentifier)
        .startClean();
  }

  final MqttServerClient _client;
  final _messages = StreamController<LiveMessage>.broadcast();
  final _connection = StreamController<bool>.broadcast();
  StreamSubscription<List<MqttReceivedMessage<MqttMessage?>>>? _updates;

  void _emit(bool connected) {
    if (!_connection.isClosed) _connection.add(connected);
  }

  @override
  Stream<LiveMessage> get messages => _messages.stream;

  @override
  Stream<bool> get connection => _connection.stream;

  @override
  bool get isConnected =>
      _client.connectionStatus?.state == MqttConnectionState.connected;

  @override
  Future<bool> connect() async {
    try {
      final status = await _client.connect();
      if (status?.state != MqttConnectionState.connected) {
        _client.disconnect();
        return false;
      }
      _updates ??= _client.updates?.listen(_onUpdates);
      return true;
    } on Object {
      try {
        _client.disconnect();
      } on Object {
        // 이미 끊김
      }
      return false;
    }
  }

  void _onUpdates(List<MqttReceivedMessage<MqttMessage?>>? messages) {
    for (final received in messages ?? const []) {
      final publish = received.payload;
      if (publish is! MqttPublishMessage || _messages.isClosed) continue;
      _messages.add(LiveMessage(
          received.topic, mqttPayloadText(publish.payload.message),
          retained: publish.header?.retain == true));
    }
  }

  @override
  void subscribe(String topic) => _client.subscribe(topic, MqttQos.atLeastOnce);

  @override
  void publish(String topic, String text, {bool retain = false}) {
    if (!isConnected) return;
    final builder = MqttClientPayloadBuilder()..addUTF8String(text);
    _client.publishMessage(topic, MqttQos.atLeastOnce, builder.payload!,
        retain: retain);
  }

  @override
  void close() {
    _updates?.cancel();
    _client.autoReconnect = false;
    try {
      _client.disconnect();
    } on Object {
      // 이미 끊김
    }
    _messages.close();
    _connection.close();
  }
}
