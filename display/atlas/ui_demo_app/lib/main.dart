import 'dart:async';
import 'dart:convert';
import 'dart:io' show exit;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'demo_model.dart';
import 'profile_avatars.dart';
import 'profile_picker.dart';
import 'personal_settings.dart';
import 'page_transition.dart';
import 'pr56_workspace_view.dart';
import 'live/home_items.dart';
import 'live/link_settings.dart';
import 'live/live_link.dart';
import 'live/live_request.dart';
import 'pr56/co2_grade.dart';
import 'pr56/display_state.dart';

const ink = Color(0xFF263B37);
const paper = Color(0xFFF4F2EB);
const green = Color(0xFF356C59);
const muted = Color(0xFF7A8580);
const line = Color(0xFFE1E5DC);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final values =
      jsonDecode(await rootBundle.loadString('assets/config/demo.json'))
          as Map<String, dynamic>;
  // V18: 보드 주소가 저장돼 있으면 연결을 시작한다. 없거나 닿지 않으면 메모리 동작 그대로.
  final live = LiveLink(store: LinkSettingsStore())..start();
  runApp(DemoApp(model: DemoModel(DemoConfig(values)), live: live));
}

void _exitDemo() => exit(0);

class DemoApp extends StatefulWidget {
  const DemoApp(
      {super.key,
      required this.model,
      this.ticking = true,
      this.onExit = _exitDemo,
      this.live});
  final DemoModel model;
  final bool ticking;

  /// 보드 연결(V18). null 이면 V17 과 같은 메모리 전용 화면.
  final LiveLink? live;
  final VoidCallback onExit;
  @override
  State<DemoApp> createState() => _DemoAppState();
}

class _DemoAppState extends State<DemoApp> {
  Timer? timer;
  @override
  void initState() {
    super.initState();
    final live = widget.live;
    final model = widget.model;
    if (live != null) {
      live.onState =
          (state) => model.followLive(state, postureLabel: postureText(state));
      model.onProfileChanged = live.publishProfile;
      model.onPlacement = live.sendPlacement;
      live.publishProfile(model.persona);
    }
    if (widget.ticking) {
      timer = Timer.periodic(const Duration(seconds: 1), (_) {
        model.tick();
        // 연결이 끊기거나 상태가 오래되면 화면은 메모리 흐름으로 돌아간다.
        if (live != null && !live.live) model.leaveLive();
      });
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          fontFamily: 'High1',
          fontFamilyFallback: const ['LGText'],
          colorScheme: ColorScheme.fromSeed(seedColor: green, surface: paper),
          scaffoldBackgroundColor: paper,
          textTheme: ThemeData.light()
              .textTheme
              .apply(fontFamily: 'High1', bodyColor: ink, displayColor: ink),
          filledButtonTheme: FilledButtonThemeData(
              style: FilledButton.styleFrom(
                  backgroundColor: green,
                  foregroundColor: Colors.white,
                  minimumSize: const Size(100, 48),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(16)))),
          outlinedButtonTheme: OutlinedButtonThemeData(
              style: OutlinedButton.styleFrom(
                  minimumSize: const Size(90, 48),
                  foregroundColor: ink,
                  side: const BorderSide(color: line),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(16)))),
        ),
        home: Scaffold(
            body: ColoredBox(
                color: paper,
                child: Center(
                    child: FittedBox(
                  fit: BoxFit.contain,
                  child: SizedBox(
                      width: 1024,
                      height: 600,
                      child: DemoShell(
                          model: widget.model,
                          onExit: widget.onExit,
                          live: widget.live)),
                )))),
      );
}

class DemoShell extends StatefulWidget {
  final VoidCallback onExit;
  const DemoShell(
      {super.key, required this.model, required this.onExit, this.live});
  final DemoModel model;
  final LiveLink? live;
  @override
  State<DemoShell> createState() => _DemoShellState();
}

class _DemoShellState extends State<DemoShell> {
  final workspaceSession = WorkspaceDemoSession();
  Co2Grades? _grades;

  @override
  void initState() {
    super.initState();
    Co2Grades.load().then((grades) {
      if (mounted) setState(() => _grades = grades);
    }, onError: (Object _) {});
  }

