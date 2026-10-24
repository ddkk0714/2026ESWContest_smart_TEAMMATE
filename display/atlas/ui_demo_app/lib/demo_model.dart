import 'package:flutter/foundation.dart';

enum DemoPage { home, focus, details, widgets, report }

class DemoConfig {
  DemoConfig(this.values);
  final Map<String, dynamic> values;
  int seconds(String key) => (values[key] as num).toInt();
  double number(String key) => (values[key] as num).toDouble();
}

class DeskPreferences {
  DeskPreferences({this.brightness = .85, this.musicEnabled = true});
  bool lightEnabled = true;
  double brightness;
  double warmth = .45;
  bool fanEnabled = false;
  int fanSpeed = 1;
  bool musicEnabled;
  double volume = .3;
  int focusMinutes = 25;
  bool autoStart = true;
  bool autoAdjust = true;
  bool startNotice = true;
  double fatigueBrightness = .95;
  double fatigueWarmth = .25;
  int fatigueFanSpeed = 3;
  DeskPreferences copy() =>
      DeskPreferences(brightness: brightness, musicEnabled: musicEnabled)
        ..lightEnabled = lightEnabled
        ..warmth = warmth
        ..fanEnabled = fanEnabled
        ..fanSpeed = fanSpeed
        ..volume = volume
        ..focusMinutes = focusMinutes
        ..autoStart = autoStart
        ..autoAdjust = autoAdjust
        ..startNotice = startNotice
        ..fatigueBrightness = fatigueBrightness
        ..fatigueWarmth = fatigueWarmth
        ..fatigueFanSpeed = fatigueFanSpeed;
}

class Persona {
  Persona(
      {required this.name,
      required this.job,
      required this.rounds,
      this.age = 19,
      this.sleep = 7,
      this.sound = '백색 소음',
      this.order = const ['조명', '선풍기', '알림'],
      this.mood = '보통',
      this.avatar = 'cat',
      DeskPreferences? preferences})
      : preferences = preferences ??
            DeskPreferences(
                brightness: job == '수험생' ? .85 : .45,
                musicEnabled: sound != '무음');
  String name;
  String job;
  int rounds;
  int age;
  int sleep;
  String sound;
  List<String> order;
  String mood;
  String avatar;
  final DeskPreferences preferences;
  Persona copy() => Persona(
      name: name,
      job: job,
      rounds: rounds,
      age: age,
      sleep: sleep,
      sound: sound,
      order: List.of(order),
      mood: mood,
      avatar: avatar,
      preferences: preferences.copy());
}

class DemoModel extends ChangeNotifier {
  DemoModel(this.config);
  final DemoConfig config;
  final profiles = [
    Persona(name: '지우', job: '수험생', rounds: 12),
    Persona(
        name: '민준',
        job: '재택 직장인',
        rounds: 12,
        age: 29,
        sound: '카페 음악',
        avatar: 'sprout',
        order: ['알림', '조명', '선풍기']),
    Persona(name: '새 사용자', job: '수험생', rounds: 0, avatar: 'star'),
  ];
  int profileIndex = 0;
  Persona get persona => profiles[profileIndex];
  int get profileSlotCount => ((profiles.length + 2) ~/ 2) * 2;

  void setAvatar(int index, String avatar) {
    profiles[index].avatar = avatar;
    notifyListeners();
  }

  bool get trained => persona.rounds >= config.seconds('feedbackRounds');
  DemoPage page = DemoPage.home;
  DemoPage returnPage = DemoPage.home;
  DateTime now = DateTime.now();
  int sessionSeconds = 0;
  int completedSeconds = 0;
  int completedSessions = 0;
  int workTicks = 0;
  int pageTicks = 0;
  int noticeTicks = 0;
  int absentTicks = 0;
  int fatigueEvents = 0;
  int interventionStep = 0;
  bool preferencesOpen = false;
  bool active = false;
  bool scenario = false;
  bool absent = false;
  bool proposal = false;
  bool ledOn = true;
  bool fanOn = false;
  int fanSpeed = 1;
  double brightness = .65;
  double warmth = .45;
  String notice = '';
  String feeling = '';
  String posture = '바른 자세';
  String? pendingAction;
  // The fatigue card stays only until the user answers it.
  bool askingFeedback = false;
  final events = <String>[];

