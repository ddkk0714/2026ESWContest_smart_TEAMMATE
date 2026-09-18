import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_motion.dart';
import 'atlas_home.dart';
import 'bluetooth_control_page.dart';
import 'bluetooth_service.dart';
import 'display_state.dart';
import 'feedback_policy.dart';
import 'dashboard_view.dart';
import 'deskmate_theme.dart';
import 'fsm_graph.dart';
import 'keystroke_capture.dart';
import 'music_playback.dart';
import 'sensor_test_page.dart';
import 'session_report.dart';
import 'state_source.dart';

const _hubUrl = String.fromEnvironment('DESKMATE_HUB_URL');

void main() => runApp(const DeskmateApp());

class DeskmateApp extends StatelessWidget {
  const DeskmateApp({super.key, this.music});

  final MusicPlayback? music;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DESKMATE',
      debugShowCheckedModeBanner: false,
      theme: buildDeskmateTheme(),
      home: DashboardPage(music: music),
    );
  }
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key, this.music});

  final MusicPlayback? music;

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  late StateSource _source;
  Timer? _timer;
  DisplayState? _state;
  String? _error;
  bool _busy = false;
  bool _demoCyclingEnabled = true;
  _AppView _view = _AppView.dashboard;
  final _viewDirection = ValueNotifier<double>(1);
  late final MusicPlayback _music;
  late final StreamSubscription<bool> _musicChanges;
  late final StreamSubscription<double> _musicVolumeChanges;
  bool _musicOn = false;
  bool _musicBusy = false;
  double _musicVolume = 1;
  bool _showFocusDetail = false;
  late final AtlasBluetoothService _bluetooth;
  late final FeedbackCoordinator _feedbackController;

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
    }, onError: (Object error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('음악 재생 실패: $error')),
        );
      }
    });
    _musicVolumeChanges = _music.volumeChanges.listen((volume) {
      if (mounted) setState(() => _musicVolume = volume);
    }, onError: (Object error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('음량 동기화 실패: $error')),
        );
      }
    });
    _bluetooth = AtlasBluetoothService();
    _feedbackController = FeedbackCoordinator(
      music: _music,
      lamp: IlinkLampFeedback(_bluetooth),
      bluetooth: _bluetooth,
    );
    HardwareKeyboard.instance.addHandler(_onKey);
    _source =
        _hubUrl.trim().isEmpty ? DemoStateSource() : HttpStateSource(_hubUrl);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_source is! DemoStateSource || _demoCyclingEnabled) _refresh();
      _sampleKeystroke();
      unawaited(_feedbackController.reconcileAudioOutput());
    });
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
      if (mounted) {
        setState(() {
          _state = next;
          _error = null;
        });
        unawaited(_feedbackController.apply(next));
      }
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
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
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('곡 변경 실패: $error')),
        );
      }
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
    try {
      await _music.setVolume(value);
      if (mounted) setState(() => _musicVolume = _music.volume);
    } catch (error) {
      if (mounted) {
        setState(() => _musicVolume = _music.volume);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('음량 변경 실패: $error')),
        );
      }
    }
  }

  Future<void> _showVolumeControl() async {
    await showDialog<void>(
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
  }

  void _toggleDemoCycling() {
    if (_source is! DemoStateSource) return;
    setState(() => _demoCyclingEnabled = !_demoCyclingEnabled);
    if (_demoCyclingEnabled) _refresh();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 1),
        content: Text(
          _demoCyclingEnabled ? '자동 순환을 시작했습니다.' : '자동 순환을 멈췄습니다.',
        ),
      ),
    );
  }

  void _changeView(_AppView next) {
    if (next == _view) return;
    _viewDirection.value = next.index > _view.index ? 1 : -1;
    setState(() => _view = next);
  }

  Widget _buildViewTransition(
    Widget child,
    Animation<double> animation,
  ) =>
      DirectionalSharedAxisTransition(
        animation: animation,
        direction: _viewDirection,
        child: child,
      );

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
    HardwareKeyboard.instance.removeHandler(_onKey);
    _clock.stop();
    _source.close();
    _viewDirection.dispose();
    unawaited(_musicChanges.cancel());
    unawaited(_musicVolumeChanges.cancel());
    unawaited(_music.dispose());
    unawaited(_bluetooth.close());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(44, 24, 44, 28),
          child: state == null
              ? _Loading(
                  error: _error,
                  source: _source.label,
                  connectionLabel: _source.connectionLabel,
                  connected: _source.isConnected,
                  mqttMode: _source is MqttStateSource,
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _Header(
                        source: _source.label,
                        connectionLabel: _source.connectionLabel,
                        online: _source.isConnected,
                        sequence: state.sequence,
                        view: _view,
                        onViewChanged: _changeView,
                        musicOn: _musicOn,
                        musicBusy: _musicBusy,
                        onMusic: _toggleMusic,
                        selectedTrack: _music.selectedTrack,
                        onSelectMusic: _selectMusic,
                        musicVolume: _musicVolume,
                        onVolume: _showVolumeControl,
                        onExit: _confirmExit),
                    const SizedBox(height: 12),
                    if (_source is MqttStateSource) ...[
                      _Pi4MqttLinkCard(
                        connected: _source.isConnected,
                        broker: _source.label,
                      ),
                      const SizedBox(height: 12),
                    ],
                    Expanded(
                      child: AnimatedSwitcher(
                        duration: AppMotion.page,
                        transitionBuilder: _buildViewTransition,
                        layoutBuilder: (currentChild, previousChildren) =>
                            Stack(
                          fit: StackFit.expand,
                          children: [
                            ...previousChildren,
                            if (currentChild != null) currentChild,
                          ],
                        ),
                        child: KeyedSubtree(
                          key: ValueKey(_view),
                          child: switch (_view) {
                            _AppView.dashboard => DashboardView(
                                state: state,
                                displayMessage: _source.displayMessage,
                                hasPendingRequest: _source.hasPendingRequest,
                                keystroke: _localKeystroke ?? state.keystroke,
                                keystrokeReference: _localKeystroke != null
                                    ? DateTime.now()
                                    : state.timestamp,
                                liveKeys:
                                    _localKeystroke != null ? _liveKeys : null,
                                onFeedback: _feedback,
                                showDemoControl: _source is DemoStateSource,
                                demoCyclingEnabled: _demoCyclingEnabled,
                                onToggleDemoCycling: _toggleDemoCycling,
                                showFocusDetail: _showFocusDetail,
                                onShowFocusDetail: (value) =>
                                    setState(() => _showFocusDetail = value),
                              ),
                            _AppView.sensorTest => SensorTestPage(
                                source: _source,
                                state: state,
                                onConnect: _connectHub,
                                onStateChanged: (next) {
                                  if (mounted) setState(() => _state = next);
                                  unawaited(_feedbackController.apply(next));
                                },
                              ),
                            _AppView.fsmGraph =>
                              FsmGraphPage(currentState: state.fsmState),
                            _AppView.bluetooth => BluetoothControlPage(
                                bluetooth: _bluetooth,
                                feedback: _feedbackController,
                                music: _music,
                              ),
                            _AppView.sessionReport =>
                              SessionReportCard(report: _source.sessionReport),
                          },
                        ),
                      ),
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}

enum _AppView { dashboard, sensorTest, fsmGraph, bluetooth, sessionReport }

class _MusicVolumeDialog extends StatefulWidget {
  const _MusicVolumeDialog({
    required this.initialValue,
    required this.changes,
    required this.onChanged,
  });

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
  Widget build(BuildContext context) => Dialog(
        child: SizedBox(
          width: 300,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Row(children: [
                const Text('음량 조절',
                    style: TextStyle(fontWeight: FontWeight.w700)),
                const Spacer(),
                IconButton(
                  tooltip: '닫기',
                  onPressed: () => Navigator.pop(context),
                  padding: EdgeInsets.zero,
                  constraints:
                      const BoxConstraints.tightFor(width: 32, height: 32),
                  icon: const Icon(Icons.close_rounded, size: 19),
                ),
              ]),
              const SizedBox(height: 2),
              Row(children: [
                Icon(
                  _value == 0
                      ? Icons.volume_off_rounded
                      : _value < .5
                          ? Icons.volume_down_rounded
                          : Icons.volume_up_rounded,
                  size: 20,
                ),
                const SizedBox(width: 4),
                Expanded(
                  child: Slider(
                    key: const ValueKey('music-volume-slider'),
                    value: _value,
                    divisions: 20,
                    label: '${(_value * 100).round()}%',
                    onChanged: (value) {
                      setState(() => _value = value);
                      widget.onChanged(value);
                    },
                  ),
                ),
                SizedBox(
                  width: 40,
                  child: Text(
                    '${(_value * 100).round()}%',
                    textAlign: TextAlign.end,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
              ]),
            ]),
          ),
        ),
      );
}

