import 'dart:async';
import 'dart:convert';
import 'dart:io' show ProcessSignal, exit;

import 'package:flutter/foundation.dart';

import '../live/live_link.dart';
import '../live/payload.dart';
import '../live/transport.dart';
import 'pr74/bluetooth_link.dart';
import 'pr74/bluetooth_service.dart';
import 'control_progress.dart';
import 'pr74/command_log.dart';
import 'pr74/connection_page.dart';
import 'pr74/developer_page.dart';
import 'pr74/device_state_board.dart';
import 'pr74/display_state.dart';
import 'pr74/fan_control.dart';
import 'pr74/feedback_policy.dart';
import 'pr74/lamp_defaults.dart';
import 'pr74/link_status.dart' as links;
import 'pr74/music_playback.dart';
import 'pr74/music_state.dart';
import 'pr74/voice_alert.dart';
import 'pr74/preview_page.dart';
import 'pr74/settings_store.dart';

/// Owns the imported PR74 services for the entire app, including when dialogs close.
/// MQTT is shared with UI18; only this coordinator executes Bluetooth commands.
class DeveloperController extends ChangeNotifier {
  DeveloperController(
      {required this.live,
      SettingsStore? settings,
      AtlasBluetoothService? bluetooth,
      MusicPlayback? music,
      bool startHardware = true})
      : settings = settings ?? SettingsStore(),
        bluetooth = bluetooth ?? AtlasBluetoothService(),
        music = music ?? AtlasMusicPlayback() {
    feedback = FeedbackCoordinator(
        music: this.music,
        lamp: IlinkLampFeedback(this.bluetooth),
        bluetooth: this.bluetooth,
        fan: BleFanControl(this.bluetooth),
        alerts: _alerts,
        speech: _speech,
        notices: live.notices);
    feedback.brightnessFloor =
        this.settings.load().lampBrightnessFloor ?? kLampBrightnessFloorDefault;
    btLinks = BluetoothLinkManager(
        bluetooth: this.bluetooth, feedback: feedback, settings: this.settings);
    btLinks.addListener(_changed);
    live.addListener(_linkChanged);
    _messages = live.messages.listen(receive);
    // V32: 음악 상태 → hub `deskmate/display/audio`(retain). 곡만 바꾸면 스트림이 없어 1초 타이머가 챙긴다.
    musicReporter = MusicStateReporter(this.music, _publishMusic);
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      _changed();
      musicReporter.report();
      // V38: hub 명령 중 새로 맺은 GATT 링크의 clientId 도 남긴다(다음 실행이 시작할 때 정리)
      btLinks.rememberOpenLinks();
      if (startHardware) unawaited(feedback.reconcileAudioOutput());
    });
    if (startHardware) {
      unawaited(_restoreHardware());
      _closeLinksOnStop();
    }
  }

  StreamSubscription<ProcessSignal>? _stopSignal;

  /// V38: ATLAS 가 앱을 멈출 때(SIGTERM) 열린 GATT 링크를 끊고 나간다. 끊지 않고 나가면 링크가 bluetooth-service 에
  /// 남아 다음 실행의 Connect 가 "Already connected" 로 막혔다(11-02 Pi 5). 신호를 못 받는 곳에서는 조용히 넘어간다.
  void _closeLinksOnStop() {
    try {
      _stopSignal = ProcessSignal.sigterm.watch().listen((_) async {
        try {
          await bluetooth.close().timeout(const Duration(seconds: 2));
        } catch (_) {}
        exit(0);
      });
    } catch (_) {}
  }

  final _alerts = AtlasAlertSound();
  final _speech = AtlasSpeech();

  Future<void> _restoreHardware() async {
    await btLinks.restore();
    if (!_disposed) btLinks.keepLampConnected();
  }

  final LiveLink live;
  final SettingsStore settings;
  final AtlasBluetoothService bluetooth;
  final MusicPlayback music;
  late final FeedbackCoordinator feedback;
  late final BluetoothLinkManager btLinks;
  late final MusicStateReporter musicReporter;

  void _publishMusic(Map<String, Object?> state) {
    final id = feedback.lastMusicCommandId;
    live.publishAudio({...state, if (id != null) 'last_command_id': id});
  }
  final log = CommandLog();

  /// '제어 진행' 탭(V30) — hub 제어 로그 또는 MQTT 추정. 폴링은 탭이 떠 있을 때만.
  final progress = ControlProgress();
  final board = DeviceStateBoard();
  StreamSubscription<LiveMessage>? _messages;
  Timer? _timer;
  bool _disposed = false;
  String? _address;
  DisplayState? state;
  KeystrokeMetrics? keystroke;
  DateTime? stateAt, keystrokeAt, connectedAt, healthAt;
  String? parseError;
  Map<String, dynamic>? stateEnvelope, keystrokeEnvelope;
  DisplayState? overrideState;
  DevPreset? preset;
  bool pending = false;
  int previewRevision = 0;
  Set<String> envFlags = {};

  String? get host =>
      settings.load().previewHost ?? (live.configured ? live.host : null);
  bool get defaultOnStart => settings.load().devModeDefault;
  DisplayState get previewState => overrideState ?? state ?? emptyState();
  bool get freshState => stateAt != null && live.live;

  static DisplayState emptyState() => DisplayState(
      fsmState: 'IDLE',
      phase: 'idle',
      context: 'unknown',
      focus: 0,
      fatigue: 0,
      confidence: 0,
      gate: 'none',
      reasons: const [],
      sequence: 0,
      timestamp: DateTime.fromMillisecondsSinceEpoch(0));

  void _linkChanged() {
    final address = '${live.host}:${live.port}';
    if (_address != null && _address != address) {
      state = null;
      stateAt = null;
      keystroke = null;
      keystrokeAt = null;
      stateEnvelope = keystrokeEnvelope = null;
      connectedAt = healthAt = null;
      parseError = null;
      clearOverride();
      log.clear();
      board.reset();
    }
    _address = address;
    connectedAt = live.status == LinkStatus.connected
        ? connectedAt ?? DateTime.now()
        : null;
    _changed();
  }

  void receive(LiveMessage message) {
    if (_disposed || message.text.isEmpty) return;
    try {
      final envelope = jsonMap(jsonDecode(message.text));
      final data = jsonMap(envelope['data']);
      if (!message.retained) {
        log.addMessage(message.topic, envelope);
        progress.addMessage(message.topic, envelope);
        board.addMessage(message.topic, envelope);
      }
      switch (message.topic) {
        case LiveLink.stateTopic:
          state = DisplayState.fromEnvelope(envelope);
          stateEnvelope = envelope;
          stateAt = DateTime.now();
          parseError = null;
          unawaited(feedback.apply(state!));
        case LiveLink.keystrokeTopic:
          keystroke = KeystrokeMetrics.fromEnvelope(envelope);
          keystrokeEnvelope = envelope;
          keystrokeAt = DateTime.now();
        case LiveLink.healthTopic:
          healthAt = DateTime.now();
        case LiveLink.commandTopic:
          if (!message.retained) unawaited(_execute(data));
      }
      _changed();
    } on Object catch (error) {
      parseError = error.runtimeType.toString();
      _changed();
    }
  }

  Future<void> _execute(Map<String, dynamic> data) async {
    final address = '${live.host}:${live.port}';
    final result = await feedback.handleControlCommand(data);
    if (!_disposed &&
        address == '${live.host}:${live.port}' &&
        result != null) {
      live.publishData(LiveLink.resultTopic, result);
      if (data['target_id'] == 'music') musicReporter.report();
    }
  }

  void applyPreset(DevPreset value) {
    previewRevision++;
    preset = value;
    pending = value.pending;
    overrideState = buildOverrideState(value.fsmState, state ?? emptyState(),
        gate: value.gate, envFlags: envFlags);
    // A screen overrideState must never actuate devices or change the real profile.
    _changed();
  }

  void setFlags(Set<String> flags) {
    envFlags = flags;
    if (preset case final value?) applyPreset(value);
    _changed();
  }

  void clearOverride() {
    preset = null;
    overrideState = null;
    pending = false;
    _changed();
  }

  void answerPreview() {
    pending = false;
    _changed();
  }

  void setHost(String value) {
    settings.save(settings.load().copyWith(previewHost: value));
    _changed();
  }

  void setDefault(bool value) {
    settings.save(settings.load().copyWith(devModeDefault: value));
    _changed();
  }

  void setPlacement(String value) {
    if (settings.load().placement == value) return;
    settings.save(settings.load().copyWith(placement: value));
  }

  Future<void> reloadAll() async {
    reconnect();
    await btLinks.restore();
  }

  Future<void> sendFeedback(PreviewFeedback value) async {
    if (overrideState != null) return;
    final sent = live.publishData(LiveLink.feedbackTopic, {
      'request_id': live.request?.requestId ?? 'atlas-display',
      'verdict': value.verdict,
      'response_ms': 0,
      if (value.correctedState != null) 'corrected_state': value.correctedState,
      if (value.note != null && value.note!.trim().isNotEmpty)
        'note': value.note!
            .trim()
            .substring(0, value.note!.trim().length.clamp(0, 200)),
    });
    if (!sent) throw StateError('책상 연결을 먼저 확인해 주세요.');
  }

  void manualAdjust(String target, int? from, int to) {
    live.publishData(LiveLink.feedbackTopic, {
      'kind': 'manual_adjust',
      'target_id': target,
      if (from != null) 'from': from,
      'to': to,
      'ts': DateTime.now().millisecondsSinceEpoch,
    });
  }

  links.LinkSnapshot get snapshot => assembleLinkSnapshot(
      now: DateTime.now(),
      kind: StateSourceKind.mqtt,
      mqttAddress: live.configured ? '${live.host}:${live.port}' : null,
      mqttConnected: live.status == LinkStatus.connected,
      mqttConnectedAt: connectedAt,
      lastStateAt: stateAt,
      hubHealth: live.hubHealth,
      hubHealthAt: healthAt,
      parseError: parseError,
      state: state,
      keystrokeAt: keystroke?.timestamp,
      posture: links.LinkStatus(
          id: links.LinkId.posture,
          health: freshState && state?.tofPosture != null
              ? links.LinkHealth.ok
              : links.LinkHealth.checking,
          lastSeen: stateAt,
          detail: state?.tofPostureSource),
      speaker: btLinks.speakerStatus,
      lamp: btLinks.lampStatus,
      fan: btLinks.fanStatus);

  void reconnect() => live.configure(live.host, live.port);
  void _changed() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    _stopSignal?.cancel();
    _messages?.cancel();
    live.removeListener(_linkChanged);
    btLinks.removeListener(_changed);
    btLinks.dispose();
    musicReporter.dispose();
    log.dispose();
    progress.dispose();
    board.dispose();
    unawaited(_alerts.dispose());
    unawaited(_speech.dispose());
    unawaited(music.dispose());
    unawaited(bluetooth.close());
    super.dispose();
  }
}
