/// 블루투스 스피커·램프를 기억하고, 앱을 켤 때 검색 없이 다시 붙인다.
///
/// 지금까지는 켤 때마다 검색(8초) → 스피커 선택 → 램프 선택을 다시 해야 했다.
/// 시연 중 앱을 다시 켜야 하는 일이 생기면 그 절차가 통째로 반복된다. 여기서
/// 연결에 성공한 기기를 보드 설정 파일(SettingsStore)에 적어 두고, 시작할 때
/// 그 주소로 바로 붙는다. 실패해도 앱은 계속 돈다 — 블루투스는 선택적 출력이다.
///
/// D-Bus 호출(검색·A2DP·GATT)은 AtlasBluetoothService·IlinkLampFeedback 이 한다.
/// 여기는 "무엇을 언제 다시 붙일지"와 연결 탭에 보일 상태만 맡는다.
///
/// 계획: docs/plan/app-connection-robustness-plan.md §4 "3. 블루투스".
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import 'bluetooth_service.dart';
import 'feedback_policy.dart';
import 'link_status.dart';
import 'settings_store.dart';

/// 스피커+램프 일체형(예: KLZS-L1)은 주소가 둘이다: 오디오(BR/EDR, A2DP)와 BLE(GATT
/// 램프, 이름 뒤에 " app" 등이 붙는다). 검색 결과에서 그 짝을 찾는다.
///
/// 규칙: BLE 전용 기기의 이름이 오디오 기기 이름으로 시작하면 짝이다. 짝이 여럿이면
/// 이름이 가장 긴 오디오 기기를 고른다(더 구체적인 이름이 맞는 짝이다).
({BluetoothDeviceInfo speaker, BluetoothDeviceInfo lamp})? findSpeakerLampPair(
    List<BluetoothDeviceInfo> devices) {
  final speakers = devices.where((d) => d.isAudioSink).toList()
    ..sort((a, b) => b.name.length.compareTo(a.name.length));
  final lamps = devices.where((d) => d.isBleOnly && !d.isAudioSink).toList();
  for (final speaker in speakers) {
    final base = speaker.name.trim().toLowerCase();
    if (base.isEmpty) continue;
    for (final lamp in lamps) {
      final name = lamp.name.trim().toLowerCase();
      if (name != base && name.startsWith(base)) {
        return (speaker: speaker, lamp: lamp);
      }
    }
  }
  return null;
}

