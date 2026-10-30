/// 장치 위치의 저장·동기화 규칙. 화면(`placement_page.dart`)과 main 이 같이 쓴다.
///
/// - 사용자 선택이 기준이다: 고르면 **먼저 로컬(settings.json `placement`)에 저장**하고 Pi 4 에 POST 한다.
///   POST 가 실패해도 저장값은 남고 앱은 계속 돈다.
/// - Pi 4 에 붙을 때([connect]) GET 으로 지원 여부·적용값을 읽고, 저장값과 다르면 저장값으로 한 번 POST.
/// - 첫 실행 질문은 "저장값 없음 + 건너뛴 적 없음 + 서비스가 지원" 일 때만([shouldAskOnStart]).
///   건너뛰면 `placement_asked` 를 기록해 다시 묻지 않는다.
library;

import 'package:flutter/foundation.dart';

import 'placement_client.dart';
import 'settings_store.dart';

typedef PlacementClientFactory = PlacementClient Function(String host);

class PlacementApplyResult {
  PlacementApplyResult.ok(PlacementInfo this.info)
      : failure = null,
        text = '적용됨: ${info.model ?? info.placement}';
  const PlacementApplyResult.failed(this.failure, this.text) : info = null;

  final PlacementInfo? info;
  final PlacementException? failure;
  final String text;
  bool get ok => failure == null;
}

class PlacementController extends ChangeNotifier {
  PlacementController({
    required SettingsStore store,
    PlacementClientFactory clientFactory = HttpPlacementClient.new,
    this.onSettingsChanged,
  })  : _store = store,
        _clientFactory = clientFactory;

  final SettingsStore _store;
  final PlacementClientFactory _clientFactory;

  /// 로컬 저장 뒤 main 이 자기 복사본을 갱신하도록.
  final ValueChanged<DeskmateSettings>? onSettingsChanged;

  PlacementClient? _client;

  /// 마지막 GET/POST 가 돌려준 Pi 4 쪽 값. 아직 못 읽었으면 null.
  PlacementInfo? remote;

  /// 서비스가 `/api/placement` 를 지원하는지. 아직 모르면 null, 404 면 false.
  bool? supported;

  /// 마지막 실패 이유(연결 안 됨 등). 성공하면 null.
  PlacementException? lastError;

  /// 이번 실행에서 첫 질문을 이미 띄웠는지(두 번 띄우지 않게).
  bool askedThisRun = false;

  String? get host => _client?.host;

  String? get saved => _store.load().placement;

  bool get shouldAskOnStart {
    if (askedThisRun || supported != true) return false;
    final s = _store.load();
    return s.placement == null && !s.placementAsked;
  }

  /// 연결 탭용: Pi 4 적용값 한 줄. 모르면 null.
  String? get appliedLabel {
    if (supported == false) return '옛 서비스 (위치 선택 없음)';
    final r = remote;
    if (r == null) return lastError?.userText;
    return '${placementKo(r.placement)}${r.model == null ? '' : ' · ${r.model}'}';
  }

  /// Pi 4 주소가 정해지거나 바뀔 때. GET → (저장값과 다르면) POST. 실패해도 던지지 않는다.
  Future<void> connect(String? host) async {
    _client?.close();
    _client = null;
    remote = null;
    supported = null;
    lastError = null;
    notifyListeners();
    if (host == null || host.isEmpty) return;
    final client = _clientFactory(host);
    _client = client;
    try {
      var info = await client.fetch();
      if (!identical(client, _client)) return;
      supported = true;
      final want = _store.load().placement;
      if (want != null && want != info.placement) {
        try {
          info = await client.apply(want);
        } on PlacementException catch (e) {
          lastError = e;
        }
      }
      remote = info;
    } on PlacementException catch (e) {
      if (!identical(client, _client)) return;
      lastError = e;
      if (e.failure == PlacementFailure.unsupported) supported = false;
    }
    notifyListeners();
  }

  /// 사용자가 골랐다: 로컬 저장 → Pi 4 POST. 결과 문구를 돌려준다(화면이 그대로 보여 준다).
  Future<PlacementApplyResult> apply(String placement) async {
    final valid = DeskmateSettings.validPlacement(placement);
    if (valid == null) {
      return PlacementApplyResult.failed(
          PlacementException(PlacementFailure.invalid, placement),
          '알 수 없는 위치: $placement');
    }
    _saveSettings(_store.load().copyWith(placement: valid, placementAsked: true));
    final client = _client;
    if (client == null) {
      final e = const PlacementException(
          PlacementFailure.unreachable, 'Pi 4 주소가 없어 저장만 했어요');
      lastError = e;
      notifyListeners();
      return PlacementApplyResult.failed(e, '저장했어요. ${e.userText}');
    }
    try {
      final info = await client.apply(valid);
      remote = info;
      supported = true;
      lastError = null;
      notifyListeners();
      return PlacementApplyResult.ok(info);
    } on PlacementException catch (e) {
      lastError = e;
      if (e.failure == PlacementFailure.unsupported) supported = false;
      notifyListeners();
      return PlacementApplyResult.failed(e, '저장했어요. ${e.userText}');
    }
  }

  /// 첫 질문을 건너뛴다 — 다시 묻지 않도록 기록.
  void skip() {
    askedThisRun = true;
    _saveSettings(_store.load().copyWith(placementAsked: true));
    notifyListeners();
  }

  void _saveSettings(DeskmateSettings next) {
    _store.save(next);
    onSettingsChanged?.call(next);
  }

  @override
  void dispose() {
    _client?.close();
    _client = null;
    super.dispose();
  }
}
