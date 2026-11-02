import 'dart:async';

import 'package:deskmate_display/bluetooth_service.dart';
import 'package:deskmate_display/bluetooth_link.dart';
import 'package:deskmate_display/link_status.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:deskmate_display/fan_control.dart';
import 'package:deskmate_display/feedback_policy.dart';
import 'package:deskmate_display/music_playback.dart';
import 'package:flutter_test/flutter_test.dart';

// Deliberately non-hardware identifiers; this fake never creates a D-Bus client.
const deviceA = 'fixture-device-a';
const deviceB = 'fixture-device-b';

void main() {
  for (final isFan in [false, true]) {
    group(isFan ? 'fan undo binding' : 'lamp undo binding', () {
      late _Bluetooth bluetooth;
      late IlinkLampFeedback lamp;
      late BleFanControl fan;
      late FeedbackCoordinator coordinator;
      void select(String device) =>
          isFan ? fan.select(device) : lamp.select(device);
      void forget() => isFan ? fan.forget() : lamp.forget();
      void disconnect() => isFan ? fan.disconnect() : lamp.disconnect();
      Map<String, dynamic> command(String id, {String? undoOf}) => {
            'command_id': id,
            'target_id': isFan ? 'vent_fan' : 'desk_lamp',
            'operation': 'set_power',
            'value': undoOf == null ? 'on' : 'off',
            'gate': undoOf == null ? 'suggest' : 'undo',
            'requires_confirmation': false,
            'expires_ts_ms': DateTime.now()
                .add(const Duration(seconds: 15))
                .millisecondsSinceEpoch,
            if (undoOf != null) 'undo_of_command_id': undoOf,
          };
      Future<Map<String, dynamic>?> send(String id, {String? undoOf}) =>
          coordinator.handleControlCommand(command(id, undoOf: undoOf));
      Future<void> parent() async {
        final result = await send('parent');
        expect(result?['status'], isFan ? 'succeeded' : 'executing');
        expect(bluetooth.writes.length, 1);
      }

      Future<void> blocked(
          {String id = 'undo', String undoOf = 'parent'}) async {
        final before = bluetooth.writes.length;
        final result = await send(id, undoOf: undoOf);
        expect(result?['status'], 'cancelled');
        expect(result?['error_code'], 'superseded_command');
        expect(bluetooth.writes.length, before,
            reason:
                'Refusal must happen before the BLE write function is called');
      }

      setUp(() {
        bluetooth = _Bluetooth();
        lamp = IlinkLampFeedback(bluetooth);
        fan = BleFanControl(bluetooth, retryDelay: Duration.zero);
        coordinator = FeedbackCoordinator(
            music: _Music(), lamp: lamp, bluetooth: bluetooth, fan: fan)
          ..enabled = true;
        select(deviceA);
      });

      test('A command followed by its own undo works once', () async {
        await parent();
        final result = await send('undo', undoOf: 'parent');
        expect(result?['status'], isFan ? 'succeeded' : 'executing');
        expect(bluetooth.writes, [deviceA, deviceA]);
        expect(
            bluetooth.frames.last, isFan ? [1, 0] : ilinkFrame([1, 8, 5, 0]));
        final count = bluetooth.writes.length;
        await send('undo', undoOf: 'parent'); // QoS duplicate is cached.
        expect(bluetooth.writes.length, count);
        await blocked(id: 'reused-parent');
        await blocked(id: 'undo-an-undo', undoOf: 'undo');
      });
      test('selecting B rejects A undo but permits a fresh B command',
          () async {
        await parent();
        select(deviceB);
        await blocked();
        final result = await send('b-command');
        expect(result?['status'], isFan ? 'succeeded' : 'executing');
        expect(bluetooth.writes, [deviceA, deviceB]);
        await blocked(id: 'old-parent-again');
        expect((await send('b-undo', undoOf: 'b-command'))?['status'],
            isFan ? 'succeeded' : 'executing');
        expect(bluetooth.writes, [deviceA, deviceB, deviceB]);
      });
      test('forget rejects old undo and does not write or reconnect', () async {
        await parent();
        forget();
        final connects = bluetooth.connects;
        await blocked();
        expect(bluetooth.connects, connects);
      });
      test('A to B to A cannot revive the previous A undo', () async {
        await parent();
        select(deviceB);
        select(deviceA);
        await blocked();
      });
      test('forget and reselect A cannot revive undo', () async {
        await parent();
        forget();
        select(deviceA);
        await blocked();
      });
      test('disconnect and rebind same A invalidate old undo', () async {
        await parent();
        disconnect();
        await blocked();
        select(deviceA);
        expect(await (isFan ? fan.connect() : lamp.connect()), isTrue);
        await blocked(id: 'rebound-undo');
        expect((await send('new-a'))?['status'],
            isFan ? 'succeeded' : 'executing');
      });
      for (final roundTrip in [false, true]) {
        test('selection during connect blocks write; round trip=$roundTrip',
            () async {
          bluetooth.holdNextConnection();
          final pending = send('pending');
          await bluetooth.connectStarted!.future;
          select(deviceB);
          if (roundTrip) select(deviceA);
          bluetooth.releaseConnection();
          await pending;
          expect(bluetooth.writes, isEmpty);
          expect((await send('fresh'))?['status'],
              isFan ? 'succeeded' : 'executing');
          expect(bluetooth.writes.single, roundTrip ? deviceA : deviceB);
          await blocked(undoOf: 'pending');
        });
      }
      test('queued undo rechecks binding immediately before BLE write',
          () async {
        await parent();
        bluetooth.holdNextWrite();
        final blocker = isFan
            ? fan.send([1, 1], control: true).then<void>((_) {})
            : lamp.writeFrames([
                ilinkFrame([1, 8, 5, 1])
              ], control: true).then<void>((_) {});
        await bluetooth.writeStarted!.future;
        final pendingUndo = send('queued-undo', undoOf: 'parent');
        select(deviceB);
        final count = bluetooth.writes.length;
        bluetooth.releaseWrite();
        await blocker;
        expect((await pendingUndo)?['error_code'], 'superseded_command');
        expect(bluetooth.writes.length, count);
      });
      test(
          'selection manager invalidates a pending connect and forgets controller',
          () async {
        final manager = BluetoothLinkManager(
            bluetooth: bluetooth,
            feedback: coordinator,
            settings: SettingsStore(
                candidates: const [], legacyPostureCandidates: const []));
        addTearDown(manager.dispose);
        BluetoothDeviceInfo device(String id) => BluetoothDeviceInfo(
            address: id, name: 'fixture', paired: false, connected: false);
        bluetooth.holdNextConnection();
        final connecting = isFan
            ? manager.selectFan(device(deviceA))
            : manager.selectLamp(device(deviceA));
        await bluetooth.connectStarted!.future;
        if (isFan) {
          await manager.selectFan(device(deviceB));
        } else {
          await manager.selectLamp(device(deviceB));
        }
        bluetooth.releaseConnection();
        await connecting;
        expect(isFan ? fan.address : lamp.address, deviceB);
        expect(bluetooth.writes, isEmpty);
        await send('fresh-manager');
        expect(bluetooth.writes.single, deviceB);
        manager.forget(isFan ? LinkId.fan : LinkId.lamp);
        expect(isFan ? fan.configured : lamp.configured, isFalse);
        await blocked(undoOf: 'fresh-manager');
      });
      test(
          'explicit same-device rebind invalidates undo without duplicate GATT connect',
          () async {
        final manager = BluetoothLinkManager(
            bluetooth: bluetooth,
            feedback: coordinator,
            settings: SettingsStore(
                candidates: const [], legacyPostureCandidates: const []));
        addTearDown(manager.dispose);
        const device = BluetoothDeviceInfo(
            address: deviceA, name: 'fixture', paired: false, connected: false);
        if (isFan) {
          await manager.selectFan(device);
        } else {
          await manager.selectLamp(device);
        }
        await parent();
        final connects = bluetooth.connects;
        if (isFan) {
          expect(await manager.reconnectFan(), isTrue);
        } else {
          expect(await manager.reconnectLamp(), isTrue);
        }
        expect(bluetooth.connects, connects);
        await blocked();
      });
      test('manual action invalidates old undo', () async {
        await parent();
        if (isFan) {
          await fan.send([1, 0]);
        } else {
          await lamp.setBrightness(33);
        }
        await blocked();
      });
      test('manual action during connect supersedes automatic write', () async {
        bluetooth.holdNextConnection();
        final automatic = send('pending-auto');
        await bluetooth.connectStarted!.future;
        final manual = isFan
            ? fan.send([1, 0]).then<void>((_) {})
            : lamp.setBrightness(33).then<void>((_) {});
        bluetooth.releaseConnection();
        await automatic;
        await manual;
        expect(
            bluetooth.frames,
            isFan
                ? [
                    [1, 0]
                  ]
                : [
                    ilinkFrame([1, 8, 5, 1]),
                    ilinkFrame([1, 8, 1, 33])
                  ]);
      });
      test('disabled then enabled does not resurrect undo', () async {
        await parent();
        coordinator.enabled = false;
        coordinator.enabled = true;
        await blocked();
      });
      test('different latest command supersedes previous undo', () async {
        await parent();
        await send('second');
        await blocked();
      });
      test('link write failure invalidates previous undo even after retry',
          () async {
        await parent();
        bluetooth.failWrites = 1;
        await send('retry');
        await blocked();
      });
    });
  }
}

