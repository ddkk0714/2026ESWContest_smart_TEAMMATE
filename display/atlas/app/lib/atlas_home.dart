import 'dart:io';

import 'package:dbus/dbus.dart';

/// Atlas does not automatically foreground an already-running home app after a
/// fullscreen third-party app exits. Try to recreate the built-in home window,
/// then still close DESKMATE if this Atlas image denies the request.
Future<void> returnToAtlasHome() async {
  final client = DBusClient.system();
  final manager = DBusRemoteObject(
    client,
    name: 'com.atlas.AppManager1',
    path: DBusObjectPath('/com/atlas/AppManager1'),
  );
  try {
    await manager.callMethod(
        'com.atlas.AppManager1', 'Stop', [const DBusString('com.atlas.app.home')]);
    await Future<void>.delayed(const Duration(milliseconds: 350));
    await manager.callMethod(
        'com.atlas.AppManager1', 'Start', [const DBusString('com.atlas.app.home')]);
  } catch (_) {
    // Some Atlas policies do not permit this call from third-party apps.
  } finally {
    await client.close();
    exit(0);
  }
}