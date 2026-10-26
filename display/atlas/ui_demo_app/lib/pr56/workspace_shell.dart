import 'dart:async';
import '../page_transition.dart';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'clock_tools.dart';
import 'focus_report.dart';
import 'focus_report_dialog.dart';
import 'device_widgets.dart';
import 'music_device_page.dart';
import 'network_status_indicator.dart';

import 'display_state.dart';
import 'calendar_panel.dart';
import 'popup_tap_region.dart';
import 'feedback_policy.dart';
import 'home_widgets.dart';
import 'home_layout.dart';
import 'home_resize_handle.dart';
import 'home_weather_tile.dart';
import 'home_widget_picker.dart';
import 'weather_snapshot.dart';
import 'co2_grade.dart';
import 'irumae.dart';
import 'music_playback.dart';
import 'workspace_store.dart';

/// The daily interface. Existing diagnostics remain behind the developer entry.
class DeskmateWorkspace extends StatefulWidget {
  const DeskmateWorkspace(
      {super.key,
      required this.state,
      required this.demo,
      required this.music,
      required this.feedback,
      required this.developerBuilder,
      required this.onConnections,
      required this.onAccept,
      required this.onReject,
      required this.onUndo,
      this.pendingRequest = false,
      this.showFocusGoal = false,
      this.musicAvailable = true,
      this.onReturnToClock,
      this.onExit,
      this.onPreferences,
      this.store,
      this.now,
      this.deviceStatus = const {}});
  final DisplayState? state;

  /// V18: 보드가 알려 준 기기 상태(target_id → '밝기 176' 같은 한 줄). 표시 전용.
  final Map<String, String> deviceStatus;
  final bool demo;
  final bool showFocusGoal;
  final bool musicAvailable;
  final MusicPlayback music;
  final FeedbackCoordinator feedback;
  final WidgetBuilder developerBuilder;
  final VoidCallback onConnections;
  final VoidCallback onAccept;
  final VoidCallback onReject;
  final VoidCallback onUndo;
  final bool pendingRequest;
  final VoidCallback? onReturnToClock;
  final VoidCallback? onExit;
  final VoidCallback? onPreferences;
  final WorkspaceStore? store;
  final DateTime Function()? now;
  @override
  State<DeskmateWorkspace> createState() => _WorkspaceState();
}

class _WorkspaceState extends State<DeskmateWorkspace> {
  late final WorkspaceStore _store;
  late final WorkspacePreferences _prefs;
  late final Timer _clock;
  final _subscriptions = <StreamSubscription<dynamic>>[];
  final _records = <ControlRecord>[];
  DateTime _now = DateTime.now();
  late DateTime _month = DateTime(_now.year, _now.month);
  late DateTime _selectedDay = DateUtils.dateOnly(_now);
  String _page = 'home';
  bool _editing = false;
  List<String>? _editBefore;
  Map<String, int>? _editSizesBefore;
  List<String>? _previewOrder;
  String? _draggingId;
  Co2Grades? _grades;
  WeatherKind _weatherKind = WeatherKind.cloudy;
  bool? _weatherNight;
  IrumaeMood _focusMood = IrumaeMood.greeting;
  IrumaeMood? _demoMood;
  DateTime? _moodUntil;
  bool _resting = false;
  bool _lampBusy = false;
  int _lampLevel = 4;
  int _lampTone = 3;
  int _confirmedLampLevel = 4;
  int _confirmedLampTone = 3;
  String? _deviceDetail;
  bool? _speakerConnected;
  bool _checkingDevices = false;
  late final DeskClockController _tools;
  int _focusMinutes = 25;
  int _focusSeconds = 25 * 60;
  DateTime? _focusEnd;
  late DateTime _focusStartedAt;
  ControlRecord? _notice;
  Timer? _noticeTimer;

