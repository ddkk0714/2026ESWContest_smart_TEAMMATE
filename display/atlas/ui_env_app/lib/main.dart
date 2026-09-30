import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:camtest/diag.dart';
import 'package:camtest/hub_setup.dart' show showHubSetup;

import 'atlas_home.dart';
import 'bluetooth_control_page.dart';
import 'bluetooth_service.dart';
import 'connection_guide.dart';
import 'connection_page.dart';
import 'display_state.dart';
import 'feedback_policy.dart';
import 'dashboard_view.dart';
import 'deskmate_theme.dart';
import 'fsm_graph.dart';
import 'keystroke_capture.dart';
import 'link_status.dart';
import 'music_playback.dart';
import 'mqtt_watchdog.dart';
import 'posture_screen.dart';
import 'sensor_overview_page.dart';
import 'sensor_test_page.dart';
import 'session_report.dart';
import 'settings_store.dart';
import 'state_source.dart';

const _hubUrl = String.fromEnvironment('DESKMATE_HUB_URL');
const _mqttHost = String.fromEnvironment('DESKMATE_MQTT_HOST');
const _mqttPort = int.fromEnvironment('DESKMATE_MQTT_PORT', defaultValue: 1883);

const _autoScreenCycleInterval = Duration(seconds: 5);
const _autoScreenPhases = ['idle', 'focus', 'fatigue', 'recovery', 'end'];

/// 화면 강조용 경계값. FSM 판정 임계값이 아니라 색만 바꾸는 힌트다.
/// 판정 임계값은 hub/deskmate_hub/config/*.yaml 에만 둔다.
const _ksWarnCv = 0.55;
const _ksWarnIdle = 0.35;
const _ksWarnCorrection = 0.09;

/// collector 표본이 이보다 묵으면 값을 흐리고 '수신 끊김' 으로 표시한다.
/// collector 가 죽어도 hub 는 국면을 계속 내보내므로 이게 없으면
/// 마지막 값이 화면에 그대로 굳는다.
const _ksStaleAfter = Duration(seconds: 5);

void main() => runApp(const DeskmateApp());

class DeskmateApp extends StatelessWidget {
  const DeskmateApp({super.key, this.music, this.settings, this.prober});

