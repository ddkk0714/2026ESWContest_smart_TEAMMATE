import 'dart:async';

import 'diag.dart';
import 'package:dbus/dbus.dart';

const _bluetoothBusName = 'com.atlas.Bluetooth1';
const _bluetoothPath = '/com/atlas/Bluetooth1';
const _adapterInterface = 'com.atlas.Bluetooth1.Adapter';
const _deviceInterface = 'com.atlas.Bluetooth1.Device';
const _a2dpInterface = 'com.atlas.Bluetooth1.A2dp';
const _gattInterface = 'com.atlas.Bluetooth1.Gatt';
const _permissionInterface = 'com.atlas.PermissionAgent1';

const _audioManagerBusName = 'com.atlas.AudioManager1';
const _audioManagerPath = '/com/atlas/AudioManager1';
const _audioManagerInterface = 'com.atlas.AudioManager1';

/// Bluetooth SIG Class of Device, major device class "Audio/Video".
const _majorClassAudioVideo = 0x04;

/// A2DP Audio Sink service class UUID prefix (0000110b-...).
const _a2dpSinkUuidPrefix = '0000110b';

class BluetoothDeviceInfo {
  const BluetoothDeviceInfo({
    required this.address,
    required this.name,
    required this.paired,
    required this.connected,
    this.deviceClass = 0,
    this.profiles = const [],
  });

  final String address;
  final String name;
  final bool paired;
  final bool connected;

  /// Class of Device from the BR/EDR inquiry response; 0 when the record was
  /// built from a BLE advertisement only.
  final int deviceClass;

  /// Service UUIDs reported by the device (may be empty before SDP).
  final List<String> profiles;

  int get majorDeviceClass => (deviceClass >> 8) & 0x1f;

  /// A classic-Bluetooth audio sink we can pair for A2DP output. The KLZS-L1
  /// speaker/lamp advertises two addresses: the speaker (BR/EDR, class
  /// Audio/Video) and "KLZS-L1 app" (BLE, class 0, GATT lamp control). Pairing
  /// the BLE one goes over SMP, which the lamp does not support: BlueZ reports
  /// connected, then times out after 25 s ("connected then disconnected").
  bool get isAudioSink =>
      majorDeviceClass == _majorClassAudioVideo ||
      profiles
          .any((uuid) => uuid.toLowerCase().startsWith(_a2dpSinkUuidPrefix));

  /// BLE-only record (no inquiry data): candidate for GATT lamp control, never
  /// for A2DP pairing.
  bool get isBleOnly => deviceClass == 0;

  BluetoothDeviceInfo copyWith({bool? paired, bool? connected}) =>
      BluetoothDeviceInfo(
        address: address,
        name: name,
        paired: paired ?? this.paired,
        connected: connected ?? this.connected,
        deviceClass: deviceClass,
        profiles: profiles,
      );
}

/// V35: 저장된 BLE 기기(램프·선풍기)를 다시 찾는다. 주소가 보였어도 [atLeast] 동안은 검색을 이어 간다.
///
/// Atlas `GetDiscoverableDevices` 는 검색을 시작하자마자 예전에 알던 기기도 돌려준다. 11-01 Pi 5 실기: 첫 결과에
/// 램프 주소가 있다고 바로 끝내 검색이 같은 초에 멈췄고, BlueZ 는 30 초 전에 잊은 램프를 그대로 잊은 채라
/// `Gatt.Connect` 가 BlueZ 를 부르기도 전에 취소됐다(명령 bluetooth_write_failed). 몇 초 검색을 이어 가면 BlueZ 가
/// 광고를 다시 받아 기기를 되살린다. [limit] 가 [atLeast] 보다 짧으면(명령 만료가 가까우면) 끝까지 본 결과를 돌려준다.
Future<bool> rediscoverDevice(AtlasBluetoothService bluetooth, String address,
    {required Duration limit,
    Duration atLeast = const Duration(seconds: 3)}) async {
  final wanted = address.toLowerCase();
  final clock = Stopwatch()..start();
  var found = false;
  try {
    await for (final devices in bluetooth.scanProgressive(duration: limit)) {
      found = found || devices.any((d) => d.address.toLowerCase() == wanted);
      if (found && clock.elapsed >= atLeast) return true;
    }
  } catch (_) {
    // 검색이 안 되면 지금까지 본 것으로 판정한다.
  }
  return found;
}