  bool get _dark =>
      _prefs.appearance == 'dark' ||
      (_prefs.appearance == 'auto' &&
          MediaQuery.platformBrightnessOf(context) == Brightness.dark);
  Color get _bg => _dark ? const Color(0xff101b19) : const Color(0xfff4f2eb);
  Color get _surface => _dark ? const Color(0xff263c33) : Colors.white;
  Color get _soft => _dark ? const Color(0xff30483d) : const Color(0xffe8ecdf);
  Color get _ink => _dark ? const Color(0xffe3ebd9) : const Color(0xff263b37);
  Color get _muted => _dark ? const Color(0xffb4beb2) : const Color(0xff7a8580);
  Color get _accent =>
      _dark ? const Color(0xffb9d3bc) : const Color(0xff356c59);
  Color get _tint => _dark ? const Color(0xff30483d) : const Color(0xffe4ebdd);
  Color get _onAccent => _dark ? const Color(0xff263b37) : Colors.white;
  String _two(int n) => n.toString().padLeft(2, '0');
  String _time(DateTime d) => '${_two(d.hour)}:${_two(d.minute)}';
  String _duration(int seconds) =>
      '${_two(seconds ~/ 60)}:${_two(seconds % 60)}';
  String _date(DateTime d) => '${d.month}월 ${d.day}일 ${[
        '월',
        '화',
        '수',
        '목',
        '금',
        '토',
        '일'
      ][d.weekday - 1]}요일';
  String get _timerText => _duration(_focusSeconds);
  DateTime get _clockNow {
    if (widget.now != null) return widget.now!();
    final korea = DateTime.now().toUtc().add(const Duration(hours: 9));
    return DateTime(korea.year, korea.month, korea.day, korea.hour,
        korea.minute, korea.second, korea.millisecond);
  }

  @override
  void initState() {
    super.initState();
    _store = widget.store ?? WorkspaceStore();
    _prefs = _store.load();
    _now = _clockNow;
    _focusStartedAt = _now;
    _tools = DeskClockController(
        now: () => _clockNow,
        alarms: _prefs.alarms,
        timerSeconds: _prefs.timerSeconds,
        onSave: () {
          _prefs.alarms = [..._tools.alarms];
          _prefs.timerSeconds = _tools.timerSeconds;
          _save();
        },
        onAlert: _timeAlert);
    _focusMinutes = _prefs.focusMinutes;
    _focusSeconds = _focusMinutes * 60;
    if (widget.demo) _focusEnd = _now.add(Duration(seconds: _focusSeconds));
    Co2Grades.load().then((grades) {
      if (mounted) setState(() => _grades = grades);
    });
    if (widget.demo) _focusMood = IrumaeMood.working;
    _configureAutomatic();
    _subscriptions.add(widget.music.playingChanges.listen((_) {
      if (mounted) setState(() {});
    }));
    _subscriptions.add(widget.music.volumeChanges.listen((_) {
      if (mounted) setState(() {});
    }));
    if (widget.music case final MusicTimeline timeline) {
      _subscriptions.add(timeline.positionChanges.listen((_) {
        if (mounted) setState(() {});
      }));
      _subscriptions.add(timeline.durationChanges.listen((_) {
        if (mounted) setState(() {});
      }));
    }
    _subscriptions.add(widget.feedback.records.listen(_receivedRecord));
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      setState(() {
        _now = _clockNow;
        if (_moodUntil != null && !_now.isBefore(_moodUntil!)) {
          _focusMood = IrumaeMood.greeting;
          _moodUntil = null;
        }
        if (_focusEnd != null) {
          _focusSeconds = (_focusEnd!.difference(_now).inMilliseconds / 1000)
              .ceil()
              .clamp(0, _focusMinutes * 60);
          if (_focusSeconds == 0) {
            final report = FocusReport(
                startedAt: _focusStartedAt,
                endedAt: _focusEnd!,
                timerSeconds: _focusMinutes * 60,
                demo: widget.demo);
            _prefs.focusReports.insert(0, report);
            if (_prefs.focusReports.length > 200) {
              _prefs.focusReports.removeLast();
            }
            _focusEnd = null;
            _save();
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (!mounted) return;
              _goPage('home');
              unawaited(_showFocusReport(report));
            });
            _focusMood = IrumaeMood.celebrating;
            _moodUntil = _now.add(const Duration(seconds: 12));
          }
        }
      });
      if (_page == 'devices' && _now.second % 5 == 0) {
        unawaited(_refreshDevices());
      }
    });
  }

  void _configureAutomatic() {
    widget.feedback.configureTargets(
        lamp: _prefs.lampAutomatic, speaker: _prefs.speakerAutomatic);
    widget.feedback.enabled = _prefs.automatic;
  }

  void _save() {
    _configureAutomatic();
    if (!_store.save(_prefs)) _message('설정을 저장할 수 없어요. 현재 실행 중에는 유지됩니다.');
  }

  void _receivedRecord(ControlRecord record) {
    if (!mounted) return;
    _noticeTimer?.cancel();
    setState(() {
      _records.insert(0, record);
      if (_records.length > 100) _records.removeLast();
      _notice = record;
    });
    if (record.succeeded) {
      _noticeTimer = Timer(const Duration(seconds: 8), () {
        if (mounted) setState(() => _notice = null);
      });
    }
  }

  void _message(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _musicAction(Future<void> Function() action) async {
    try {
      await action();
      if (mounted) setState(() {});
    } catch (_) {
      _message('사운드를 조절하지 못했어요. 스피커 연결을 확인해주세요.');
    }
  }

  Future<void> _timeAlert(String text) async {
    if (!mounted) return;
    unawaited(
        SystemSound.play(SystemSoundType.alert).catchError((Object _) {}));
    await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (context) => AlertDialog(
                title: const Text('시계 알림'),
                content: Text(text),
                actions: [
                  FilledButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('확인'))
                ]));
  }

  void _goPage(String page, {String? device}) {
    if (_editing) _finishHomeEditing();
    setState(() {
      _page = page;
      _deviceDetail = device;
    });
    if (page == 'devices') unawaited(_refreshDevices());
  }

  Future<void> _refreshDevices() async {
    if (_checkingDevices) return;
    final address = widget.feedback.speakerAddress;
    if (address == null) return;
    _checkingDevices = true;
    bool? connected;
    try {
      connected = await widget.feedback.bluetooth.isA2dpConnected(address);
    } catch (_) {
      connected = null;
    }
    _checkingDevices = false;
    if (mounted) setState(() => _speakerConnected = connected);
  }

  // V18: 보드 상태(state)가 있으면 그 값을, 없을 때만 화면 기본값을 쓴다.
  double? get _temperature => widget.state != null
      ? widget.state!.temperatureC
      : (widget.demo ? 24.6 : null);
  double? get _humidity => widget.state != null
      ? widget.state!.humidityPct
      : (widget.demo ? 48 : null);
  int? get _co2 =>
      widget.state != null ? widget.state!.co2Ppm : (widget.demo ? 720 : null);

  String get _lampStatus => widget.deviceStatus['desk_lamp'] ?? _lampLink;
  String get _lampLink => widget.feedback.lamp.connected
      ? '연결됨'
      : widget.feedback.lamp.configured
          ? '연결 확인 전'
          : widget.demo
              ? '내 책상'
              : '연결 안 됨';
  String get _musicStatus => !widget.musicAvailable
      ? '음원 없음'
      : _speakerConnected == true
          ? '연결됨'
          : widget.feedback.speakerAddress == null
              ? (widget.demo ? '내장 재생' : '내장 오디오')
              : _speakerConnected == false
                  ? '연결 안 됨'
                  : '연결 확인 전';

  Future<void> _setLampStep(int step, {bool tone = false}) async {
    if (_lampBusy) return;
    if (!widget.feedback.lamp.configured) {
      if (widget.demo) {
        setState(() {
          if (tone) {
            _confirmedLampTone = _lampTone = step;
          } else {
            _confirmedLampLevel = _lampLevel = step;
          }
        });
        return;
      }
      setState(() {
        _lampLevel = _confirmedLampLevel;
        _lampTone = _confirmedLampTone;
      });
      _message('기기 연결에서 조명을 연결해주세요.');
      return;
    }
    setState(() => _lampBusy = true);
    final ok = await widget.feedback.lamp.writeFrames(tone
        ? [
            ilinkFrame([1, 8, 9, lampToneCommands[step]])
          ]
        : step == 0
            ? [
                ilinkFrame([1, 8, 5, 0])
              ]
            : [
                ilinkFrame([1, 8, 5, 1]),
                ilinkFrame([1, 8, 1, lampLevelValues[step]])
              ]);
    if (!mounted) return;
    setState(() {
      _lampBusy = false;
      if (ok) {
        if (tone) {
          _confirmedLampTone = _lampTone = step;
        } else {
          _confirmedLampLevel = _lampLevel = step;
        }
      } else {
        _lampLevel = _confirmedLampLevel;
        _lampTone = _confirmedLampTone;
      }
    });
    if (!ok) _message('조명 연결을 확인해주세요.');
  }

  void _stepTrack(int step) {
    final music = widget.music;
    final queue = music is MusicQueue
        ? (music as MusicQueue).queue
        : List.generate(classicalPlaylist.length, (i) => i);
    final index = queue.indexOf(music.selectedTrack);
    final next = queue[(index + step) % queue.length];
    unawaited(_musicAction(() => music.selectTrack(next)));
  }

  Future<void> _playQueue(List<int> tracks) async {
    if (tracks.isEmpty) return;
    await _musicAction(() async {
      if (widget.music is MusicQueue) {
        (widget.music as MusicQueue).setQueue(tracks);
      }
      await widget.music.selectTrack(tracks.first);
      if (!widget.music.isPlaying) await widget.music.toggle();
    });
  }

  @override
  void dispose() {
    _clock.cancel();
    _tools.dispose();
    _noticeTimer?.cancel();
    for (final subscription in _subscriptions) {
      unawaited(subscription.cancel());
    }
    super.dispose();
  }

  Widget _button(String text, VoidCallback? onPressed,
          {IconData? icon, bool primary = false, Key? key}) =>
      TextButton.icon(
          key: key,
          onPressed: onPressed,
          icon: icon == null ? const SizedBox.shrink() : Icon(icon, size: 18),
          label: Text(text),
          style: TextButton.styleFrom(
              foregroundColor: primary ? _onAccent : _ink,
              backgroundColor: primary ? _accent : _soft,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(14))));
  Widget _card(Widget child, {Color? color, EdgeInsets? padding}) => Container(
      padding: padding ?? const EdgeInsets.all(23),
      decoration: BoxDecoration(
          color: color ?? _surface, borderRadius: BorderRadius.circular(28)),
      child: child);
  Widget _sectionTitle(IconData icon, String text) => Row(children: [
        Icon(icon, color: _accent, size: 21),
        const SizedBox(width: 10),
        Flexible(
            child: Text(text,
                style:
                    const TextStyle(fontSize: 19, fontWeight: FontWeight.w600)))
      ]);
  Widget _heading(String title, String subtitle, [Widget? action]) => Padding(
      padding: const EdgeInsets.only(bottom: 22),
      child: Wrap(
          alignment: WrapAlignment.spaceBetween,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 18,
          runSpacing: 12,
          children: [
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title,
                  style: const TextStyle(
                      fontSize: 27,
                      fontWeight: FontWeight.w600,
                      letterSpacing: -.7)),
              if (subtitle.isNotEmpty)
                Padding(
                    padding: const EdgeInsets.only(top: 5),
                    child: Text(subtitle,
                        style: TextStyle(color: _muted, fontSize: 13))),
            ]),
            if (action != null) action
          ]));

  ThemeData get _workspaceTheme {
    final scheme = ColorScheme.fromSeed(
            seedColor: _accent,
            brightness: _dark ? Brightness.dark : Brightness.light)
        .copyWith(
            surface: _surface,
            onSurface: _ink,
            onSurfaceVariant: _muted,
            primary: _accent,
            onPrimary: _onAccent);
    return ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        scaffoldBackgroundColor: _bg,
        fontFamily: 'High1',
        textTheme: _page == 'home'
            ? const TextTheme(
                bodyLarge: homeKoreanTextStyle,
                bodyMedium: homeKoreanTextStyle,
                bodySmall: homeKoreanTextStyle,
                titleLarge: homeKoreanTextStyle,
                titleMedium: homeKoreanTextStyle,
                titleSmall: homeKoreanTextStyle,
                labelLarge: homeKoreanTextStyle,
                labelMedium: homeKoreanTextStyle,
                labelSmall: homeKoreanTextStyle)
            : null,
        fontFamilyFallback: const [
          'LGText',
          'LGClock',
          'Noto Sans CJK KR',
          'Noto Sans KR',
          'sans-serif'
        ]);
  }

  @override
  Widget build(BuildContext context) {
    final theme = _workspaceTheme;
    return Theme(
        data: theme,
        child: Scaffold(
            backgroundColor: _bg,
            body: SafeArea(child: LayoutBuilder(builder: (context, bounds) {
              if (_page == 'developer') {
                return Column(children: [
                  Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 6),
                      child: Row(children: [
                        _button(
                            '사용자 화면', () => setState(() => _page = 'settings'),
                            icon: Icons.arrow_back_rounded),
                        const SizedBox(width: 14),
                        const Text('개발 도구'),
                      ])),
                  Expanded(child: widget.developerBuilder(context))
                ]);
              }
              final narrow = bounds.maxWidth < 680;
              return Stack(children: [
                Column(children: [
                  Padding(
                      padding: const EdgeInsets.fromLTRB(26, 20, 26, 18),
                      child: SizedBox(
                          height: 38,
                          child: Row(children: [
                            if (!_editing || !narrow) ...[
                              Container(
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 9, vertical: 4),
                                  decoration: BoxDecoration(
                                      color: _soft,
                                      borderRadius: BorderRadius.circular(20)),
                                  child: Text(
                                      _editing
                                          ? '홈 편집'
                                          : widget.demo
                                              ? '내 책상'
                                              : '내 책상',
                                      style: TextStyle(
                                          color: _muted, fontSize: 11))),
                            ],
                            Expanded(
                                child: AnimatedSwitcher(
                                    duration: const Duration(milliseconds: 280),
                                    child: !_editing
                                        ? GestureDetector(
                                            key: const ValueKey(
                                                'home-header-space'),
                                            behavior: HitTestBehavior.opaque,
                                            onLongPress: _page == 'home'
                                                ? _beginHomeEditing
                                                : null,
                                            child: const SizedBox(
                                                height: 38,
                                                width: double.infinity))
                                        : Row(
                                            key: const ValueKey(
                                                'home-edit-controls'),
                                            mainAxisAlignment:
                                                MainAxisAlignment.end,
                                            children: [
                                                TextButton.icon(
                                                    onPressed:
                                                        _draggingId == null
                                                            ? _widgetEditor
                                                            : null,
                                                    icon: const Icon(
                                                        Icons.add_rounded,
                                                        size: 18),
                                                    label: const Text('위젯')),
                                                TextButton(
                                                    onPressed: () =>
                                                        _finishHomeEditing(
                                                            cancel: true),
                                                    child: const Text('취소')),
                                                TextButton(
                                                    onPressed: () =>
                                                        _finishHomeEditing(),
                                                    child: const Text('완료')),
                                              ]))),
                            const SizedBox(width: 10),
                            const NetworkStatusIndicator(
                                key: ValueKey('workspace-network-status')),
                            if (_page != 'home')
                              TextButton.icon(
                                  key: const ValueKey('workspace-return-home'),
                                  onPressed: () => _goPage('home'),
                                  icon:
                                      const Icon(Icons.home_outlined, size: 18),
                                  label: const Text('홈으로')),
                            if (widget.onReturnToClock != null)
                              TextButton.icon(
                                  key: const ValueKey('workspace-return-clock'),
                                  onPressed: widget.onReturnToClock,
                                  icon: const Icon(Icons.arrow_back_rounded,
                                      size: 18),
                                  label: const Text('시계로')),
                            if (widget.onPreferences != null)
                              IconButton(
                                  tooltip: '나의 책상 설정',
                                  onPressed: widget.onPreferences,
                                  icon: const Icon(Icons.settings_outlined)),
                            if (widget.onExit != null)
                              IconButton(
                                  tooltip: '앱 종료',
                                  onPressed: widget.onExit,
                                  icon: const Icon(Icons.close_rounded)),
                          ]))),
                  Divider(height: 1, color: _soft),
                  Expanded(
                      child: PageTransitionView(
                          pageKey: '$_page-$_deviceDetail',
                          child: _pageBody(narrow))),
                ]),
                if (widget.pendingRequest) _suggestion(),
                if (_notice != null) _noticeCard(),
              ]);
            }))));
  }

  Widget _pageBody(bool narrow) {
    if (_page == 'home') {
      return Stack(fit: StackFit.expand, children: [
        Positioned.fill(
            child: GestureDetector(
                key: const ValueKey('home-edit-space'),
                behavior: HitTestBehavior.opaque,
                onLongPress: _editing ? null : _beginHomeEditing)),
        Padding(padding: EdgeInsets.all(narrow ? 12 : 22), child: _home())
      ]);
    }
    final padding = EdgeInsets.all(narrow ? 14 : 22);
    if (_page == 'calendar') {
      return Padding(padding: padding, child: _calendar());
    }
    if (_page == 'clock') {
      return Padding(padding: padding, child: DeskClockPage(tools: _tools));
    }
    if (_page == 'devices') return Padding(padding: padding, child: _devices());
    return SingleChildScrollView(
        key: const ValueKey('workspace-scroll'),
        padding: padding,
        child: switch (_page) {
          'focus' => _focus(),
          'weather' => _weatherPage(),
          'environment' => _environmentPage(),
          'settings' => _settings(),
          _ => _home()
        });
  }

  Widget _home() => LayoutBuilder(builder: (context, box) {
        final order = _prefs.widgets;
        if (order.isEmpty) {
          return IgnorePointer(
              child: Center(
                  child: Text('빈 곳을 길게 눌러 위젯을 추가하세요.',
                      style: TextStyle(color: _muted))));
        }
        final columns = box.maxWidth >= 560 ? 4 : 2;
        const gap = 16.0;
        final slots = packHomeWidgets(order, columns, sizes: _prefs.sizes);
        final rows = columns == 4 ? 2 : 4;
        final unit = (box.maxWidth - gap * (columns - 1)) / columns;
        final height = (box.maxHeight - gap * (rows - 1)) / rows;
        return Stack(key: const ValueKey('home-fixed-grid'), children: [
          for (final slot in slots)
            AnimatedPositioned(
              key: ValueKey('home-position-${slot.id}'),
              duration: const Duration(milliseconds: 320),
              curve: Curves.easeInOutCubic,
              left: slot.column * (unit + gap),
              top: slot.row * (height + gap),
              width: unit * slot.span + gap * (slot.span - 1),
              height: height,
              child: Listener(
                  behavior: HitTestBehavior.opaque,
                  child: _editableTile(slot.id,
                      unit * slot.span + gap * (slot.span - 1), height)),
            ),
        ]);
      });

  Widget _editableTile(String id, double width, double height) {
    return AnimatedScale(
      scale: _editing ? .985 : 1,
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeInOutCubic,
      child: Stack(fit: StackFit.expand, children: [
        IgnorePointer(
            ignoring: _editing,
            child: AnimatedOpacity(
                opacity: _draggingId == id ? .25 : 1,
                duration: const Duration(milliseconds: 180),
                child: RepaintBoundary(child: _homeTile(id)))),
        TweenAnimationBuilder<double>(
          tween: Tween(begin: 0, end: _editing ? 1 : 0),
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeInOutCubic,
          builder: (context, progress, _) => progress == 0
              ? const SizedBox.shrink()
              : IgnorePointer(
                  ignoring: !_editing,
                  child: Opacity(
                      opacity: progress,
                      child: _editOverlay(id, width, height))),
        ),
      ]),
    );
  }

  Widget _editOverlay(String id, double width, double height) =>
      DragTarget<String>(
        key: ValueKey('home-drop-$id'),
        onWillAcceptWithDetails: (details) {
          if (details.data == id || !_prefs.widgets.contains(details.data)) {
            return false;
          }
          setState(() =>
              _previewOrder = moveHomeWidget(_prefs.widgets, details.data, id));
          return true;
        },
        onAcceptWithDetails: (details) {
          final order =
              _previewOrder ?? moveHomeWidget(_prefs.widgets, details.data, id);
          if (!homeWidgetsFit(order, sizes: _prefs.sizes)) return;
          setState(() => _prefs.widgets = order);
        },
        builder: (context, candidates, rejected) =>
            Stack(fit: StackFit.expand, children: [
          Draggable<String>(
            data: id,
            maxSimultaneousDrags: 1,
            onDragStarted: () => setState(() => _draggingId = id),
            onDragEnd: (_) => setState(() {
              _previewOrder = null;
              _draggingId = null;
            }),
            feedback: Theme(
                data: _workspaceTheme,
                child: Material(
                    color: Colors.transparent,
                    elevation: 12,
                    borderRadius: BorderRadius.circular(28),
                    child: SizedBox(
                        width: width,
                        height: height,
                        child: Opacity(
                            opacity: .9,
                            child: IgnorePointer(child: _homeTile(id)))))),
            childWhenDragging: Container(
                decoration: BoxDecoration(
                    color: _tint.withValues(alpha: .5),
                    border: Border.all(color: _accent, width: 2),
                    borderRadius: BorderRadius.circular(28))),
            child: DecoratedBox(
                decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(28),
                    border: Border.all(
                        color: candidates.isNotEmpty
                            ? _accent
                            : _accent.withValues(alpha: .35),
                        width: candidates.isNotEmpty ? 3 : 1.5))),
          ),
          // Controls are siblings of the move gesture, so grabbing the corner never moves the card.
          Positioned(
              top: 7,
              left: 7,
              child: IconButton.filledTonal(
                  tooltip: '${widgetNames[id]} 숨기기',
                  visualDensity: VisualDensity.compact,
                  onPressed: () => setState(() {
                        _prefs.widgets.remove(id);
                        _previewOrder = null;
                      }),
                  icon: const Icon(Icons.remove_rounded, size: 17))),
          Positioned(
              right: 8,
              bottom: 7,
              child: resizableHomeWidgets.contains(id)
                  ? HomeResizeHandle(
                      key: ValueKey('home-resize-$id'),
                      label: widgetNames[id]!,
                      span: _prefs.sizes[id] ?? homeWidgetSpans[id]!,
                      onChanged: (span) => _resizeHomeWidget(id, span))
                  : Padding(
                      padding: const EdgeInsets.all(5),
                      child: Icon(Icons.drag_indicator_rounded,
                          color: _muted, size: 18))),
        ]),
      );

  void _beginHomeEditing() => setState(() {
        if (_editing) return;
        _editBefore = [..._prefs.widgets];
        _editSizesBefore = {..._prefs.sizes};
        _page = 'home';
        _editing = true;
      });

  void _finishHomeEditing({bool cancel = false}) {
    setState(() {
      if (cancel && _editBefore != null) _prefs.widgets = [..._editBefore!];
      if (cancel && _editSizesBefore != null) {
        _prefs.sizes = {..._editSizesBefore!};
      }
      _editing = false;
      _previewOrder = null;
      _draggingId = null;
      _editBefore = null;
      _editSizesBefore = null;
    });
    if (!cancel) _save();
  }

  void _resizeHomeWidget(String id, int span) {
    if (!_editing ||
        !resizableHomeWidgets.contains(id) ||
        (span != 1 && span != 2)) {
      return;
    }
    if (_prefs.sizes[id] == span) return;
    if (!homeWidgetsFit(_prefs.widgets, sizes: {..._prefs.sizes, id: span})) {
      _message('홈이 가득 찼어요. 다른 위젯을 줄이거나 빼주세요.');
      return;
    }
    setState(() {
      _prefs.sizes[id] = span;
      _previewOrder = null;
    });
  }

  Future<void> _widgetEditor() => showDialog<void>(
      context: context,
      builder: (context) => Theme(
          data: _workspaceTheme,
          child: AlertDialog(
            title: const Text('홈 위젯'),
            content: SingleChildScrollView(
                child: HomeWidgetPicker(
              selected: _prefs.widgets,
              onToggle: (id) {
                final order = [..._prefs.widgets];
                if (!order.remove(id)) order.add(id);
                if (!homeWidgetsFit(order, sizes: _prefs.sizes)) {
                  _message('홈이 가득 찼어요. 다른 위젯을 줄이거나 빼주세요.');
                  return false;
                }
                setState(() {
                  _prefs.widgets = order;
                  _previewOrder = null;
                });
                return true;
              },
            )),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('닫기'))
            ],
          )));

  Widget _homeTile(String id) {
    if (id == 'clock') {
      return StandbyClockTile(
          now: _now,
          compact: _prefs.sizes['clock'] == 1,
          onOpen: () => _goPage('clock'));
    }
    if (id == 'weather') {
      return HomeWeatherTile(
          now: _now,
          demo: widget.demo,
          wide: _prefs.sizes['weather'] == 2,
          weather: widget.demo ? _weatherPreview : null,
          onTap: () => _goPage('weather'));
    }
    if (id == 'calendar') {
      return MonthCalendarTile(
          now: _now, events: _prefs.events, onOpen: () => _goPage('calendar'));
    }
    if (id == 'environment') {
      return EnvironmentTile(
          onOpen: () => _goPage('environment'),
          temperature: _temperature,
          humidity: _humidity,
          grade: _grades?.forValue(_co2));
    }
    if (id == 'focus') {
      return FocusCountdownTile(
          showGoal: widget.showFocusGoal,
          seconds: _focusSeconds,
          minutes: _focusMinutes,
          todaySeconds: _todayFocus.fold<int>(
              0, (sum, report) => sum + report.timerSeconds),
          todaySessions: _todayFocus.length,
          running: _focusEnd != null,
          mood: _irumaeMood,
          onOpen: () => _goPage('focus'));
    }
    if (!widget.musicAvailable) {
      return const HomeTile(
          key: ValueKey('music-unavailable'),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.music_note_outlined, size: 34),
            SizedBox(height: 10),
            Text('등록된 음원이 없어요'),
          ]));
    }
    final music = widget.music;
    final timeline = music is MusicTimeline ? music as MusicTimeline : null;
    return HomeMusicTile(
        title: classicalTrackTitles[music.selectedTrack],
        playing: music.isPlaying,
        position: timeline?.position ?? Duration.zero,
        duration: timeline?.duration,
        favorite: _prefs.favoriteTracks.contains(music.selectedTrack),
        onFavorite: _toggleFavoriteTrack,
        onVolume: _volumePopup,
        onSeek: timeline == null
            ? null
            : (position) => _musicAction(() => timeline.seek(position)),
        onPlaylist: () => _goPage('devices', device: 'music'),
        onToggle: () => _musicAction(() async {
              await music.toggle();
            }),
        onPrevious: () => _stepTrack(-1),
        onNext: () => _stepTrack(1));
  }

  WeatherSnapshot get _weatherPreview => WeatherSnapshot.demo(_weatherKind,
      isNight: _weatherNight ?? (_now.hour < 6 || _now.hour >= 18));

  Widget _weatherPage() =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _heading('날씨', ''),
        SizedBox(
            height: 260,
            child: HomeWeatherTile(
                now: _now,
                demo: widget.demo,
                wide: true,
                weather: widget.demo ? _weatherPreview : null)),
        if (widget.demo) ...[
          const SizedBox(height: 20),
          const Text('날씨', style: TextStyle(fontSize: 18)),
          const SizedBox(height: 9),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final kind in WeatherKind.values)
              ChoiceChip(
                  key: ValueKey('weather-demo-${kind.name}'),
                  label: Text(weatherLabels[kind]!,
                      style: homeHandwritingTextStyle),
                  selected: _weatherKind == kind,
                  onSelected: (_) => setState(() => _weatherKind = kind))
          ]),
          SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('밤하늘'),
              value: _weatherPreview.isNight,
              onChanged: (night) => setState(() => _weatherNight = night)),
        ],
      ]);

  Widget _environmentPage() =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _heading('실내', widget.demo ? '내 책상' : ''),
        SizedBox(
            width: 460,
            height: 350,
            child: EnvironmentTile(
                temperature: _temperature,
                humidity: _humidity,
                grade: _grades?.forValue(_co2))),
      ]);

  void _update(VoidCallback change) => setState(change);

  Widget _calendar() => DeskCalendarPanel(
      month: _month,
      selectedDay: _selectedDay,
      today: _now,
      events: _prefs.events,
      onMonth: (month) => _update(() {
            _month = month;
            final day = _selectedDay.day
                .clamp(1, DateTime(month.year, month.month + 1, 0).day);
            _selectedDay = DateTime(month.year, month.month, day);
          }),
      onDay: (day) => _update(() => _selectedDay = day),
      onAdd: () => _editEvent(),
      onEdit: _editEvent,
      onDelete: (event) {
        _update(() => _prefs.events.removeWhere((e) => e.id == event.id));
        _save();
      });

  Future<void> _editEvent([DeskEvent? event]) async {
    final title = TextEditingController(text: event?.title ?? '');
    final location = TextEditingController(text: event?.location ?? '');
    final form = GlobalKey<FormState>();
    var date = event?.date ?? _selectedDay;
    var start = TimeOfDay(hour: event?.hour ?? 14, minute: event?.minute ?? 0);
    final defaultEnd = (start.hour * 60 + start.minute + 60).clamp(0, 1439);
    var end = TimeOfDay(
        hour: event?.endHour ?? defaultEnd ~/ 60,
        minute: event?.endMinute ?? defaultEnd % 60);
    var invalidTime = false;
    final result = await showDialog<DeskEvent>(
        context: context,
        builder: (dialogContext) => Theme(
            data: _workspaceTheme,
            child: StatefulBuilder(builder: (context, update) {
              Future<void> pickTime(bool isStart) async {
                final selected = await showTimePicker(
                    context: context,
                    initialTime: isStart ? start : end,
                    initialEntryMode: TimePickerEntryMode.input,
                    builder: (context, child) => MediaQuery(
                        data: MediaQuery.of(context)
                            .copyWith(alwaysUse24HourFormat: true),
                        child: child!));
                if (selected != null && context.mounted) {
                  update(() {
                    if (isStart) {
                      start = selected;
                    } else {
                      end = selected;
                    }
                    invalidTime = false;
                  });
                }
              }

              Widget timeField(String label, TimeOfDay value, bool isStart) =>
                  Expanded(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                        Text(label,
                            style: TextStyle(fontSize: 12, color: _muted)),
                        const SizedBox(height: 5),
                        OutlinedButton.icon(
                            key: ValueKey(isStart
                                ? 'workspace-event-start'
                                : 'workspace-event-end'),
                            onPressed: () => pickTime(isStart),
                            icon: const Icon(Icons.schedule_rounded, size: 18),
                            label: Text(
                                '${_two(value.hour)}:${_two(value.minute)}',
                                style: const TextStyle(fontSize: 18)),
                            style: OutlinedButton.styleFrom(
                                padding:
                                    const EdgeInsets.symmetric(vertical: 12),
                                shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(12)))),
                      ]));
              return AlertDialog(
                  backgroundColor: _surface,
                  surfaceTintColor: Colors.transparent,
                  title: Text(event == null ? '새 일정' : '일정 수정'),
                  content: SizedBox(
                      width: 380,
                      child: SingleChildScrollView(
                          child: Form(
                              key: form,
                              child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    TextFormField(
                                        key: const ValueKey(
                                            'workspace-event-title'),
                                        controller: title,
                                        maxLength: 60,
                                        validator: (value) => value == null ||
                                                value.trim().isEmpty
                                            ? '일정 이름을 입력해주세요.'
                                            : null,
                                        decoration: const InputDecoration(
                                            labelText: '일정 이름')),
                                    ListTile(
                                        contentPadding: EdgeInsets.zero,
                                        leading: const Icon(
                                            Icons.calendar_today_outlined),
                                        title: Text(_date(date)),
                                        onTap: () async {
                                          final selected = await showDatePicker(
                                              context: context,
                                              initialDate: date,
                                              firstDate: DateTime(2000),
                                              lastDate: DateTime(2100));
                                          if (selected != null &&
                                              context.mounted) {
                                            update(() => date = selected);
                                          }
                                        }),
                                    Row(children: [
                                      timeField('시작 시간', start, true),
                                      const SizedBox(width: 14),
                                      timeField('끝 시간', end, false)
                                    ]),
                                    if (invalidTime)
                                      Padding(
                                          padding:
                                              const EdgeInsets.only(top: 7),
                                          child: Text('끝 시간은 시작 시간보다 늦게 정해주세요.',
                                              style: TextStyle(
                                                  color: Theme.of(context)
                                                      .colorScheme
                                                      .error,
                                                  fontSize: 12))),
                                    const SizedBox(height: 16),
                                    TextFormField(
                                        key: const ValueKey(
                                            'workspace-event-location'),
                                        controller: location,
                                        maxLength: 100,
                                        decoration: const InputDecoration(
                                            labelText: '장소',
                                            hintText: '장소를 입력하세요',
                                            prefixIcon: Icon(
                                                Icons.location_on_outlined))),
                                  ])))),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(dialogContext),
                        child: const Text('취소')),
                    FilledButton(
                        key: const ValueKey('workspace-event-save'),
                        onPressed: () {
                          final validName = form.currentState!.validate();
                          final validTime = end.hour * 60 + end.minute >
                              start.hour * 60 + start.minute;
                          update(() => invalidTime = !validTime);
                          if (!validName || !validTime) return;
                          Navigator.pop(
                              dialogContext,
                              DeskEvent(
                                  id: event?.id ??
                                      '${DateTime.now().microsecondsSinceEpoch}',
                                  title: title.text.trim(),
                                  date: date,
                                  hour: start.hour,
                                  minute: start.minute,
                                  endHour: end.hour,
                                  endMinute: end.minute,
                                  location: location.text.trim()));
                        },
                        child: const Text('저장')),
                  ]);
            })));
    // The text fields remain mounted during the dialog's exit transition.
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      title.dispose();
      location.dispose();
    });
    if (result == null || !mounted) return;
    _update(() {
      _prefs.events.removeWhere((e) => e.id == result.id);
      _prefs.events.add(result);
      _selectedDay = result.date;
      _month = DateTime(result.date.year, result.date.month);
    });
    _save();
  }

  void _toggleFocus() => _update(() {
        if (_focusEnd != null) {
          _focusEnd = null;
          _focusMood = IrumaeMood.greeting;
        } else {
          _resting = false;
          _demoMood = null;
          _focusMood = IrumaeMood.working;
          _moodUntil = null;
          if (_focusSeconds == 0 || _focusSeconds == _focusMinutes * 60) {
            _focusStartedAt = _clockNow;
          }
          if (_focusSeconds == 0) _focusSeconds = _focusMinutes * 60;
          _focusEnd = _clockNow.add(Duration(seconds: _focusSeconds));
        }
      });

  void _applyFocus(int minutes) {
    _update(() {
      final running = _focusEnd != null;
      _demoMood = null;
      _resting = false;
      _moodUntil = null;
      _focusMood = running ? IrumaeMood.working : IrumaeMood.greeting;
      _focusMinutes = minutes;
      _focusStartedAt = _clockNow;
      _prefs.focusMinutes = minutes;
      _focusSeconds = minutes * 60;
      _focusEnd =
          running ? _clockNow.add(Duration(seconds: _focusSeconds)) : null;
    });
    _save();
  }

  Future<void> _customFocus() async {
    final controller = TextEditingController(text: '$_focusMinutes');
    String? error;
    final minutes = await showDialog<int>(
        context: context,
        barrierDismissible: true,
        builder: (dialogContext) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title: const Text('집중 시간 설정'),
                  content: TextField(
                      controller: controller,
                      keyboardType: TextInputType.number,
                      decoration: InputDecoration(
                          labelText: '분',
                          helperText: '1~180분',
                          errorText: error)),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(dialogContext),
                        child: const Text('취소')),
                    FilledButton(
                        onPressed: () {
                          final value = int.tryParse(controller.text);
                          if (value == null || value < 1 || value > 180) {
                            update(() => error = '1~180분으로 입력해주세요.');
                            return;
                          }
                          Navigator.pop(dialogContext, value);
                        },
                        child: const Text('설정'))
                  ],
                )));
    Future<void>.delayed(const Duration(milliseconds: 400), controller.dispose);
    if (minutes != null && mounted) _applyFocus(minutes);
  }

  List<FocusReport> _reportsFor(DateTime day, bool demo) => _prefs.focusReports
      .where((r) => sameFocusDay(r.endedAt, day) && r.demo == demo)
      .toList();
  List<FocusReport> get _todayFocus => _reportsFor(_now, widget.demo);

  Future<void> _showFocusReport(FocusReport report,
      {bool preview = false}) async {
    final today = await showDialog<bool>(
        context: context,
        barrierDismissible: true,
        builder: (_) => Theme(
            data: _workspaceTheme,
            child: FocusReportDialog(
                report: report,
                today: _reportsFor(report.endedAt, report.demo),
                preview: preview)));
    if (today == true && mounted) await _showFocusHistory(day: report.endedAt);
  }

  Future<void> _showFocusHistory({DateTime? day}) async {
    final records = _prefs.focusReports
        .where((r) =>
            r.demo == widget.demo &&
            (day == null || sameFocusDay(r.endedAt, day)))
        .toList();
    final selected = await showDialog<FocusReport>(
        context: context,
        builder: (context) => Theme(
            data: _workspaceTheme,
            child: AlertDialog(
                key: const ValueKey('focus-history-dialog'),
                title: Text(day == null ? '집중 기록' : '오늘 기록'),
                content: SizedBox(
                    width: 560,
                    height: 300,
                    child: records.isEmpty
                        ? const Center(child: Text('아직 완료한 집중이 없어요.'))
                        : ListView.separated(
                            itemCount: records.length,
                            separatorBuilder: (_, __) => const Divider(),
                            itemBuilder: (context, i) {
                              final report = records[i];
                              return ListTile(
                                  leading: const Icon(
                                      Icons.local_fire_department_rounded),
                                  title: Text(
                                      '${report.endedAt.month}월 ${report.endedAt.day}일 · ${_time(report.endedAt)}'),
                                  subtitle: Text(
                                      '타이머 ${focusDuration(report.timerSeconds)}${report.demo ? '' : ''}'),
                                  trailing:
                                      const Icon(Icons.chevron_right_rounded),
                                  onTap: () => Navigator.pop(context, report));
                            })),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('닫기'))
                ])));
    if (selected != null && mounted) await _showFocusReport(selected);
  }

  Widget _focus() => _card(Column(children: [
        Wrap(
            spacing: 10,
            runSpacing: 8,
            alignment: WrapAlignment.center,
            children: [
              OutlinedButton.icon(
                  key: const ValueKey('focus-open-history'),
                  onPressed: _showFocusHistory,
                  icon: const Icon(Icons.history_rounded),
                  label: const Text('집중 기록')),
              if (widget.demo)
                TextButton.icon(
                    key: const ValueKey('focus-preview-report'),
                    onPressed: () => _showFocusReport(
                        FocusReport(
                            startedAt:
                                _clockNow.subtract(const Duration(minutes: 25)),
                            endedAt: _clockNow,
                            timerSeconds: 1500,
                            demo: true),
                        preview: true),
                    icon: const Icon(Icons.analytics_outlined),
                    label: const Text('리포트 미리보기')),
            ]),
        const SizedBox(height: 10),
        Icon(Icons.local_fire_department_rounded,
            color: const Color(0xffd98343), size: 54),
        const SizedBox(height: 14),
        Wrap(
            spacing: 9,
            runSpacing: 8,
            alignment: WrapAlignment.center,
            children: [
              for (final n in [25, 50, 90])
                ChoiceChip(
                    label: Text('$n분'),
                    selected: _focusMinutes == n,
                    onSelected: (_) => _applyFocus(n)),
              ActionChip(
                  label: const Text('직접 설정'),
                  avatar: const Icon(Icons.tune_rounded, size: 16),
                  onPressed: _customFocus),
            ]),
        const SizedBox(height: 22),
        FittedBox(
            child: Text(_timerText,
                key: const ValueKey('focus-popup-countdown'),
                style: const TextStyle(
                    fontSize: 80,
                    fontWeight: FontWeight.w400,
                    height: 1.1,
                    letterSpacing: 0))),
        const SizedBox(height: 22),
        ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 400),
            child: LinearProgressIndicator(
                value: 1 - _focusSeconds / (_focusMinutes * 60),
                color: _accent,
                backgroundColor: _soft,
                minHeight: 4,
                borderRadius: BorderRadius.circular(4))),
        const SizedBox(height: 24),
        Wrap(
            spacing: 12,
            runSpacing: 8,
            alignment: WrapAlignment.center,
            children: [
              _button(_focusEnd == null ? '계속하기' : '잠시 멈춤', _toggleFocus,
                  icon: _focusEnd == null
                      ? Icons.play_arrow_rounded
                      : Icons.pause_rounded,
                  primary: true),
              _button('쉬기', _restFocus, icon: Icons.bedtime_outlined),
              _button('이번 집중 종료', _abandonFocus,
                  icon: Icons.stop_circle_outlined),
              _button('초기화', () => _applyFocus(_focusMinutes),
                  icon: Icons.refresh_rounded),
            ]),
        const SizedBox(height: 12),
        SizedBox(
            height: 100, width: 160, child: IrumaeAnimation(mood: _irumaeMood)),
        if (widget.demo)
          Wrap(
              spacing: 7,
              runSpacing: 7,
              alignment: WrapAlignment.center,
              children: [
                for (final mood in IrumaeMood.values)
                  ChoiceChip(
                      label: Text(irumaeLabels[mood]!),
                      selected: _demoMood == mood,
                      onSelected: (_) => _update(() => _demoMood = mood)),
              ]),
      ]));

  IrumaeMood get _irumaeMood {
    if (widget.demo && _demoMood != null) return _demoMood!;
    // Only explicit rest/sleep signals show the sleep pose; inactivity is not sleep.
    if (_resting ||
        widget.state?.phase == 'sleep' ||
        widget.state?.fsmState == 'REST') {
      return IrumaeMood.sleeping;
    }
    return _focusMood;
  }

  void _restFocus() => _update(() {
        _focusEnd = null;
        _resting = true;
        _demoMood = null;
      });

  void _abandonFocus() => _update(() {
        _focusEnd = null;
        _focusSeconds = _focusMinutes * 60;
        _focusStartedAt = _clockNow;
        _resting = false;
        _demoMood = null;
        _focusMood = IrumaeMood.slumped;
        _moodUntil = _clockNow.add(const Duration(seconds: 10));
      });

  Future<void> _volumePopup() => showDialog<void>(
      context: context,
      builder: (context) => StatefulBuilder(
          builder: (context, refresh) => PopupTapRegion(
              dismiss: true,
              child: AlertDialog(
                title: const PopupTapRegion(child: Text('볼륨')),
                content: PopupTapRegion(
                    child: SizedBox(
                        width: 340,
                        child: Row(children: [
                          const Icon(Icons.volume_down_rounded),
                          Expanded(
                              child: Slider(
                                  value: widget.music.volume.clamp(0, 1),
                                  onChanged: (v) async {
                                    await _musicAction(
                                        () => widget.music.setVolume(v));
                                    if (context.mounted) refresh(() {});
                                  })),
                          const Icon(Icons.volume_up_rounded),
                        ]))),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('닫기'))
                ],
              ))));

  Widget _devices() {
    if (_deviceDetail == 'music') {
      if (!widget.musicAvailable) {
        return Center(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.music_note_outlined, size: 42),
          const SizedBox(height: 16),
          const Text('등록된 음원이 없어요'),
          TextButton(
              onPressed: () => _goPage('devices'), child: const Text('기기 목록')),
        ]));
      }
      return MusicDevicePage(
          nickname: _prefs.musicNickname,
          status: _musicStatus,
          playerBuilder: _musicPlayer,
          favorites: _prefs.favoriteTracks,
          playlists: _prefs.playlists,
          favorite: _prefs.favoriteTracks.contains(widget.music.selectedTrack),
          onFavorite: _toggleFavoriteTrack,
          onVolume: _volumePopup,
          onBack: () => _goPage('devices'),
          onRename: () => _renameDevice(true),
          onTrack: (index) => _playQueue(
              List.generate(classicalPlaylist.length, (i) => i)
                  .skip(index)
                  .followedBy(List.generate(index, (i) => i))
                  .toList()),
          onPlayQueue: _playQueue,
          onSavePlaylist: (playlist) {
            setState(() {
              _prefs.playlists.removeWhere((p) => p.id == playlist.id);
              _prefs.playlists.add(playlist);
            });
            _save();
          },
          onDeletePlaylist: (id) {
            setState(() => _prefs.playlists.removeWhere((p) => p.id == id));
            _save();
          });
    }
    if (_deviceDetail == 'lamp') {
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          IconButton(
              tooltip: '기기 목록',
              onPressed: () => _goPage('devices'),
              icon: const Icon(Icons.arrow_back_rounded)),
          Expanded(
              child: Text(_prefs.lampNickname,
                  style: const TextStyle(
                      fontSize: 26, fontWeight: FontWeight.w600))),
          Text(_lampStatus, style: TextStyle(fontSize: 12, color: _muted)),
          IconButton(
              tooltip: '별명 변경',
              onPressed: () => _renameDevice(false),
              icon: const Icon(Icons.edit_outlined, size: 19))
        ]),
        const SizedBox(height: 12),
        Expanded(
            child: SingleChildScrollView(
                child: Align(
                    alignment: Alignment.topCenter,
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 720),
                        child: LampControlCard(
                            level: _lampLevel,
                            tone: _lampTone,
                            busy: _lampBusy,
                            onLevel: (v) => setState(() => _lampLevel = v),
                            onLevelEnd: (v) => _setLampStep(v),
                            onTone: (v) => setState(() => _lampTone = v),
                            onToneEnd: (v) => _setLampStep(v, tone: true),
                            hardwareFiveSteps:
                                widget.feedback.lamp.configured))))),
      ]);
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _heading(
          '기기 제어',
          '',
          _button('자동 제어 내역', _showHistory,
              icon: Icons.history_rounded,
              key: const ValueKey('workspace-history'))),
      Expanded(
          child: LayoutBuilder(
              builder: (context, box) => GridView.count(
                      crossAxisCount: (box.maxWidth / 220).floor().clamp(1, 4),
                      mainAxisSpacing: 16,
                      crossAxisSpacing: 16,
                      children: [
                        DevicePreviewCard(
                            key: const ValueKey('device-card-lamp'),
                            music: false,
                            nickname: _prefs.lampNickname,
                            status: _lampStatus,
                            onTap: () => _goPage('devices', device: 'lamp')),
                        DevicePreviewCard(
                            key: const ValueKey('device-card-music'),
                            music: true,
                            nickname: _prefs.musicNickname,
                            status: _musicStatus,
                            onTap: () => _goPage('devices', device: 'music')),
                        // V18: 보드가 선풍기 상태를 알려 줬을 때만. 조작은 deskmate_display 가 맡는다.
                        if (widget.deviceStatus['vent_fan'] case final fan?)
                          DevicePreviewCard(
                              key: const ValueKey('device-card-fan'),
                              music: false,
                              icon: Icons.air_rounded,
                              kindLabel: '블루투스 선풍기',
                              nickname: '선풍기',
                              status: fan,
                              onTap: () =>
                                  _message('선풍기는 책상 상태에 맞춰 자동으로 움직여요.')),
                      ]))),
      Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
              onPressed: () {
                widget.onConnections();
                setState(() => _page = 'developer');
              },
              icon: const Icon(Icons.bluetooth_rounded, size: 18),
              label: const Text('기기 연결'))),
    ]);
  }

  Future<void> _renameDevice(bool music) async {
    final text = TextEditingController(
        text: music ? _prefs.musicNickname : _prefs.lampNickname);
    final result = await showDialog<String>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('별명 변경'),
                content: TextField(
                    controller: text,
                    maxLength: 20,
                    decoration: const InputDecoration(labelText: '별명')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('취소')),
                  FilledButton(
                      onPressed: () {
                        if (text.text.trim().isNotEmpty) {
                          Navigator.pop(context, text.text.trim());
                        }
                      },
                      child: const Text('저장'))
                ]));
    Future<void>.delayed(const Duration(milliseconds: 400), text.dispose);
    if (result == null || !mounted) return;
    setState(() {
      if (music) {
        _prefs.musicNickname = result;
      } else {
        _prefs.lampNickname = result;
      }
    });
    _save();
  }

  Widget _musicPlayer(VoidCallback onPlaylist) {
    final music = widget.music;
    final timeline = music is MusicTimeline ? music as MusicTimeline : null;
    return HomeMusicTile(
        title: classicalTrackTitles[music.selectedTrack],
        playing: music.isPlaying,
        position: timeline?.position ?? Duration.zero,
        duration: timeline?.duration,
        favorite: _prefs.favoriteTracks.contains(music.selectedTrack),
        onFavorite: _toggleFavoriteTrack,
        onVolume: _volumePopup,
        onPlaylist: onPlaylist,
        onToggle: () => _musicAction(() => music.toggle()),
        onPrevious: () => _stepTrack(-1),
        onNext: () => _stepTrack(1),
        onSeek: timeline == null
            ? null
            : (position) => _musicAction(() => timeline.seek(position)));
  }

  void _toggleFavoriteTrack() {
    setState(() {
      if (!_prefs.favoriteTracks.remove(widget.music.selectedTrack)) {
        _prefs.favoriteTracks.add(widget.music.selectedTrack);
      }
    });
    _save();
  }

  Widget _settings() =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _heading('내 방식으로', '보이는 모습과 사용하는 기능을 골라요.'),
        _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          _sectionTitle(Icons.palette_outlined, '화면'),
          const SizedBox(height: 18),
          Wrap(
              spacing: 9,
              runSpacing: 9,
              children: [
                ('auto', Icons.brightness_auto, '자동'),
                ('light', Icons.light_mode_outlined, '라이트'),
                ('dark', Icons.dark_mode_outlined, '다크')
              ]
                  .map((p) => ChoiceChip(
                      avatar: Icon(p.$2, size: 17),
                      label: Text(p.$3),
                      selected: _prefs.appearance == p.$1,
                      onSelected: (_) {
                        setState(() => _prefs.appearance = p.$1);
                        _save();
                      }))
                  .toList()),
        ])),
        const SizedBox(height: 18),
        _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SwitchListTile(
              key: const ValueKey('workspace-auto-master'),
              contentPadding: EdgeInsets.zero,
              secondary: Icon(Icons.auto_awesome_outlined, color: _accent),
              title: const Text('자동 제어'),
              subtitle:
                  Text(_prefs.automatic ? '사용할 기기를 선택하세요.' : '자동 제어가 꺼져 있어요.'),
              value: _prefs.automatic,
              onChanged: (v) {
                setState(() => _prefs.automatic = v);
                _save();
              }),
          const Divider(),
          SwitchListTile(
              key: const ValueKey('workspace-auto-lamp'),
              contentPadding: EdgeInsets.zero,
              secondary: const Icon(Icons.desk_outlined),
              title: const Text('조명'),
              subtitle: const Text('전원 · 밝기'),
              value: _prefs.lampAutomatic,
              onChanged: !_prefs.automatic
                  ? null
                  : (v) {
                      setState(() => _prefs.lampAutomatic = v);
                      _save();
                    }),
          SwitchListTile(
              key: const ValueKey('workspace-auto-speaker'),
              contentPadding: EdgeInsets.zero,
              secondary: const Icon(Icons.speaker_outlined),
              title: const Text('책상 스피커'),
              subtitle: const Text('재생 · 볼륨'),
              value: _prefs.speakerAutomatic,
              onChanged: !_prefs.automatic
                  ? null
                  : (v) {
                      setState(() => _prefs.speakerAutomatic = v);
                      _save();
                    }),
        ])),
        const SizedBox(height: 18),
        _card(Wrap(spacing: 15, runSpacing: 12, children: [
          _button('기기 연결', () {
            widget.onConnections();
            setState(() => _page = 'developer');
          }, icon: Icons.bluetooth_rounded),
          _button('개발자 화면', () => setState(() => _page = 'developer'),
              icon: Icons.code_rounded,
              key: const ValueKey('workspace-developer')),
        ])),
      ]);
  Future<void> _showHistory() => showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
            title: Row(children: [
              const Icon(Icons.history_rounded),
              const SizedBox(width: 10),
              const Expanded(child: Text('자동 제어 내역')),
              IconButton(
                  tooltip: '닫기',
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close))
            ]),
            content: SizedBox(
                width: 600,
                child: _records.isEmpty
                    ? const Padding(
                        padding: EdgeInsets.all(20),
                        child: Text('아직 자동 제어 내역이 없어요.'))
                    : ListView.separated(
                        shrinkWrap: true,
                        itemCount: _records.length,
                        separatorBuilder: (_, __) => const Divider(),
                        itemBuilder: (context, i) {
                          final record = _records[i];
                          return ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(record.succeeded
                                  ? record.device == 'lamp'
                                      ? Icons.desk_outlined
                                      : Icons.speaker_outlined
                                  : Icons.error_outline),
                              title: Text(record.title),
                              subtitle: Text(
                                  '${record.detail}\n${_time(record.time)} · ${record.succeeded ? '완료' : '실패'}'));
                        })),
          ));
  Widget _noticeCard() {
    final record = _notice!;
    return Positioned(
        top: 82,
        right: 18,
        left: MediaQuery.sizeOf(context).width < 550 ? 18 : null,
        child: SizedBox(
            width: MediaQuery.sizeOf(context).width < 550 ? null : 380,
            child: Material(
                color: _surface,
                elevation: 12,
                borderRadius: BorderRadius.circular(24),
                child: Padding(
                    padding: const EdgeInsets.all(21),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Row(children: [
                            Icon(
                                record.succeeded
                                    ? Icons.check_circle_outline
                                    : Icons.error_outline,
                                color: _accent,
                                size: 19),
                            const SizedBox(width: 8),
                            Expanded(
                                child: Text(
                                    record.succeeded ? '자동 제어 완료' : '자동 제어 실패',
                                    style: TextStyle(
                                        color: _muted, fontSize: 12))),
                            IconButton(
                                tooltip: '알림 닫기',
                                onPressed: () => setState(() => _notice = null),
                                icon: const Icon(Icons.close, size: 18))
                          ]),
                          Text(record.title,
                              style: const TextStyle(
                                  fontSize: 19, fontWeight: FontWeight.w500)),
                          const SizedBox(height: 8),
                          Text(record.detail,
                              style: TextStyle(color: _muted, fontSize: 13)),
                          const SizedBox(height: 12),
                          Wrap(spacing: 8, children: [
                            _button('내역 보기', () {
                              setState(() => _notice = null);
                              _showHistory();
                            }, icon: Icons.history),
                            if (record.succeeded &&
                                !widget.demo &&
                                widget.state?.gate == 'auto')
                              TextButton(
                                  onPressed: () {
                                    widget.onUndo();
                                    setState(() => _notice = null);
                                  },
                                  child: const Text('되돌리기 요청'))
                          ]),
                          if (record.succeeded)
                            Padding(
                                padding: const EdgeInsets.only(top: 10),
                                child: Text('8초 뒤 닫힘',
                                    style: TextStyle(
                                        fontSize: 11, color: _muted))),
                        ])))));
  }

  Widget _suggestion() => Positioned(
      top: 82,
      right: 18,
      left: MediaQuery.sizeOf(context).width < 550 ? 18 : null,
      child: SizedBox(
          width: MediaQuery.sizeOf(context).width < 550 ? null : 380,
          child: _card(Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _sectionTitle(Icons.lightbulb_outline, '책상 환경을 조절할까요?'),
                const SizedBox(height: 12),
                const Text('적용 여부를 선택해주세요.'),
                const SizedBox(height: 16),
                Wrap(spacing: 10, children: [
                  _button('적용', widget.onAccept, primary: true),
                  _button('지금은 유지', widget.onReject)
                ]),
              ]))));
}
