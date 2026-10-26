import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'demo_model.dart';
import 'live/live_link.dart';
import 'pr56/feedback_policy.dart';
import 'pr56/music_playback.dart';
import 'pr56/workspace_shell.dart';
import 'pr56/workspace_store.dart';
import 'developer/pr74/music_playback.dart' as native;

class WorkspaceDemoSession {
  WorkspaceDemoSession(
      {native.MusicPlayback? playback, bool memoryOnly = false})
      : store = MemoryWorkspaceStore(memoryOnly: memoryOnly),
        music = WorkspaceMusicAdapter(playback);
  final MemoryWorkspaceStore store;
  final WorkspaceMusicAdapter music;
  final musicAvailable = AssetManifest.loadFromAssetBundle(rootBundle).then(
      (manifest) => classicalPlaylist
          .every((track) => manifest.listAssets().contains('assets/$track')),
      onError: (Object _, StackTrace __) => false);
  final feedback = FeedbackCoordinator();
  Persona? _profile;
  void syncPreferences(DemoModel model) {
    if (identical(_profile, model.persona)) return;
    _profile = model.persona;
    store.preferences.focusMinutes = model.persona.preferences.focusMinutes;
  }

  void dispose() {}
}

class MemoryWorkspaceStore extends WorkspaceStore {
  MemoryWorkspaceStore({this.memoryOnly = false})
      : super(paths: memoryOnly ? const [] : null) {
    preferences = super.load();
  }
  final bool memoryOnly;
  late WorkspacePreferences preferences;
  @override
  WorkspacePreferences load() => preferences;
  @override
  bool save(WorkspacePreferences preferences) {
    this.preferences = preferences;
    return memoryOnly || super.save(preferences);
  }
}

class Pr56WorkspaceView extends StatelessWidget {
  const Pr56WorkspaceView(
      {super.key,
      required this.model,
      required this.session,
      required this.onExit,
      required this.onPreferences,
      this.live,
      this.onConnections});
  final DemoModel model;

  /// 보드 연결. 이어져 있으면 실내 값·기기 상태를 보드에서 받아 보여 준다.
  final LiveLink? live;
  final WorkspaceDemoSession session;
  final VoidCallback onExit;
  final VoidCallback onPreferences;
  final VoidCallback? onConnections;
  @override
  Widget build(BuildContext context) => FutureBuilder<bool>(
      future: session.musicAvailable,
      builder: (context, audio) => Listener(
            onPointerDown: (_) => model.touch(),
            onPointerMove: (_) => model.touch(),
            child: DeskmateWorkspace(
              state: live?.live == true ? live!.state : null,
              deviceStatus: live?.live == true
                  ? Map.unmodifiable(live!.devices.states)
                  : const {},
              demo: false,
              musicAvailable: audio.data ?? false,
              showFocusGoal: model.showFocusGoal,
              music: session.music,
              feedback: session.feedback,
              store: session.store,
              now: () => model.now,
              onReturnToClock: () =>
                  model.show(model.active ? DemoPage.details : DemoPage.home),
              onExit: onExit,
              onPreferences: onPreferences,
              developerBuilder: (_) =>
                  const Center(child: Text('연결된 센서가 없습니다')),
              onConnections: onConnections ?? onPreferences,
              onAccept: () {},
              onReject: () {},
              onUndo: () {},
            ),
          ));
}

class WorkspaceMusicAdapter implements MusicPlayback {
  WorkspaceMusicAdapter(this.playback);
  final native.MusicPlayback? playback;
  @override
  bool get isPlaying => playback?.isPlaying ?? false;
  @override
  int get selectedTrack => playback?.selectedTrack ?? 0;
  @override
  double get volume => playback?.volume ?? 0;
  @override
  Stream<bool> get playingChanges =>
      playback?.playingChanges ?? const Stream.empty();
  @override
  Stream<double> get volumeChanges =>
      playback?.volumeChanges ?? const Stream.empty();
  native.MusicPlayback get _player =>
      playback ?? (throw StateError("오디오 연결을 확인해 주세요."));
  @override
  Future<bool> toggle() => _player.toggle();
  @override
  Future<void> selectTrack(int index) => _player.selectTrack(index);
  @override
  Future<void> setVolume(double value) => _player.setVolume(value);
  @override
  Future<void> dispose() async {}
}
