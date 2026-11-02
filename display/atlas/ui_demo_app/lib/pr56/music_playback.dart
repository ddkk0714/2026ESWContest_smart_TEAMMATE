abstract interface class MusicPlayback {
  bool get isPlaying;
  int get selectedTrack;
  double get volume;
  Stream<bool> get playingChanges;
  Stream<double> get volumeChanges;
  Future<bool> toggle();
  Future<void> selectTrack(int index);
  Future<void> setVolume(double value);
  Future<void> dispose();
}

/// Optional timeline capability; callers without native timing remain usable.
abstract interface class MusicTimeline {
  Duration get position;
  Duration? get duration;
  Stream<Duration> get positionChanges;
  Stream<Duration?> get durationChanges;
  Future<void> seek(Duration position);
}

/// Optional queues keep custom playlists and station playback within their set.
abstract interface class MusicQueue {
  List<int> get queue;
  void setQueue(List<int> tracks);
}

/// Local assets, played in order and wrapped back to Debussy after Bach.
const classicalPlaylist = [
  'audio/deskmate_test_music.mp3',
  'audio/chopin_nocturne_op9_no2.mp3',
  'audio/satie_gymnopedie_no1.mp3',
  'audio/beethoven_fur_elise.mp3',
  'audio/mozart_sonata_k545_allegro.mp3',
  'audio/bach_prelude_bwv846.mp3',
];

const classicalTrackTitles = [
  '드뷔시 · 달빛',
  '쇼팽 · 녹턴 Op.9 No.2',
  '사티 · 짐노페디 1번',
  '베토벤 · 엘리제를 위하여',
  '모차르트 · 소나타 K.545 1악장',
  '바흐 · 프렐류드 BWV 846',
];
