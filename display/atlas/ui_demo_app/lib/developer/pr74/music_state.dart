// V32: 음악 상태를 hub 와 주고받는 공통 모양 — 계약은 docs/mqtt-topics.md `deskmate/display/audio`·target `music`.
//
// 같은 {on, volume, track, track_id} 를 두 곳에서 쓴다.
//   - 앱 → hub `deskmate/display/audio`(retain): 지금 음악 상태(바뀔 때마다·연결 직후).
//   - hub → 앱 `control/cmd` target `music` set_music 의 결과 `previous`·`actual`.
// 곡 이름(한글 제목)이나 사용자 이름은 싣지 않는다 — track_id 는 앱에 들어 있는 음원 파일 이름이다.
library;

import 'dart:async';

import 'music_playback.dart';

/// 볼륨은 0~1, 소수 둘째 자리까지. 슬라이더 잡음으로 같은 값이 다시 나가지 않게.
double roundedVolume(double value) =>
    (value.clamp(0.0, 1.0) * 100).roundToDouble() / 100;

/// `audio/chopin_nocturne_op9_no2.mp3` → `chopin_nocturne_op9_no2`.
String trackIdOf(int index) {
  if (index < 0 || index >= classicalPlaylist.length) return '';
  final file = classicalPlaylist[index].split('/').last;
  final dot = file.lastIndexOf('.');
  return dot < 0 ? file : file.substring(0, dot);
}

/// 지금 음악 상태 한 장.
Map<String, Object?> musicStateOf(MusicPlayback music) {
  final track = music.selectedTrack;
  return {
    'on': music.isPlaying,
    'volume': roundedVolume(music.volume),
    'track': track,
    'track_id': trackIdOf(track),
  };
}

/// 음악 상태가 바뀔 때마다 [publish] 를 부른다. 볼륨 슬라이더처럼 잇따르는 변화는 [settle] 만큼 모아 마지막 값 한 번만,
/// 직전에 보낸 것과 같으면 보내지 않는다. 연결 직후 다시 보내는 일은 받는 쪽(LiveLink, retain)이 맡는다.
class MusicStateReporter {
  MusicStateReporter(this.music, this.publish,
      {this.settle = const Duration(milliseconds: 400)}) {
    _subs = [
      music.playingChanges.listen((_) => _schedule()),
      music.volumeChanges.listen((_) => _schedule()),
    ];
    report();
  }

  final MusicPlayback music;
  final void Function(Map<String, Object?> state) publish;
  final Duration settle;
  late final List<StreamSubscription<Object?>> _subs;
  Timer? _timer;
  Map<String, Object?>? _last;

  void _schedule() {
    _timer?.cancel();
    _timer = Timer(settle, report);
  }

  /// 곡을 바꾼 뒤처럼 스트림이 알려 주지 않는 변화도 이걸로 바로 보낸다.
  void report() {
    _timer?.cancel();
    final state = musicStateOf(music);
    final last = _last;
    if (last != null &&
        last.length == state.length &&
        state.entries.every((e) => last[e.key] == e.value)) {
      return;
    }
    _last = state;
    publish(state);
  }

  void dispose() {
    _timer?.cancel();
    for (final sub in _subs) {
      unawaited(sub.cancel());
    }
  }
}