/// 블루투스 주소를 저장 형식(대문자, 콜론)으로 맞춘다. 형식이 아니면 null.
String? normalizeBluetoothAddress(String raw) {
  final text = raw.trim().toUpperCase();
  return RegExp(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$').hasMatch(text) ? text : null;
}

/// 연결 대상 하나의 진행 상태. 연결 탭의 스피커·램프 줄이 된다.
enum _Phase { idle, connecting, connected, failed }

class BluetoothLinkManager extends ChangeNotifier {
  BluetoothLinkManager({
    required this.bluetooth,
    required this.feedback,
    required this.settings,
  });

  final AtlasBluetoothService bluetooth;
  final FeedbackCoordinator feedback;
  final SettingsStore settings;

  SavedBluetoothDevice? _speaker;
  SavedBluetoothDevice? _lamp;
  _Phase _speakerPhase = _Phase.idle;
  _Phase _lampPhase = _Phase.idle;
  DateTime? _speakerOkAt;
  String? _speakerError;
  String? _lampError;
  bool _disposed = false;

  SavedBluetoothDevice? get savedSpeaker => _speaker;
  SavedBluetoothDevice? get savedLamp => _lamp;
  bool get speakerBusy => _speakerPhase == _Phase.connecting;
  bool get lampBusy => _lampPhase == _Phase.connecting;

  /// 앱 시작 때 부른다. 저장된 기기가 있으면 검색 없이 붙는다. 던지지 않는다.
  Future<void> restore() async {
    final saved = settings.load();
    _speaker = saved.speaker;
    _lamp = saved.lamp;
    _notify();
    await Future.wait([
      if (_speaker != null) reconnectSpeaker(),
      if (_lamp != null) reconnectLamp(),
    ]);
  }

  /// 저장된 스피커에 다시 붙는다(검색 없이).
  Future<bool> reconnectSpeaker() async {
    final speaker = _speaker;
    if (speaker == null || speakerBusy) return false;
    _speakerPhase = _Phase.connecting;
    _speakerError = null;
    _notify();
    try {
      await bluetooth.connectSpeakerAddress(speaker.address);
      feedback.selectSpeaker(speaker.address);
      _speakerPhase = _Phase.connected;
      _speakerOkAt = DateTime.now();
      return true;
    } on Object catch (error) {
      _speakerPhase = _Phase.failed;
      _speakerError = '$error';
      return false;
    } finally {
      _notify();
    }
  }

  /// 저장된 램프에 GATT 연결을 미리 맺는다(첫 phase 전환 때 늦지 않게).
  Future<bool> reconnectLamp() async {
    final lamp = _lamp;
    if (lamp == null || lampBusy) return false;
    feedback.lamp.select(lamp.address);
    _lampPhase = _Phase.connecting;
    _lampError = null;
    _notify();
    final ok = await feedback.lamp.connect();
    _lampPhase = ok ? _Phase.connected : _Phase.failed;
    _lampError = ok ? null : '${feedback.lamp.lastError ?? '램프에 연결하지 못했습니다'}';
    _notify();
    return ok;
  }

  /// 검색 결과의 스피커에 붙고, 성공하면 저장한다.
  Future<void> connectSpeaker(BluetoothDeviceInfo device) async {
    _speakerPhase = _Phase.connecting;
    _speakerError = null;
    _notify();
    try {
      await bluetooth.connectSpeaker(device);
      feedback.selectSpeaker(device.address);
      _speaker = _saved(device);
      _speakerPhase = _Phase.connected;
      _speakerOkAt = DateTime.now();
      _persist();
    } on Object catch (error) {
      _speakerPhase = _Phase.failed;
      _speakerError = '$error';
      rethrow;
    } finally {
      _notify();
    }
  }

  /// 검색 결과의 BLE 램프를 고르고 저장한 뒤 연결을 미리 맺는다.
  Future<bool> selectLamp(BluetoothDeviceInfo device) async {
    _lamp = _saved(device);
    _persist();
    return reconnectLamp();
  }

  /// 일체형의 스피커와 램프를 한 번에. 스피커가 실패하면 램프는 시도하지 않는다 —
  /// 한쪽만 붙은 어중간한 상태보다 원인을 보고 다시 누르는 편이 낫다.
  Future<void> connectPair(
      BluetoothDeviceInfo speaker, BluetoothDeviceInfo lamp) async {
    await connectSpeaker(speaker);
    await selectLamp(lamp);
  }

  /// 저장된 기기를 잊는다. 다음 시작부터 자동 연결하지 않는다.
  void forget(LinkId id) {
    if (id == LinkId.speaker) {
      _speaker = null;
      _speakerPhase = _Phase.idle;
      _speakerError = null;
    } else if (id == LinkId.lamp) {
      _lamp = null;
      _lampPhase = _Phase.idle;
      _lampError = null;
    }
    _persist();
    _notify();
  }

  LinkStatus get speakerStatus {
    final speaker = _speaker;
    if (speaker == null) {
      return const LinkStatus(
          id: LinkId.speaker,
          health: LinkHealth.unconfigured,
          cause: LinkCause.notConfigured);
    }
    return switch (_speakerPhase) {
      _Phase.connected => LinkStatus(
          id: LinkId.speaker,
          health: LinkHealth.ok,
          lastSeen: _speakerOkAt,
          detail: speaker.name),
      _Phase.failed => LinkStatus(
          id: LinkId.speaker,
          health: LinkHealth.down,
          cause: LinkCause.disconnected,
          lastSeen: _speakerOkAt,
          detail: _speakerError),
      _ => LinkStatus(
          id: LinkId.speaker,
          health: LinkHealth.checking,
          detail: speaker.name),
    };
  }

  /// 램프는 phase 가 바뀔 때마다 쓰기를 하므로, 연결 뒤에는 그 결과(lastError·
  /// lastOkAt)를 본다. 한 번 끊겨도 IlinkLampFeedback 이 다음 전환 때 다시 붙는다.
  LinkStatus get lampStatus {
    final lamp = _lamp;
    if (lamp == null) {
      return const LinkStatus(
          id: LinkId.lamp,
          health: LinkHealth.unconfigured,
          cause: LinkCause.notConfigured);
    }
    if (_lampPhase == _Phase.connecting) {
      return LinkStatus(
          id: LinkId.lamp, health: LinkHealth.checking, detail: lamp.name);
    }
    final error = feedback.lamp.lastError;
    if (error != null || _lampPhase == _Phase.failed) {
      return LinkStatus(
          id: LinkId.lamp,
          health: LinkHealth.down,
          cause: LinkCause.disconnected,
          lastSeen: feedback.lamp.lastOkAt,
          detail: '${error ?? _lampError}');
    }
    if (feedback.lamp.connected || feedback.lamp.lastOkAt != null) {
      return LinkStatus(
          id: LinkId.lamp,
          health: LinkHealth.ok,
          lastSeen: feedback.lamp.lastOkAt,
          detail: lamp.name);
    }
    return LinkStatus(
        id: LinkId.lamp, health: LinkHealth.checking, detail: lamp.name);
  }

  SavedBluetoothDevice _saved(BluetoothDeviceInfo device) =>
      SavedBluetoothDevice(
          address: normalizeBluetoothAddress(device.address) ?? device.address,
          name: device.name);

  /// 저장 직전에 파일을 다시 읽어 합친다. 다른 곳(MQTT 주소 변경)이 그사이 저장한
  /// 값을 덮어쓰지 않게 하기 위해서다.
  void _persist() {
    var next = settings.load();
    next = _speaker == null
        ? next.copyWith(clearSpeaker: true)
        : next.copyWith(speaker: _speaker);
    next = _lamp == null
        ? next.copyWith(clearLamp: true)
        : next.copyWith(lamp: _lamp);
    settings.save(next);
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
