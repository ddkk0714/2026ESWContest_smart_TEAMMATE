/// Pi 4 중계기(:8772/raw)·자세 서비스(:8771) 를 주기적으로 읽어 두는 폴러.
///
/// 프리뷰 탭(`preview_page.dart`) 안의 폴링과 같은 규칙이다 — 응답이 늦으면 요청을 겹치지 않고,
/// 주소가 바뀌면 이전 소스를 닫고 처음부터. 개발자 모드 화면이 쓴다.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import 'tof_raw_client.dart';

typedef TofSourceFactory = PreviewTofSource Function(String host);

class TofPoller extends ChangeNotifier {
  TofPoller({
    this.sourceFactory = HttpPreviewTofSource.new,
    this.interval = const Duration(milliseconds: 500),
  });

  final TofSourceFactory sourceFactory;
  final Duration interval;

  PreviewTofSource? _source;
  Timer? _timer;
  bool _inFlight = false;

  TofRawSnapshot? raw;
  TofPostureLabel? posture;
  String? rawError;
  String? postureError;
  int fails = 0;
  DateTime? lastOkAt;

  String? get host => _source?.host;

  /// 주소를 바꾼다. null·빈 문자열이면 폴링을 멈추고 값을 비운다.
  void open(String? host) {
    _timer?.cancel();
    _timer = null;
    _source?.close();
    _source = null;
    raw = null;
    posture = null;
    rawError = null;
    postureError = null;
    fails = 0;
    lastOkAt = null;
    if (host == null || host.isEmpty) {
      notifyListeners();
      return;
    }
    _source = sourceFactory(host);
    _timer = Timer.periodic(interval, (_) => tick());
    notifyListeners();
    tick();
  }

  Future<void> tick() async {
    if (_inFlight) return;
    final source = _source;
    if (source == null) return;
    _inFlight = true;
    try {
      final results = await Future.wait<Object?>([
        source.fetchRaw().then<Object?>((v) => v, onError: (Object e) => e),
        source.fetchPosture().then<Object?>((v) => v, onError: (Object e) => e),
      ]);
      if (!identical(source, _source)) return;
      final r = results[0];
      final p = results[1];
      if (r is TofRawSnapshot) {
        raw = r;
        rawError = null;
        fails = 0;
        lastOkAt = DateTime.now();
      } else {
        rawError = shortError(r);
        fails++;
      }
      if (p is TofPostureLabel) {
        posture = p;
        postureError = null;
      } else {
        postureError = shortError(p);
      }
      notifyListeners();
    } finally {
      _inFlight = false;
    }
  }

  static String shortError(Object? e) {
    final text = e.toString();
    final nl = text.indexOf('\n');
    final line = nl < 0 ? text : text.substring(0, nl);
    return line.length > 80 ? '${line.substring(0, 80)}…' : line;
  }

  @override
  void dispose() {
    _timer?.cancel();
    _source?.close();
    _source = null;
    super.dispose();
  }
}
