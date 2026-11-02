import 'dart:async';
import 'package:flutter/material.dart';
import 'workspace_store.dart';

String clockTime(int seconds) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${seconds >= 3600 ? '${two(seconds ~/ 3600)}:' : ''}${two(seconds ~/ 60 % 60)}:${two(seconds % 60)}';
}

class DeskClockController extends ChangeNotifier {
  DeskClockController(
      {required this.now,
      required this.onSave,
      required this.onAlert,
      required List<DeskAlarm> alarms,
      this.timerSeconds = 300,
      bool autoTick = true})
      : alarms = [...alarms],
        remaining = timerSeconds {
    if (autoTick) {
      _ticker =
          Timer.periodic(const Duration(milliseconds: 100), (_) => tick());
    }
  }
  final DateTime Function() now;
  final VoidCallback onSave;
  final ValueChanged<String> onAlert;
  List<DeskAlarm> alarms;
  int timerSeconds;
  int remaining;
  DateTime? _deadline;
  Timer? _ticker;
  final Stopwatch watch = Stopwatch();
  final List<Duration> laps = [];
  bool get timerRunning => _deadline != null;
  void setTimer(int seconds) {
    if (seconds <= 0 || seconds >= 86400) return;
    _deadline = null;
    remaining = timerSeconds = seconds;
    onSave();
    notifyListeners();
  }

  void toggleTimer() {
    if (_deadline != null) {
      remaining = ((_deadline!.difference(now()).inMilliseconds) / 1000)
          .ceil()
          .clamp(0, timerSeconds);
      _deadline = null;
    } else {
      if (remaining == 0) remaining = timerSeconds;
      _deadline = now().add(Duration(seconds: remaining));
    }
    notifyListeners();
  }

  void resetTimer() {
    _deadline = null;
    remaining = timerSeconds;
    notifyListeners();
  }

  void toggleWatch() {
    watch.isRunning ? watch.stop() : watch.start();
    notifyListeners();
  }

  void resetWatch() {
    watch.stop();
    watch.reset();
    laps.clear();
    notifyListeners();
  }

  void lap() {
    if (watch.isRunning) {
      laps.insert(0, watch.elapsed);
      notifyListeners();
    }
  }

  void saveAlarm(DeskAlarm alarm) {
    alarms.removeWhere((a) => a.id == alarm.id);
    alarms.add(alarm);
    alarms.sort(
        (a, b) => (a.hour * 60 + a.minute).compareTo(b.hour * 60 + b.minute));
    onSave();
    notifyListeners();
  }

  void removeAlarm(String id) {
    alarms.removeWhere((a) => a.id == id);
    onSave();
    notifyListeners();
  }

  void tick() {
    final time = now();
    var changed = watch.isRunning;
    if (_deadline != null) {
      final left = (_deadline!.difference(time).inMilliseconds / 1000)
          .ceil()
          .clamp(0, timerSeconds);
      changed |= left != remaining;
      remaining = left;
      if (remaining == 0) {
        _deadline = null;
        onAlert('타이머가 끝났어요.');
      }
    }
    final day = '${time.year}-${time.month}-${time.day}';
    for (var i = 0; i < alarms.length; i++) {
      final alarm = alarms[i];
      if (alarm.enabled &&
          alarm.hour == time.hour &&
          alarm.minute == time.minute &&
          alarm.lastFiredDay != day) {
        alarms[i] = alarm.copyWith(lastFiredDay: day);
        onSave();
        changed = true;
        onAlert(alarm.label);
      }
    }
    if (changed) notifyListeners();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    watch.stop();
    super.dispose();
  }
}

class DeskClockPage extends StatefulWidget {
  const DeskClockPage({super.key, required this.tools});
  final DeskClockController tools;
  @override
  State<DeskClockPage> createState() => _DeskClockPageState();
}

