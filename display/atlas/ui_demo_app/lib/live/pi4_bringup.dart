/// 재부팅한 Pi 4 를 앱이 깨운다.
///
/// ATLAS 는 부팅 자동 시작이 안 돼서(docs/reboot-recovery.md) Pi 4 를 재부팅하면 브로커·hub·ToF 서비스가
/// 다 꺼진 채로 올라온다. 그때 Pi 4 에서 확실히 떠 있는 것은 SSH(dropbear) 뿐이라, hub 상태가 한동안
/// 안 오면 앱이 SSH 로 `bringup.sh` 를 한 번 부른다. 앱 AppArmor 프로필은 외부 프로그램 실행 규칙이 없고
/// TCP 는 허용해서 dbclient 대신 순수 Dart SSH 를 쓴다.
///
/// Pi 4 root 는 원래 빈 비밀번호로 들어가진다(dropbear -B). 앱이 새 비밀번호나 키를 갖지는 않는다.
library;

import 'dart:async';
import 'dart:convert';

import 'package:dartssh2/dartssh2.dart';

const bringupCommand = 'sh /data/share/deskmate/recovery/bringup.sh';

/// 한 번 깨운 결과. [summary] 는 스크립트 마지막 줄("bringup hub=ok tof=ok uart=…") 또는 오류.
class BringupResult {
  const BringupResult({required this.ok, required this.summary, this.reached = true});
  final bool ok;
  final String summary;

  /// SSH 까지 닿았는가. 못 닿았으면(Pi 4 가 아직 부팅 중) 쿨다운 없이 곧 다시 시도한다.
  final bool reached;
}

typedef BringupRunner = Future<BringupResult> Function(String host);

/// 언제 깨울지. hub 상태가 [graceAfter] 넘게 안 오면 부르고, 다시 부르기 전에 [cooldown] 을 둔다.
/// SSH 에 못 닿았으면(재부팅 중) [unreachableRetry] 뒤 다시 — 11-03 실기에서 부팅 중 첫 시도가 실패해 2분을 기다렸다.
class BringupGuard {
  BringupGuard({
    this.graceAfter = const Duration(seconds: 20),
    this.cooldown = const Duration(minutes: 2),
    this.unreachableRetry = const Duration(seconds: 15),
  });

  final Duration graceAfter;
  final Duration cooldown;
  final Duration unreachableRetry;

  DateTime? _downSince;
  DateTime? _lastAttempt;
  Duration _wait = Duration.zero;

  /// 방금 시도가 SSH 에 못 닿았다. 다음 시도는 [unreachableRetry] 뒤.
  void unreachable() => _wait = unreachableRetry;

  /// 지금 깨워야 하는가. true 면 시도한 것으로 기록한다.
  bool shouldRun(DateTime now, {required bool live}) {
    if (live) {
      _downSince = null;
      return false;
    }
    final since = _downSince ??= now;
    if (now.difference(since) < graceAfter) return false;
    final last = _lastAttempt;
    if (last != null && now.difference(last) < _wait) return false;
    _lastAttempt = now;
    _wait = cooldown;
    return true;
  }
}

/// Pi 4 에 root 로 SSH 해서 [bringupCommand] 를 실행한다. 스크립트는 길어야 30 s 쯤 걸린다.
Future<BringupResult> sshBringup(
  String host, {
  int port = 22,
  Duration timeout = const Duration(seconds: 90),
}) async {
  // 스크립트가 끝까지 돌아 결과 줄을 냈을 때만 "닿았다"로 친다. 부팅 중엔 SSH 는 받아도 /data 가 아직이거나
  // 세션이 끊길 수 있는데(11-03 실기), 그걸 닿은 것으로 치면 2분을 쉬어 버린다.
  SSHClient? client;
  try {
    final socket = await SSHSocket.connect(host, port, timeout: const Duration(seconds: 5));
    client = SSHClient(socket, username: 'root', onPasswordRequest: () => '');
    final output = await client.run(bringupCommand).timeout(timeout);
    final lines = const LineSplitter()
        .convert(utf8.decode(output, allowMalformed: true))
        .where((line) => line.trim().isNotEmpty)
        .toList();
    final summary = lines.isEmpty ? 'bringup: 출력 없음' : lines.last.trim();
    return BringupResult(
        ok: summary.contains('hub=ok'), reached: summary.contains('bringup hub='), summary: summary);
  } catch (error) {
    return BringupResult(ok: false, reached: false, summary: 'Pi 4 를 아직 못 깨움(부팅 중?): $error');
  } finally {
    client?.close();
  }
}