class _Header extends StatelessWidget {
  const _Header(
      {required this.source,
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
  final String source;
  final String connectionLabel;
  final bool online;
  final int sequence;
  final _AppView view;
  final ValueChanged<_AppView> onViewChanged;
  final bool musicOn;
  final bool musicBusy;
  final VoidCallback onMusic;
  final int selectedTrack;
  final ValueChanged<int> onSelectMusic;
  final double musicVolume;
  final VoidCallback onVolume;
  final VoidCallback onExit;

  @override
  Widget build(BuildContext context) => Row(children: [
        Container(
          width: 7,
          height: 7,
          decoration: const BoxDecoration(
              color: DeskmateColors.ink, shape: BoxShape.circle),
        ),
        const SizedBox(width: 9),
        const Text('DESKMATE',
            style: TextStyle(
                fontSize: 13, fontWeight: FontWeight.w700, letterSpacing: -.2)),
        const Spacer(),
        _HeaderNavigation(view: view, onViewChanged: onViewChanged),
        const SizedBox(width: 10),
        AnimatedContainer(
          duration: AppMotion.fast,
          curve: AppMotion.standardCurve,
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
              color: (online ? DeskmateColors.accent : DeskmateColors.offline)
                  .withValues(alpha: .12),
              borderRadius: BorderRadius.circular(99)),
          child: Row(children: [
            Icon(Icons.circle,
                size: 10,
                color: online
                    ? DeskmateColors.accentStrong
                    : DeskmateColors.offline),
            const SizedBox(width: 6),
            Tooltip(
              message: source,
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Text(connectionLabel,
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(width: 4),
                Text('#$sequence',
                    style: const TextStyle(fontWeight: FontWeight.w600)),
              ]),
            ),
          ]),
        ),
        const SizedBox(width: 8),
        PopupMenuButton<int>(
          key: const ValueKey('music-select'),
          tooltip: '재생할 곡 선택',
          enabled: !musicBusy,
          initialValue: selectedTrack,
          onSelected: onSelectMusic,
          itemBuilder: (context) => [
            for (var i = 0; i < classicalTrackTitles.length; i++)
              CheckedPopupMenuItem<int>(
                key: ValueKey('music-track-$i'),
                value: i,
                checked: i == selectedTrack,
                child: Text(classicalTrackTitles[i]),
              ),
          ],
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              SizedBox(
                width: 128,
                child: Text(classicalTrackTitles[selectedTrack],
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12)),
              ),
              const Icon(Icons.arrow_drop_down, size: 18),
            ]),
          ),
        ),
        IconButton(
          key: const ValueKey('music-volume'),
          tooltip: '음량 ${(musicVolume * 100).round()}%',
          onPressed: onVolume,
          color: DeskmateColors.inkMuted,
          visualDensity: VisualDensity.compact,
          icon: AnimatedSwitcher(
            duration: AppMotion.fast,
            switchInCurve: AppMotion.standardCurve,
            switchOutCurve: AppMotion.reverseCurve,
            transitionBuilder: AppMotion.fadeTransition,
            child: Icon(
              musicVolume == 0
                  ? Icons.volume_off_rounded
                  : musicVolume < .5
                      ? Icons.volume_down_rounded
                      : Icons.volume_up_rounded,
              key: ValueKey(musicVolume == 0
                  ? 'muted'
                  : musicVolume < .5
                      ? 'low'
                      : 'high'),
            ),
          ),
        ),
        TextButton.icon(
          key: const ValueKey('music-toggle'),
          onPressed: musicBusy ? null : onMusic,
          style: TextButton.styleFrom(
            foregroundColor:
                musicOn ? DeskmateColors.accentStrong : DeskmateColors.inkMuted,
            backgroundColor: musicOn
                ? DeskmateColors.accent.withValues(alpha: .22)
                : Colors.transparent,
          ),
          icon: AnimatedSwitcher(
            duration: AppMotion.fast,
            switchInCurve: AppMotion.standardCurve,
            switchOutCurve: AppMotion.reverseCurve,
            transitionBuilder: AppMotion.fadeTransition,
            child: musicBusy
                ? const SizedBox(
                    key: ValueKey('music-busy'),
                    width: 17,
                    height: 17,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : Icon(
                    musicOn
                        ? Icons.volume_up_rounded
                        : Icons.volume_off_rounded,
                    key: ValueKey(musicOn ? 'music-on' : 'music-off'),
                    size: 20),
          ),
          label: Text(musicOn ? 'ON' : 'OFF'),
        ),
        const SizedBox(width: 4),
        IconButton(
          key: const ValueKey('app-exit'),
          tooltip: '앱 종료',
          onPressed: onExit,
          color: DeskmateColors.inkMuted,
          icon: const Icon(Icons.power_settings_new),
        ),
      ]);
}

