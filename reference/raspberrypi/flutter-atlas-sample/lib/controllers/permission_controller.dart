import 'dart:io';
import 'package:dbus/dbus.dart';
import 'dbus_config.dart';

class PermissionController {
  static const int stateGranted = 1;

  static const List<String> appUserConsentPermissions = [
    'com.atlas.permission.user_consent.contacts',
    'com.atlas.permission.user_consent.bluetooth_connect',
    'com.atlas.permission.user_consent.bluetooth_scan',
    'com.atlas.permission.user_consent.mediaplayer',
  ];

  static String permissionLabel(String permission) {
    final key = permission.split('.').last;
    return switch (key) {
      'contacts'          => '연락처 (Contacts)',
      'bluetooth_connect' => '블루투스 연결 (Bluetooth Connect)',
      'bluetooth_scan'    => '블루투스 검색 (Bluetooth Scan)',
      'mediaplayer'       => '미디어 재생 (Media Player)',
      _                   => key,
    };
  }

  late DBusClient _client;
  late DBusRemoteObject _agent;

  PermissionController() {
    _client = DBusClient.system();
    _agent = DBusRemoteObject(
      _client,
      name: DBusConfig.permissionAgentBusName,
      path: DBusObjectPath(DBusConfig.permissionAgentObjectPath),
    );
  }

  // ── 권한 체크: CheckSelfUserConsentPermission ─────────────────
  // 호출 프로세스 자신의 UID를 기준으로 확인 → UID 파라미터 불필요
  // 성공(empty) = 승인됨 / 에러(UserConsentNeeded, UserConsentDenied) = 미승인

  Future<bool> checkSelf(String permission) async {
    try {
      await _agent.callMethod(
        DBusConfig.permissionAgentInterface,
        'CheckSelfUserConsentPermission',
        [DBusString(permission)],
      );
      return true;
    } catch (e) {
      print('[Permission] checkSelf ($permission): $e');
      return false;
    }
  }

  /// 미승인 권한 목록 반환 (CheckSelfUserConsentPermission 기반)
  Future<List<String>> ungrantedPermissions() async {
    final ungranted = <String>[];
    for (final perm in appUserConsentPermissions) {
      if (!await checkSelf(perm)) ungranted.add(perm);
    }
    return ungranted;
  }

  // ── app_uid 취득: 권한 부여 호출용 ───────────────────────────
  // GetUserConsentPermissions([]) → app_uid 필드 추출
  // 실패 시 AppManager1.GetStatus → /proc/self/status 순 폴백

  Future<String> getAppUid() async {
    try {
      final result = await _agent.callMethod(
        DBusConfig.permissionAgentInterface,
        'GetUserConsentPermissions',
        [DBusArray(DBusSignature('s'), [])],
      );
      if (result.returnValues.isNotEmpty) {
        final raw = result.returnValues.first;
        if (raw is DBusArray) {
          for (final item in raw.children) {
            if (item is! DBusDict) continue;
            final dict = item.children;
            if (_strVal(dict[DBusString('app_id')]) != DBusConfig.appId) continue;
            final uid = _strVal(dict[DBusString('app_uid')]);
            if (uid.isNotEmpty) {
              print('[Permission] getAppUid from GetUserConsentPermissions: $uid');
              return uid;
            }
          }
        }
      }
    } catch (e) {
      print('[Permission] GetUserConsentPermissions failed: $e');
    }

    // 폴백: AppManager1.GetStatus
    final numeric = await _numericUidFromAppManager();
    if (numeric > 0) {
      final uid = 'u0_a$numeric';
      print('[Permission] getAppUid from AppManager: $uid');
      return uid;
    }

    // 폴백: /proc/self/status
    try {
      final lines = await File('/proc/self/status').readAsLines();
      for (final line in lines) {
        if (line.startsWith('Uid:')) {
          final parts = line.split(RegExp(r'\s+'));
          if (parts.length >= 2) {
            final uid = 'u0_a${parts[1]}';
            print('[Permission] getAppUid from /proc/self/status: $uid');
            return uid;
          }
        }
      }
    } catch (e) {
      print('[Permission] /proc/self/status read failed: $e');
    }
    return '';
  }

  Future<int> _numericUidFromAppManager() async {
    try {
      final appManager = DBusRemoteObject(
        _client,
        name: DBusConfig.appManagerBusName,
        path: DBusObjectPath(DBusConfig.appManagerObjectPath),
      );
      final result = await appManager.callMethod(
        DBusConfig.appManagerInterface,
        'GetStatus',
        [DBusString(DBusConfig.appId)],
      );
      if (result.returnValues.isNotEmpty) {
        final raw = result.returnValues.first;
        if (raw is DBusDict) {
          final v = raw.children[DBusString('uid')];
          final inner = v is DBusVariant ? v.value : v;
          if (inner is DBusDouble)  return inner.value.toInt();
          if (inner is DBusUint32)  return inner.value;
          if (inner is DBusInt32)   return inner.value;
        }
      }
    } catch (e) {
      print('[Permission] AppManager1.GetStatus failed: $e');
    }
    return 0;
  }

  static String _strVal(DBusValue? v) {
    if (v is DBusVariant) v = v.value;
    return v is DBusString ? v.value : '';
  }

  // ── 권한 부여 ────────────────────────────────────────────────
  // NOTE: D-Bus 정책상 일반 앱(uid=5003)은 UpdateUserConsentPermission 호출이 거부됨.
  //       appinfo.json에 permission_agent 권한 추가 또는 Settings 앱 경유 필요.

  Future<bool> grant(String permission, String appUid) async {
    try {
      await _agent.callMethod(
        DBusConfig.permissionAgentInterface,
        'UpdateUserConsentPermission',
        [DBusString(appUid), DBusString(permission), DBusInt32(stateGranted)],
      );
      print('[Permission] Granted: $permission (appUid=$appUid)');
      return true;
    } catch (e) {
      print('[Permission] grant failed ($permission appUid=$appUid): $e');
      return false;
    }
  }

  Future<void> grantAll(List<String> permissions, String appUid) async {
    for (final perm in permissions) {
      await grant(perm, appUid);
    }
  }

  Future<void> dispose() async {
    await _client.close();
  }
}
