/// 앱이 스스로 남기는 진단 로그.
///
/// 자세 화면에서 UART 를 왜 못 열었는지 보려고 만든 것인데, 통합 앱에서는 허브
/// 연결 같은 다른 경로도 같은 문제를 겪는다 - 보드에서 앱이 무슨 일을 겪었는지
/// 알 방법이 화면밖에 없다. 그래서 앱 전체가 쓰는 자리로 올렸다.
///
/// 이 보드는 **앱 표준출력이 journal 에 안 남는다.** `journalctl -u
/// am.com.atlas.app.deskmate_display_ui_test.service` 에는 systemd 의 시작/정지 줄만 찍히고 앱이
/// print 한 것은 사라진다. 그래서 UART 를 왜 못 열었는지 같은 것을 알아낼 방법이
/// 화면밖에 없었다. 파일 하나로 창구를 만든다.
///
///     ssh root@<보드> 'cat /data/share/usr/atlas/apps/com.atlas.app.deskmate_display_ui_test/deskmate.log'
///
/// 쓸 수 있는 곳을 찾지 못하면 조용히 버린다 - 로그 때문에 앱이 죽으면 안 된다.
library;

import 'dart:io';

class Diag {
  Diag({List<String>? candidates})
      : _candidates = candidates ?? _defaultCandidates();

  final List<String> _candidates;
  File? _file;
  bool _searched = false;

  /// 무한히 커지지 않게. 넘으면 새로 시작한다.
  static const int _limitBytes = 64 * 1024;

  static List<String> _defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    // 앱 설치 경로. 여기가 앱 소유라 제일 확실하고, SSH 로 읽기도 쉽다.
    add(Platform.environment['ATLAS_APP_DIR'], 'deskmate.log');
    add(File(Platform.resolvedExecutable).parent.path, 'deskmate.log');
    add(env['HOME'], '.config/deskmate/deskmate.log');
    add('/tmp', 'deskmate/deskmate.log');
    return paths;
  }

  File? _open() {
    if (_searched) return _file;
    _searched = true;
    for (final path in _candidates) {
      try {
        final file = File(path);
        file.parent.createSync(recursive: true);
        file.writeAsStringSync('', mode: FileMode.append, flush: true);
        _file = file;
        return file;
      } on FileSystemException {
        continue;
      }
    }
    return null;
  }

  void write(String line) {
    final file = _open();
    if (file == null) return;
    try {
      if (file.existsSync() && file.lengthSync() > _limitBytes) {
        file.writeAsStringSync('', flush: true);
      }
      final stamp = DateTime.now().toIso8601String();
      file.writeAsStringSync('$stamp  $line\n',
          mode: FileMode.append, flush: true);
    } on FileSystemException {
      // 쓸 수 없게 됐으면 그만둔다.
    }
  }

  /// 로그 파일 경로. 화면에 띄워 어디를 봐야 하는지 알려줄 때 쓴다.
  String? get path => _open()?.path;
}

/// 앱 전역 로그. 한 파일에 모은다.
final Diag diag = Diag();
