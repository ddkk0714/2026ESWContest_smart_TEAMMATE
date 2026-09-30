/// MQTT 가 "연결됨" 인데 상태가 끊긴 경우를 스스로 복구하는 규칙.
///
/// mqtt_client 의 autoReconnect 는 소켓이 끊긴 것만 안다. 브로커가 재시작되며
/// 구독이 사라졌거나 반쯤 열린 연결에 걸리면 "연결됨" 인 채로 메시지가 영영 안
/// 온다. 그래서 상태 수신이 [staleAfter] 넘게 끊기면 클라이언트를 새로 만든다.
/// 계속 실패하면 간격을 두 배씩 늘려 브로커와 보드를 재시도로 두드리지 않는다.
///
/// 계획: docs/plan/app-connection-robustness-plan.md §4 "자동 복구 규칙".
library;

class MqttWatchdog {
  MqttWatchdog({
    this.staleAfter = const Duration(seconds: 15),
    this.firstBackoff = const Duration(seconds: 15),
    this.maxBackoff = const Duration(minutes: 2),
  }) : _backoff = firstBackoff;

  final Duration staleAfter;
  final Duration firstBackoff;
  final Duration maxBackoff;

  Duration _backoff;
  DateTime? _lastAttempt;
  int _attempts = 0;

  /// 지금까지 연속으로 강제 재연결한 횟수. 상태가 오면 0 으로 돌아간다.
  int get attempts => _attempts;

  /// 다음 시도까지 기다릴 간격.
  Duration get backoff => _backoff;

  /// 지금 강제 재연결해야 하는가. true 를 돌려주면 시도한 것으로 기록한다.
  ///
  /// 상태를 한 번도 못 받았으면(시작 직후·주소 오류) 여기서 손대지 않는다 —
  /// 그건 시작 점검과 연결 가이드가 맡는다.
  bool shouldReconnect(DateTime now, DateTime? lastStateAt) {
    if (lastStateAt == null) return false;
    if (now.difference(lastStateAt) <= staleAfter) return false;
    final last = _lastAttempt;
    if (last != null && now.difference(last) < _backoff) return false;
    if (last != null) {
      final doubled = _backoff * 2;
      _backoff = doubled > maxBackoff ? maxBackoff : doubled;
    }
    _lastAttempt = now;
    _attempts++;
    return true;
  }

  /// 상태를 받았다. 다음 끊김은 처음 간격부터 다시 센다.
  void stateReceived() {
    _backoff = firstBackoff;
    _lastAttempt = null;
    _attempts = 0;
  }
}