  final MusicPlayback? music;
  final SettingsStore? settings;
  final MqttProber? prober;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DESKMATE',
      debugShowCheckedModeBanner: false,
      theme: buildDeskmateTheme(),
      home: DashboardPage(music: music, settings: settings, prober: prober),
    );
  }
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key, this.music, this.settings, this.prober});

  final MusicPlayback? music;

  /// 보드에 저장한 연결 설정. 테스트는 임시 경로의 저장소를 넣는다.
  final SettingsStore? settings;

  /// 시작 점검에 쓰는 진단기. 테스트는 가짜를 넣어 네트워크를 건드리지 않는다.
  final MqttProber? prober;

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  late StateSource _source;
  Timer? _timer;
  DisplayState? _state;
  String? _error;
  bool _busy = false;
  // 진단 로그를 상태가 바뀔 때만 남기기 위한 표시. 1초마다 같은 줄로 채우지 않는다.
  bool _loggedFetchOk = false;
  String? _lastLoggedError;
  // PR #21 은 기본 true 였지만 지금은 실센서가 붙어 있어 켜 두면 허브의 실제
  // 국면을 데모 국면이 덮어쓴다. 기본 OFF 로 두고 토글로만 켠다.
  bool _autoScreenCyclingEnabled = false;
  int _autoScreenPhaseIndex = 0;
  // 연결 배지는 붙은 뒤 5 초만 띄운다. '붙었다' 는 한 번 확인하면 되는
  // 정보인데 계속 떠 있으면 상단을 영구히 차지한다. 끊긴 상태는 계속 띄운다 -
  // 그건 사람이 손을 써야 하는 정보다.
  static const _linkBadgeVisible = Duration(seconds: 5);
  Timer? _linkBadgeTimer;
  // 배지는 '연결됐다는 플래그' 가 아니라 **상태가 실제로 오고 있는가** 로 본다.
  // mqtt_client 의 connectionStatus 는 재연결 중에 잠깐씩 흔들려서, 그걸 보고
  // 되돌리면 배지가 영영 안 사라진다.
  static const _linkStaleAfter = Duration(seconds: 15);
  DateTime? _lastStateAt;
  bool _showLinkLabel = true;
  // 테스트로 고정한 상태. 있으면 화면은 이것을 그리고, 라이브 갱신은 _state 에만
  // 쌓인다 - 풀면 곧바로 최신 라이브 상태로 돌아간다.
  DisplayState? _override;
  Timer? _screenCycleTimer;
  _AppView _view = _AppView.dashboard;
  late final MusicPlayback _music;
  late final StreamSubscription<bool> _musicChanges;
  late final StreamSubscription<double> _musicVolumeChanges;
  bool _musicOn = false;
  bool _musicBusy = false;
  double _musicVolume = 1;
  bool _showFocusDetail = false;
  late final AtlasBluetoothService _bluetooth;
  late final FeedbackCoordinator _feedbackController;
  late final SettingsStore _settingsStore;
  DeskmateSettings _settings = DeskmateSettings.empty;
  late final MqttStartupCheck _startupCheck;
  // 2단계: 연결 상태를 1초마다 스냅샷으로 모아 연결 탭·배지·상태 줄이 같이 읽는다.
  final _watchdog = MqttWatchdog();
  final _statusBar = StatusBarDismissal();
  LinkSnapshot _links = LinkSnapshot(const []);
  DateTime? _mqttConnectedAt;
  // 자세 탭이 알려 주는 연결 상태와, 전체 리로드 때 자세 탭에 보내는 신호.
  LinkStatus? _postureStatus;
  int _postureReloadToken = 0;

  // 보드에 꽂힌 키보드를 앱이 직접 잡는다. hub 가 주는 collector 지표보다 이걸 우선한다.
  final _capture = KeystrokeCapture();
  // 벽시계는 뒤로 갈 수 있어 이벤트 간격 계산에 쓰지 않는다.
  final _clock = Stopwatch();
  KeystrokeMetrics? _localKeystroke;
  int _liveKeys = 0;

  double get _now => _clock.elapsedMicroseconds / 1e6;

  @override
  void initState() {
    super.initState();
    _clock.start();
    _music = widget.music ?? AtlasMusicPlayback();
    _musicOn = _music.isPlaying;
    _musicVolume = _music.volume;
    _musicChanges = _music.playingChanges.listen((playing) {
      if (mounted) setState(() => _musicOn = playing);
    });
    _musicVolumeChanges = _music.volumeChanges.listen((volume) {
      if (mounted) setState(() => _musicVolume = volume);
    });
    _bluetooth = AtlasBluetoothService();
    _feedbackController = FeedbackCoordinator(
      music: _music,
      lamp: IlinkLampFeedback(_bluetooth),
      bluetooth: _bluetooth,
    );
    HardwareKeyboard.instance.addHandler(_onKey);
    _settingsStore = widget.settings ?? SettingsStore();
    _startupCheck = MqttStartupCheck(prober: widget.prober);
    _source = _initialSource();
    // 이 보드는 앱 표준출력이 journal 에도 app_log 에도 안 남는다. 허브에 왜 못
    // 붙었는지 알 방법이 화면밖에 없어서, 첫 판단과 그 결과를 파일로 남긴다.
    diag.write(
        'state: 저장="${_settings.mqttHost ?? ''}" 빌드 MQTT="$_mqttHost:$_mqttPort" '
        'HUB_URL="$_hubUrl" 설정 파일=${_settingsStore.lastPath ?? '없음'} → ${_source.label}');
    _startMqttCheck();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_source is! DemoStateSource || _autoScreenCyclingEnabled) _refresh();
      _sampleKeystroke();
      _updateLinkLabel();
      _updateLinks();
      unawaited(_feedbackController.reconcileAudioOutput());
    });
    _updateLinkLabel();
    _screenCycleTimer = Timer.periodic(
      _autoScreenCycleInterval,
      (_) => _advanceAutoScreen(),
    );
  }

  /// 키 이벤트는 소비하지 않는다(항상 false). 타건 수만 즉시 반영해 화면이 살아 보이게 한다.
  bool _onKey(KeyEvent event) {
    final counted = _capture.handle(event, _now);
    // A key press must stay cheap: rebuilding the whole dashboard for every
    // key can starve pointer, network, and media callbacks during fast typing.
    // The independent 1 Hz sampler below publishes the accumulated value.
    if (counted) _liveKeys = _capture.pressTotal;
    return false;
  }

  /// collector 규약과 같은 1Hz 로 창을 뽑는다.
  void _sampleKeystroke() {
    if (!_capture.hasData) return;
    final sample = _capture.extract(_now);
    if (mounted) setState(() => _localKeystroke = sample);
  }

  Future<void> _refresh() async {
    if (_busy) return;
    _busy = true;
    try {
      final next = await _source.fetch();
      _lastStateAt = DateTime.now();
      _watchdog.stateReceived();
      if (!_loggedFetchOk) {
        _loggedFetchOk = true;
        _lastLoggedError = null;
        diag.write(
            'state 수신 성공: ${_source.label} seq=${next.sequence} state=${next.fsmState}');
      }
      if (mounted) {
        setState(() {
          _state = next;
          _error = null;
        });
        unawaited(_feedbackController.apply(next));
      }
    } catch (error) {
      final text = error.toString();
      if (text != _lastLoggedError) {
        _lastLoggedError = text;
        _loggedFetchOk = false;
        diag.write('state 수신 실패: ${_source.label} $text');
      }
      if (mounted) setState(() => _error = text);
    } finally {
      _busy = false;
    }
  }

  Future<void> _feedback(String verdict) async {
    try {
      await _source.feedback(verdict);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              content:
                  Text(verdict == 'accept' ? '제안을 수락했습니다.' : '제안을 거절했습니다.')),
        );
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('전송 실패: $error')));
      }
    }
  }

  /// 저장된 주소 → 빌드에 넣은 주소 순으로 MQTT 를 고른다. 둘 다 없으면 예전처럼
  /// HTTP 개발 경로나 데모. 주소를 빌드에 박으면 Pi 4 IP 가 바뀔 때마다 재빌드해야
  /// 해서, 보드에서 바꾼 값을 먼저 본다.
  StateSource _initialSource() {
    _settings = _settingsStore.load();
    final port = effectiveMqttPort(_settings, _mqttPort);
    // IPv4 가 아닌 빌드 값(호스트 이름)도 예전처럼 그대로 쓴다.
    final host = effectiveMqttHost(_settings, _mqttHost) ??
        (_mqttHost.trim().isEmpty ? null : _mqttHost.trim());
    if (host != null) return MqttStateSource(host, port: port);
    return _hubUrl.trim().isEmpty
        ? DemoStateSource()
        : HttpStateSource(_hubUrl);
  }

  /// MQTT 로 붙을 때만 시작 점검을 돌린다. 결과는 대기 화면의 가이드가 그린다.
  void _startMqttCheck() {
    final source = _source;
    if (source is! MqttStateSource) {
      _startupCheck.reset();
      return;
    }
    unawaited(_startupCheck.run(source.host, source.port).then((result) {
      diag.write(result.ok
          ? 'MQTT 점검 통과: ${source.label} (${result.elapsed.inMilliseconds} ms, hub=${result.hubHealth ?? '?'})'
          : 'MQTT 점검 실패: ${source.label} ${result.failedAt?.name} ${result.cause.name} ${result.detail ?? ''}');
    }));
  }

  /// 연결 대상을 바꾸고 화면 상태를 처음부터 다시 받는다. 이전 대상에서 받은 값이
  /// 새 대상의 값인 것처럼 남아 있으면 안 된다.
  ///
  /// [keepState] 는 같은 대상에 다시 붙을 때(자동 복구·전체 리로드) 쓴다. 마지막
  /// 화면을 그대로 두고 새 상태가 오면 바꾼다 — 비우면 복구하는 몇 초 동안 화면이
  /// 연결 가이드로 튀었다가 돌아온다.
  void _replaceSource(StateSource next, {bool keepState = false}) {
    final previous = _source;
    setState(() {
      _source = next;
      if (!keepState) {
        _state = null;
        _lastStateAt = null;
      }
      _error = null;
      _mqttConnectedAt = null;
      _loggedFetchOk = false;
      _lastLoggedError = null;
      _showLinkLabel = true;
    });
    previous.close();
    diag.write('state: 연결 대상 변경 → ${next.label}');
    _startMqttCheck();
    _refresh();
  }

  /// 같은 주소로 새 클라이언트를 만든다. 자동 재연결이 멈춘 상태도 이걸로 풀린다.
  void _retryMqtt() {
    final source = _source;
    if (source is MqttStateSource) {
      _replaceSource(MqttStateSource(source.host, port: source.port));
    }
  }

  /// 보드에서 브로커 주소를 바꾼다. 저장에 실패해도 이번 실행에는 적용한다.
  Future<void> _changeMqttAddress() async {
    final source = _source;
    final current =
        source is MqttStateSource ? source.host : _settings.mqttHost;
    final result = await showHubSetup(context,
        currentIp: current,
        title: 'MQTT 브로커 주소',
        hint: 'Pi 4 의 IP · 포트는 ${effectiveMqttPort(_settings, _mqttPort)}');
    if (result == null || !mounted) return;
    if (result.isEmpty) {
      _continueWithDemo();
      return;
    }
    final next = _settings.copyWith(mqttHost: result);
    final saved = _settingsStore.save(next);
    _settings = next;
    final port = effectiveMqttPort(_settings, _mqttPort);
    _replaceSource(MqttStateSource(result, port: port));
    _notify(saved
        ? 'MQTT 브로커를 $result:$port 로 저장했습니다.'
        : '$result:$port 로 연결합니다 — 저장할 곳이 없어 이번 실행에만 적용됩니다.');
  }

  /// 이번 실행만 데모로 돌린다. 저장된 주소는 지우지 않는다 — 다음에 켤 때는
  /// 다시 실제 연결을 시도해야 한다.
  void _continueWithDemo() {
    _replaceSource(DemoStateSource());
    _notify('이번 실행은 데모로 진행합니다. 저장된 연결 설정은 그대로입니다.');
  }

  /// 같은 주소로 MQTT 클라이언트를 새로 만든다. 화면은 유지한다.
  void _reconnectMqtt({bool auto = false}) {
    final source = _source;
    if (source is! MqttStateSource) return;
    diag.write(auto
        ? 'MQTT 자동 재연결 #${_watchdog.attempts} (다음 간격 ${_watchdog.backoff.inSeconds} s)'
        : 'MQTT 수동 재연결');
    _replaceSource(MqttStateSource(source.host, port: source.port),
        keepState: true);
  }

  /// 앱을 재시작하지 않고 연결을 전부 다시 만든다: 상태 소스·시작 점검·자세 탐색.
  void _reloadAll() {
    final source = _source;
    setState(() => _postureReloadToken++);
    switch (source) {
      case MqttStateSource():
        _replaceSource(MqttStateSource(source.host, port: source.port),
            keepState: true);
      case HttpStateSource():
        _replaceSource(HttpStateSource(_hubUrl), keepState: true);
      default:
        _refresh();
    }
    _notify('연결을 모두 다시 불러옵니다.');
  }

  /// 연결 탭의 대상별 조치.
  void _linkAction(LinkId id) {
    switch (id) {
      case LinkId.mqtt || LinkId.hub || LinkId.mmwave || LinkId.environment:
        if (_source is MqttStateSource) {
          _reconnectMqtt();
          _notify('MQTT 에 다시 연결합니다.');
        } else {
          unawaited(_changeMqttAddress());
        }
      case LinkId.posture:
        setState(() => _postureReloadToken++);
        _notify('자세 카메라를 다시 찾습니다.');
      case LinkId.speaker || LinkId.lamp:
        setState(() => _view = _AppView.bluetooth);
      case LinkId.keystroke:
        break;
    }
  }

  /// 1초마다 연결 스냅샷을 새로 만들고, 상태가 끊겼으면 자동 복구를 건다.
  void _updateLinks() {
    final now = DateTime.now();
    final source = _source;
    if (source is MqttStateSource) {
      if (source.isConnected) {
        _mqttConnectedAt ??= now;
      } else {
        _mqttConnectedAt = null;
      }
      if (_watchdog.shouldReconnect(now, _lastStateAt)) {
        _reconnectMqtt(auto: true);
        return;
      }
    }
    final check = _startupCheck.result;
    final state = _state;
    final next = assembleLinkSnapshot(
      now: now,
      kind: switch (source) {
        MqttStateSource() => StateSourceKind.mqtt,
        HttpStateSource() => StateSourceKind.http,
        _ => StateSourceKind.demo,
      },
      mqttAddress: source is MqttStateSource ? source.label : null,
      mqttConnected: source.isConnected,
      mqttConnectedAt: _mqttConnectedAt,
      lastStateAt: _lastStateAt,
      startupFailure: check != null && !check.ok ? check.cause : null,
      parseError: source is MqttStateSource ? source.lastParseError : null,
      hubHealth: source is MqttStateSource ? source.hubHealth : null,
      hubHealthAt: source is MqttStateSource ? source.hubHealthAt : null,
      state: state,
      keystrokeAt: _localKeystroke != null ? now : state?.keystroke?.timestamp,
      posture: _postureStatus,
    );
    if (mounted) setState(() => _links = next);
  }

  void _notify(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _connectHub(String value) async {
    final text = value.trim();
    final uri = Uri.tryParse(text);
    if (uri == null ||
        !uri.hasAuthority ||
        !{'http', 'https'}.contains(uri.scheme)) {
      throw const FormatException('http://<Pi4-IP>:8765 형식으로 입력하세요.');
    }
    final nextSource = HttpStateSource(uri.toString());
    try {
      final nextState = await nextSource.fetch();
      _source.close();
      if (mounted) {
        setState(() {
          _source = nextSource;
          _state = nextState;
          _error = null;
        });
        unawaited(_feedbackController.apply(nextState));
      }
    } catch (_) {
      nextSource.close();
      rethrow;
    }
  }

  Future<void> _toggleMusic() async {
    if (_musicBusy) return;
    setState(() => _musicBusy = true);
    try {
      final playing = await _music.toggle();
      if (mounted) setState(() => _musicOn = playing);
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('음악 재생 실패: $error')),
        );
      }
    } finally {
      if (mounted) setState(() => _musicBusy = false);
    }
  }

  Future<void> _selectMusic(int index) async {
    if (_musicBusy) return;
    setState(() => _musicBusy = true);
    try {
      await _music.selectTrack(index);
    } finally {
      if (mounted) {
        setState(() {
          _musicOn = _music.isPlaying;
          _musicBusy = false;
        });
      }
    }
  }

  Future<void> _setMusicVolume(double value) async {
    await _music.setVolume(value);
    if (mounted) setState(() => _musicVolume = _music.volume);
  }

  Future<void> _showVolumeControl() => showDialog<void>(
        context: context,
        builder: (context) => _MusicVolumeDialog(
          initialValue: _musicVolume,
          changes: _music.volumeChanges,
          onChanged: (value) {
            if (mounted) setState(() => _musicVolume = value);
            unawaited(_setMusicVolume(value));
          },
        ),
      );

  void _toggleAutoScreenCycling() {
    setState(() {
      _autoScreenCyclingEnabled = !_autoScreenCyclingEnabled;
      if (_autoScreenCyclingEnabled) {
        _autoScreenPhaseIndex =
            (_autoScreenPhaseIndex + 1) % _autoScreenPhases.length;
      }
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 1),
        content: Text(
          _autoScreenCyclingEnabled ? '자동 화면 순환을 시작했습니다.' : '자동 화면 순환을 멈췄습니다.',
        ),
      ),
    );
  }

  /// 붙어 있으면 5 초 뒤 배지를 감춘다. 끊기면 다시 띄운다.
  ///
  /// 벽시계를 비교하지 않고 타이머로 한 번만 끈다. 시계 비교는 화면에서만
  /// 확인할 수 있어 테스트로 잡을 수가 없었다.
  void _updateLinkLabel() {
    final last = _lastStateAt;
    final stale =
        last == null || DateTime.now().difference(last) > _linkStaleAfter;
    if (stale || !_source.isConnected) {
      _linkBadgeTimer?.cancel();
      _linkBadgeTimer = null;
      if (!_showLinkLabel) setState(() => _showLinkLabel = true);
      return;
    }
    // 이미 예약했거나 이미 감췄으면 그대로 둔다.
    if (_linkBadgeTimer != null || !_showLinkLabel) return;
    _linkBadgeTimer = Timer(_linkBadgeVisible, () {
      if (mounted) setState(() => _showLinkLabel = false);
    });
  }

  void _advanceAutoScreen() {
    if (!mounted || !_autoScreenCyclingEnabled) return;
    setState(() {
      _autoScreenPhaseIndex =
          (_autoScreenPhaseIndex + 1) % _autoScreenPhases.length;
    });
  }

  Future<void> _confirmExit() async {
    final shouldExit = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('DESKMATE 종료'),
            content: const Text('앱을 종료할까요?'),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context, false),
                  child: const Text('취소')),
              FilledButton(
                  onPressed: () => Navigator.pop(context, true),
                  child: const Text('종료')),
            ],
          ),
        ) ??
        false;
    if (shouldExit) await returnToAtlasHome();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _screenCycleTimer?.cancel();
    _linkBadgeTimer?.cancel();
    HardwareKeyboard.instance.removeHandler(_onKey);
    _clock.stop();
    _source.close();
    _startupCheck.dispose();
    unawaited(_musicChanges.cancel());
    unawaited(_musicVolumeChanges.cancel());
    unawaited(_music.dispose());
    unawaited(_bluetooth.close());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = _override ?? _state;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(44, 24, 44, 28),
          child: state == null
              ? (_source is MqttStateSource
                  // MQTT 는 시작 점검 결과를 보여 준다. 실패해도 앱은 막지 않고,
                  // 상태가 오면 곧바로 평소 화면으로 넘어간다.
                  ? Center(
                      child: SingleChildScrollView(
                        child: ConnectionGuidePanel(
                          check: _startupCheck,
                          onRetry: _retryMqtt,
                          onChangeAddress: _changeMqttAddress,
                          onDemo: _continueWithDemo,
                        ),
                      ),
                    )
                  : _Loading(
                      error: _error,
                      source: _source.label,
                      connectionLabel: _source.connectionLabel,
                      connected: _source.isConnected,
                    ))
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _Header(
                        links: _links,
                        onProblems: () =>
                            setState(() => _view = _AppView.connection),
                        source: _source.label,
                        connectionLabel: _override != null
                            ? '테스트 상태 고정'
                            : (_showLinkLabel ? _source.connectionLabel : ''),
                        online: _source.isConnected,
                        sequence: state.sequence,
                        view: _view,
                        onViewChanged: (view) => setState(() => _view = view),
                        musicOn: _musicOn,
                        musicBusy: _musicBusy,
                        onMusic: _toggleMusic,
                        selectedTrack: _music.selectedTrack,
                        onSelectMusic: _selectMusic,
                        musicVolume: _musicVolume,
                        onVolume: _showVolumeControl,
                        onExit: _confirmExit),
                    // 누르면 사라지고, 새 문제가 생기면 다시 뜬다. 연결 탭에서는 겹쳐서 숨긴다.
                    if (_view != _AppView.connection &&
                        _statusBar.shouldShow(_links)) ...[
                      const SizedBox(height: 8),
                      LinkStatusBar(
                        snapshot: _links,
                        onDismiss: () =>
                            setState(() => _statusBar.dismiss(_links)),
                      ),
                    ],
                    const SizedBox(height: 12),
                    Expanded(
                        child: switch (_view) {
                      _AppView.dashboard => DashboardView(
                          state: state,
                          displayMessage: _source.displayMessage,
                          hasPendingRequest: _source.hasPendingRequest,
                          keystroke: _localKeystroke ?? state.keystroke,
                          keystrokeReference: _localKeystroke != null
                              ? DateTime.now()
                              : state.timestamp,
                          liveKeys: _localKeystroke != null ? _liveKeys : null,
                          onFeedback: _feedback,
                          showDemoControl: true,
                          demoCyclingEnabled: _autoScreenCyclingEnabled,
                          onToggleDemoCycling: _toggleAutoScreenCycling,
                          phaseOverride: _source is! DemoStateSource &&
                                  _autoScreenCyclingEnabled
                              ? _autoScreenPhases[_autoScreenPhaseIndex]
                              : null,
                          // 테스트로 상태를 고정한 동안에는 그 국면 화면을 봐야 한다.
                          pinAmbient:
                              !_autoScreenCyclingEnabled && _override == null,
                          showFocusDetail: _showFocusDetail,
                          onShowFocusDetail: (value) =>
                              setState(() => _showFocusDetail = value),
                        ),
                      _AppView.sensorOverview =>
                        SensorOverviewPage(state: state),
                      _AppView.sensorTest => SensorTestPage(
                          source: _source,
                          state: state,
                          onConnect: _connectHub,
                          onStateChanged: (next) {
                            if (mounted) setState(() => _state = next);
                            unawaited(_feedbackController.apply(next));
                          },
                          overridden: _override != null,
                          onOverride: (next) {
                            if (!mounted) return;
                            setState(() => _override = next);
                            if (next != null) {
                              unawaited(_feedbackController.apply(next));
                            }
                          },
                        ),
                      _AppView.posture => const PostureScreen(),
                      _AppView.fsmGraph =>
                        FsmGraphPage(currentState: state.fsmState),
                      _AppView.bluetooth => BluetoothControlPage(
                          bluetooth: _bluetooth,
                          feedback: _feedbackController,
                          music: _music,
                        ),
                      _AppView.sessionReport =>
                        SessionReportCard(report: _source.sessionReport),
                      _AppView.connection => ConnectionPage(
                          snapshot: _links,
                          now: DateTime.now(),
                          sourceLabel: _source.label,
                          onAction: _linkAction,
                          onReloadAll: _reloadAll,
                          onChangeAddress: _changeMqttAddress,
                        ),
                    }),
                  ],
                ),
        ),
      ),
    );
  }
}

