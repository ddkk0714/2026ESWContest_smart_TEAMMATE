/// Pi 4 주소를 보드에 남겨 두는 저장소.
///
/// 빌드에 박는(`--dart-define`) 대신 파일 한 줄로 둔다. Pi 4 가 유선(172.16.34.199)에
/// 있다가 핫스팟(172.20.10.3)으로 옮겨도 화면에서 고치면 다음 실행에도 남는다.
///
/// 어디가 쓰기 가능한지 보드마다 달라 후보를 순서대로 시도한다. ATLAS 에서 앱은
/// 전용 사용자(u0_aNNNN)로 돌고 `$HOME` 은 `/data/profiles/0/appdata/<앱ID>/home` 이다
/// (옆 앱 deskmate_display 가 거기 `.config/deskmate/settings.json` 을 남긴 것을 확인).
/// 다 실패해도 앱은 돈다 — 그땐 이번 실행에만 적용된다.
library;

import 'dart:io';

class AddressStore {
  AddressStore({List<String>? candidates})
      : candidates = candidates ?? defaultCandidates();

  final List<String> candidates;
  String? _lastPath;

  /// 마지막으로 읽거나 쓴 파일. 없으면 저장이 안 된 것이다.
  String? get lastPath => _lastPath;

  static List<String> defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    add(env['DESKMATE_CONFIG_DIR'], 'tof_posture_url.txt');
    add(env['XDG_CONFIG_HOME'], 'deskmate/tof_posture_url.txt');
    add(env['HOME'], '.config/deskmate/tof_posture_url.txt');
    // 마지막 수단. 재부팅하면 지워지지만 아무 데도 못 쓰는 것보다 낫다.
    add('/tmp', 'deskmate/tof_posture_url.txt');
    return paths;
  }

  String? load() {
    _lastPath = null;
    for (final path in candidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) continue;
        final text = file.readAsStringSync().trim();
        if (text.isEmpty) continue;
        _lastPath = path;
        return text;
      } on Object {
        // 깨진 파일·권한 오류는 다음 후보로.
      }
    }
    return null;
  }

  /// 저장에 성공한 경로. 아무 데도 못 쓰면 null.
  String? save(String url) {
    for (final path in candidates) {
      try {
        final file = File(path);
        file.parent.createSync(recursive: true);
        file.writeAsStringSync('$url\n', flush: true);
        _lastPath = path;
        return path;
      } on Object {
        // 다음 후보.
      }
    }
    return null;
  }
}
