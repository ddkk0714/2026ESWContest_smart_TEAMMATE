/// DESKMATE 블루투스 선풍기를 Pi 4 허브의 `vent_fan` 명령으로 움직인다. 펌웨어가 두 벌이라 연결할 때 구분한다(V28).
///   - C 펌웨어(`firmware/pico2w_ble_fan`, 서비스 f1970001…): 명령 프레임 [opcode, 값…], 상태 8바이트.
///   - MicroPython(`firmware/pico_fan/main.py`, feat/pico-fan, 서비스 d35e0001…): 명령 1바이트 속도 %(0 = 끄기),
///     상태 1바이트 %. 멈춘 상태에서 켤 때의 출발 킥(100 % 250 ms → 60 % 1 s → 목표)은 펌웨어가 한다 — remote.html 과 같다.
///   구분: DiscoverServices 응답의 서비스 UUID → 없으면 C 상태 특성을 읽어 8바이트 기록이면 C, 아니면 MicroPython 상태
///   특성을 읽어 1바이트면 MicroPython. 둘 다 못 읽으면 C 로 본다(V27 동작).
///
/// 램프(IlinkLampFeedback)와 같은 Atlas GATT 경로를 쓴다: Connect → DiscoverServices →
/// WriteCharacteristicValue(write_type 1, 램프로 실기 확인한 방식). 쓴 뒤 상태 특성을 읽어
/// 실제 값을 돌려주는데, 이 읽기는 아직 Pi 5 에서 확인하지 않아 실패해도 명령을 실패로 만들지 않는다.
library;

import 'dart:async';

import 'bluetooth_service.dart';

const fanDeviceName = 'DESKMATE-FAN';
const fanServiceUuid = 'f1970001-51e1-4b0e-a7f1-8d27d8e34683';
const fanCommandUuid = 'f1970002-51e1-4b0e-a7f1-8d27d8e34683';
const fanStateUuid = 'f1970003-51e1-4b0e-a7f1-8d27d8e34683';

const fanMicroServiceUuid = 'd35e0001-5f4a-4b2c-9c1e-0f6a7b8c9d01';
const fanMicroCommandUuid = 'd35e0002-5f4a-4b2c-9c1e-0f6a7b8c9d01';
const fanMicroStatusUuid = 'd35e0003-5f4a-4b2c-9c1e-0f6a7b8c9d01';

enum FanFirmware {
  c('C'),
  micropython('MicroPython');

  const FanFirmware(this.label);
  final String label;
}

/// 서비스 검색 결과로 펌웨어를 정한다. 어느 쪽 서비스도 없으면 null(특성을 읽어 정한다).
FanFirmware? fanFirmwareFromServices(Set<String> services) {
  if (services.contains(fanMicroServiceUuid)) return FanFirmware.micropython;
  if (services.contains(fanServiceUuid)) return FanFirmware.c;
  return null;
}

/// MicroPython 판의 단계 → 속도 %. remote.html 프리셋(30·45·60·100)과 같은 결: 1단 45 · 2단 70 · 3단 100.
const microLevelSpeed = [0, 45, 70, 100];

/// 속도 % → 단계(상태를 단계로 보일 때). [microLevelSpeed] 와 왕복이 맞는다.
int microLevelFor(int speed) => speed <= 0
    ? 0
    : speed <= microLevelSpeed[1]
        ? 1
        : speed <= microLevelSpeed[2]
            ? 2
            : 3;