class _MusicVolumeDialog extends StatefulWidget {
  const _MusicVolumeDialog(
      {required this.initialValue,
      required this.changes,
      required this.onChanged});
  final double initialValue;
  final Stream<double> changes;
  final ValueChanged<double> onChanged;
  @override
  State<_MusicVolumeDialog> createState() => _MusicVolumeDialogState();
}

class _MusicVolumeDialogState extends State<_MusicVolumeDialog> {
  late double _value;
  late final StreamSubscription<double> _subscription;
  @override
  void initState() {
    super.initState();
    _value = widget.initialValue;
    _subscription = widget.changes.listen((value) {
      if (mounted) setState(() => _value = value);
    });
  }

  @override
  void dispose() {
    unawaited(_subscription.cancel());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('\uC74C\uB7C9 \uC870\uC808'),
        content: Slider(
            key: const ValueKey('music-volume-slider'),
            value: _value,
            divisions: 20,
            label: (_value * 100).round().toString() + '%',
            onChanged: (value) {
              setState(() => _value = value);
              widget.onChanged(value);
            }),
        actions: [
          Text((_value * 100).round().toString() + '%'),
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('\uB2EB\uAE30'))
        ],
      );
}

enum _AppView {
  dashboard,
  sensorOverview,
  sensorTest,
  posture,
  fsmGraph,
  bluetooth,
  sessionReport,
  connection
}

