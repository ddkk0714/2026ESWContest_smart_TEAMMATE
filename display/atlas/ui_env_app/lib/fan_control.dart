/// DESKMATE 블루투스 선풍기(firmware/pico2w_ble_fan)를 Pi 4 허브의 `vent_fan` 명령으로 움직인다.
///
/// 램프(IlinkLampFeedback)와 같은 Atlas GATT 경로를 쓴다: Connect → DiscoverServices →
/// WriteCharacteristicValue(write_type 1, 램프로 실기 확인한 방식). 쓴 뒤 상태 특성을 읽어
/// 실제 값을 돌려주는데, 이 읽기는 아직 Pi 5 에서 확인하지 않아 실패해도 명령을 실패로 만들지 않는다.
library;

import 'bluetooth_service.dart';

const fanDeviceName = 'DESKMATE-FAN';
const fanServiceUuid = 'f1970001-51e1-4b0e-a7f1-8d27d8e34683';
const fanCommandUuid = 'f1970002-51e1-4b0e-a7f1-8d27d8e34683';
const fanStateUuid = 'f1970003-51e1-4b0e-a7f1-8d27d8e34683';

bool isFanDevice(BluetoothDeviceInfo device) =>
    device.name.trim().toUpperCase() == fanDeviceName;

/// control/cmd 의 operation·value → 펌웨어 명령 바이트. 받을 수 없는 조합이면 null.
List<int>? fanFrame(Object? operation, Object? value) {
  bool upTo(int max) => value is int && value >= 0 && value <= max;
  return switch (operation) {
    'set_power' when value == 'on' => [0x01, 0x01],
    'set_power' when value == 'off' => [0x01, 0x00],
    'set_level' when upTo(3) => [0x03, value as int],
    'set_speed' when upTo(100) => [0x02, value as int],
    'set_mode' when value == 'normal' => [0x04, 0x00],
    'set_mode' when value == 'natural' => [0x04, 0x01],
    'set_timer' when upTo(65535) => [0x06, (value as int) & 0xff, value >> 8],
    _ => null,
  };
}

class FanState {
  const FanState({
    required this.power,
    required this.speed,
    required this.level,
    required this.natural,
    required this.reverse,
    required this.timerSeconds,
  });

  final bool power;
  final int speed;
  final int level;
  final bool natural;
  final bool reverse;
  final int timerSeconds;

  /// 펌웨어 상태 8바이트 `[버전 1, 전원, 속도 %, 단계, 모드, 방향, 남은 타이머 초 LE]`.
  static FanState? decode(List<int>? bytes) {
    if (bytes == null || bytes.length < 8 || bytes[0] != 1) return null;
    return FanState(
      power: bytes[1] == 1,
      speed: bytes[2],
      level: bytes[3],
      natural: bytes[4] == 1,
      reverse: bytes[5] == 1,
      timerSeconds: bytes[6] | (bytes[7] << 8),
    );
  }

  /// control/result 의 actual_value — 명령한 operation 이 바꾸는 값.
  Object? actualFor(Object? operation) => switch (operation) {
        'set_power' => power ? 'on' : 'off',
        'set_level' => level,
        'set_speed' => power ? speed : 0,
        'set_mode' => natural ? 'natural' : 'normal',
        'set_timer' => timerSeconds,
        _ => null,
      };

  bool matches(Object? operation, Object? value) {
    final actual = actualFor(operation);
    if (operation == 'set_timer' && actual is int && value is int) {
      return (actual - value).abs() <= 2; // 쓰기와 읽기 사이에 초가 흐른다
    }
    return actual == value;
  }

  String get summary {
    if (!power) return '꺼짐';
    final timer = timerSeconds > 0 ? ' · ${(timerSeconds / 60).ceil()}분 뒤 꺼짐' : '';
    return '$level단${natural ? ' 자연풍' : ''}$timer'; // 세기 값은 듀티가 아니라 단계로 보인다
  }
}

class BleFanControl {
  BleFanControl(this._bluetooth,
      {this.connectAttempts = 8,
      this.retryDelay = const Duration(seconds: 3),
      this.rescanFor = const Duration(seconds: 6)});
  final AtlasBluetoothService _bluetooth;

