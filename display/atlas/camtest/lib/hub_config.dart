/// Pi 4 자세 노드 주소를 **보드에서** 바꾸기 위한 저장소.
///
/// 원래는 빌드할 때 `--dart-define=DESKMATE_HUB_URL` 로 박았다. 그런데 이 보드는
/// 개발 노트북과 다른 망에 있어서 설치를 남에게 부탁해야 하고, Pi 4 가 DHCP 면
/// 주소가 바뀔 때마다 그 부탁을 다시 해야 한다. 그래서 주소만 파일 하나로 빼서
/// 화면에서 고칠 수 있게 한다.
///
/// 파일이 없으면 빌드에 박힌 값으로, 그것도 없으면 데모로 떨어진다.
library;

import 'dart:io';

/// 노드의 기본 HTTP 포트(`--port-http` 기본값).
const kHubPort = 8765;

/// 키패드로 찍은 문자열을 주소로 받아들일지 판단한다.
///
/// IPv4 만 받는다. 보드에 DNS 가 없을 수 있고, 호스트 이름을 치려면 키보드가
/// 필요한데 이 화면은 터치만으로 되게 만든 것이라 숫자 말고는 입력 수단이 없다.
String? normalizeIp(String raw) {
  final text = raw.trim();
  final parts = text.split('.');
  if (parts.length != 4) return null;
  for (final part in parts) {
    if (part.isEmpty || part.length > 3) return null;
    final value = int.tryParse(part);
    if (value == null || value < 0 || value > 255) return null;
    // "01" 같은 건 받아는 주되 정규화해서 돌려준다.
  }
  return parts.map((part) => int.parse(part).toString()).join('.');
}

String hubUrlFor(String ip, {int port = kHubPort}) => 'http://$ip:$port';

/// 저장된 주소에서 IP 만 도로 꺼낸다. 화면에 현재 값을 채워 주기 위한 것이다.
String? ipFromUrl(String? url) {
  if (url == null) return null;
  final uri = Uri.tryParse(url.trim());
  if (uri == null || uri.host.isEmpty) return null;
  return normalizeIp(uri.host);
}

/// 주소 한 줄을 담는 파일. 후보 경로를 순서대로 훑는다.
///
/// 보드의 파일 시스템이 어디까지 쓰기 가능한지 확신할 수 없어서(앱 설치 경로는
/// 읽기 전용일 수 있다) 여러 곳을 시도하고, **다 실패해도 앱은 계속 돈다** —
/// 그때는 이번 실행에만 적용되고 화면이 그렇게 말해 준다.
class HubStore {
  HubStore({List<String>? candidates})
      : candidates = candidates ?? defaultCandidates();

  final List<String> candidates;

  static List<String> defaultCandidates() {
    final env = Platform.environment;
    final paths = <String>[];
    void add(String? base, String tail) {
      final root = base?.trim();
      if (root == null || root.isEmpty) return;
      final path = '$root/$tail';
      if (!paths.contains(path)) paths.add(path);
    }

    // 배포할 때 경로를 못 박고 싶으면 이 환경변수 하나로 덮는다.
    add(env['DESKMATE_CONFIG_DIR'], 'hub.txt');
    add(env['XDG_CONFIG_HOME'], 'camtest/hub.txt');
    add(env['HOME'], '.config/camtest/hub.txt');
    // 마지막 수단. 재부팅하면 지워지지만, 아무 데도 못 쓰는 것보다는 낫다.
    add('/tmp', 'camtest/hub.txt');
    return paths;
  }

  /// 저장된 주소. 없거나 못 읽으면 null.
  String? read() {
    for (final path in candidates) {
      try {
        final file = File(path);
        if (!file.existsSync()) continue;
        final text = file.readAsLinesSync().firstWhere(
              (line) => line.trim().isNotEmpty,
              orElse: () => '',
            );
        if (text.trim().isNotEmpty) return text.trim();
      } on FileSystemException {
        // 못 읽는 경로는 없는 것으로 친다.
      }
    }
    return null;
  }

  /// 쓸 수 있는 첫 경로에 적는다. 다 실패하면 false — 호출부가 그 사실을 화면에
  /// 띄운다. 조용히 실패하면 껐다 켰을 때 주소가 사라진 이유를 알 길이 없다.
  bool save(String url) {
    for (final path in candidates) {
      try {
        final file = File(path);
        file.parent.createSync(recursive: true);
        file.writeAsStringSync('$url\n', flush: true);
        return true;
      } on FileSystemException {
        continue;
      }
    }
    return false;
  }

  /// 저장된 주소를 지운다(데모로 되돌리기). 하나라도 지웠으면 true.
  bool clear() {
    var removed = false;
    for (final path in candidates) {
      try {
        final file = File(path);
        if (file.existsSync()) {
          file.deleteSync();
          removed = true;
        }
      } on FileSystemException {
        continue;
      }
    }
    return removed;
  }
}