class _HeaderNavigation extends StatelessWidget {
  const _HeaderNavigation({required this.view, required this.onViewChanged});

  static const _itemWidth = 52.0;
  static const _buttonSize = 48.0;

  final _AppView view;
  final ValueChanged<_AppView> onViewChanged;

  @override
  Widget build(BuildContext context) => Stack(children: [
        AnimatedPositioned(
          duration: AppMotion.indicator,
          curve: AppMotion.standardCurve,
          left: view.index * _itemWidth + (_itemWidth - _buttonSize),
          top: 0,
          width: _buttonSize,
          height: _buttonSize,
          child: IgnorePointer(
            child: DecoratedBox(
              decoration: const BoxDecoration(
                color: DeskmateColors.surfaceRaised,
                shape: BoxShape.circle,
              ),
            ),
          ),
        ),
        Row(mainAxisSize: MainAxisSize.min, children: [
          _HeaderNav(
            selected: view == _AppView.dashboard,
            icon: Icons.dashboard_outlined,
            label: '상태',
            onTap: () => onViewChanged(_AppView.dashboard),
          ),
          _HeaderNav(
            selected: view == _AppView.sensorTest,
            icon: Icons.tune,
            label: '센서 테스트',
            onTap: () => onViewChanged(_AppView.sensorTest),
          ),
          _HeaderNav(
            selected: view == _AppView.fsmGraph,
            icon: Icons.account_tree_outlined,
            label: 'FSM 전체',
            onTap: () => onViewChanged(_AppView.fsmGraph),
          ),
          _HeaderNav(
            selected: view == _AppView.bluetooth,
            icon: Icons.bluetooth_audio_rounded,
            label: 'Bluetooth',
            onTap: () => onViewChanged(_AppView.bluetooth),
          ),
          _HeaderNav(
            selected: view == _AppView.sessionReport,
            icon: Icons.summarize_outlined,
            label: '세션 리포트',
            onTap: () => onViewChanged(_AppView.sessionReport),
          ),
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
  Widget build(BuildContext context) => SizedBox(
        width: _HeaderNavigation._itemWidth,
        child: Padding(
          padding: const EdgeInsets.only(left: 4),
          child: IconButton(
            tooltip: label,
            onPressed: onTap,
            icon: TweenAnimationBuilder<Color?>(
              duration: AppMotion.indicator,
              curve: AppMotion.standardCurve,
              tween: ColorTween(
                end: selected ? DeskmateColors.ink : DeskmateColors.inkMuted,
              ),
              builder: (context, color, child) =>
                  Icon(icon, size: 21, color: color),
            ),
          ),
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
    required this.mqttMode,
  });
  final String? error;
  final String source;
  final String connectionLabel;
  final bool connected;
  final bool mqttMode;
  @override
  Widget build(BuildContext context) => Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        if (mqttMode) ...[
          _Pi4MqttLinkCard(connected: connected, broker: source),
          const SizedBox(height: 22),
        ],
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
/// MQTT 소켓 연결 여부를 화면에서 즉시 확인하는 전용 상태 카드다.
/// 연결됨은 지정한 Pi4 broker까지 TCP/MQTT 세션이 수립됐다는 뜻이다.
class _Pi4MqttLinkCard extends StatelessWidget {
  const _Pi4MqttLinkCard({required this.connected, required this.broker});

  final bool connected;
  final String broker;

  @override
  Widget build(BuildContext context) {
    final color =
        connected ? DeskmateColors.accentStrong : DeskmateColors.offline;
    final title = connected ? 'Pi4 MQTT 연결됨' : 'Pi4 MQTT 연결 대기';
    final detail = connected
        ? '$broker · 상태/피드백 통신 준비됨'
        : '$broker · 랜 케이블 · Pi4 주소 · Mosquitto(1883)를 확인하세요';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .12),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: color.withValues(alpha: .5)),
      ),
      child: Row(children: [
        Icon(connected ? Icons.link : Icons.link_off, color: color, size: 28),
        const SizedBox(width: 12),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title,
                style: TextStyle(
                    color: color, fontSize: 16, fontWeight: FontWeight.w800)),
            const SizedBox(height: 3),
            Text(detail,
                style: const TextStyle(color: DeskmateColors.inkMuted)),
          ]),
        ),
      ]),
    );
  }
}

const _ksWarnCv = 0.55;
const _ksWarnIdle = 0.35;
const _ksWarnCorrection = 0.09;

/// collector 표본이 이보다 묵으면 값을 흐리고 '수신 끊김' 으로 표시한다.
/// collector 가 죽어도 hub 는 국면을 계속 내보내므로 이게 없으면
/// 마지막 값이 화면에 그대로 굳는다.
const _ksStaleAfter = Duration(seconds: 5);

/// 화면이 7인치라 세로가 귀하다. 카드 격자 대신 _SensorPanel 과 같은
/// 세로 목록으로 두고 가로 한 칸을 차지한다.
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