class _Header extends StatelessWidget {
  const _Header(
      {required this.links,
      required this.onProblems,
      required this.source,
      required this.connectionLabel,
      required this.online,
      required this.sequence,
      required this.view,
      required this.onViewChanged,
      required this.musicOn,
      required this.musicBusy,
      required this.onMusic,
      required this.selectedTrack,
      required this.onSelectMusic,
      required this.musicVolume,
      required this.onVolume,
      required this.onExit});
  final LinkSnapshot links;
  final VoidCallback onProblems;
  final String source;
  final String connectionLabel;
  final bool online, musicOn, musicBusy;
  final int sequence, selectedTrack;
  final _AppView view;
  final ValueChanged<_AppView> onViewChanged;
  final ValueChanged<int> onSelectMusic;
  final VoidCallback onMusic, onVolume, onExit;
  final double musicVolume;
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          const Text('DESKMATE', style: TextStyle(fontWeight: FontWeight.w700)),
          const Spacer(),
          Tooltip(
              message: source,
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                if (connectionLabel.isNotEmpty) ...[
                  Text(connectionLabel),
                  const SizedBox(width: 4),
                ],
                Text('#' + sequence.toString())
              ])),
          PopupMenuButton<int>(
              key: const ValueKey('music-select'),
              tooltip: '\uC7AC\uC0DD\uD560 \uACE1 \uC120\uD0DD',
              enabled: !musicBusy,
              initialValue: selectedTrack,
              onSelected: onSelectMusic,
              itemBuilder: (context) => [
                    for (var i = 0; i < classicalTrackTitles.length; i++)
                      CheckedPopupMenuItem<int>(
                          key: ValueKey('music-track-' + i.toString()),
                          value: i,
                          checked: i == selectedTrack,
                          child: Text(classicalTrackTitles[i]))
                  ],
              child: Padding(
                  padding: const EdgeInsets.all(8),
                  child: SizedBox(
                      width: 112,
                      child: Text(classicalTrackTitles[selectedTrack],
                          maxLines: 1, overflow: TextOverflow.ellipsis)))),
          IconButton(
              key: const ValueKey('music-volume'),
              tooltip: '\uC74C\uB7C9',
              onPressed: onVolume,
              icon: Icon(musicVolume == 0
                  ? Icons.volume_off_rounded
                  : Icons.volume_up_rounded)),
          TextButton.icon(
              key: const ValueKey('music-toggle'),
              onPressed: musicBusy ? null : onMusic,
              icon: Icon(
                  musicOn ? Icons.volume_up_rounded : Icons.volume_off_rounded),
              label: Text(musicOn ? 'ON' : 'OFF')),
          // 문제가 있을 때만 보인다. 누르면 연결 탭으로 간다.
          LinkProblemBadge(snapshot: links, onTap: onProblems),
          IconButton(
              key: const ValueKey('app-exit'),
              tooltip: '\uC571 \uC885\uB8CC',
              onPressed: onExit,
              icon: const Icon(Icons.power_settings_new)),
        ]),
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          _HeaderNav(
              selected: view == _AppView.dashboard,
              icon: Icons.dashboard_outlined,
              label: '\uC0C1\uD0DC',
              onTap: () => onViewChanged(_AppView.dashboard)),
          _HeaderNav(
              selected: view == _AppView.sensorOverview,
              icon: Icons.sensors_rounded,
              label: '\uC13C\uC11C \uC804\uCCB4',
              onTap: () => onViewChanged(_AppView.sensorOverview)),
          _HeaderNav(
              selected: view == _AppView.sensorTest,
              icon: Icons.tune,
              label: '\uC13C\uC11C \uD14C\uC2A4\uD2B8',
              onTap: () => onViewChanged(_AppView.sensorTest)),
          _HeaderNav(
              selected: view == _AppView.posture,
              icon: Icons.chair_alt,
              label: '\uC790\uC138',
              onTap: () => onViewChanged(_AppView.posture)),
          _HeaderNav(
              selected: view == _AppView.fsmGraph,
              icon: Icons.account_tree_outlined,
              label: 'FSM \uC804\uCCB4',
              onTap: () => onViewChanged(_AppView.fsmGraph)),
          _HeaderNav(
              selected: view == _AppView.bluetooth,
              icon: Icons.bluetooth_audio_rounded,
              label: 'Bluetooth',
              onTap: () => onViewChanged(_AppView.bluetooth)),
          _HeaderNav(
              selected: view == _AppView.sessionReport,
              icon: Icons.summarize_outlined,
              label: '\uC138\uC158 \uB9AC\uD3EC\uD2B8',
              onTap: () => onViewChanged(_AppView.sessionReport)),
          _HeaderNav(
              selected: view == _AppView.connection,
              icon: Icons.lan_outlined,
              label: '연결',
              onTap: () => onViewChanged(_AppView.connection)),
        ]),
      ]);
}