/// V38: Gatt.Connect 가 "Already connected" 로 거절됐나 — 이전 실행이 남긴 링크라 앱만으로는 못 푼다.
bool isAlreadyConnectedError(Object? error) =>
    error != null && '$error'.toLowerCase().contains('already connected');

/// V38: 저장된 블루투스 기기에 다시 붙는 공통 틀 — 선풍기(BleFanControl, 10-24)의 방식을 램프·스피커도 쓴다.
/// 최대 [attempts] 번 [attempt] 해 보고, 실패 사이에 [retryDelay] 를 기다리며, 짝수 번째 실패 뒤에는
/// [rediscover] 로 기기를 다시 찾은 다음 붙는다. 실패 뒤마다 [stop] 을 보고 true 면 그만둔다(기기를 바꿈·명령 만료).
/// 10-26 팝업 누적(실패한 연결마다 시스템 창)을 넘지 않도록 [attempts] 가 기기당 상한이다.
Future<bool> connectWithRetry(Future<bool> Function() attempt,
    {required int attempts,
    required Duration retryDelay,
    required Future<void> Function() rediscover,
    bool Function()? stop}) async {
  for (var n = 1;; n++) {
    if (await attempt()) return true;
    if (n >= attempts || (stop?.call() ?? false)) return false;
    if (retryDelay > Duration.zero) await Future<void>.delayed(retryDelay);
    if (stop?.call() ?? false) return false;
    if (n.isEven) await rediscover();
  }
}

/// Atlas Bluetooth1 wrapper. Pairing and connection require user-consent
/// permissions declared in atlas/meta/appinfo.json; no system setting is
/// changed silently by this app.
class AtlasBluetoothService {
  AtlasBluetoothService({DBusClient? client})
      : _client = client ?? DBusClient.system() {
    _bluetooth = DBusRemoteObject(
      _client,
      name: _bluetoothBusName,
      path: DBusObjectPath(_bluetoothPath),
    );
    _permission = DBusRemoteObject(
      _client,
      name: 'com.atlas.PermissionAgent1',
      path: DBusObjectPath('/com/atlas/PermissionAgent1'),
    );
    _audio = DBusRemoteObject(
      _client,
      name: _audioManagerBusName,
      path: DBusObjectPath(_audioManagerPath),
    );
  }

  final DBusClient _client;
  late final DBusRemoteObject _bluetooth;
  late final DBusRemoteObject _permission;
  late final DBusRemoteObject _audio;
  Future<void> _adapterOperations = Future.value();
  final Map<String, GattConnection> _gattConnections = {};
  bool _closing = false;

  // Establish links one at a time, but retain every device's distinct client.
  Future<T> _adapterOperation<T>(Future<T> Function() action) {
    return _runAdapter(action);
  }

  Future<Completer<void>> _acquireAdapter() async {
    final previous = _adapterOperations;
    final release = Completer<void>();
    _adapterOperations = previous.then((_) => release.future);
    await previous;
    if (_closing) {
      release.complete();
      throw StateError('Bluetooth 연결을 종료하고 있습니다.');
    }
    return release;
  }

  Future<T> _runAdapter<T>(Future<T> Function() action) async {
    final release = await _acquireAdapter();
    try {
      return await action();
    } finally {
      release.complete();
    }
  }

