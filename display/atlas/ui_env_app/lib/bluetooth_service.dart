import 'dart:async';

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
      profiles.any((uuid) => uuid.toLowerCase().startsWith(_a2dpSinkUuidPrefix));

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

/// Atlas Bluetooth1 wrapper. Pairing and connection require user-consent
/// permissions declared in atlas/meta/appinfo.json; no system setting is
/// changed silently by this app.
class AtlasBluetoothService {
  AtlasBluetoothService() : _client = DBusClient.system() {
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

  Future<void> ensureBluetoothPermission() async {
    const scanPermission = 'com.atlas.permission.user_consent.bluetooth_scan';
    const connectPermission = 'com.atlas.permission.user_consent.bluetooth_connect';
    final scan = await _hasPermission(scanPermission);
    final connect = await _hasPermission(connectPermission);
    if (scan && connect) return;
    // PermissionAgent owns the consent UI. The app never grants itself a
    // Bluetooth privilege; after the user accepts, they can press scan again.
    for (final permission in [if (!scan) scanPermission, if (!connect) connectPermission]) {
      try {
        await _permission.callMethod(
          _permissionInterface,
          'RequestUserConsentPermission',
          [DBusString(permission)],
        );
      } catch (_) {
        // The final message below is the same for a denied or unavailable UI.
      }
    }
    throw StateError('Atlas Bluetooth 권한 요청을 표시했습니다. 허용한 뒤 다시 기기 검색을 누르세요.');
  }

  Future<String> adapterAddress() async {
    final result = await _bluetooth.callMethod(_adapterInterface, 'GetInfo', []);
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
    await ensureBluetoothPermission();
    final adapter = await adapterAddress();
    await _bluetooth.callMethod(
        _adapterInterface, 'StartDiscovery', [DBusString(adapter)]);
    try {
      await Future<void>.delayed(const Duration(seconds: 8));
      return _discoverable(adapter);
    } finally {
      try {
        await _bluetooth.callMethod(
            _adapterInterface, 'CancelDiscovery', [DBusString(adapter)]);
      } catch (_) {
        // A scan already stopped is harmless.
      }
    }
  }

  Future<List<BluetoothDeviceInfo>> _discoverable(String adapter) async {
    final result = await _bluetooth.callMethod(
      _deviceInterface,
      'GetDiscoverableDevices',
      [DBusString(adapter), const DBusString('')],
    );
    if (result.returnValues.isEmpty || result.returnValues.first is! DBusArray) {
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

  Future<void> connectSpeaker(BluetoothDeviceInfo device) async {
    if (!device.isAudioSink) {
      // Pairing a BLE-only record (e.g. "KLZS-L1 app") goes over SMP, which
      // this lamp does not support: BlueZ reports connected, then times out.
      throw StateError('${device.name}은(는) 오디오 기기가 아닙니다. 스피커를 페어링 모드로 두고 '
          '다시 검색해 오디오 기기 항목을 선택하세요.');
    }
    await ensureBluetoothPermission();
    final adapter = await adapterAddress();
    if (!device.paired) {
      await _bluetooth.callMethod(
          _adapterInterface, 'Pair', [DBusString(adapter), DBusString(device.address)]);
    }
    await _bluetooth.callMethod(
        _a2dpInterface, 'Connect', [DBusString(adapter), DBusString(device.address)]);
    // Connecting is asynchronous in Bluetooth1. Do not claim success or start
    // feedback music until the A2DP profile reports an actual connection.
    for (var attempt = 0; attempt < 5; attempt++) {
      await Future<void>.delayed(const Duration(seconds: 1));
      if (await isA2dpConnected(device.address)) {
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
      if (id.contains('bt') || id.contains('bluetooth') || id.contains('a2dp')) {
        await _audio.callMethod(_audioManagerInterface, 'SetActiveOutputDevice', [entry.$1]);
        return true;
      }
    }
    return false;
  }

  /// AudioManager1.OutputDevices is a(oss): (object path, id, name).
  Future<List<(DBusObjectPath, String)>> _outputDevices() async {
    final value = await _audio.getProperty(_audioManagerInterface, 'OutputDevices');
    if (value is! DBusArray) return const [];
    final devices = <(DBusObjectPath, String)>[];
    for (final child in value.children) {
      if (child is! DBusStruct || child.children.length < 2) continue;
      final path = child.children[0];
      final id = child.children[1];
      if (path is DBusObjectPath && id is DBusString) devices.add((path, id.value));
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

  Future<GattConnection> connectLamp(String address) async {
    await ensureBluetoothPermission();
    final adapter = await adapterAddress();
    final result = await _bluetooth.callMethod(
      _gattInterface,
      'Connect',
      [DBusString(adapter), DBusString(address), const DBusUint32(0)],
    );
    final clientId = result.returnValues.first.asString();
    await _bluetooth.callMethod(
      _gattInterface,
      'DiscoverServices',
      [DBusString(adapter), DBusString(address)],
    );
    return GattConnection(adapter: adapter, clientId: clientId);
  }

  Future<void> writeCharacteristic(
    GattConnection connection, {
    required String serviceId,
    required String characteristicId,
    required List<int> value,
  }) async {
    final bytes = DBusArray(DBusSignature.byte,
        value.map((byte) => DBusByte(byte)).toList(growable: false));
    final payload = DBusDict(DBusSignature.string, DBusSignature.variant, {
      const DBusString('value'): DBusVariant(bytes),
    });
    await _bluetooth.callMethod(_gattInterface, 'WriteCharacteristicValue', [
      DBusString(connection.adapter),
      DBusString(serviceId),
      DBusString(characteristicId),
      const DBusString(''),
      DBusString(connection.clientId),
      const DBusUint32(1),
      payload,
    ]);
  }

  Future<void> close() => _client.close();

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
  const GattConnection({required this.adapter, required this.clientId});
  final String adapter;
  final String clientId;
}