class _HeaderNav extends StatelessWidget {
  const _HeaderNav({
    required this.selected,
    required this.icon,
    required this.label,
    required this.onTap,
  });
  final bool selected;
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(left: 4),
        child: IconButton(
          tooltip: label,
          onPressed: onTap,
          style: IconButton.styleFrom(
            foregroundColor:
                selected ? DeskmateColors.ink : DeskmateColors.inkMuted,
            backgroundColor:
                selected ? DeskmateColors.surfaceRaised : Colors.transparent,
          ),
          icon: Icon(icon, size: 21),
        ),
      );
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
            color: const Color(0xFF121D2E),
            borderRadius: BorderRadius.circular(22),
            border: Border.all(color: const Color(0xFF24344C))),
        child: child,
      );
}

class _Loading extends StatelessWidget {
  const _Loading({
    this.error,
    required this.source,
    required this.connectionLabel,
    required this.connected,
  });
  final String? error;
  final String source;
  final String connectionLabel;
  final bool connected;
  @override
  Widget build(BuildContext context) => Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        const CircularProgressIndicator(),
        const SizedBox(height: 18),
        Text(
          connectionLabel,
          style: TextStyle(
            fontWeight: FontWeight.w700,
            color: connected
                ? DeskmateColors.accentStrong
                : DeskmateColors.offline,
          ),
        ),
        const SizedBox(height: 6),
        Text(source, style: const TextStyle(color: DeskmateColors.inkMuted)),
        const SizedBox(height: 10),
        Text(error ?? 'FSM 상태를 기다리고 있습니다.')
      ]));
}