class _DeskClockPageState extends State<DeskClockPage> {
  int _tab = 0;
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
      animation: widget.tools,
      builder: (context, _) {
        final tools = widget.tools;
        final scheme = Theme.of(context).colorScheme;
        final compact = MediaQuery.sizeOf(context).width < 500;
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('시계',
                  style: TextStyle(fontSize: 27, fontWeight: FontWeight.w600)),
              const SizedBox(height: 14),
              SegmentedButton<int>(
                  showSelectedIcon: false,
                  segments: [
                    ButtonSegment(
                        value: 0,
                        icon: compact ? null : const Icon(Icons.timer_outlined),
                        label: const Text('타이머')),
                    ButtonSegment(
                        value: 1,
                        icon:
                            compact ? null : const Icon(Icons.av_timer_rounded),
                        label: const Text('스톱워치')),
                    ButtonSegment(
                        value: 2,
                        icon: compact ? null : const Icon(Icons.alarm_rounded),
                        label: const Text('알람'))
                  ],
                  selected: {_tab},
                  onSelectionChanged: (v) => setState(() => _tab = v.first),
                  style: ButtonStyle(
                      textStyle: WidgetStateProperty.all(const TextStyle(
                          fontFamily: 'High1Wonchuri', fontSize: 13)))),
              const SizedBox(height: 14),
              Expanded(
                  child: Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                    color: scheme.surface,
                    borderRadius: BorderRadius.circular(26),
                    border: Border.all(
                        color: scheme.primary.withValues(alpha: .18))),
                child: _tab == 2
                    ? _alarms()
                    : LayoutBuilder(
                        builder: (context, box) => SingleChildScrollView(
                            child: ConstrainedBox(
                                constraints:
                                    BoxConstraints(minHeight: box.maxHeight),
                                child: Column(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    children: [
                                      Icon(
                                          _tab == 0
                                              ? Icons.hourglass_bottom_rounded
                                              : Icons.av_timer_rounded,
                                          size: 34,
                                          color: scheme.primary),
                                      const SizedBox(height: 12),
                                      FittedBox(
                                          fit: BoxFit.scaleDown,
                                          child: Text(
                                              _tab == 0
                                                  ? clockTime(tools.remaining)
                                                  : '${clockTime(tools.watch.elapsed.inSeconds)}.${(tools.watch.elapsed.inMilliseconds ~/ 10 % 100).toString().padLeft(2, '0')}',
                                              key: ValueKey(_tab == 0
                                                  ? 'clock-timer-readout'
                                                  : 'clock-watch-readout'),
                                              style: TextStyle(
                                                  fontSize: 66,
                                                  height: 1.15,
                                                  fontWeight: FontWeight.w400,
                                                  color: scheme.primary))),
                                      const SizedBox(height: 20),
                                      Wrap(
                                          alignment: WrapAlignment.center,
                                          spacing: 12,
                                          runSpacing: 8,
                                          children: _tab == 0
                                              ? [
                                                  FilledButton.icon(
                                                      key: const ValueKey(
                                                          'clock-timer-toggle'),
                                                      onPressed:
                                                          tools.toggleTimer,
                                                      icon: Icon(tools
                                                              .timerRunning
                                                          ? Icons.pause_rounded
                                                          : Icons
                                                              .play_arrow_rounded),
                                                      label: Text(
                                                          tools.timerRunning
                                                              ? '일시정지'
                                                              : '시작')),
                                                  OutlinedButton(
                                                      onPressed:
                                                          tools.resetTimer,
                                                      child: const Text('초기화')),
                                                  OutlinedButton(
                                                      onPressed: _setDuration,
                                                      child:
                                                          const Text('시간 설정')),
                                                ]
                                              : [
                                                  FilledButton.icon(
                                                      key: const ValueKey(
                                                          'clock-watch-toggle'),
                                                      onPressed:
                                                          tools.toggleWatch,
                                                      icon: Icon(tools
                                                              .watch.isRunning
                                                          ? Icons.pause_rounded
                                                          : Icons
                                                              .play_arrow_rounded),
                                                      label: Text(
                                                          tools.watch.isRunning
                                                              ? '정지'
                                                              : '시작')),
                                                  OutlinedButton(
                                                      onPressed:
                                                          tools.watch.isRunning
                                                              ? tools.lap
                                                              : null,
                                                      child: const Text('랩')),
                                                  OutlinedButton(
                                                      onPressed:
                                                          tools.resetWatch,
                                                      child: const Text('초기화')),
                                                ]),
                                      if (_tab == 0)
                                        Padding(
                                            padding:
                                                const EdgeInsets.only(top: 14),
                                            child: Wrap(
                                                spacing: 7,
                                                runSpacing: 5,
                                                alignment: WrapAlignment.center,
                                                children: [
                                                  for (final minutes in [
                                                    1,
                                                    5,
                                                    10,
                                                    25,
                                                    50
                                                  ])
                                                    ActionChip(
                                                        label:
                                                            Text('$minutes분'),
                                                        onPressed: () =>
                                                            tools.setTimer(
                                                                minutes * 60))
                                                ])),
                                      if (_tab == 1) ...[
                                        for (var i = 0;
                                            i < tools.laps.length;
                                            i++)
                                          ListTile(
                                              title: Text(
                                                  '랩 ${tools.laps.length - i}'),
                                              trailing: Text(clockTime(
                                                  tools.laps[i].inSeconds)))
                                      ],
                                    ])))),
              )),
            ]);
      });

  Widget _alarms() {
    final tools = widget.tools;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(children: [
        const Expanded(child: Text('매일 알람', style: TextStyle(fontSize: 20))),
        FilledButton.icon(
            key: const ValueKey('clock-add-alarm'),
            onPressed: () => _editAlarm(),
            icon: const Icon(Icons.add_rounded),
            label: const Text('추가'))
      ]),
      const SizedBox(height: 8),
      Text('앱이 실행 중일 때 알려드려요.',
          style: TextStyle(
              fontSize: 12,
              color: Theme.of(context).colorScheme.onSurfaceVariant)),
      const SizedBox(height: 12),
      Expanded(
          child: tools.alarms.isEmpty
              ? const Center(child: Text('알람을 추가해보세요.'))
              : ListView(children: [
                  for (final alarm in tools.alarms)
                    ListTile(
                        key: ValueKey('clock-alarm-${alarm.id}'),
                        contentPadding: EdgeInsets.zero,
                        title: Text(
                            '${alarm.hour.toString().padLeft(2, '0')}:${alarm.minute.toString().padLeft(2, '0')}',
                            style: const TextStyle(fontSize: 30)),
                        subtitle: Text(alarm.label),
                        onTap: () => _editAlarm(alarm),
                        trailing:
                            Row(mainAxisSize: MainAxisSize.min, children: [
                          Switch(
                              value: alarm.enabled,
                              onChanged: (v) =>
                                  tools.saveAlarm(alarm.copyWith(enabled: v))),
                          IconButton(
                              tooltip: '알람 삭제',
                              onPressed: () => tools.removeAlarm(alarm.id),
                              icon: const Icon(Icons.delete_outline_rounded)),
                        ])),
                ])),
    ]);
  }

  Future<void> _setDuration() async {
    final value = widget.tools.timerSeconds;
    final fields = [
      TextEditingController(text: '${value ~/ 3600}'),
      TextEditingController(text: '${value ~/ 60 % 60}'),
      TextEditingController(text: '${value % 60}')
    ];
    final form = GlobalKey<FormState>();
    var zero = false;
    final result = await showDialog<int>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, refresh) => AlertDialog(
                  title: const Text('타이머 시간'),
                  content: SizedBox(
                      width: 320,
                      child: Form(
                          key: form,
                          child:
                              Column(mainAxisSize: MainAxisSize.min, children: [
                            Row(children: [
                              for (var i = 0; i < 3; i++)
                                Expanded(
                                    child: Padding(
                                        padding: const EdgeInsets.all(5),
                                        child: TextFormField(
                                            controller: fields[i],
                                            keyboardType: TextInputType.number,
                                            maxLength: 2,
                                            decoration: InputDecoration(
                                                labelText: ['시간', '분', '초'][i],
                                                counterText: ''),
                                            validator: (text) {
                                              final n =
                                                  int.tryParse(text ?? '');
                                              return n == null ||
                                                      n < 0 ||
                                                      n > (i == 0 ? 23 : 59)
                                                  ? '범위 확인'
                                                  : null;
                                            })))
                            ]),
                            if (zero) const Text('1초 이상으로 설정해주세요.'),
                          ]))),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('취소')),
                    FilledButton(
                        onPressed: () {
                          if (!form.currentState!.validate()) return;
                          final seconds = int.parse(fields[0].text) * 3600 +
                              int.parse(fields[1].text) * 60 +
                              int.parse(fields[2].text);
                          if (seconds == 0) {
                            refresh(() => zero = true);
                            return;
                          }
                          Navigator.pop(context, seconds);
                        },
                        child: const Text('설정'))
                  ],
                )));
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      for (final c in fields) {
        c.dispose();
      }
    });
    if (result != null && mounted) widget.tools.setTimer(result);
  }

  Future<void> _editAlarm([DeskAlarm? alarm]) async {
    var time = TimeOfDay(hour: alarm?.hour ?? 7, minute: alarm?.minute ?? 0);
    final label = TextEditingController(text: alarm?.label ?? '알람');
    final result = await showDialog<DeskAlarm>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, refresh) => AlertDialog(
                  title: Text(alarm == null ? '알람 추가' : '알람 수정'),
                  content: SizedBox(
                      width: 330,
                      child: Column(mainAxisSize: MainAxisSize.min, children: [
                        ListTile(
                            leading: const Icon(Icons.alarm_rounded),
                            title: Text(
                                '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}',
                                style: const TextStyle(fontSize: 32)),
                            onTap: () async {
                              final selected = await showTimePicker(
                                  context: context,
                                  initialTime: time,
                                  initialEntryMode: TimePickerEntryMode.input,
                                  builder: (context, child) => MediaQuery(
                                      data: MediaQuery.of(context).copyWith(
                                          alwaysUse24HourFormat: true),
                                      child: child!));
                              if (selected != null && context.mounted) {
                                refresh(() => time = selected);
                              }
                            }),
                        TextField(
                            key: const ValueKey('clock-alarm-label'),
                            controller: label,
                            maxLength: 30,
                            decoration: const InputDecoration(labelText: '이름')),
                      ])),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('취소')),
                    FilledButton(
                        key: const ValueKey('clock-save-alarm'),
                        onPressed: () => Navigator.pop(
                            context,
                            DeskAlarm(
                                id: alarm?.id ??
                                    '${DateTime.now().microsecondsSinceEpoch}',
                                hour: time.hour,
                                minute: time.minute,
                                label: label.text.trim().isEmpty
                                    ? '알람'
                                    : label.text.trim(),
                                enabled: alarm?.enabled ?? true)),
                        child: const Text('저장'))
                  ],
                )));
    Future<void>.delayed(const Duration(milliseconds: 400), label.dispose);
    if (result != null && mounted) widget.tools.saveAlarm(result);
  }
}
