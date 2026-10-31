import 'package:deskmate_display/clock_tools.dart';
import 'package:deskmate_display/workspace_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('timer pauses, resumes, expires once, and resets', () {
    var now = DateTime(2026, 10, 18, 10);
    final alerts = <String>[];
    final tools = DeskClockController(
        now: () => now,
        onSave: () {},
        onAlert: alerts.add,
        alarms: [],
        autoTick: false);
    addTearDown(tools.dispose);
    tools.setTimer(10);
    tools.toggleTimer();
    now = now.add(const Duration(seconds: 3));
    tools.tick();
    expect(tools.remaining, 7);
    tools.toggleTimer();
    now = now.add(const Duration(minutes: 1));
    tools.tick();
    expect(tools.remaining, 7);
    tools.toggleTimer();
    now = now.add(const Duration(seconds: 7));
    tools.tick();
    tools.tick();
    expect(tools.remaining, 0);
    expect(tools.timerRunning, isFalse);
    expect(alerts, ['타이머가 끝났어요.']);
    tools.resetTimer();
    expect(tools.remaining, 10);
  });

  test('daily alarms fire once per date and retain the fired day on reload',
      () {
    var now = DateTime(2026, 10, 18, 7);
    final alerts = <String>[];
    var saves = 0;
    final tools = DeskClockController(
        now: () => now,
        onSave: () => saves++,
        onAlert: alerts.add,
        alarms: const [
          DeskAlarm(id: 'a', hour: 7, minute: 0, label: '기상'),
          DeskAlarm(id: 'b', hour: 7, minute: 0, label: '꺼진 알람', enabled: false)
        ],
        autoTick: false);
    addTearDown(tools.dispose);
    tools.tick();
    tools.tick();
    expect(alerts, ['기상']);
    expect(saves, 1);
    final prefs = WorkspacePreferences();
    prefs.alarms = [...tools.alarms];
    prefs.timerSeconds = 123;
    final reloaded = WorkspacePreferences.fromJson(prefs.toJson());
    expect(reloaded.timerSeconds, 123);
    final restored = DeskClockController(
        now: () => now,
        onSave: () {},
        onAlert: alerts.add,
        alarms: reloaded.alarms,
        autoTick: false);
    addTearDown(restored.dispose);
    restored.tick();
    expect(alerts, ['기상']);
    now = now.add(const Duration(days: 1));
    restored.tick();
    expect(alerts, ['기상', '기상']);
    restored.removeAlarm('a');
    expect(restored.alarms.length, 1);
  });
}
