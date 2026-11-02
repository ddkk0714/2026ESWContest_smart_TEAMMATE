import 'dart:convert';
import 'dart:io';

import 'settings_store.dart';
import 'home_layout.dart';
import 'focus_report.dart';

class DeskEvent {
  const DeskEvent(
      {required this.id,
      required this.title,
      required this.date,
      required this.hour,
      required this.minute,
      this.endHour,
      this.endMinute,
      this.location = ''});
  final String id;
  final String title;
  final DateTime date;
  final int hour;
  final int minute;
  final int? endHour;
  final int? endMinute;
  final String location;
  int get startMinutes => hour * 60 + minute;
  int? get endMinutes =>
      endHour == null || endMinute == null ? null : endHour! * 60 + endMinute!;
  Map<String, Object> toJson() => {
        'id': id,
        'title': title,
        'date': date.toIso8601String(),
        'hour': hour,
        'minute': minute,
        if (endHour != null) 'endHour': endHour!,
        if (endMinute != null) 'endMinute': endMinute!,
        'location': location,
      };
  static DeskEvent? parse(Object? raw) {
    if (raw is! Map) return null;
    final date = DateTime.tryParse('${raw['date']}');
    final title = raw['title'];
    final hour = raw['hour'];
    final minute = raw['minute'];
    final endHour = raw['endHour'];
    final endMinute = raw['endMinute'];
    if (date == null ||
        title is! String ||
        title.trim().isEmpty ||
        hour is! int ||
        hour < 0 ||
        hour > 23 ||
        minute is! int ||
        minute < 0 ||
        minute > 59) {
      return null;
    }
    if ((endHour != null || endMinute != null) &&
        (endHour is! int ||
            endHour < 0 ||
            endHour > 23 ||
            endMinute is! int ||
            endMinute < 0 ||
            endMinute > 59 ||
            endHour * 60 + endMinute <= hour * 60 + minute)) {
      return null;
    }
    return DeskEvent(
        id: '${raw['id']}',
        title: title,
        date: DateTime(date.year, date.month, date.day),
        hour: hour,
        minute: minute,
        endHour: endHour as int?,
        endMinute: endMinute as int?,
        location: raw['location'] is String
            ? (raw['location'] as String).trim()
            : '');
  }
}

class DeskPlaylist {
  const DeskPlaylist(
      {required this.id, required this.name, required this.tracks});
  final String id;
  final String name;
  final List<int> tracks;
  Map<String, Object> toJson() => {'id': id, 'name': name, 'tracks': tracks};
  static DeskPlaylist? parse(Object? raw) {
    if (raw is! Map ||
        raw['id'] is! String ||
        raw['name'] is! String ||
        raw['tracks'] is! List ||
        (raw['name'] as String).trim().isEmpty) {
      return null;
    }
    return DeskPlaylist(
        id: raw['id'],
        name: raw['name'],
        tracks: (raw['tracks'] as List)
            .whereType<int>()
            .where((i) => i >= 0 && i < 6)
            .toSet()
            .toList());
  }
}

class DeskAlarm {
  const DeskAlarm(
      {required this.id,
      required this.hour,
      required this.minute,
      this.label = '알람',
      this.enabled = true,
      this.lastFiredDay = ''});
  final String id;
  final int hour;
  final int minute;
  final String label;
  final bool enabled;
  final String lastFiredDay;
  DeskAlarm copyWith({bool? enabled, String? lastFiredDay}) => DeskAlarm(
      id: id,
      hour: hour,
      minute: minute,
      label: label,
      enabled: enabled ?? this.enabled,
      lastFiredDay: lastFiredDay ?? this.lastFiredDay);
  Map<String, Object> toJson() => {
        'id': id,
        'hour': hour,
        'minute': minute,
        'label': label,
        'enabled': enabled,
        'lastFiredDay': lastFiredDay
      };
  static DeskAlarm? parse(Object? raw) {
    if (raw is! Map ||
        raw['id'] is! String ||
        raw['hour'] is! int ||
        raw['minute'] is! int ||
        raw['hour'] < 0 ||
        raw['hour'] > 23 ||
        raw['minute'] < 0 ||
        raw['minute'] > 59) {
      return null;
    }
    return DeskAlarm(
        id: raw['id'],
        hour: raw['hour'],
        minute: raw['minute'],
        label: raw['label'] is String ? raw['label'] : '알람',
        enabled: raw['enabled'] != false,
        lastFiredDay: raw['lastFiredDay'] is String ? raw['lastFiredDay'] : '');
  }
}

class WorkspacePreferences {
  String appearance = 'auto';
  bool standby = false;
  bool automatic = false;
  bool lampAutomatic = true;
  bool speakerAutomatic = false;
  int focusMinutes = 25;
  List<int> favoriteTracks = [];
  List<String> widgets = ['clock', 'calendar', 'environment', 'focus', 'music'];
  Map<String, int> sizes = {'clock': 2, 'music': 2, 'weather': 1};
  List<DeskEvent> events = [];
  List<DeskAlarm> alarms = [];
  int timerSeconds = 300;
  String lampNickname = '조명';
  String musicNickname = '뮤직';
  List<DeskPlaylist> playlists = [];
  List<FocusReport> focusReports = [];

