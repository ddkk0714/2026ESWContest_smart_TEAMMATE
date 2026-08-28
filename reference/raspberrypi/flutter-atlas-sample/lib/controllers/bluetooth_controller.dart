import 'package:dbus/dbus.dart';
import 'dbus_config.dart';

/// Bluetooth 어댑터 제어 — 검색 시작/중단, 페어링, 어댑터 정보 조회
class BluetoothAdapterController {
  late DBusClient _client;
  late DBusRemoteObject _object;

  BluetoothAdapterController() {
    _client = DBusClient.system();
    _object = DBusRemoteObject(_client, name: DBusConfig.bluetoothBusName, path: DBusObjectPath(DBusConfig.bluetoothObjectPath));
  }

  Future<void> setInfo(String info) async {
    await _object.callMethod(DBusConfig.adapterInterface, 'SetInfo', [DBusString(info)]);
  }

  Future<Map<String, dynamic>> getInfo() async {
    try {
      var result = await _object.callMethod(DBusConfig.adapterInterface, 'GetInfo', []);

      DBusDict? targetDict;
      var rootValue = result.returnValues[0];

      print('=== DEBUG: getInfo() Raw Response ===');
      print('Signature: ${rootValue.signature.value}');
      print('Type: ${rootValue.runtimeType}');
      print('Raw Data: $rootValue');
      print('=====================================');

      if (rootValue is DBusArray && rootValue.children.isNotEmpty && rootValue.children[0] is DBusDict) {
        var outerDict = rootValue.children[0] as DBusDict;
        if (outerDict.children.containsKey(DBusString('adapter_address'))) {
          targetDict = outerDict;
        } else if (outerDict.children.containsKey(DBusString('adapter_info'))) {
          var innerValue = outerDict.children[DBusString('adapter_info')]?.asVariant();
          if (innerValue is DBusArray && innerValue.children.isNotEmpty && innerValue.children[0] is DBusDict) {
            targetDict = innerValue.children[0] as DBusDict;
          } else if (innerValue is DBusDict) {
            targetDict = innerValue;
          }
        }
      } else if (rootValue is DBusDict) {
        if (rootValue.children.containsKey(DBusString('adapter_address'))) {
          targetDict = rootValue;
        } else if (rootValue.children.containsKey(DBusString('adapter_info'))) {
          var innerValue = rootValue.children[DBusString('adapter_info')]?.asVariant();
          if (innerValue is DBusArray && innerValue.children.isNotEmpty && innerValue.children[0] is DBusDict) {
            targetDict = innerValue.children[0] as DBusDict;
          } else if (innerValue is DBusDict) {
            targetDict = innerValue;
          }
        }
      }

      if (targetDict != null) {
        return {
          'adapter_address': targetDict.children[DBusString('adapter_address')]?.asVariant().asString(),
          'name':            targetDict.children[DBusString('name')]?.asVariant().asString(),
          'powered':         targetDict.children[DBusString('powered')]?.asVariant().asBoolean(),
          'discoverable':    targetDict.children[DBusString('discoverable')]?.asVariant().asBoolean(),
          'discovering':     targetDict.children[DBusString('discovering')]?.asVariant().asBoolean(),
        };
      }
    } catch (e) {
      print('Error parsing Adapter GetInfo: $e');
    }
    return {};
  }

  Future<void> startDiscovery([String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothAdapterController] startDiscovery called with address: "$adapterAddress"');
    await _object.callMethod(DBusConfig.adapterInterface, 'StartDiscovery', [DBusString(adapterAddress)]);
  }

  Future<void> cancelDiscovery([String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothAdapterController] cancelDiscovery called with address: "$adapterAddress"');
    await _object.callMethod(DBusConfig.adapterInterface, 'CancelDiscovery', [DBusString(adapterAddress)]);
  }

  Future<void> pair(String deviceAddress, [String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothAdapterController] pair called for MAC: $deviceAddress');
    await _object.callMethod(DBusConfig.adapterInterface, 'Pair', [DBusString(adapterAddress), DBusString(deviceAddress)]);
  }

  Future<void> unpair(String deviceAddress, [String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothAdapterController] unpair called for MAC: $deviceAddress');
    await _object.callMethod(DBusConfig.adapterInterface, 'Unpair', [DBusString(adapterAddress), DBusString(deviceAddress)]);
  }

