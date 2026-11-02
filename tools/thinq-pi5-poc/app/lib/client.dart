import 'dart:convert';
import 'package:dbus/dbus.dart';

abstract class PocClient {
  Future<Map<String, dynamic>> exchange(Map<String, dynamic> request);
  Future<Map<String, dynamic>> authStatus();
  Future<void> close();
}

class NativePocClient implements PocClient {
  final DBusClient _bus = DBusClient.system();
  @override
  Future<Map<String, dynamic>> exchange(Map<String, dynamic> request) async {
    try {
      final object = DBusRemoteObject(_bus,
          name: 'com.deskmate.thinqpoc1',
          path: DBusObjectPath('/com/deskmate/thinqpoc1'));
      final response = await object
          .callMethod('com.deskmate.thinqpoc1', 'Exchange',
              [DBusString(jsonEncode(request))],
              replySignature: DBusSignature('s'))
          .timeout(const Duration(seconds: 30));
      return Map<String, dynamic>.from(
          jsonDecode(response.returnValues.single.asString()) as Map);
    } catch (_) {
      // Never expose raw D-Bus exceptions, identifiers or credentials.
      return {
        'status': 'unavailable',
        'error': 'service_policy_runtime_or_timeout_unverified',
        'post_enabled': false
      };
    }
  }

  @override
  Future<Map<String, dynamic>> authStatus() async {
    try {
      final object = DBusRemoteObject(_bus,
          name: 'com.deskmate.thinqpoc1',
          path: DBusObjectPath('/com/deskmate/thinqpoc1'));
      final response = await object
          .callMethod('com.deskmate.thinqpoc1', 'AuthStatus', [],
              replySignature: DBusSignature('s'))
          .timeout(const Duration(seconds: 5));
      return Map<String, dynamic>.from(
          jsonDecode(response.returnValues.single.asString()) as Map);
    } catch (_) {
      return {'status': 'platform_binding_unverified', 'post_enabled': false};
    }
  }

  @override
  Future<void> close() => _bus.close();
}
