/// 허브의 `speaker` 명령(speak·chime)과 화면 알림(쉬기 알림·자동 정보 카드).
///
/// 10-26 Pi 5: ATLAS TTS(`com.atlas.VoiceManager1`)가 이 보드 이미지에 없다 — 서비스 파일이
/// 없고, 보드의 `ecoai-tts-cli` 는 root 전용인 데다 엔진(supertonic2)을 못 만든다. 그래서
/// speak 는 먼저 TTS 를 부르고, 안 되면 알림음 + 화면 문구로 대신한 뒤 그렇게 했다고 답한다.
library;

import 'dart:async';

import 'package:audioplayers/audioplayers.dart';
import 'package:dbus/dbus.dart';
import 'package:flutter/foundation.dart';

import 'music_playback.dart' show AtlasMediaPermission;

const kChimeSingle = 'audio/chime_single.mp3';
const kChimeSoft = 'audio/chime_soft.mp3';
const kChimeAlert = 'audio/chime_alert.mp3';

/// chime 의 pattern → 소리(hub ad76ff0: single|double|triple). single = 한 음, double = 두 음,
/// triple = 짧은 경고음 세 번. 예전 이름 alert·warning·urgent 는 triple, 모르는 값·없음은 double.
String chimeAssetFor(Object? pattern) =>
    switch (pattern?.toString().trim().toLowerCase()) {
      'single' => kChimeSingle,
      'triple' || 'alert' || 'warning' || 'urgent' => kChimeAlert,
      _ => kChimeSoft,
    };

/// chime 의 volume(사용자 sound_pref) → 재생 음량 0~1. 출력 장치 음량에 곱해진다. 없거나 모르면 1.
double chimeVolumeFor(Object? volume) =>
    switch (volume?.toString().trim().toLowerCase()) {
      'low' => 0.35,
      'mid' => 0.65,
      _ => 1.0,
    };

/// 짧은 소리 하나를 지금 출력 장치(블루투스 스피커가 붙어 있으면 거기)로 낸다.
abstract interface class AlertSound {
  Future<void> play(String asset, {double volume = 1});
}

class AtlasAlertSound implements AlertSound {
  // 처음 울릴 때 만든다 — 앱 화면 테스트처럼 소리를 안 쓰는 곳에서 플러그인을 부르지 않게.
  AudioPlayer? _player;
  AtlasMediaPermission? _permission;
  bool _granted = false;

  @override
  Future<void> play(String asset, {double volume = 1}) async {
    if (!_granted) {
      final permission = _permission ??= AtlasMediaPermission();
      if (!await permission.ensureGranted()) {
        throw StateError('미디어 재생 권한이 없습니다.');
      }
      _granted = true;
    }
    final player = _player ??= AudioPlayer();
    await player.stop();
    await player.setReleaseMode(ReleaseMode.stop);
    // 음악 재생과 같은 순서: 불러온 뒤 음량, 그다음 재생(Atlas 는 불러올 때 음량을 되돌린다).
    await player.setSource(AssetSource(asset));
    await player.setVolume(volume.clamp(0.0, 1.0));
    await player.resume();
  }

  Future<void> dispose() async {
    await _player?.dispose();
    _player = null;
  }
}

/// 문장을 소리 내어 읽는다. 할 수 없으면 던진다.
abstract interface class SpeechOutput {
  Future<void> speak(String text, {required String lang});
}

/// ATLAS 내장 TTS. 문서상 `StartTTS(s text, a{sv} opts) -> s key`, 말하는 중이면 OperationInProgress.
class AtlasSpeech implements SpeechOutput {
  AtlasSpeech({DBusClient? client}) : _client = client;

  DBusClient? _client;

  Future<void> dispose() async {
    await _client?.close();
    _client = null;
  }

  @override
  Future<void> speak(String text, {required String lang}) async {
    final voice = DBusRemoteObject(_client ??= DBusClient.system(),
        name: 'com.atlas.VoiceManager1',
        path: DBusObjectPath('/com/atlas/VoiceManager1'));
    await voice.callMethod(
      'com.atlas.VoiceManager1',
      'StartTTS',
      [
        DBusString(text),
        DBusDict.stringVariant(
            {'language': DBusString(lang == 'ko' ? 'ko-KR' : lang)}),
      ],
      replySignature: DBusSignature('s'),
    );
  }
}

/// 화면 위에 잠깐 띄우는 알림 한 장. [requestId] 가 있으면 닫을 때 hub 에 dismiss 를 보낸다.
@immutable
class ScreenNotice {
  const ScreenNotice({
    required this.text,
    required this.shownAt,
    required this.showFor,
    this.requestId,
    this.kind,
    this.countdown,
    this.label,
  });

  final String text;

  /// 자동 정보 카드의 `arm_label`(예: "알림음") — 어떤 알림 방식으로 골랐는지. 없으면 null.
  final String? label;
  final DateTime shownAt;

  /// 이만큼 지나면 저절로 사라진다.
  final Duration showFor;
  final String? requestId;
  final String? kind;

  /// 화면에 거꾸로 셀 시간(쉬기 알림의 쉬는 시간). 없으면 세지 않는다.
  final Duration? countdown;

  bool expired(DateTime now) => !now.isBefore(shownAt.add(showFor));

  Duration? remaining(DateTime now) {
    final total = countdown;
    if (total == null) return null;
    final left = total - now.difference(shownAt);
    return left.isNegative ? Duration.zero : left;
  }

  /// `interaction/request` 중 버튼이 닫기 하나뿐인 알림인지 — rest_notice, 또는 `auto: true`·
  /// options [dismiss] 인 정보 카드. 이런 것은 수락·거절 카드로 띄우지 않는다.
  static bool isNoticeRequest(Map<String, dynamic> data) {
    if (data['kind'] == 'rest_notice' || data['auto'] == true) return true;
    final options = data['options'];
    return options is List && options.length == 1 && options.first == 'dismiss';
  }

  static ScreenNotice fromRequest(Map<String, dynamic> data, DateTime now) {
    final text = data['text'] ?? data['explanation'];
    final expires = data['expires_in_s'];
    final rest = data['rest_min_sec'];
    final requestId = data['request_id'];
    final label = data['arm_label'];
    final showFor = expires is num && expires > 0
        ? expires
        : rest is num && rest > 0
            ? rest
            : 60;
    final count = rest is num && rest > 0
        ? rest
        : expires is num && expires > 0
            ? expires
            : null;
    return ScreenNotice(
      text: text is String && text.trim().isNotEmpty ? text.trim() : '알림',
      shownAt: now,
      showFor: Duration(seconds: showFor.round()),
      requestId: requestId is String && requestId.isNotEmpty ? requestId : null,
      kind: data['kind']?.toString(),
      countdown: count == null ? null : Duration(seconds: count.round()),
      label: label is String && label.trim().isNotEmpty ? label.trim() : null,
    );
  }
}

/// 지금 떠 있는 화면 알림. 새 알림이 오면 앞의 것을 바꾼다.
class NoticeBoard extends ChangeNotifier {
  ScreenNotice? _current;

  ScreenNotice? current(DateTime now) {
    final notice = _current;
    return notice == null || notice.expired(now) ? null : notice;
  }

  void show(ScreenNotice notice) {
    _current = notice;
    notifyListeners();
  }

  /// 닫는다. 닫힌 알림을 돌려준다(이미 없거나 지났으면 null).
  ScreenNotice? dismiss(DateTime now) {
    final notice = current(now);
    _current = null;
    notifyListeners();
    return notice;
  }
}
