import 'dart:async';

class AudioMetadata {
  final String title;
  final String artist;
  final String album;
  final int durationMs;

  const AudioMetadata({
    this.title = '',
    this.artist = '',
    this.album = '',
    this.durationMs = 0,
  });
}

/// AudioPluginController / AudioDbusController 공통 인터페이스.
/// main.dart는 이 타입에만 의존하므로 구현체를 한 줄 교체로 바꿀 수 있다.
abstract class AudioController {
  Stream<String> get playbackStatus;
  Stream<int> get position;
  Stream<AudioMetadata> get metadata;
  Stream<int> get bufferingPercent;

  Future<void> initialize();
  Future<void> setInitialMetadata(AudioMetadata meta);
  Future<bool> load(String uri);
  Future<bool> play();
  Future<void> pause();
  Future<void> seek(int positionMs);
  Future<void> setVolume(double volume);
  Future<double> getVolume();
  Future<void> stop();
  Future<void> sendKeyEvent(String event);
  Future<void> dispose();
}