class _Bluetooth implements AtlasBluetoothService {
  final writes = <String>[];
  final frames = <List<int>>[];
  final addresses = <String, String>{};
  Completer<void>? writeStarted;
  Completer<void>? _releaseWrite;
  void holdNextWrite() {
    writeStarted = Completer<void>();
    _releaseWrite = Completer<void>();
  }

  void releaseWrite() => _releaseWrite!.complete();
  int connects = 0;
  int failWrites = 0;
  bool power = true;
  Completer<void>? connectStarted;
  Completer<void>? _release;
  void holdNextConnection() {
    connectStarted = Completer<void>();
    _release = Completer<void>();
  }

  void releaseConnection() => _release!.complete();
  @override
  Future<GattConnection> connectLamp(String address) async {
    final release = _release;
    if (release != null) {
      connectStarted!.complete();
      await release.future;
      _release = null;
    }
    final id = 'fixture-${++connects}';
    addresses[id] = address;
    return GattConnection(adapter: 'fake-adapter', clientId: id);
  }

  @override
  Future<void> writeCharacteristic(GattConnection connection,
      {required String serviceId,
      required String characteristicId,
      required List<int> value}) async {
    writes.add(addresses[connection.clientId]!);
    frames.add(List.of(value));
    final release = _releaseWrite;
    if (release != null) {
      writeStarted!.complete();
      await release.future;
      _releaseWrite = null;
    }
    if (failWrites > 0) {
      failWrites--;
      throw StateError('fixture link loss');
    }
    if (serviceId == fanServiceUuid) power = value[1] == 1;
  }

  @override
  Future<List<int>?> readCharacteristic(GattConnection connection,
          {required String serviceId,
          required String characteristicId}) async =>
      [1, power ? 1 : 0, 60, 2, 0, 0, 0, 0];
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnimplementedError();
}

class _Music implements MusicPlayback {
  @override
  bool get isPlaying => false;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnimplementedError();
}
