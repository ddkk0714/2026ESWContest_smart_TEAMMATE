import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'display_state.dart';
import 'dashboard_view.dart';
import 'deskmate_theme.dart';
import 'fsm_graph.dart';
import 'keystroke_capture.dart';
import 'music_playback.dart';
import 'sensor_test_page.dart';
import 'state_source.dart';

const _hubUrl = String.fromEnvironment('DESKMATE_HUB_URL');
const _mqttHost = String.fromEnvironment('DESKMATE_MQTT_HOST');
const _mqttPort = int.fromEnvironment('DESKMATE_MQTT_PORT', defaultValue: 1883);

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
  late final MusicPlayback _music;
  bool _musicOn = false;
  bool _musicBusy = false;
  bool _showFocusDetail = false;

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
    HardwareKeyboard.instance.addHandler(_onKey);
    _source = _mqttHost.trim().isNotEmpty
        ? MqttStateSource(_mqttHost.trim(), port: _mqttPort)
        : _hubUrl.trim().isEmpty
            ? DemoStateSource()
            : HttpStateSource(_hubUrl);
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_source is! DemoStateSource || _demoCyclingEnabled) _refresh();
      _sampleKeystroke();
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
    if (shouldExit) exit(0);
  }

  @override
  void dispose() {
    _timer?.cancel();
    HardwareKeyboard.instance.removeHandler(_onKey);
    _clock.stop();
    _source.close();
    unawaited(_music.dispose());
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
              ? _Loading(error: _error)
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _Header(
                        source: _source.label,
                        online: _error == null,
                        sequence: state.sequence,
                        view: _view,
                        onViewChanged: (view) => setState(() => _view = view),
                        musicOn: _musicOn,
                        musicBusy: _musicBusy,
                        onMusic: _toggleMusic,
                        onExit: _confirmExit),
                    const SizedBox(height: 12),
                    Expanded(
                        child: switch (_view) {
                      _AppView.dashboard => DashboardView(
                          state: state,
                          displayMessage: _source.displayMessage,
                          keystroke: _localKeystroke ?? state.keystroke,
                          keystrokeReference: _localKeystroke != null
                              ? DateTime.now()
                              : state.timestamp,
                          liveKeys: _localKeystroke != null ? _liveKeys : null,
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
                          },
                        ),
                      _AppView.fsmGraph =>
                        FsmGraphPage(currentState: state.fsmState),
                    }),
                  ],
                ),
        ),
      ),
    );
  }
}

enum _AppView { dashboard, sensorTest, fsmGraph }

class _Header extends StatelessWidget {
  const _Header(
      {required this.source,
      required this.online,
      required this.sequence,
      required this.view,
      required this.onViewChanged,
      required this.musicOn,
      required this.musicBusy,
      required this.onMusic,
      required this.onExit});
  final String source;
  final bool online;
  final int sequence;
  final _AppView view;
  final ValueChanged<_AppView> onViewChanged;
  final bool musicOn;
  final bool musicBusy;
  final VoidCallback onMusic;
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
        _HeaderNav(
            selected: view == _AppView.dashboard,
            icon: Icons.dashboard_outlined,
            label: '상태',
            onTap: () => onViewChanged(_AppView.dashboard)),
        _HeaderNav(
            selected: view == _AppView.sensorTest,
            icon: Icons.tune,
            label: '센서 테스트',
            onTap: () => onViewChanged(_AppView.sensorTest)),
        _HeaderNav(
            selected: view == _AppView.fsmGraph,
            icon: Icons.account_tree_outlined,
            label: 'FSM 전체',
            onTap: () => onViewChanged(_AppView.fsmGraph)),
        const SizedBox(width: 10),
        Container(
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
              child: Text('#$sequence',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
            ),
          ]),
        ),
        const SizedBox(width: 8),
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
          icon: musicBusy
              ? const SizedBox(
                  width: 17,
                  height: 17,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : Icon(
                  musicOn ? Icons.volume_up_rounded : Icons.volume_off_rounded,
                  size: 20),
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
  const _Loading({this.error});
  final String? error;
  @override
  Widget build(BuildContext context) => Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        const CircularProgressIndicator(),
        const SizedBox(height: 18),
        Text(error ?? 'FSM 상태를 기다리고 있습니다.')
      ]));
}

/// 화면 강조용 경계값. FSM 판정 임계값이 아니라 색만 바꾸는 힌트다.
/// 판정 임계값은 hub/deskmate_hub/config/*.yaml 에만 둔다.
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