/// C 펌웨어 프레임([fanFrame]) → MicroPython 명령 1바이트. 받을 수 없는 명령(자연풍·타이머)은 null.
/// 켜기는 마지막으로 돌던 속도(없으면 1단)로.
List<int>? microFanCommand(List<int> frame, {int? lastOnSpeed}) {
  if (frame.length < 2) return null;
  final value = frame[1];
  return switch (frame[0]) {
    0x01 => [value == 1 ? (lastOnSpeed ?? microLevelSpeed[1]) : 0],
    0x02 when value <= 100 => [value],
    0x03 when value <= 3 => [microLevelSpeed[value]],
    _ => null,
  };
}

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

  /// MicroPython 판 상태 1바이트(속도 %) → 같은 모양의 상태. 단계는 [microLevelFor].
  static FanState? decodeMicro(List<int>? bytes) {
    if (bytes == null || bytes.isEmpty || bytes[0] > 100) return null;
    final speed = bytes[0];
    return FanState(
        power: speed > 0,
        speed: speed,
        level: microLevelFor(speed),
        natural: false,
        reverse: false,
        timerSeconds: 0);
  }

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

  /// V43: control/result 의 `previous` — 명령 직전 상태. hub 가 자리 비움(absent)에 끈 선풍기를 다시 앉았을 때
  /// 그대로 되돌린다(set_power "on" + set_speed speed).
  Map<String, Object> get asPrevious =>
      {'power': power ? 'on' : 'off', 'speed': speed, 'level': level};

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
      {this.connectAttempts = 3,
      this.retryDelay = const Duration(seconds: 3),
      this.rescanFor = const Duration(seconds: 6),
      this.rescanAtLeast = const Duration(seconds: 3)});
  final AtlasBluetoothService _bluetooth;

  /// 다시 찾을 때 주소가 보여도 이만큼은 검색한다([rediscoverDevice], V35).
  final Duration rescanAtLeast;

  /// 10-24 Pi 5 실기: Pi 5 → Pico 2 W 링크는 두세 번에 한 번만 맺어지고
  /// (`le-connection-abort-by-local`, 노트북은 매번 성공), 페어링하지 않은 기기는 BlueZ 가 곧 잊어서
  /// 저장된 주소로 바로 붙으면 실패한다. 그래서 실패하면 잠깐 검색해 다시 찾은 뒤 다시 붙는다.
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
  FanFirmware? _firmware;
  int? _lastOnSpeed;
  int _generation = 0;
  bool? _lastRediscovered;
  bool? get lastRediscovered => _lastRediscovered;

  /// V43: 마지막 [send] 가 쓰기 직전에 본 상태(control/result 의 previous). 연결도 못 했거나 못 읽었으면 null.
  FanState? _stateBeforeSend;
  FanState? get stateBeforeSend => _stateBeforeSend;

  Future<void> _release(GattConnection? connection) async {
    if (connection == null) return;
    try {
      await _bluetooth.disconnectGatt(connection);
    } catch (_) {}
  }

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;

  /// 지금 맺은 GATT 링크(clientId 를 앱 데이터에 남겨 다음 실행이 정리하게, V38). 없으면 null.
  GattConnection? get connection => _connection;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;
  FanState? get lastState => _lastState;

  /// 연결한 선풍기의 펌웨어. 아직 모르면 null.
  FanFirmware? get firmware => _firmware;

  void select(String address) {
    if (_address == address) return;
    final old = _connection;
    _generation++;
    _connecting = null;
    _address = address;
    _connection = null;
    _lastOkAt = null;
    _lastError = null;
    _lastState = null;
    _firmware = null;
    _lastOnSpeed = null;
    unawaited(_release(old));
  }

  /// 잊은 뒤에는 vent_fan 명령에 대답하지 않는다(다른 어댑터가 맡을 수 있게).
  void forget() {
    final old = _connection;
    _generation++;
    _connecting = null;
    _address = null;
    _connection = null;
    _lastState = null;
    _firmware = null;
    _lastOnSpeed = null;
    unawaited(_release(old));
  }

  /// 동시에 불러도 GATT Connect 는 한 번 — 두 번째는 "Already Connected" 로 실패한다.
  /// 시작 복원·다시 연결·명령 모두 이 길이다(V38: 시작 복원도 한 번이 아니라 [connectAttempts] 번까지).
  Future<bool> connect() {
    if (!configured) return Future.value(false);
    if (_connection != null) return Future.value(true);
    final generation = _generation;
    return _connecting ??= _connect().whenComplete(() {
      if (generation == _generation) _connecting = null;
    });
  }

  Future<bool> _connect() {
    final generation = _generation;
    final address = _address!;
    return connectWithRetry(() async {
      try {
        final connection = await _bluetooth.connectLamp(address, role: '선풍기');
        if (generation != _generation) {
          await _release(connection);
          return false;
        }
        _connection = connection;
        _lastError = null;
        return true;
      } catch (error) {
        if (generation != _generation) return false;
        _connection = null;
        _lastError = error;
        return false;
      }
    },
        attempts: connectAttempts,
        retryDelay: retryDelay,
        rediscover: () async {
          final found = await _rediscover();
          if (generation == _generation) {
            _lastRediscovered = (_lastRediscovered ?? false) || found;
          }
        },
        // 이전 실행의 링크가 남아 거절되면(Already connected) 검색·재시도로는 안 풀린다 — 더 시도하지 않는다
        stop: () =>
            generation != _generation || isAlreadyConnectedError(_lastError));
  }

  /// 선풍기가 다시 보일 때까지(최대 [rescanFor]) 검색한다. 검색 실패는 다음 연결 시도가 판정한다.
  Future<bool> _rediscover() => rediscoverDevice(_bluetooth, _address!,
      limit: rescanFor, atLeast: rescanAtLeast);

  /// 명령 하나를 쓰고 상태를 읽는다. 쓰기는 순서대로, 끊긴 연결은 한 번만 다시 붙는다.
  /// [written] 이 false 면 선풍기에 닿지 않은 것, [state] 가 null 이면 상태를 못 읽은 것.
  Future<({bool written, FanState? state})> send(List<int> frame,
      {num? expiresTsMs}) {
    final generation = _generation;
    final next = _writes.then((_) => _send(frame, expiresTsMs, generation));
    _writes = next.then<void>((_) {});
    return next;
  }

  Future<({bool written, FanState? state})> _send(
      List<int> frame, num? expiresTsMs, int generation) async {
    const notSent = (written: false, state: null);
    if (generation != _generation) return notSent;
    _lastRediscovered = null;
    _stateBeforeSend = null;
    if (!configured) return notSent;
    bool expired() =>
        expiresTsMs != null &&
        DateTime.now().millisecondsSinceEpoch >= expiresTsMs;
    for (var attempt = 0; attempt < 2; attempt++) {
      if (expired() || generation != _generation) return notSent;
      if (!await connect()) continue;
      if (expired() || generation != _generation) return notSent;
      final firmware = _firmware ??= await _detectFirmware(_connection!);
      if (generation != _generation) return notSent;
      final micro = firmware == FanFirmware.micropython;
      final payload =
          micro ? microFanCommand(frame, lastOnSpeed: _lastOnSpeed) : frame;
      // V43: 쓰기 전 상태 — 마지막으로 읽은 상태가 있으면 그것, 없으면 같은 연결로 한 번 읽는다(못 읽으면 null).
      _stateBeforeSend ??= _lastState ?? await _readState();
      if (generation != _generation) return notSent;
      final writtenSpeed =
          micro ? payload : microFanCommand(frame, lastOnSpeed: _lastOnSpeed);
      if (payload == null) {
        _lastError = UnsupportedError(
            'MicroPython 선풍기는 이 명령(${frame.first})을 받지 않습니다 — 켜기·끄기·단계·속도만');
        return notSent;
      }
      try {
        await _bluetooth.writeCharacteristic(_connection!,
            serviceId: micro ? fanMicroServiceUuid : fanServiceUuid,
            characteristicId: micro ? fanMicroCommandUuid : fanCommandUuid,
            value: payload);
      } catch (error) {
        if (generation != _generation) return notSent;
        final old = _connection;
        _connection = null;
        _lastError = error;
        await _release(old);
        continue;
      }
      if (generation != _generation) return (written: true, state: null);
      _lastOkAt = DateTime.now();
      _lastError = null;
      final state = await _readState();
      // V45: 11-02 Pi 5 — MicroPython 선풍기 상태를 못 읽어 자리 비움 끄기 결과에 previous 가 없었고, 복귀 때
      // 선풍기만 다시 켜지지 않았다. 못 읽었으면 방금 쓴 속도 1바이트를 마지막 상태로 둔다(다음 쓰기의 previous).
      // V46: 15:19 V45 실기에도 previous 가 없었다 — 서비스 목록이 비어 판(펌웨어)을 C 로 잘못 알았을 수 있어
      // 판과 상관없이 쓴 명령의 속도(켜기·끄기·단계·속도)로 둔다. 자연풍·타이머는 상태를 바꾸지 않는다.
      if (state == null && writtenSpeed != null && generation == _generation) {
        final written = FanState.decodeMicro(writtenSpeed);
        if (written != null) {
          _lastState = written;
          if (written.power && written.speed > 0) _lastOnSpeed = written.speed;
        }
      }
      return (written: true, state: state);
    }
    return notSent;
  }

  Future<FanState?> _readState() async {
    final connection = _connection;
    final generation = _generation;
    if (connection == null) return null;
    final micro = _firmware == FanFirmware.micropython;
    try {
      final bytes = await _bluetooth.readCharacteristic(connection,
          serviceId: micro ? fanMicroServiceUuid : fanServiceUuid,
          characteristicId: micro ? fanMicroStatusUuid : fanStateUuid);
      final state =
          micro ? FanState.decodeMicro(bytes) : FanState.decode(bytes);
      if (generation != _generation) return null;
      if (state != null) {
        _lastState = state;
        if (state.power && state.speed > 0) _lastOnSpeed = state.speed;
      }
      return state;
    } catch (_) {
      return null;
    }
  }

  /// 서비스 검색 결과 → 없으면 C 상태(8바이트) → MicroPython 상태(1바이트) 순서로 읽어 본다. 다 못 읽으면 C.
  Future<FanFirmware> _detectFirmware(GattConnection connection) async {
    final byService = fanFirmwareFromServices(connection.services);
    if (byService != null) return byService;
    Future<List<int>?> read(String service, String characteristic) async {
      try {
        return await _bluetooth.readCharacteristic(connection,
            serviceId: service, characteristicId: characteristic);
      } catch (_) {
        return null;
      }
    }

    if (FanState.decode(await read(fanServiceUuid, fanStateUuid)) != null) {
      return FanFirmware.c;
    }
    if (FanState.decodeMicro(
            await read(fanMicroServiceUuid, fanMicroStatusUuid)) !=
        null) {
      return FanFirmware.micropython;
    }
    return FanFirmware.c;
  }
}
