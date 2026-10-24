import 'dart:async';
import 'package:flutter/material.dart';
import 'demo_model.dart';
import 'pr56/feedback_policy.dart';
import 'pr56/music_playback.dart';
import 'pr56/workspace_shell.dart';
import 'pr56/workspace_store.dart';

class WorkspaceDemoSession {
  final store = MemoryWorkspaceStore();
  final music = DemoWorkspaceMusic();
  final feedback = FeedbackCoordinator();
  Persona? _profile;
  bool? _active;
  void syncPreferences(DemoModel model) {
    if (identical(_profile, model.persona) && _active == model.active) return;
    _profile = model.persona;
    _active = model.active;
    final p = model.persona.preferences;
    store.preferences.focusMinutes = p.focusMinutes;
    unawaited(music.setVolume(p.volume));
    final play = model.active && p.musicEnabled && model.persona.sound != '무음';
    if (music.isPlaying != play) unawaited(music.toggle());
  }

  void dispose() => unawaited(music.dispose());
}

class MemoryWorkspaceStore extends WorkspaceStore {
  MemoryWorkspaceStore() : super(paths: const []);
  final preferences = WorkspacePreferences();
  @override
  WorkspacePreferences load() => preferences;
  @override
  bool save(WorkspacePreferences preferences) => true;
}

class Pr56WorkspaceView extends StatelessWidget {
  const Pr56WorkspaceView(
      {super.key,
      required this.model,
      required this.session,
      required this.onExit,
      required this.onPreferences});
  final DemoModel model;
  final WorkspaceDemoSession session;
  final VoidCallback onExit;
  final VoidCallback onPreferences;
  @override
  Widget build(BuildContext context) => Listener(
        onPointerDown: (_) => model.touch(),
        onPointerMove: (_) => model.touch(),
        child: DeskmateWorkspace(
          state: null,
          demo: true,
          music: session.music,
          feedback: session.feedback,
          store: session.store,
          now: () => model.now,
          onReturnToClock: () =>
              model.show(model.active ? DemoPage.details : DemoPage.home),
          onExit: onExit,
          onPreferences: onPreferences,
          developerBuilder: (_) => const Center(child: Text('연결된 센서가 없습니다')),
          onConnections: () => ScaffoldMessenger.of(context)
              .showSnackBar(const SnackBar(content: Text('기기 연결을 준비하고 있어요.'))),
          onAccept: () {},
          onReject: () {},
          onUndo: () {},
        ),
      );
}

class DemoWorkspaceMusic implements MusicPlayback, MusicQueue, MusicTimeline {
  DemoWorkspaceMusic() {
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!_playing) return;
      _position += const Duration(seconds: 1);
      if (_position >= duration!) {
        _position = Duration.zero;
        _track = _queue[(_queue.indexOf(_track) + 1) % _queue.length];
      }
      _positions.add(_position);
    });
  }
  late final Timer _timer;
  final _plays = StreamController<bool>.broadcast();
  final _volumes = StreamController<double>.broadcast();
  final _positions = StreamController<Duration>.broadcast();
  bool _playing = false;
  int _track = 0;
  double _volume = .3;
  Duration _position = Duration.zero;
  List<int> _queue = List.generate(classicalPlaylist.length, (i) => i);
  @override
  bool get isPlaying => _playing;
  @override
  int get selectedTrack => _track;
  @override
  double get volume => _volume;
  @override
  Stream<bool> get playingChanges => _plays.stream;
  @override
  Stream<double> get volumeChanges => _volumes.stream;
  @override
  Future<bool> toggle() async {
    _playing = !_playing;
    _plays.add(_playing);
    return _playing;
  }

  @override
  Future<void> selectTrack(int index) async {
    _track = index;
    await seek(Duration.zero);
  }

  @override
  Future<void> setVolume(double value) async {
    _volume = value;
    _volumes.add(value);
  }

  @override
  List<int> get queue => List.unmodifiable(_queue);
  @override
  void setQueue(List<int> tracks) {
    if (tracks.isNotEmpty) _queue = List.of(tracks);
  }

  @override
  Duration get position => _position;
  @override
  Duration? get duration => const Duration(minutes: 3);
  @override
  Stream<Duration> get positionChanges => _positions.stream;
  @override
  Stream<Duration?> get durationChanges => const Stream.empty();
  @override
  Future<void> seek(Duration value) async {
    _position = value;
    _positions.add(value);
  }

  @override
  Future<void> dispose() async {
    _timer.cancel();
    await _plays.close();
    await _volumes.close();
    await _positions.close();
  }
}