/// 화면 강조용 경계값. FSM 판정 임계값이 아니라 색만 바꾸는 힌트다.
/// 판정 임계값은 hub/deskmate_hub/config/*.yaml 에만 둔다.
class _KeystrokePanel extends StatelessWidget {
  const _KeystrokePanel({
    required this.metrics,
    required this.reference,
    this.liveKeys,
  });

  final KeystrokeMetrics? metrics;

  /// 신선도를 재는 기준 시각. hub 지표면 envelope 의 ts, 보드 캡처면 지금이다.
  final DateTime reference;

  /// 보드 키보드를 직접 잡는 중일 때의 누적 타건 수. hub 지표면 null.
  final int? liveKeys;

  @override
  Widget build(BuildContext context) {
    final ks = metrics;
    final age = ks?.ageFrom(reference);
    final stale = ks == null || age == null || age > _ksStaleAfter;
    final live = !stale && (ks.valid ?? true);

    return _Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // 3열 배치라 이 칸이 좁다. 800px 화면에서는 제목+칩이 폭을 넘겨서
        // 잘리는 대신 통째로 줄어들게 둔다.
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Row(children: [
            const Text('키스트로크',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
            const SizedBox(width: 10),
            _KsStatusChip(metrics: ks, live: live),
          ]),
        ),
        const Spacer(),
        _KsRow(
          label: '체류',
          value: ks?.dwellMeanMs == null ? '--' : '${_ms(ks!.dwellMeanMs)} ms',
          live: live,
        ),
        _KsRow(
          label: '비행',
          value:
              ks?.flightMeanMs == null ? '--' : '${_ms(ks!.flightMeanMs)} ms',
          live: live,
        ),
        _KsRow(
          label: '리듬 불규칙',
          value: ks?.flightCv == null ? '--' : ks!.flightCv!.toStringAsFixed(2),
          warn: (ks?.flightCv ?? 0) >= _ksWarnCv,
          live: live,
        ),
        _KsRow(
          label: '입력 공백',
          value: ks?.idleRatio == null ? '--' : '${_pct(ks!.idleRatio)}%',
          warn: (ks?.idleRatio ?? 0) >= _ksWarnIdle,
          live: live,
        ),
        _KsRow(
          label: '오타 교정',
          value: ks?.correctionRate == null
              ? '--'
              : '${_pct(ks!.correctionRate)}%',
          warn: (ks?.correctionRate ?? 0) >= _ksWarnCorrection,
          live: live,
        ),
        const Spacer(),
        Text(_ksMeta(ks, age, live, liveKeys),
            style: const TextStyle(fontSize: 11, color: Color(0xFF6B7C96))),
      ]),
    );
  }
}