  /// 보드 값을 쓸 수 있으면 그 상태, 아니면 null(메모리 값으로 그린다).
  DisplayState? get _live =>
      widget.live?.live == true ? widget.live!.state : null;

  @override
  void dispose() {
    workspaceSession.dispose();
    super.dispose();
  }

  DemoModel get m => widget.model;
  String two(int n) => n.toString().padLeft(2, '0');
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
      animation: widget.live == null ? m : Listenable.merge([m, widget.live]),
      builder: (context, _) {
        workspaceSession.syncPreferences(m);
        final live = widget.live;
        final request =
            live?.status == LinkStatus.connected ? live!.request : null;
        return Stack(children: [
          Positioned.fill(
              child: PageTransitionView(pageKey: m.page, child: _page())),
          if (request != null)
            Positioned.fill(
                child: KeyedSubtree(
                    key: ValueKey('live-card-${request.requestId}'),
                    child: _liveCard(request))),
        ]);
      });

  /// 보드가 보낸 확인 질문. 답하면 바로 닫히고 답변 문구만 상단에 잠깐 남는다(V15 카드 규칙).
  Widget _liveCard(LiveRequest request) {
    final card = request.card(widget.live!.state);
    return _suggestionCard(title: card.title, subtitle: card.body, actions: [
      OutlinedButton(
          key: const ValueKey('live-reject'),
          onPressed: () => _answerLive(false),
          child: Text(card.reject)),
      const SizedBox(width: 16),
      FilledButton(
          key: const ValueKey('live-accept'),
          onPressed: () => _answerLive(true),
          child: Text(card.accept)),
    ]);
  }

  void _answerLive(bool accepted) {
    unawaited(widget.live!.answer(accepted ? 'accept' : 'reject'));
    m.liveAnswered(accepted);
  }

  Widget _linkDot(LiveLink live) {
    final (color, text) = live.live
        ? live.hubHealth == 'offline'
            ? (const Color(0xFFC9A15A), '책상 응답을 기다려요')
            : (green.withValues(alpha: .7), '책상과 이어져 있어요')
        : (muted.withValues(alpha: .45), '책상을 찾는 중이에요');
    return Tooltip(
        message: text,
        child: Container(
            key: const ValueKey('link-dot'),
            width: 8,
            height: 8,
            margin: const EdgeInsets.only(right: 10),
            decoration: BoxDecoration(color: color, shape: BoxShape.circle)));
  }

  Widget _page() {
    if (m.page == DemoPage.focus) return _focus();
    if (m.page == DemoPage.widgets) {
      return Pr56WorkspaceView(
          model: m,
          session: workspaceSession,
          onExit: widget.onExit,
          onPreferences: _openPreferences,
          live: widget.live);
    }
    return Listener(
        onPointerDown: (_) => m.touch(),
        child: ColoredBox(
          color: paper,
          child: Padding(
              padding: const EdgeInsets.fromLTRB(32, 20, 32, 20),
              child: Column(children: [
                _header(),
                const SizedBox(height: 14),
                Expanded(
                    child: switch (m.page) {
                  DemoPage.home => _home(),
                  DemoPage.details => _details(),
                  DemoPage.widgets => const SizedBox(),
                  DemoPage.report => _report(),
                  DemoPage.focus => const SizedBox(),
                }),
              ])),
        ));
  }

  Widget _header() => SizedBox(
      height: 44,
      child: Row(children: [
        const Spacer(),
        if (widget.live case final live? when live.configured) _linkDot(live),
        if (m.page != DemoPage.home)
          TextButton.icon(
              onPressed: () =>
                  m.show(m.active ? DemoPage.details : DemoPage.home),
              icon: const Icon(Icons.arrow_back_rounded, size: 18),
              label: Text(m.active ? '집중으로' : '시계로')),
        TextButton.icon(
            onPressed: m.active ? null : _openProfiles,
            icon: ProfileAvatar(id: m.persona.avatar, size: 28),
            label: Text('${m.persona.name} · ${m.persona.job}')),
        const SizedBox(width: 6),
        IconButton(
            tooltip: '나의 책상 설정',
            onPressed: _openPreferences,
            icon: const Icon(Icons.settings_outlined)),
        IconButton(
            tooltip: '발표 조작창',
            onPressed: _controls,
            icon: const Icon(Icons.tune_rounded)),
        IconButton(
            tooltip: '앱 종료',
            onPressed: widget.onExit,
            icon: const Icon(Icons.close_rounded)),
      ]));

  Widget _pill(String label, Color color) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
          color: color.withValues(alpha: .09),
          borderRadius: BorderRadius.circular(30)),
      child: Text(label,
          style: TextStyle(
              fontSize: 12, color: color, fontWeight: FontWeight.w600)));

  Widget _clock({bool dark = false, double size = 132}) => Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Text(two(m.now.hour),
                style: TextStyle(
                    fontFamily: 'LGClock',
                    fontSize: size,
                    height: 1,
                    color: dark ? const Color(0xFFD2D8CF) : ink)),
            Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Text(':',
                    style: TextStyle(
                        fontFamily: 'LGClock',
                        fontSize: size * .73,
                        height: 1,
                        color: dark
                            ? const Color(0xFF829283)
                            : green.withValues(alpha: .5)))),
            Text(two(m.now.minute),
                style: TextStyle(
                    fontFamily: 'LGClock',
                    fontSize: size,
                    height: 1,
                    color: dark ? const Color(0xFFD2D8CF) : ink)),
          ]);

  Widget _home() => GestureDetector(
        onHorizontalDragEnd: (d) {
          if ((d.primaryVelocity ?? 0) < -100) m.show(DemoPage.widgets);
        },
        child: Stack(children: [
          if (!m.proposal && m.notice.isNotEmpty)
            Positioned(
                top: 0,
                left: 0,
                right: 0,
                height: 82,
                child: Center(
                    child: Text(m.notice,
                        style: const TextStyle(fontSize: 18, color: green)))),
          const Positioned(
              top: 96,
              left: 0,
              right: 0,
              child: Center(
                  child: Text('오늘도, 당신의 속도로.',
                      key: ValueKey('home-message'),
                      style: TextStyle(
                          fontSize: 25, fontWeight: FontWeight.w600)))),
          Positioned(
              top: 140,
              left: 0,
              right: 0,
              height: 164,
              child: Center(
                  child: Semantics(
                      button: true,
                      label: '시계를 눌러 집중 시작',
                      child: GestureDetector(
                          key: const ValueKey('home-clock-start'),
                          behavior: HitTestBehavior.opaque,
                          onTap: () => m.start(),
                          child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: _clock()))))),
          Positioned(
              top: 324,
              left: 0,
              right: 0,
              child: Center(
                  child: Text(
                      '${m.now.month}월 ${m.now.day}일  ${[
                        '월요일',
                        '화요일',
                        '수요일',
                        '목요일',
                        '금요일',
                        '토요일',
                        '일요일'
                      ][m.now.weekday - 1]}',
                      key: const ValueKey('home-date'),
                      style: const TextStyle(
                          color: muted, fontSize: 17, letterSpacing: 2)))),
          if (m.scenario)
            Positioned(
                top: 370,
                left: 0,
                right: 0,
                child: Center(
                    child: Text(
                        '작업 징후를 살펴보는 중 · ${m.workTicks}/${m.config.seconds('workCueSeconds')}초',
                        style: const TextStyle(color: muted, fontSize: 14)))),
          Positioned(bottom: 0, left: 0, right: 0, child: _statusStrip()),
          if (m.proposal)
            Positioned(
                top: -78,
                bottom: -20,
                left: -32,
                right: -32,
                child: _proposal()),
        ]),
      );
  Widget _suggestionCard(
          {required String title,
          String? subtitle,
          required List<Widget> actions}) =>
      GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () {},
          child: ColoredBox(
              color: paper.withValues(alpha: .45),
              child: Center(
                  child: Container(
                key: const ValueKey('suggestion-card'),
                width: 600,
                padding: const EdgeInsets.all(32),
                decoration: BoxDecoration(
                    color: const Color(0xFFFFFEF8),
                    borderRadius: BorderRadius.circular(28),
                    border: Border.all(color: const Color(0xFFD5DFCF)),
                    boxShadow: [
                      BoxShadow(
                          color: ink.withValues(alpha: .18),
                          blurRadius: 36,
                          offset: const Offset(0, 12))
                    ]),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.auto_awesome_outlined,
                      color: green, size: 32),
                  const SizedBox(height: 18),
                  Text(title,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          color: ink, fontSize: 27, height: 1.4)),
                  if (subtitle != null) ...[
                    const SizedBox(height: 12),
                    Text(subtitle,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: muted, fontSize: 16)),
                  ],
                  if (actions.isNotEmpty) ...[
                    const SizedBox(height: 26),
                    Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: actions),
                  ],
                ]),
              ))));

  Widget _proposal() => _suggestionCard(
          title: '지금, 집중을 시작할까요?',
          subtitle:
              '아직 알아가는 중이에요 · 확인 ${m.persona.rounds}/${m.config.seconds('feedbackRounds')}회',
          actions: [
            OutlinedButton(onPressed: m.decline, child: const Text('나중에')),
            const SizedBox(width: 16),
            FilledButton(
                onPressed: () => m.start(), child: const Text('시작할게요')),
          ]);

  Widget _statusStrip() {
    final s = _live;
    return Container(
        height: 64,
        decoration: BoxDecoration(
            border:
                Border(top: BorderSide(color: ink.withValues(alpha: .12)))),
        child:
            Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          _status(Icons.air_rounded, '공기',
              s == null ? '${m.config.seconds('co2')} ppm' : airText(s, _grades)),
          _status(
              Icons.thermostat_rounded,
              '실내',
              s == null
                  ? '${m.config.number('temperature')}° · ${m.config.seconds('humidity')}%'
                  : roomText(s)),
          _status(Icons.accessibility_new_rounded, '자세', m.posture),
          _status(
              Icons.spa_outlined,
              '컨디션',
              s == null
                  ? (m.fatigueEvents > 0 ? '환기해요' : '편안해요')
                  : conditionText(s)),
        ]));
  }

  Widget _status(IconData icon, String name, String value) => Flexible(
      child: InkWell(
          onTap: () => m.show(DemoPage.widgets),
          borderRadius: BorderRadius.circular(14),
          child: Padding(
              padding: const EdgeInsets.all(10),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(icon, size: 23, color: green),
                const SizedBox(width: 10),
                Text('$name  ',
                    style: const TextStyle(color: muted, fontSize: 13)),
                Flexible(
                    child: Text(value,
                        key: ValueKey('status-$name'),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 14)))
              ]))));

  Widget _focus() => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => m.show(DemoPage.details),
        onLongPress: () => m.show(DemoPage.widgets),
        child: ColoredBox(
            color: const Color(0xFF101B19),
            child: Stack(children: [
              Center(child: _clock(dark: true, size: 160)),
              if (m.notice.isNotEmpty && !m.askingFeedback)
                Positioned(
                    top: 34,
                    left: 100,
                    right: 100,
                    child: Center(
                        child: Text(m.notice,
                            style: const TextStyle(
                                color: Color(0xFFE3EBD9), fontSize: 18)))),
              if (m.notice.isNotEmpty && m.askingFeedback)
                Positioned.fill(
                    child: _suggestionCard(
                  title: m.notice,
                  actions: [
                    OutlinedButton(
                        onPressed: () => m.feedback(false),
                        child: const Text('다른 방법')),
                    const SizedBox(width: 16),
                    FilledButton(
                        onPressed: () => m.feedback(true),
                        child:
                            Text(m.pendingAction == null ? '도움 됐어요' : '좋아요')),
                  ],
                )),
              Positioned(
                  right: 20,
                  top: 20,
                  child: IconButton(
                      tooltip: '앱 종료',
                      onPressed: widget.onExit,
                      color: const Color(0xFF829283),
                      icon: const Icon(Icons.close_rounded))),
            ])),
      );

  Widget _details() =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const SizedBox(height: 8),
        Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(
                    m.absent
                        ? '잠깐 자리를 비우셨네요'
                        : m.resting
                            ? '잠깐 쉬어가는 중이에요'
                            : '당신의 몰입을 지키는 중',
                    style: const TextStyle(fontSize: 28)),
                const SizedBox(height: 8),
                Text(
                    m.absent
                        ? m.liveDriven
                            ? '돌아오면 이어서 함께해요 · 경과 시간 일시정지'
                            : '${m.config.seconds('absenceGraceSeconds') - m.absentTicks}초 후 세션 종료 · 경과 시간 일시정지'
                        : m.resting
                            ? '쉬는 동안은 함께한 시간을 멈춰 둘게요.'
                            : '화면을 건드리지 않으면 조용한 시계로 돌아가요.',
                    style: const TextStyle(color: muted, fontSize: 14)),
              ])),
          _pill(
              m.absent
                  ? '일시정지'
                  : m.resting
                      ? '쉬는 중'
                      : m.liveSession
                          ? '목표 ${m.persona.preferences.focusMinutes}분'
                          : '목표 ${m.persona.preferences.focusMinutes}분 · 60배속',
              green),
        ]),
        const SizedBox(height: 22),
        Expanded(
            child:
                Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Expanded(
              flex: 5,
              child: _tile(
                  child:
                      Stack(key: const ValueKey('focus-time-card'), children: [
                const Align(
                    alignment: Alignment.topLeft,
                    child: Text('함께 집중한 시간',
                        style: TextStyle(color: muted, fontSize: 16))),
                Center(
                    child: FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Text(m.elapsed,
                            key: const ValueKey('focus-elapsed-clock'),
                            style: const TextStyle(
                                fontFamily: 'LGClock',
                                fontSize: 66,
                                color: ink)))),
                Align(
                    alignment: Alignment.bottomLeft,
                    child: Row(children: [
                      const Icon(Icons.light_mode_outlined,
                          size: 18, color: green),
                      const SizedBox(width: 9),
                      Text(
                          '${m.persona.job} · ${(_live == null ? null : widget.live!.devices.summary) ?? '조명 ${(m.brightness * 100).round()}%'} · ${m.persona.preferences.musicEnabled ? m.persona.sound : '고요한 집중'}',
                          style: const TextStyle(fontSize: 13)),
                    ])),
              ]))),
          const SizedBox(width: 18),
          Expanded(
              flex: 4,
              child: Column(children: [
                Expanded(
                    child: _tile(
                        child: Row(
                            key: const ValueKey('desk-environment-card'),
                            children: [
                      const Icon(Icons.eco_outlined, color: green, size: 42),
                      const SizedBox(width: 18),
                      Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                            const Text('책상 주변', style: TextStyle(color: muted)),
                            const SizedBox(height: 8),
                            Text(
                                _live == null
                                    ? '${m.config.number('temperature')}° · ${m.config.seconds('humidity')}%'
                                    : deskText(_live!).main,
                                style: const TextStyle(fontSize: 20)),
                            const SizedBox(height: 6),
                            Text(
                                _live == null
                                    ? 'CO₂ ${m.config.seconds('co2')} ppm · ${m.config.seconds('lux')} lx'
                                    : deskText(_live!).sub,
                                style: const TextStyle(
                                    color: muted, fontSize: 12)),
                          ])),
                    ]))),
                const SizedBox(height: 16),
                Expanded(
                    child: _tile(
                        color: const Color(0xFFE6EBDD),
                        child: Row(
                            key: const ValueKey('desk-posture-card'),
                            children: [
                              const Icon(Icons.accessibility_new_rounded,
                                  color: green, size: 42),
                              const SizedBox(width: 18),
                              Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    const Text('지금 자세',
                                        style: TextStyle(color: muted)),
                                    const SizedBox(height: 8),
                                    Text(m.posture,
                                        style: const TextStyle(fontSize: 20))
                                  ])
                            ]))),
              ])),
        ])),
        const SizedBox(height: 22),
        Row(children: [
          OutlinedButton.icon(
              onPressed: () => m.show(DemoPage.widgets),
              icon: const Icon(Icons.grid_view_rounded, size: 18),
              label: const Text('멀티 위젯')),
          const SizedBox(width: 12),
          OutlinedButton(
              onPressed: m.absent ? m.resume : () => m.show(DemoPage.focus),
              child: Text(m.absent ? '돌아왔어요' : '조용한 시계로')),
          const Spacer(),
          TextButton(
              onPressed: () {
                m.fatigue();
                m.show(DemoPage.focus);
              },
              child: const Text('잠깐 환기하기')),
          const SizedBox(width: 12),
          FilledButton.icon(
              onPressed: m.finish,
              icon: const Icon(Icons.stop_rounded),
              label: const Text('작업 종료')),
        ]),
        const SizedBox(height: 10),
      ]);

  /// 리포트의 피로 횟수. 보드 리포트가 있으면 그 값(hub 기준), 없으면 화면이 센 값.
  int get _fatigueCount => _live == null
      ? m.fatigueEvents
      : (widget.live!.report?.fatigueEpisodes ?? m.fatigueEvents);

  /// 마무리 카드의 기기 상태. 보드가 알려 준 상태가 있으면 그것, 없으면 기존 'OFF'.
  String _deviceAfter(String target, String fallbackName) {
    final known = _live == null ? null : widget.live!.devices.text(target);
    return known == null ? '$fallbackName OFF' : '$fallbackName $known';
  }

  Widget _tile(
          {required Widget child,
          Color color = Colors.white,
          EdgeInsets padding = const EdgeInsets.all(22)}) =>
      Container(
          padding: padding,
          decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: line.withValues(alpha: .65))),
          child: child);

  Future<void> _openProfiles() async {
    final action = await showDialog<String>(
      context: context,
      barrierDismissible: false,
      barrierColor: Colors.black.withValues(alpha: .32),
      builder: (context) => ProfilePicker(model: m),
    );
    if (action == 'preferences' && mounted) await _openPreferences();
  }

  Future<void> _openPreferences() async {
    m.preferencesOpen = true;
    try {
      await showDialog<void>(
          context: context,
          barrierDismissible: false,
          barrierColor: Colors.black.withValues(alpha: .32),
          builder: (_) => PersonalSettings(model: m, live: widget.live));
    } finally {
      m.preferencesOpen = false;
      m.touch();
    }
  }

  Widget _report() =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const SizedBox(height: 8),
        Row(children: [
          const Text('오늘도, 한 걸음 쌓았어요.', style: TextStyle(fontSize: 30)),
          const Spacer(),
          _pill('세션 요약', green)
        ]),
        const SizedBox(height: 10),
        const Text('집중의 질을 점수로 단정하지 않고, 이번 시간을 짧게 돌아봐요.',
            style: TextStyle(color: muted, fontSize: 15)),
        const SizedBox(height: 24),
        SizedBox(
            height: 150,
            child:
                Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Expanded(
                  child: _tile(
                      color: const Color(0xFFE5EBDC),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('함께한 시간',
                                style: TextStyle(color: muted)),
                            const Spacer(),
                            FittedBox(
                                fit: BoxFit.scaleDown,
                                child: Text(m.elapsed,
                                    style: const TextStyle(
                                        fontFamily: 'LGClock', fontSize: 48))),
                          ]))),
              const SizedBox(width: 16),
              Expanded(
                  child: _tile(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                    const Text('잠깐의 피로', style: TextStyle(color: muted)),
                    const Spacer(),
                    Text('$_fatigueCount회',
                        style: const TextStyle(fontSize: 36)),
                    Text(_fatigueCount == 0 ? '이번에는 없었어요' : '선호 순서에 맞춰 반응했어요',
                        style: const TextStyle(color: muted, fontSize: 12)),
                  ]))),
              const SizedBox(width: 16),
              Expanded(
                  child: _tile(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                    const Text('환경 · 마무리', style: TextStyle(color: muted)),
                    const Spacer(),
                    Text(_deviceAfter('desk_lamp', '조명'),
                        style: const TextStyle(fontSize: 27)),
                    const SizedBox(height: 7),
                    Text(
                        '${_deviceAfter('vent_fan', '선풍기')} · 조정 ${_live == null ? m.interventionStep : (widget.live!.report?.interventions ?? m.interventionStep)}회',
                        style: const TextStyle(color: muted, fontSize: 13)),
                  ]))),
            ])),
        const SizedBox(height: 20),
        _tile(
            child: Row(children: [
          const Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text('오늘 피곤했나요?', style: TextStyle(fontSize: 23)),
                SizedBox(height: 7),
                Text('다음에 더 잘 도울 수 있게, 한 번만 알려주세요.',
                    style: TextStyle(color: muted, fontSize: 13)),
              ])),
          for (final value in ['괜찮았어요', '조금 피곤해요', '많이 피곤해요'])
            Padding(
                padding: const EdgeInsets.only(left: 8),
                child: ChoiceChip(
                    selected: m.feeling == value,
                    label: Text(value),
                    onSelected: (_) => m.recordFeeling(value))),
        ])),
        const Spacer(),
        Row(children: [
          Text(
              m.feeling.isEmpty
                  ? '응답은 선택이에요 · 기록은 앱 종료 시 사라집니다.'
                  : '고마워요. 다음 집중을 위한 피드백로 남겼어요.',
              style: const TextStyle(color: muted, fontSize: 13)),
          const Spacer(),
          FilledButton.icon(
              onPressed: () => m.show(DemoPage.home),
              icon: const Icon(Icons.done_rounded),
              label: const Text('시계로 돌아가기')),
        ]),
        const SizedBox(height: 12),
      ]);

  void _controls() {
    showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: paper,
        builder: (sheetContext) => AnimatedBuilder(
            animation: m,
            builder: (context, _) => Padding(
                padding: const EdgeInsets.all(28),
                child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('발표 조작창', style: TextStyle(fontSize: 26)),
                      const SizedBox(height: 8),
                      Text(
                          _live != null
                              ? '책상과 이어져 있어요 · 화면은 책상 상태를 따라가요'
                              : '실제 센서·기기 연결 없음 · 1초 = ${m.config.seconds('simulatedSecondsPerTick')}초 · 작업 진입 ${m.config.seconds('workCueSeconds')}초',
                          style: const TextStyle(color: muted, fontSize: 13)),
                      const SizedBox(height: 20),
                      Wrap(spacing: 10, children: [
                        for (int i = 0; i < m.profiles.length; i++)
                          ChoiceChip(
                              label: Text(
                                  '${m.profiles[i].name} · ${m.profiles[i].rounds >= m.config.seconds('feedbackRounds') ? '개인화됨' : '새 프로필'}'),
                              selected: m.profileIndex == i,
                              onSelected:
                                  m.active ? null : (_) => m.selectProfile(i))
                      ]),
                      const SizedBox(height: 15),
                      Text('개입 순서: ${m.persona.order.join(' → ')}',
                          style: const TextStyle(fontSize: 15)),
                      const SizedBox(height: 8),
                      Text(
                          m.trained
                              ? '신뢰도 ${m.config.number('trainedConfidence')} · ${m.config.number('autoConfidence')} 이상으로 자동 진입'
                              : '초반 ${m.config.seconds('feedbackRounds')}회는 시작 제안과 피드백을 먼저 보여줘요.',
                          style: const TextStyle(color: muted, fontSize: 13)),
                      const SizedBox(height: 22),
                      Wrap(spacing: 12, runSpacing: 12, children: [
                        FilledButton.icon(
                            onPressed: m.active
                                ? null
                                : () {
                                    Navigator.pop(sheetContext);
                                    m.beginScenario();
                                  },
                            icon: const Icon(Icons.play_circle_outline),
                            label: const Text('재실·작업 상황 재생')),
                        OutlinedButton(
                            onPressed: () {
                              Navigator.pop(sheetContext);
                              if (!m.active) {
                                m.start();
                              }
                              m.fatigue();
                              m.show(DemoPage.focus);
                            },
                            child: const Text('피로·졸음 상황')),
                        OutlinedButton(
                            onPressed: () {
                              Navigator.pop(sheetContext);
                              if (!m.active) {
                                m.start();
                              }
                              m.away();
                            },
                            child: const Text('부재 → 일시정지·종료')),
                        TextButton(
                            onPressed: () => Navigator.pop(sheetContext),
                            child: const Text('닫기')),
                      ]),
                      const SizedBox(height: 16),
                    ]))));
  }
}