  /// 10-24 Pi 5 실기: Pi 5 → Pico 2 W 링크는 두 번에 한 번꼴로만 맺어지고
  /// (`le-connection-abort-by-local`, 스피커 연결 여부와 무관, 노트북은 매번 성공), 페어링하지 않은 기기는
  /// BlueZ 가 곧 잊는다. Atlas Connect 는 실패를 먼저 돌려준 뒤에도 BlueZ 가 링크를 계속 맺고 있어서,
  /// 곧바로 검색을 시작하면 그 링크가 끊긴다. 그래서 실패하면 [retryDelay] 기다렸다 다시 붙고,
  /// BlueZ 가 잊었을 때를 위해 두 번에 한 번만 검색한다.
  final int connectAttempts;
  final Duration retryDelay;
  final Duration rescanFor;
  String? _address;
  GattConnection? _connection;
  Future<bool>? _connecting;
  Future<void> _writes = Future.value();
  DateTime? _lastOkAt;
  Object? _lastError;
  FanState? _lastState;
  bool? _lastRediscovered;

  bool get configured => _address?.isNotEmpty ?? false;

  /// 마지막 [send] 에서 선풍기를 검색해 다시 찾았는지: null = 검색 안 함, true = 찾음, false = 못 찾음.
  bool? get lastRediscovered => _lastRediscovered;
  String? get address => _address;
  bool get connected => _connection != null;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;
  FanState? get lastState => _lastState;

  void select(String address) {
    if (_address == address) return;
    _address = address;
    _connection = null;
    _lastOkAt = null;
    _lastError = null;
    _lastState = null;
  }

  /// 잊은 뒤에는 vent_fan 명령에 대답하지 않는다(다른 어댑터가 맡을 수 있게).
  void forget() {
    _address = null;
    _connection = null;
    _lastState = null;
  }

  /// 동시에 불러도 GATT Connect 는 한 번 — 두 번째는 "Already Connected" 로 실패한다.
  Future<bool> connect() {
    if (!configured) return Future.value(false);
    if (_connection != null) return Future.value(true);
    return _connecting ??= _connect().whenComplete(() => _connecting = null);
  }

  Future<bool> _connect() async {
    for (var attempt = 1;; attempt++) {
      try {
        _connection = await _bluetooth.connectLamp(_address!); // 이름과 달리 일반 GATT 연결이다
        _lastError = null;
        return true;
      } catch (error) {
        _connection = null;
        _lastError = error;
        if (attempt >= connectAttempts) return false;
      }
      await Future<void>.delayed(retryDelay);
      if (attempt.isEven) {
        final found = await _rediscover();
        _lastRediscovered = (_lastRediscovered ?? false) || found;
      }
    }
  }

  /// 선풍기가 다시 보일 때까지(최대 [rescanFor]) 검색한다. 찾았는지 돌려준다.
  Future<bool> _rediscover() async {
    final wanted = _address!.toLowerCase();
    try {
      await for (final devices
          in _bluetooth.scanProgressive(duration: rescanFor)) {
        if (devices.any((d) => d.address.toLowerCase() == wanted)) return true;
      }
    } catch (_) {}
    return false;
  }

  /// 명령 하나를 쓰고 상태를 읽는다. 쓰기는 순서대로, 끊긴 연결은 한 번만 다시 붙는다.
  /// [written] 이 false 면 선풍기에 닿지 않은 것, [state] 가 null 이면 상태를 못 읽은 것.
  Future<({bool written, FanState? state})> send(List<int> frame,
      {num? expiresTsMs}) {
    final next = _writes.then((_) => _send(frame, expiresTsMs));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<({bool written, FanState? state})> _send(
      List<int> frame, num? expiresTsMs) async {
    const notSent = (written: false, state: null);
    _lastRediscovered = null;
    if (!configured) return notSent;
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    for (var attempt = 0; attempt < 2; attempt++) {
      if (expired()) return notSent;
      if (!await connect()) continue;
      try {
        await _bluetooth.writeCharacteristic(_connection!,
            serviceId: fanServiceUuid,
            characteristicId: fanCommandUuid,
            value: frame);
      } catch (error) {
        _connection = null;
        _lastError = error;
        continue;
      }
      _lastOkAt = DateTime.now();
      _lastError = null;
      return (written: true, state: await _readState());
    }
    return notSent;
  }

  Future<FanState?> _readState() async {
    final connection = _connection;
    if (connection == null) return null;
    try {
      final state = FanState.decode(await _bluetooth.readCharacteristic(
          connection,
          serviceId: fanServiceUuid,
          characteristicId: fanStateUuid));
      if (state != null) _lastState = state;
      return state;
    } catch (_) {
      return null;
    }
  }
}