class _KsStatusChip extends StatelessWidget {
  const _KsStatusChip({required this.metrics, required this.live});
  final KeystrokeMetrics? metrics;
  final bool live;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch ((live, metrics?.typingActive)) {
      (false, _) => ('수신 끊김', const Color(0xFFFF7B7B)),
      (true, true) => ('타이핑 중', const Color(0xFF52D6C7)),
      (true, false) => ('입력 없음', const Color(0xFF9DABC2)),
      // typing_active 는 규약 추가분이라 안 올 수 있다. 그때는 수신만 알린다.
      (true, null) => ('수신 중', const Color(0xFF69A9FF)),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(label,
          style: TextStyle(
              fontSize: 11, color: color, fontWeight: FontWeight.w700)),
    );
  }
}

class _KsRow extends StatelessWidget {
  const _KsRow({
    required this.label,
    required this.value,
    required this.live,
    this.warn = false,
  });

  final String label;
  final String value;
  final bool live;
  final bool warn;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(children: [
          Text(label, style: const TextStyle(color: Color(0xFF9DABC2))),
          const Spacer(),
          Text(value,
              style: TextStyle(
                fontWeight: FontWeight.w700,
                color: !live
                    ? const Color(0xFF6B7C96)
                    : warn
                        ? const Color(0xFFFFB45E)
                        : Colors.white,
              )),
        ]),
      );
}

String _ms(double? value) => value == null ? '--' : value.round().toString();
String _pct(double? value) =>
    value == null ? '--' : (value * 100).round().toString();

String _ksMeta(
    KeystrokeMetrics? metrics, Duration? age, bool live, int? liveKeys) {
  if (metrics == null) return 'collector 대기';
  return [
    metrics.node ?? 'collector',
    if (metrics.windowS != null) '${metrics.windowS}초 윈도',
    if (liveKeys != null) '$liveKeys타',
    if (live) '수신 중' else if (age != null) '${age.inSeconds}초 전',
  ].join(' · ');
}

/// 테스트에서 키스트로크 패널만 따로 띄우기 위한 통로.
/// 패널 자체는 비공개로 두고 노출은 이 한 줄로 제한한다.
class KeystrokePanelForTest extends StatelessWidget {
  const KeystrokePanelForTest({
    super.key,
    required this.metrics,
    required this.reference,
    this.liveKeys,
  });
  final KeystrokeMetrics? metrics;
  final DateTime reference;
  final int? liveKeys;
  @override
  Widget build(BuildContext context) => _KeystrokePanel(
      metrics: metrics, reference: reference, liveKeys: liveKeys);
}