  Future<bool> _hasPermission(String permission) async {
    try {
      await _permission.callMethod(
        _permissionInterface,
        'CheckSelfUserConsentPermission',
        [DBusString(permission)],
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<bool> ensureBluetoothPermission({
    Duration waitForConsent = const Duration(seconds: 20),
  }) async {
    const scanPermission = 'com.atlas.permission.user_consent.bluetooth_scan';
    const connectPermission =
        'com.atlas.permission.user_consent.bluetooth_connect';
    final scan = await _hasPermission(scanPermission);
    final connect = await _hasPermission(connectPermission);
    if (scan && connect) return true;
    // PermissionAgent가 동의를 받은 뒤 권한 상태가 반영될 때까지 기다린다.
    for (final permission in [
      if (!scan) scanPermission,
      if (!connect) connectPermission
    ]) {
      try {
        await _permission.callMethod(
          _permissionInterface,
          'RequestUserConsentPermission',
          [DBusString(permission)],
        );
      } catch (_) {
        // 동의 UI가 거절되거나 없어도 마지막 권한 상태로 판정한다.
      }
    }
    final clock = Stopwatch()..start();
    while (true) {
      if (await _hasPermission(scanPermission) &&
          await _hasPermission(connectPermission)) {
        return true;
      }
      final remaining = waitForConsent - clock.elapsed;
      if (remaining <= Duration.zero) return false;
      await Future<void>.delayed(
        remaining < const Duration(seconds: 1)
            ? remaining
            : const Duration(seconds: 1),
      );
    }
  }

  Future<String> adapterAddress() async {
    final result =
        await _bluetooth.callMethod(_adapterInterface, 'GetInfo', []);
    final values = result.returnValues;
    if (values.isEmpty || values.first is! DBusArray) return '';
    final adapters = values.first as DBusArray;
    for (final value in adapters.children) {
      if (value is! DBusDict) continue;
      final address = _string(value, 'adapter_address');
      if (address.isNotEmpty) return address;
    }
    return '';
  }

  Future<List<BluetoothDeviceInfo>> scan() async {
    var latest = <BluetoothDeviceInfo>[];
    await for (final devices in scanProgressive()) {
      latest = devices;
    }
    return latest;
  }

  Stream<List<BluetoothDeviceInfo>> scanProgressive({
    Duration duration = const Duration(seconds: 8),
    Duration interval = const Duration(seconds: 1),
  }) async* {
    if (interval <= Duration.zero || duration < Duration.zero) {
      throw ArgumentError('scan duration and interval must be nonnegative');
    }
    final lease = await _acquireAdapter();
    try {
      if (!await ensureBluetoothPermission()) {
        throw StateError(
            'Atlas Bluetooth 권한이 허용되지 않았습니다. 권한 요청을 허용한 뒤 다시 시도하세요.');
      }
      final adapter = await adapterAddress();
      try {
        await _bluetooth.callMethod(
            _adapterInterface, 'StartDiscovery', [DBusString(adapter)]);
        final clock = Stopwatch()..start();
        final found = <String, BluetoothDeviceInfo>{};
        while (true) {
          for (final device in await _discoverable(adapter)) {
            found[device.address] = device;
          }
          yield List<BluetoothDeviceInfo>.unmodifiable(found.values);
          final remaining = duration - clock.elapsed;
          if (remaining <= Duration.zero) break;
          await Future<void>.delayed(
              remaining < interval ? remaining : interval);
        }
      } finally {
        try {
          await _bluetooth.callMethod(
              _adapterInterface, 'CancelDiscovery', [DBusString(adapter)]);
        } catch (_) {
          // A scan already stopped is harmless.
        }
      }
    } finally {
      lease.complete();
    }
  }

  Future<List<BluetoothDeviceInfo>> _discoverable(String adapter) async {
    final result = await _bluetooth.callMethod(
      _deviceInterface,
      'GetDiscoverableDevices',
      [DBusString(adapter), const DBusString('')],
    );
    if (result.returnValues.isEmpty ||
        result.returnValues.first is! DBusArray) {
      return const [];
    }
    final devices = <BluetoothDeviceInfo>[];
    for (final value in (result.returnValues.first as DBusArray).children) {
      if (value is! DBusDict) continue;
      final address = _string(value, 'address');
      final name = _string(value, 'name');
      if (address.isEmpty || name.isEmpty) continue;
      devices.add(BluetoothDeviceInfo(
        address: address,
        name: name,
        paired: _bool(value, 'paired'),
        connected: false,
        deviceClass: _uint(value, 'device_class'),
        profiles: _strings(value, 'device_profiles_support'),
      ));
    }
    return devices;
  }

  Future<void> connectSpeaker(BluetoothDeviceInfo device) =>
      _adapterOperation(() => _connectSpeaker(device));

  Future<void> _connectSpeaker(BluetoothDeviceInfo device) async {
    if (!device.isAudioSink) {
      // Pairing a BLE-only record (e.g. "KLZS-L1 app") goes over SMP, which
      // this lamp does not support: BlueZ reports connected, then times out.
      throw StateError('${device.name}은(는) 오디오 기기가 아닙니다. 스피커를 페어링 모드로 두고 '
          '다시 검색해 오디오 기기 항목을 선택하세요.');
    }
    if (!await ensureBluetoothPermission()) {
      throw StateError(
          'Atlas Bluetooth 권한이 허용되지 않았습니다. 권한 요청을 허용한 뒤 다시 시도하세요.');
    }
    if (await isA2dpConnected(device.address)) {
      await selectBluetoothOutput();
      return;
    }
    final adapter = await adapterAddress();
    if (!device.paired) {
      await _bluetooth.callMethod(_adapterInterface, 'Pair',
          [DBusString(adapter), DBusString(device.address)]);
    }
    await _bluetooth.callMethod(_a2dpInterface, 'Connect',
        [DBusString(adapter), DBusString(device.address)]);
    await _confirmSpeakerConnected(device.address);
  }

  /// 저장된 클래식 Bluetooth 주소는 이미 페어링된 기기로 취급한다.
  Future<void> connectSpeakerAddress(String address) =>
      _adapterOperation(() => _connectSpeakerAddress(address));

  Future<void> _connectSpeakerAddress(String address) async {
    if (!RegExp(r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$').hasMatch(address)) {
      throw StateError('저장된 스피커 주소가 올바르지 않습니다.');
    }
    if (!await ensureBluetoothPermission()) {
      throw StateError(
          'Atlas Bluetooth 권한이 허용되지 않았습니다. 권한 요청을 허용한 뒤 다시 시도하세요.');
    }
    try {
      if (await isA2dpConnected(address)) {
        await selectBluetoothOutput();
        return;
      }
      final adapter = await adapterAddress();
      await _bluetooth.callMethod(_a2dpInterface, 'Connect',
          [DBusString(adapter), DBusString(address)]);
      await _confirmSpeakerConnected(address);
    } catch (error) {
      if (error is StateError) rethrow;
      throw StateError('저장된 스피커에 연결하지 못했습니다: $error');
    }
  }

  /// V38: 저장된 스피커를 검색으로 다시 찾고, 페어링이 안 된 채(paired=false)면 페어링한다. 찾았으면 true.
  /// 11-02 Pi 5: 스피커 "KLZS-L1" 이 paired=false 라 [connectSpeakerAddress](A2dp.Connect 만)가 계속 실패로 보였다.
  /// 페어링이 실패해도 던지지 않는다 — 다음 연결 시도가 결과를 판정한다.
  Future<bool> rediscoverSpeaker(String address,
      {Duration limit = const Duration(seconds: 6),
      Duration atLeast = const Duration(seconds: 3)}) async {
    final wanted = address.toLowerCase();
    final clock = Stopwatch()..start();
    BluetoothDeviceInfo? seen;
    try {
      await for (final devices in scanProgressive(duration: limit)) {
        for (final d in devices) {
          if (d.address.toLowerCase() == wanted) seen = d;
        }
        if (seen != null && clock.elapsed >= atLeast) break;
      }
    } catch (_) {
      // 검색이 안 되면 지금까지 본 것으로 판정한다.
    }
    final device = seen;
    if (device == null) return false;
    if (!device.paired) {
      try {
        await _adapterOperation(() async {
          final adapter = await adapterAddress();
          diag.write('스피커 페어링 ${device.address}');
          await _bluetooth.callMethod(_adapterInterface, 'Pair',
              [DBusString(adapter), DBusString(device.address)]);
        });
      } catch (error) {
        diag.write('스피커 페어링 실패 ${device.address}: $error');
      }
    }
    return true;
  }

  /// V40: 스피커 A2DP 를 끊는다 — 같은 칩(KLZS-L1)의 램프 GATT 를 먼저 맺으려고 잠깐(바로 다시 붙인다).
  Future<void> disconnectSpeaker(String address) =>
      _adapterOperation(() async {
        final adapter = await adapterAddress();
        diag.write('스피커 잠깐 끊기 $address (램프 먼저)');
        await _bluetooth.callMethod(_a2dpInterface, 'Disconnect',
            [DBusString(adapter), DBusString(address)]);
      });

  Future<void> _confirmSpeakerConnected(String address) async {
    // Connecting is asynchronous in Bluetooth1. Do not claim success or start
    // feedback music until the A2DP profile reports an actual connection.
    for (var attempt = 0; attempt < 5; attempt++) {
      await Future<void>.delayed(const Duration(seconds: 1));
      if (await isA2dpConnected(address)) {
        // Verified on ATLAS 26.06: an A2DP connection registers a "bt_speaker"
        // output but the active output stays on hdmi_speaker until switched.
        await selectBluetoothOutput();
        return;
      }
    }
    throw StateError('A2DP 연결이 완료되지 않았습니다. 스피커의 페어링 모드와 출력을 확인하세요.');
  }

  /// Route system audio to the Bluetooth sink registered by AudioManager.
  /// Returns false when no Bluetooth output device is listed.
  Future<bool> selectBluetoothOutput() async {
    for (final entry in await _outputDevices()) {
      final id = entry.$2.toLowerCase();
      if (id.contains('bt') ||
          id.contains('bluetooth') ||
          id.contains('a2dp')) {
        if (await _activeOutputDevice() == entry.$1) return true;
        await _audio.callMethod(
            _audioManagerInterface, 'SetActiveOutputDevice', [entry.$1]);
        return true;
      }
    }
    return false;
  }

  Future<DBusObjectPath?> _activeOutputDevice() async {
    try {
      final value = await _audio.getProperty(
          _audioManagerInterface, 'ActiveOutputDevice');
      return value is DBusObjectPath ? value : null;
    } catch (_) {
      return null;
    }
  }

  /// AudioManager1.OutputDevices is a(oss): (object path, id, name).
  Future<List<(DBusObjectPath, String)>> _outputDevices() async {
    final value =
        await _audio.getProperty(_audioManagerInterface, 'OutputDevices');
    if (value is! DBusArray) return const [];
    final devices = <(DBusObjectPath, String)>[];
    for (final child in value.children) {
      if (child is! DBusStruct || child.children.length < 2) continue;
      final path = child.children[0];
      final id = child.children[1];
      if (path is DBusObjectPath && id is DBusString) {
        devices.add((path, id.value));
      }
    }
    return devices;
  }

  Future<bool> isA2dpConnected(String address) async {
    final adapter = await adapterAddress();
    final result = await _bluetooth.callMethod(
      _a2dpInterface,
      'GetStatus',
      [DBusString(adapter), DBusString(address)],
    );
    if (result.returnValues.isEmpty || result.returnValues.first is! DBusDict) {
      return false;
    }
    return _bool(result.returnValues.first as DBusDict, 'connected');
  }

  /// BLE 기기(램프·선풍기)에 GATT 로 붙는다. [role] 은 진단 로그의 이름일 뿐 동작은 같다(V38 — 전엔 선풍기도 "램프 …"로 찍혔다).
  Future<GattConnection> connectLamp(String address, {String role = '램프'}) =>
      _adapterOperation(() => _connectGatt(address.toUpperCase(), role));

  Future<GattConnection> _connectGatt(String address, String role) async {
    final existing = _gattConnections[address];
    if (existing != null) return existing;
    if (!await ensureBluetoothPermission()) {
      throw StateError(
          'Atlas Bluetooth 권한이 허용되지 않았습니다. 권한 요청을 허용한 뒤 다시 시도하세요.');
    }
    var adapter = await adapterAddress();
    final result = await _bluetooth.callMethod(
      _gattInterface,
      'Connect',
      [DBusString(adapter), DBusString(address), const DBusUint32(gattManualConnect)],
    );
    final clientId = result.returnValues.first.asString();
    try {
      // Connect returns the client id before the link is up. Discovering at
      // once failed with "Device with supplied address is not available"
      // (fan 10-24, lamp 10-26), then our Disconnect dropped the late link.
      final link = await waitForGattLink(_gattStatus, address,
          onChange: (state, elapsed) => diag.write(
              '$role GATT 링크 $address ($clientId): "$state" ${elapsed.inMilliseconds} ms'));
      if (adapter.isEmpty) {
        // Connect tolerates an empty adapter; DiscoverServices and writes do not.
        adapter = link.adapter;
        diag.write('$role 어댑터: GetInfo 비어 있음 → GetStatus "$adapter"');
      }
    } catch (_) {
      try {
        await _bluetooth.callMethod(_gattInterface, 'Disconnect',
            [DBusString(adapter), DBusString(clientId)]);
      } catch (_) {}
      rethrow;
    }
    var discovered = const <DBusValue>[];
    try {
      discovered = await discoverUntilServices(
          () async => (await _bluetooth.callMethod(
                _gattInterface,
                'DiscoverServices',
                [DBusString(adapter), DBusString(address)],
              ))
                  .returnValues,
          onError: (error, attempt) => diag
              .write('$role 서비스 검색 실패 $attempt ($address, $clientId): $error'),
          onEmpty: (retry) => diag
              .write('$role GATT 서비스 $address: 빈 목록 — 다시 검색 $retry'));
    } catch (_) {
      // Release by client id (not address), or every later Connect fails
      // with "Already Connected".
      try {
        await _bluetooth.callMethod(_gattInterface, 'Disconnect',
            [DBusString(adapter), DBusString(clientId)]);
      } catch (_) {}
      rethrow;
    }
    final services = gattServiceUuids(discovered);
    diag.write('$role GATT 서비스 $address: ${services.isEmpty ? '응답에 UUID 없음' : services.join(', ')}');
    final connection =
        GattConnection(adapter: adapter, clientId: clientId, services: services);
    _gattConnections[address] = connection;
    return connection;
  }

  Future<DBusArray?> _gattStatus() async {
    final status = await _bluetooth.callMethod(_gattInterface, 'GetStatus', []);
    final values = status.returnValues;
    return values.isNotEmpty && values.first is DBusArray
        ? values.first as DBusArray
        : null;
  }

  Future<void> disconnectGatt(GattConnection connection) =>
      _adapterOperation(() => _releaseGatt(connection));

  /// V38: 이전 실행이 남긴 GATT 링크를 그때 저장한 clientId 로 끊어 본다. 실패는 무시한다(이미 끊겼거나
  /// 다른 프로세스의 id 라 거절될 수 있다 — 그다음 Connect 가 판정한다). 11-02 Pi 5: 앱을 껐다 켜면 이전 링크가
  /// bluetooth-service 에 남아 새 Connect 가 "Already connected" 로 막혔고, GetStatus 에는 clientId 가 없다.
  Future<void> releaseStaleGatt(String adapter, String clientId) =>
      _adapterOperation(() async {
        try {
          await _bluetooth.callMethod(_gattInterface, 'Disconnect',
              [DBusString(adapter), DBusString(clientId)]);
          diag.write('이전 GATT 링크 정리 $clientId');
        } catch (error) {
          diag.write('이전 GATT 링크 정리 실패 $clientId: $error');
        }
      });

  Future<void> _releaseGatt(GattConnection connection) async {
    try {
      await _bluetooth.callMethod(_gattInterface, 'Disconnect',
          [DBusString(connection.adapter), DBusString(connection.clientId)]);
    } finally {
      _gattConnections.removeWhere((_, value) => identical(value, connection));
    }
  }

  Future<void> writeCharacteristic(
    GattConnection connection, {
    required String serviceId,
    required String characteristicId,
    required List<int> value,
  }) async {
    await _bluetooth.callMethod(_gattInterface, 'WriteCharacteristicValue', [
      DBusString(connection.adapter),
      DBusString(serviceId),
      DBusString(characteristicId),
      const DBusString(''),
      DBusString(connection.clientId),
      const DBusUint32(1),
      gattWritePayload(value),
    ]);
  }

  /// ReadCharacteristicValues reads the remote device, not a local cache.
  /// Not yet exercised on a Pi 5; callers treat a failure as "unknown".
  Future<List<int>?> readCharacteristic(
    GattConnection connection, {
    required String serviceId,
    required String characteristicId,
  }) async {
    final result = await _bluetooth
        .callMethod(_gattInterface, 'ReadCharacteristicValues', [
      DBusString(connection.adapter),
      DBusString(serviceId),
      DBusArray.string([characteristicId]),
      const DBusString(''),
      DBusString(connection.clientId),
    ]);
    return gattReadBytes(result.returnValues, characteristicId);
  }

  Future<void> close() async {
    _closing = true;
    await _adapterOperations;
    for (final connection in _gattConnections.values.toList()) {
      try {
        await _releaseGatt(connection);
      } catch (_) {}
    }
    await _client.close();
  }

  static String _string(DBusDict dict, String key) {
    final value = dict.children[DBusString(key)];
    final inner = value is DBusVariant ? value.value : value;
    return inner is DBusString ? inner.value : '';
  }

  static bool _bool(DBusDict dict, String key) {
    final value = dict.children[DBusString(key)];
    final inner = value is DBusVariant ? value.value : value;
    return inner is DBusBoolean && inner.value;
  }

  static int _uint(DBusDict dict, String key) {
    final value = dict.children[DBusString(key)];
    final inner = value is DBusVariant ? value.value : value;
    if (inner is DBusUint32) return inner.value;
    if (inner is DBusInt32) return inner.value;
    if (inner is DBusUint16) return inner.value;
    return 0;
  }

  static List<String> _strings(DBusDict dict, String key) {
    final value = dict.children[DBusString(key)];
    final inner = value is DBusVariant ? value.value : value;
    if (inner is! DBusArray) return const [];
    return [
      for (final item in inner.children)
        if (item is DBusString) item.value,
    ];
  }
}

class GattConnection {
  const GattConnection(
      {required this.adapter, required this.clientId, this.services = const {}});
  final String adapter;
  final String clientId;

  /// DiscoverServices 응답에서 찾은 128-bit UUID(소문자). ATLAS 응답 형식이 기록돼 있지 않아 비어 있을 수 있다 —
  /// 쓰는 쪽(선풍기 펌웨어 구분)은 비어 있으면 특성을 읽어 보고 정한다.
  final Set<String> services;
}

final _uuidPattern = RegExp(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    caseSensitive: false);

/// D-Bus 응답 어디에 있든 128-bit UUID 문자열을 모은다(서비스·특성 구분 없이).
Set<String> gattServiceUuids(List<DBusValue> reply) {
  final found = <String>{};
  void walk(DBusValue value) {
    switch (value) {
      case DBusString(:final value):
        for (final m in _uuidPattern.allMatches(value)) {
          found.add(m.group(0)!.toLowerCase());
        }
      case DBusVariant(:final value):
        walk(value);
      case DBusArray(:final children):
        children.forEach(walk);
      case DBusStruct(:final children):
        children.forEach(walk);
      case DBusDict(:final children):
        for (final entry in children.entries) {
          walk(entry.key);
          walk(entry.value);
        }
      default:
        break;
    }
  }

  reply.forEach(walk);
  return found;
}

/// Atlas WriteCharacteristicValue wraps this dict as "value" itself, so the
/// frame goes under "bytes" (verified on Pi 5, 10-12).
DBusDict gattWritePayload(List<int> frame) =>
    DBusDict(DBusSignature.string, DBusSignature.variant, {
      const DBusString('bytes'): DBusVariant(DBusArray(DBusSignature.byte,
          frame.map((byte) => DBusByte(byte)).toList(growable: false))),
    });

/// Bytes of [characteristicId] in a ReadCharacteristicValues reply, or null.
/// The value dict mirrors writes ({"bytes": [...]}); items may be bytes or ints.
List<int>? gattReadBytes(List<DBusValue> reply, String characteristicId) {
  if (reply.isEmpty || reply.first is! DBusArray) return null;
  final wanted = characteristicId.toLowerCase();
  DBusValue? unwrap(DBusValue? value) =>
      value is DBusVariant ? value.value : value;
  int? asInt(DBusValue value) => switch (value) {
        DBusByte(:final value) => value,
        DBusInt16(:final value) => value,
        DBusUint16(:final value) => value,
        DBusInt32(:final value) => value,
        DBusUint32(:final value) => value,
        _ => null,
      };
  for (final entry
      in (reply.first as DBusArray).children.whereType<DBusDict>()) {
    final uuid =
        AtlasBluetoothService._string(entry, 'characteristic').toLowerCase();
    if (uuid.isNotEmpty && uuid != wanted) continue;
    final value = unwrap(entry.children[const DBusString('value')]);
    final bytes = value is DBusDict
        ? unwrap(value.children[const DBusString('bytes')])
        : value;
    if (bytes is! DBusArray) continue;
    return bytes.children
        .map(asInt)
        .whereType<int>()
        .map((v) => v & 0xff)
        .toList();
  }
  return null;
}

/// Gatt.Connect `auto_connect`: 1 = connect now. 0 (used until 0.1.12) is
/// "connect whenever the device becomes available" — a background connection
/// that returns at once and comes up late or never (ATLAS Gatt spec, Connect).
const gattManualConnect = 1;

/// Adapter of [address] in a Gatt.GetStatus reply, or '' when not connected.
String gattAdapterFor(DBusArray status, String address) =>
    _gattEntry(status, address, 'adapterAddress');

/// Link state of [address] in a Gatt.GetStatus reply: "connecting",
/// "connected", "discovering", or '' when the device is not listed.
String gattStateFor(DBusArray status, String address) =>
    _gattEntry(status, address, 'status');

String _gattEntry(DBusArray status, String address, String key) {
  final wanted = address.toLowerCase();
  for (final entry in status.children.whereType<DBusDict>()) {
    if (AtlasBluetoothService._string(entry, 'address').toLowerCase() == wanted) {
      return AtlasBluetoothService._string(entry, key);
    }
  }
  return '';
}

/// Polls Gatt.GetStatus until [address] is "connected" (or already
/// "discovering") and returns that entry's adapter. Never-seen states are
/// reported through [onChange] so the diag log shows how long the link took.
/// Throws a StateError when the link is not up within [timeout].
Future<({String adapter, String state})> waitForGattLink(
    Future<DBusArray?> Function() status, String address,
    {Duration timeout = const Duration(seconds: 15),
    Duration interval = const Duration(milliseconds: 500),
    void Function(String state, Duration elapsed)? onChange}) async {
  final clock = Stopwatch()..start();
  String? last;
  while (true) {
    final reply = await status();
    final state =
        reply == null ? '' : gattStateFor(reply, address).toLowerCase();
    if (state != last) {
      last = state;
      onChange?.call(state, clock.elapsed);
    }
    if (state == 'connected' || state == 'discovering') {
      return (adapter: gattAdapterFor(reply!, address), state: state);
    }
    if (clock.elapsed >= timeout) {
      throw StateError('GATT 링크가 ${timeout.inSeconds}초 안에 올라오지 않았습니다 '
          '(마지막 상태 "${state.isEmpty ? '목록에 없음' : state}"). 기기 전원과 다른 기기 연결을 확인하세요.');
    }
    await Future<void>.delayed(interval);
  }
}

/// Retries DiscoverServices; the first call after Connect can fail.
/// V36: [discoverWithRetry] 가 성공했는데 서비스 UUID 가 하나도 없으면 [emptyDelay] 기다려 다시 검색한다(최대
/// [emptyRetries] 번). 11-01 Pi 5 실기: 선풍기 링크가 맺어진 직후 DiscoverServices 가 빈 목록으로 성공해, 앱이 그대로
/// 쓰기를 해 실패했다(램프는 쓸 즈음 BlueZ 가 서비스를 다 읽어 와 성공). 끝까지 비면 예전처럼 빈 목록으로 넘긴다.
Future<List<DBusValue>> discoverUntilServices(
    Future<List<DBusValue>> Function() discover,
    {int attempts = 4,
    Duration delay = const Duration(seconds: 2),
    int emptyRetries = 3,
    Duration emptyDelay = const Duration(seconds: 1),
    void Function(Object error, int attempt)? onError,
    void Function(int retry)? onEmpty}) async {
  var reply = const <DBusValue>[];
  for (var retry = 0;; retry++) {
    await discoverWithRetry(() async => reply = await discover(),
        attempts: attempts, delay: delay, onError: onError);
    if (gattServiceUuids(reply).isNotEmpty || retry >= emptyRetries) {
      return reply;
    }
    onEmpty?.call(retry + 1);
    await Future<void>.delayed(emptyDelay);
  }
}

Future<void> discoverWithRetry(Future<void> Function() discover,
    {int attempts = 4,
    Duration delay = const Duration(seconds: 2),
    void Function(Object error, int attempt)? onError}) async {
  for (var attempt = 1;; attempt++) {
    try {
      return await discover();
    } catch (error) {
      onError?.call(error, attempt);
      if (attempt >= attempts) rethrow;
      await Future<void>.delayed(delay);
    }
  }
}
