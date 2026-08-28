import 'package:flutter/foundation.dart';
import 'package:video_player/video_player.dart';

/// video_player_atlas 플러그인 기반 비디오 컨트롤러.
/// VideoPlayerController 수명주기를 관리하고 상태 변경 시 [notifyChanged]를 호출한다.
/// 위젯은 [notifyChanged]에서 setState를 호출해 UI를 갱신한다.
class VideoPluginController {
  VideoPluginController({required this.notifyChanged});

  /// 상태 변경 시 호출할 콜백 (위젯의 setState)
  final VoidCallback notifyChanged;

  // ── 상태 ─────────────────────────────────────────────────

  /// video_player 패키지의 플레이어 인스턴스 (렌더링 위젯에 직접 전달)
  VideoPlayerController? controller;

  bool isInitialized = false;
  bool isPlaying = false;
  bool isLoading = false;
  String? error;

  // ── 제어 ─────────────────────────────────────────────────

  /// [url]을 로드하고 재생을 시작한다. 15초 내 초기화 실패 시 에러 상태로 전환.
  Future<void> load(String url) async {
    if (url.trim().isEmpty) return;

    isLoading = true;
    isInitialized = false;
    error = null;
    notifyChanged();

    controller?.removeListener(_onUpdate);
    await controller?.dispose();
    controller = null;

    final ctrl = VideoPlayerController.network(url.trim());
    controller = ctrl;
    try {
      await ctrl.initialize().timeout(
        const Duration(seconds: 60),
        onTimeout: () => throw Exception('연결 시간 초과 (60s)'),
      );
      // 로드 도중 새 요청이 들어왔으면 이 controller는 이미 dispose됨
      if (controller != ctrl) return;

      ctrl.addListener(_onUpdate);
      await ctrl.play();

      isInitialized = true;
      isLoading = false;
      notifyChanged();
    } catch (e) {
      print('[Video] load failed: $e');
      await ctrl.dispose();
      if (controller == ctrl) controller = null;
      isLoading = false;
      error = e.toString().replaceFirst('Exception: ', '');
      notifyChanged();
    }
  }

  Future<void> togglePlay() async {
    final ctrl = controller;
    if (ctrl == null || !ctrl.value.isInitialized) return;
    ctrl.value.isPlaying ? await ctrl.pause() : await ctrl.play();
  }

  String formatDuration(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  void dispose() {
    controller?.removeListener(_onUpdate);
    controller?.dispose();
    controller = null;
  }

  // ── 내부 ──────────────────────────────────────────────────

  void _onUpdate() {
    isPlaying = controller?.value.isPlaying ?? false;
    notifyChanged();
  }
}
