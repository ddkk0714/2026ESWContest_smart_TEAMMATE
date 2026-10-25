/// DESKMATE 블루투스 선풍기(firmware/pico2w_ble_fan)를 Pi 4 허브의 `vent_fan` 명령으로 움직인다.
///
/// 램프(IlinkLampFeedback)와 같은 Atlas GATT 경로를 쓴다: Connect → DiscoverServices →
/// WriteCharacteristicValue(write_type 1, 램프로 실기 확인한 방식). 쓴 뒤 상태 특성을 읽어
/// 기기 보고 상태를 돌려준다. 읽기 실패는 전송 완료와 상태 미확인으로 구분한다.
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
    if (bytes == null ||
        bytes.length < 8 ||
        bytes[0] != 1 ||
        bytes.take(8).any((b) => b < 0 || b > 255) ||
        bytes[1] > 1 ||
        bytes[2] > 100 ||
        bytes[3] > 3 ||
        bytes[4] > 1 ||
        bytes[5] > 1) {
      return null;
    }
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
    final timer =
        timerSeconds > 0 ? ' · ${(timerSeconds / 60).ceil()}분 뒤 꺼짐' : '';
    return '$level단${natural ? ' 자연풍' : ''}$timer'; // 세기 값은 듀티가 아니라 단계로 보인다
  }
}

class BleFanControl {
  BleFanControl(this._bluetooth,
      {this.connectAttempts = 5,
      this.retryDelay = const Duration(milliseconds: 1500),
      this.rescanFor = const Duration(seconds: 6)});
  final AtlasBluetoothService _bluetooth;

  /// 10-24 Pi 5 실기: Pi 5 → Pico 2 W 링크는 두세 번에 한 번만 맺어지고
  /// (`le-connection-abort-by-local`, 노트북은 매번 성공), 페어링하지 않은 기기는 BlueZ 가 곧 잊어서
  /// 저장된 주소로 바로 붙으면 실패한다. 그래서 실패하면 잠깐 검색해 다시 찾은 뒤 다시 붙는다.
  final int connectAttempts;
  final Duration retryDelay;
  final Duration rescanFor;
  int _selectionGeneration = 0;
  int _connectionGeneration = 0;
  int get selectionGeneration => _selectionGeneration;
  int get connectionGeneration => _connectionGeneration;
  String? _address;
  GattConnection? _connection;
  Future<bool>? _connecting;
  Future<void> _writes = Future.value();
  DateTime? _lastOkAt;
  Object? _lastError;
  FanState? _lastState;

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;
  FanState? get lastState => _lastState;

  void select(String address) {
    if (_address == address) return;
    _selectionGeneration++;
    _address = address;
    _connection = null;
    _lastOkAt = null;
    _lastError = null;
    _lastState = null;
  }

  /// Invalidate pending commands and undo without duplicating a GATT connect.
  void invalidateUndo() {
    _selectionGeneration++;
  }

  void disconnect() {
    invalidateUndo();
    _connectionGeneration++;
    _connection = null;
    _lastState = null;
  }

  /// Normal vent_fan commands are left to other adapters after forgetting.
  void forget() {
    disconnect();
    _address = null;
    _lastState = null;
  }

  /// 동시에 불러도 GATT Connect 는 한 번 — 두 번째는 "Already Connected" 로 실패한다.
  Future<bool> connect() {
    if (!configured) return Future.value(false);
    if (_connection != null) return Future.value(true);
    return _connecting ??= _connect().whenComplete(() => _connecting = null);
  }

  Future<bool> _connect() async {
    final generation = _selectionGeneration;
    final address = _address!;
    for (var attempt = 1;; attempt++) {
      try {
        final connection = await _bluetooth.connectLamp(address); // 일반 GATT 연결
        if (_selectionGeneration != generation || _address != address) {
          return false;
        }
        _connection = connection;
        _lastError = null;
        return true;
      } catch (error) {
        if (_selectionGeneration != generation) return false;
        _connectionGeneration++;
        _connection = null;
        _lastError = error;
        if (attempt >= connectAttempts) return false;
      }
      if (_selectionGeneration != generation) return false;
      await _rediscover();
      await Future<void>.delayed(retryDelay);
      if (_selectionGeneration != generation) return false;
    }
  }

  /// 선풍기가 다시 보일 때까지(최대 [rescanFor]) 검색한다. 검색 실패는 다음 연결 시도가 판정한다.
  Future<void> _rediscover() async {
    final wanted = _address!.toLowerCase();
    try {
      await for (final devices
          in _bluetooth.scanProgressive(duration: rescanFor)) {
        if (devices.any((d) => d.address.toLowerCase() == wanted)) break;
      }
    } catch (_) {}
  }

  /// 명령 하나를 쓰고 상태를 읽는다. 쓰기는 순서대로, 끊긴 연결은 한 번만 다시 붙는다.
  /// [written]은 GATT 쓰기 반환 여부다. 실패·타임아웃은 기기 미수신의 증거가 아니다.
  /// [state]가 null이면 상태를 못 읽었으며 요청값을 실제값으로 대체하지 않는다.
  Future<({bool written, FanState? state})> send(List<int> frame,
      {num? expiresTsMs,
      bool control = false,
      bool Function()? stillAuthorized}) {
    // Direct UI writes supersede pending automation and its undo eligibility.
    if (!control) invalidateUndo();
    final generation = _selectionGeneration;
    final next = _writes.then((_) => _send(
        frame,
        expiresTsMs,
        () =>
            _selectionGeneration == generation &&
            (stillAuthorized?.call() ?? true)));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<({bool written, FanState? state})> _send(
      List<int> frame, num? expiresTsMs, bool Function() authorized) async {
    const notSent = (written: false, state: null);
    if (!configured) return notSent;
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    for (var attempt = 0; attempt < 2; attempt++) {
      if (expired() || !authorized()) return notSent;
      if (!await connect()) continue;
      if (expired() || !authorized()) return notSent;
      try {
        await _bluetooth.writeCharacteristic(_connection!,
            serviceId: fanServiceUuid,
            characteristicId: fanCommandUuid,
            value: frame);
      } catch (error) {
        _connectionGeneration++;
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
    final generation = _selectionGeneration;
    if (connection == null) return null;
    try {
      final state = FanState.decode(await _bluetooth.readCharacteristic(
          connection,
          serviceId: fanServiceUuid,
          characteristicId: fanStateUuid));
      if (generation != _selectionGeneration || connection != _connection) {
        return null;
      }
      if (state != null) _lastState = state;
      return state;
    } catch (_) {
      return null;
    }
  }
}
