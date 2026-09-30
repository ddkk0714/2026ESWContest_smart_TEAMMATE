import 'bluetooth_service.dart';
import 'display_state.dart';
import 'music_playback.dart';

const ilinkServiceUuid = '0000a032-0000-1000-8000-00805f9b34fb';
const ilinkWriteUuid = '0000a040-0000-1000-8000-00805f9b34fb';

List<int> ilinkFrame(List<int> body) {
  if (body.isEmpty || body.any((value) => value < 0 || value > 255)) {
    throw ArgumentError.value(body, 'body', 'iLink bytes must be 0..255');
  }
  // Checksum = 0xFF - sum(every byte incl. 55 AA and the mode byte): the rule the
  // lamp uses in its own status frames and in donandren/ilink_light. For 0x01
  // frames the 55+AA+01 prefix cancels out (0x100); for 0x03 RGB frames it does not.
  final head = [0x55, 0xaa, ...body];
  final checksum = (0xff - head.fold<int>(0, (sum, v) => sum + v)) & 0xff;
  return [...head, checksum];
}

List<List<int>> ilinkFramesForPhase(String phase) {
  switch (phase) {
    case 'idle':
    case 'end':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x00])
      ];
    case 'start':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 48]),
        ilinkFrame([0x03, 0x08, 0x02, 255, 190, 120]),
      ];
    case 'focus':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 110]),
        ilinkFrame([0x01, 0x08, 0x09, 0x02]),
      ];
    case 'fatigue':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 180]),
        ilinkFrame([0x03, 0x08, 0x02, 255, 120, 40]),
      ];
    case 'recovery':
      return [
        ilinkFrame([0x01, 0x08, 0x05, 0x01]),
        ilinkFrame([0x01, 0x08, 0x01, 90]),
        ilinkFrame([0x03, 0x08, 0x02, 80, 180, 255]),
      ];
    default:
      return const [];
  }
}

class IlinkLampFeedback {
  IlinkLampFeedback(this._bluetooth);
  final AtlasBluetoothService _bluetooth;
  String? _address;
  GattConnection? _connection;
  DateTime? _lastOkAt;
  Object? _lastError;

  bool get configured => _address?.isNotEmpty ?? false;
  String? get address => _address;
  bool get connected => _connection != null;
  DateTime? get lastOkAt => _lastOkAt;
  Object? get lastError => _lastError;

  void select(String address) {
    if (_address == address) return;
    _address = address;
    _connection = null;
    _lastOkAt = null;
    _lastError = null;
  }

  Future<bool> connect() async {
    if (!configured) return false;
    if (_connection != null) return true;
    try {
      _connection = await _bluetooth.connectLamp(_address!);
      _lastError = null;
      return true;
    } catch (error) {
      _connection = null;
      _lastError = error;
      return false;
    }
  }

  Future<void> apply(String phase) async {
    if (!configured) return;
    final frames = ilinkFramesForPhase(phase);
    if (frames.isEmpty) return;
    // 끊긴 GATT 세션은 재사용하지 않고 한 번만 새 세션으로 되풀이한다.
    for (var attempt = 0; attempt < 2; attempt++) {
      if (!await connect()) continue;
      try {
        for (final frame in frames) {
          await _bluetooth.writeCharacteristic(_connection!,
              serviceId: ilinkServiceUuid,
              characteristicId: ilinkWriteUuid,
              value: frame);
          _lastOkAt = DateTime.now();
        }
        _lastError = null;
        return;
      } catch (error) {
        _connection = null;
        _lastError = error;
      }
    }
  }
}

/// A state transition, rather than raw sensor data, triggers feedback. The
/// FSM remains the sole decision maker; failures only affect this optional UI
/// output and never alter the next FSM state.
class FeedbackCoordinator {
  FeedbackCoordinator({
    required this.music,
    required this.lamp,
    required this.bluetooth,
  });
  final MusicPlayback music;
  final IlinkLampFeedback lamp;
  final AtlasBluetoothService bluetooth;
  String? _speakerAddress;
  String? get speakerAddress => _speakerAddress;
  bool enabled = false;
  String? _lastPhase;

  void selectSpeaker(String address) => _speakerAddress = address;

  /// [_speakerAddress] is the classic-Bluetooth (A2DP) address, distinct from
  /// the lamp's BLE address on combined speaker/lamp products.
  Future<void> reconcileAudioOutput() async {
    final address = _speakerAddress;
    if (address == null || !music.isPlaying) return;
    try {
      if (!await bluetooth.isA2dpConnected(address)) {
        if (music.isPlaying) {
          await music.toggle();
        }
      }
    } catch (_) {
      // A lost Bluetooth service is treated like a disconnected output.
      if (music.isPlaying) await music.toggle();
    }
  }

  Future<void> apply(DisplayState state) async {
    if (!enabled || state.phase == _lastPhase) return;
    _lastPhase = state.phase;
    try {
      await lamp.apply(state.phase);
    } catch (_) {
      // Bluetooth light is optional. Music and the UI must continue if absent.
    }
    if (state.phase == 'fatigue') {
      if (!music.isPlaying) await music.toggle();
    } else if (state.phase == 'recovery' ||
        state.phase == 'idle' ||
        state.phase == 'end') {
      if (music.isPlaying) await music.toggle();
    }
  }
}