  String get elapsed {
    final h = sessionSeconds ~/ 3600;
    final m = sessionSeconds ~/ 60 % 60;
    final s = sessionSeconds % 60;
    return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  void tick([DateTime? time]) {
    now = time ?? DateTime.now();
    pageTicks++;
    if (noticeTicks > 0 && --noticeTicks == 0) {
      notice = '';
      askingFeedback = false;
    }
    if (active && !absent) {
      sessionSeconds += config.seconds('simulatedSecondsPerTick');
    }
    if (active &&
        absent &&
        ++absentTicks >= config.seconds('absenceGraceSeconds')) {
      finish();
      return;
    }
    if (!preferencesOpen &&
        scenario &&
        !active &&
        !proposal &&
        page == DemoPage.home) {
      if (++workTicks >= config.seconds('workCueSeconds')) {
        if (trained &&
            persona.preferences.autoStart &&
            config.number('trainedConfidence') >=
                config.number('autoConfidence')) {
          start(automatic: true);
          return;
        }
        proposal = true;
        scenario = false;
      }
    }
    if (!preferencesOpen &&
        page == DemoPage.details &&
        pageTicks >= config.seconds('detailSeconds') &&
        !absent) {
      page = DemoPage.focus;
      pageTicks = 0;
    }
    if (!preferencesOpen &&
        page == DemoPage.widgets &&
        pageTicks >= config.seconds('widgetIdleSeconds')) {
      page = returnPage;
      pageTicks = 0;
    }
    notifyListeners();
  }

  void touch() => pageTicks = 0;

  void show(DemoPage next) {
    if (next == DemoPage.widgets) {
      returnPage = active ? DemoPage.details : DemoPage.home;
    }
    page = next;
    pageTicks = 0;
    notifyListeners();
  }

  void selectProfile(int index) {
    if (active) return;
    profileIndex = index;
    proposal = false;
    scenario = false;
    workTicks = 0;
    notifyListeners();
  }

  void beginScenario() {
    if (active) return;
    page = DemoPage.home;
    scenario = true;
    proposal = false;
    workTicks = 0;
    notifyListeners();
  }

  void start({bool automatic = false}) {
    if (active) return;
    active = true;
    scenario = false;
    absent = false;
    absentTicks = 0;
    sessionSeconds = 0;
    fatigueEvents = 0;
    interventionStep = 0;
    posture = '바른 자세';
    pendingAction = null;
    askingFeedback = false;
    feeling = '';
    events.clear();
    events.add(automatic ? '집중 자동 진입' : '집중 시작 · 사용자 확인');
    if (proposal) persona.rounds++;
    proposal = false;
    _applyPreferences();
    page = DemoPage.focus;
    pageTicks = 0;
    notice = persona.preferences.startNotice ? '집중 모드 시작됨' : '';
    noticeTicks = config.seconds('focusNoticeSeconds');
    notifyListeners();
  }

  void decline() {
    proposal = false;
    scenario = false;
    notice = '괜찮아요. 편할 때 시작해요.';
    noticeTicks = config.seconds('focusNoticeSeconds');
    notifyListeners();
  }

  void fatigue() {
    if (!active) return;
    fatigueEvents++;
    posture = '앞으로 기울어짐';
    final action = persona.order[interventionStep % persona.order.length];
    interventionStep++;
    final automatic = trained && persona.preferences.autoAdjust;
    if (automatic) {
      _applyAction(action);
    } else {
      pendingAction = action;
    }
    events.add('피로 → $action ${automatic ? '자동 조정' : '제안'}');
    notice = automatic ? '$action 조정으로 잠깐 환기해요' : '$action 조정이 도움이 될까요?';
    askingFeedback = true;
    noticeTicks = config.seconds('detailSeconds');
    notifyListeners();
  }

  void feedback(bool helpful) {
    if (pendingAction != null && helpful) _applyAction(pendingAction!);
    pendingAction = null;
    askingFeedback = false;
    events.add(helpful ? '개입 피드백 · 도움 됐어요' : '개입 피드백 · 다른 방법');
    if (!trained) persona.rounds++;
    notice = helpful ? '고마워요. 선호에 반영할게요' : '다음 선호 방법을 제안할게요';
    noticeTicks = config.seconds('focusNoticeSeconds');
    notifyListeners();
  }

  void _applyAction(String action) {
    if (action == '조명') {
      ledOn = true;
      brightness = persona.preferences.fatigueBrightness;
      warmth = persona.preferences.fatigueWarmth;
    }
    if (action == '선풍기') {
      fanOn = true;
      fanSpeed = persona.preferences.fatigueFanSpeed;
    }
  }

  void away() {
    if (!active) return;
    absent = true;
    absentTicks = 0;
    page = DemoPage.details;
    pageTicks = 0;
    events.add('부재 · 일시정지');
    notifyListeners();
  }

  void resume() {
    absent = false;
    absentTicks = 0;
    show(DemoPage.focus);
  }

  void finish() {
    if (!active) return;
    active = false;
    scenario = false;
    completedSessions++;
    completedSeconds += sessionSeconds;
    ledOn = false;
    fanOn = false;
    notice = '';
    events.add('작업 종료 → 조명·플러그 OFF');
    page = DemoPage.report;
    pageTicks = 0;
    notifyListeners();
  }

  void recordFeeling(String value) {
    feeling = value;
    notifyListeners();
  }

  void manual({double? light, double? color, bool? fan, bool? led}) {
    if (light != null) brightness = light;
    if (color != null) warmth = color;
    if (fan != null) fanOn = fan;
    if (led != null) ledOn = led;
    events.add('수동 조정 · 선호 피드백');
    if (events.length > 30) events.removeAt(0);
    touch();
    notifyListeners();
  }

  void _applyPreferences() {
    final p = persona.preferences;
    ledOn = p.lightEnabled;
    brightness = p.brightness;
    warmth = p.warmth;
    fanOn = p.fanEnabled;
    fanSpeed = p.fanSpeed;
  }

  void updateProfile(Persona draft) {
    profiles[profileIndex] = draft;
    _applyPreferences();
    touch();
    notifyListeners();
  }

  void saveProfile(Persona draft) {
    profiles.add(draft);
    profileIndex = profiles.length - 1;
    scenario = false;
    workTicks = 0;
    proposal = false;
    show(DemoPage.home);
  }
}