  void dispose() => _client.close();
}

/// A2DP 프로파일 연결/해제 제어
class BluetoothA2dpController {
  late DBusClient _client;
  late DBusRemoteObject _object;

  BluetoothA2dpController() {
    _client = DBusClient.system();
    _object = DBusRemoteObject(_client, name: DBusConfig.bluetoothBusName, path: DBusObjectPath(DBusConfig.bluetoothObjectPath));
  }

  Future<Map<String, dynamic>> getStatus(String deviceAddress, [String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothA2dpController] getStatus called for MAC: $deviceAddress');
    var result = await _object.callMethod(
      DBusConfig.a2dpInterface,
      'GetStatus',
      [DBusString(adapterAddress), DBusString(deviceAddress)],
      replySignature: DBusSignature('a{sv}'),
    );
    var dict = result.returnValues[0] as DBusDict;
    return {
      'adapter_address': dict.children[DBusString('adapter_address')]?.asVariant().asString(),
      'connecting':      dict.children[DBusString('connecting')]?.asVariant().asBoolean(),
      'connected':       dict.children[DBusString('connected')]?.asVariant().asBoolean(),
      'playing':         dict.children[DBusString('playing')]?.asVariant().asBoolean(),
    };
  }

  Future<void> connect(String deviceAddress, [String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothA2dpController] connect called for MAC: $deviceAddress');
    await _object.callMethod(DBusConfig.a2dpInterface, 'Connect', [DBusString(adapterAddress), DBusString(deviceAddress)]);
  }

  Future<void> disconnect(String deviceAddress, [String adapterAddress = ""]) async {
    print('DEBUG: [BluetoothA2dpController] disconnect called for MAC: $deviceAddress');
    await _object.callMethod(DBusConfig.a2dpInterface, 'Disconnect', [DBusString(adapterAddress), DBusString(deviceAddress)]);
  }

  void dispose() => _client.close();
}

/// 검색된 Bluetooth 기기 목록 조회
class BluetoothDeviceController {
  late DBusClient _client;
  late DBusRemoteObject _object;

  BluetoothDeviceController() {
    _client = DBusClient.system();
    _object = DBusRemoteObject(_client, name: DBusConfig.bluetoothBusName, path: DBusObjectPath(DBusConfig.bluetoothObjectPath));
  }

  Future<List<String>> getConnectedDevices() async {
    var result = await _object.callMethod(DBusConfig.deviceInterface, 'GetConnectedDevices', [], replySignature: DBusSignature('as'));
    return result.returnValues[0].asStringArray().toList();
  }

  Future<List<Map<String, dynamic>>> getDiscoverableDevices([String filter1 = "", String filter2 = ""]) async {
    print('DEBUG: [BluetoothDeviceController] getDiscoverableDevices called');
    try {
      var result = await _object.callMethod(
        DBusConfig.deviceInterface,
        'GetDiscoverableDevices',
        [DBusString(filter1), DBusString(filter2)],
      );

      List<Map<String, dynamic>> devices = [];
      var rootValue = result.returnValues[0];

      if (rootValue is DBusArray) {
        for (var child in rootValue.children) {
          if (child is DBusDict) {
            var address = child.children[DBusString('address')]?.asVariant().asString() ?? '';
            var rawName = child.children[DBusString('name')]?.asVariant().asString() ?? '';
            if (rawName.trim().isEmpty ||
                rawName.replaceAll('-', ':').toUpperCase() == address.replaceAll('-', ':').toUpperCase()) {
              continue;
            }
            devices.add({
              'address': address,
              'name':    rawName,
              'paired':  child.children[DBusString('paired')]?.asVariant().asBoolean() ?? false,
              'trusted': child.children[DBusString('trusted')]?.asVariant().asBoolean() ?? false,
              'blocked': child.children[DBusString('blocked')]?.asVariant().asBoolean() ?? false,
            });
          }
        }
      }
      return devices;
    } catch (e) {
      final s = e.toString();
      if (s.contains('org.freedesktop.DBus.Error.InvalidArgs')) {
        throw Exception('Adapter address is invalid or not recognized.');
      } else if (s.contains('org.freedesktop.DBus.Error.Failed')) {
        throw Exception('General error.');
      }
      print('Error parsing GetDiscoverableDevices: $e');
      return [];
    }
  }

  void dispose() => _client.close();
}