  Map<String, Object?> toJson() => {
        'version': 2,
        'appearance': appearance,
        'standby': standby,
        'automatic': automatic,
        'lampAutomatic': lampAutomatic,
        'speakerAutomatic': speakerAutomatic,
        'focusMinutes': focusMinutes,
        'favoriteTracks': favoriteTracks,
        'widgets': widgets,
        'sizes': sizes,
        'events': events.map((e) => e.toJson()).toList(),
        'alarms': alarms.map((e) => e.toJson()).toList(),
        'timerSeconds': timerSeconds,
        'lampNickname': lampNickname,
        'musicNickname': musicNickname,
        'playlists': playlists.map((p) => p.toJson()).toList(),
        'focusReports': focusReports.take(200).map((r) => r.toJson()).toList(),
      };

  factory WorkspacePreferences.fromJson(Map raw) {
    final p = WorkspacePreferences();
    if (raw['focusReports'] is List) {
      p.focusReports = (raw['focusReports'] as List)
          .map(FocusReport.parse)
          .whereType<FocusReport>()
          .take(200)
          .toList();
    }
    if (raw['playlists'] is List) {
      p.playlists = (raw['playlists'] as List)
          .map(DeskPlaylist.parse)
          .whereType<DeskPlaylist>()
          .toList();
    }
    if (raw['alarms'] is List) {
      p.alarms = (raw['alarms'] as List)
          .map(DeskAlarm.parse)
          .whereType<DeskAlarm>()
          .toList();
    }
    if (raw['timerSeconds'] is int &&
        raw['timerSeconds'] > 0 &&
        raw['timerSeconds'] < 86400) {
      p.timerSeconds = raw['timerSeconds'];
    }
    if (raw['lampNickname'] is String &&
        (raw['lampNickname'] as String).trim().isNotEmpty) {
      p.lampNickname = (raw['lampNickname'] as String).trim();
    }
    if (raw['musicNickname'] is String &&
        (raw['musicNickname'] as String).trim().isNotEmpty) {
      p.musicNickname = (raw['musicNickname'] as String).trim();
    }
    if (['auto', 'light', 'dark'].contains(raw['appearance'])) {
      p.appearance = raw['appearance'] as String;
    }
    p.standby = raw['standby'] == true;
    p.automatic = raw['automatic'] == true;
    p.lampAutomatic = raw['lampAutomatic'] != false;
    p.speakerAutomatic = raw['speakerAutomatic'] == true;
    if (raw['favoriteTracks'] is List) {
      p.favoriteTracks = (raw['favoriteTracks'] as List)
          .whereType<int>()
          .where((i) => i >= 0 && i < 6)
          .toSet()
          .toList();
    }
    if (raw['focusMinutes'] is int &&
        raw['focusMinutes'] >= 1 &&
        raw['focusMinutes'] <= 180) {
      p.focusMinutes = raw['focusMinutes'] as int;
    }
    if (raw['widgets'] is List) {
      p.widgets = (raw['widgets'] as List)
          .whereType<String>()
          .where(widgetNames.containsKey)
          .toSet()
          .toList();
    }
    if (raw['sizes'] is Map) {
      (raw['sizes'] as Map).forEach((k, v) {
        if (k is String &&
            resizableHomeWidgets.contains(k) &&
            v is int &&
            [1, 2].contains(v)) {
          p.sizes[k] = v;
        }
      });
    }
    if (raw['events'] is List) {
      p.events = (raw['events'] as List)
          .map(DeskEvent.parse)
          .whereType<DeskEvent>()
          .toList();
    }
    return p;
  }

  WorkspacePreferences();
}

const widgetNames = {
  'clock': '시계',
  'calendar': '캘린더',
  'environment': '실내',
  'focus': '집중 타이머',
  'music': '사운드',
  'weather': '날씨'
};

/// UI preferences are separate from connectivity settings and never overwrite them.
class WorkspaceStore {
  WorkspaceStore({List<String>? paths})
      : paths = paths ??
            SettingsStore.defaultCandidates()
                .map((p) => '${File(p).parent.path}/workspace.json')
                .toList();
  final List<String> paths;
  WorkspacePreferences load() {
    for (final path in paths) {
      try {
        final decoded = jsonDecode(File(path).readAsStringSync());
        if (decoded is Map) return WorkspacePreferences.fromJson(decoded);
      } on Object {/* Try the next writable config location. */}
    }
    return WorkspacePreferences();
  }

  bool save(WorkspacePreferences preferences) {
    for (final path in paths) {
      try {
        final file = File(path);
        file.parent.createSync(recursive: true);
        final pending = File('$path.pending');
        pending.writeAsStringSync(jsonEncode(preferences.toJson()),
            flush: true);
        pending.renameSync(path);
        return true;
      } on Object {/* Read-only board locations fall through. */}
    }
    return false;
  }
}
