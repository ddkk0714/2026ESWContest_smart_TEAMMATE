// DESKMATE — 자세 화면 (ATLAS Flutter)
//
// ESP32-CAM ──UART 921600──> Pi 5 GPIO15 ──PeripheralManager──> 이 앱
//                                                                ├ 프레임 디코딩
//                                                                ├ 자세 판정
//                                                                └ 화면
//
// **판정까지 앱 안에서 한다.** ATLAS 는 Python 을 앱 런타임으로 지원하지 않으므로
// (`/restricted/python3` 은 AppArmor 가 막는다) Pi 4 의 Python 노드를 그대로 쓸 수
// 없었다. 그래서 `tools/posture.py` 를 `posture_judge.dart` 로 옮겼고, 옮긴 결과가
// 원본과 같은 숫자를 내는지는 골든 벡터로 채점한다.
//
// 보드에 물린 센서를 못 열면(개발 PC·배선 전) 저장된 주소 → 빌드에 박힌 주소 →
// 화면 내장 데모 순으로 떨어진다. 화면 코드는 어느 경로든 같다.
//
// 옆 앱: `display/atlas/app`(허브 FSM 대시보드) · `display/atlas/keystroke`(국면).
// 앱 ID 가 서로 달라 Pi 5 에 셋을 나란히 설치해 비교할 수 있다.
import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'diag.dart';
import 'hub_config.dart';
import 'hub_setup.dart';
import 'palette.dart';
import 'posture_source.dart';
import 'serial_posture_source.dart';
import 'vision_view.dart';
import 'posture_state.dart';

/// 배포 스크립트(`deploy/device.env` 의 `ATLAS_HUB_URL`)가 그대로 넘겨주는 값.
/// 여기서는 허브가 아니라 **Pi 4 자세 노드**의 주소다 — 두 API 모양이 같아서
/// 이름을 그대로 쓴다. 비어 있으면 화면 내장 데모가 돈다.
const _hubUrl = String.fromEnvironment('DESKMATE_HUB_URL');

void main() => runApp(const PostureApp());

class PostureLook {
  const PostureLook(this.ko, this.en, this.color, this.icon, this.desc);
  final String ko;
  final String en;
  final Color color;
  final IconData icon;
  final String desc;
}

/// 라벨별 화면 표현. 한국어 이름은 노드의 `LABEL_TEXT` 와 같은 말을 쓴다 —
/// 같은 상태를 Pi 4 로그와 Pi 5 화면이 다르게 부르면 시연 중에 못 맞춘다.
const kPostureLook = <PostureLabel, PostureLook>{
  PostureLabel.upright: PostureLook('바른 자세', 'UPRIGHT', kGreen,
      Icons.airline_seat_recline_normal, '좋아요. 지금 자세를 그대로 유지해 주세요'),
  PostureLabel.slump: PostureLook('엎드림', 'SLUMP', kRed, Icons.airline_seat_flat,
      '머리가 기준보다 내려간 채로 이어지고 있어요'),
  PostureLabel.recline: PostureLook('뒤로 젖힘', 'RECLINE', kAmber,
      Icons.airline_seat_recline_extra, '센서에서 멀어졌어요. 책상 쪽으로 다시 앉아 보세요'),
  PostureLabel.drowsy: PostureLook('졸음', 'DROWSY', kViolet, Icons.bedtime_outlined,
      '머리가 반복해서 끄덕이고 있어요. 잠깐 쉬어 가는 건 어때요?'),
  PostureLabel.absent: PostureLook('자리 비움', 'ABSENT', kGray,
      Icons.person_off_outlined, '책상 앞에 사람이 없어요'),
  PostureLabel.baseline: PostureLook('기준 측정 중', 'BASELINE', kBlue,
      Icons.straighten, '바른 자세 기준을 재고 있어요'),
  PostureLabel.unknown: PostureLook('기준 없음', 'UNKNOWN', kGray,
      Icons.help_outline, '아직 판정할 기준이 없어요'),
};

/// 노드가 보내는 근거 코드 → 사람이 읽는 한국어.
///
/// 코드는 `envelope.py` 의 `reasons_for()` 와 `posture.py` 의 `note` 에서 온다.
/// 숫자가 붙어 오는 것들은 접두사로 잘라 읽고, 모르는 코드는 **그대로 보여준다** —
/// 안 보여주면 판정이 왜 그렇게 나왔는지 화면에서 확인할 길이 없어진다.
const kReasonLabel = <String, String>{
  'posture_only': '자세 신호만 사용',
  'head_dropped_and_held': '머리가 내려간 채 유지',
  'moved_away_from_sensor': '센서에서 멀어짐',
  'desk_empty': '책상이 비어 있음',
  'low occupancy': '사람이 거의 안 잡힘',
  'no baseline': '기준 없음',
  'baseline clipped - press b': '기준 프레임이 잘렸어요 · 기준 다시 잡기',
};

String reasonLabel(String raw) {
  final known = kReasonLabel[raw];
  if (known != null) return known;
  if (raw.startsWith('nods_per_min_')) {
    return '꾸벅임 ${raw.substring('nods_per_min_'.length)}회/분';
  }
  if (raw.startsWith('head_width_ratio_')) {
    return '머리 크기 비 ${raw.substring('head_width_ratio_'.length)}';
  }
  if (raw.startsWith('background ')) {
    return '배경 측정 ${raw.substring('background '.length)}';
  }
  if (raw.startsWith('posture ')) {
    return '자세 기준 ${raw.substring('posture '.length)}';
  }
  return raw;
}

class PostureApp extends StatelessWidget {
  const PostureApp({super.key, this.source, this.store});

  /// 테스트에서 가짜 소스를 꽂는 자리. 비우면 저장된 주소 → 빌드에 박힌 주소
  /// → 데모 순으로 정한다.
  final PostureSource? source;

  /// 주소를 적어 두는 파일. 테스트에서는 임시 폴더를 준다.
  final HubStore? store;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'DESKMATE 자세',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          brightness: Brightness.dark,
          fontFamily: 'Roboto',
          scaffoldBackgroundColor: kBg,
        ),
        home: PostureScreen(source: source, store: store),
      );
}

class PostureScreen extends StatefulWidget {
  const PostureScreen({super.key, this.source, this.store});
  final PostureSource? source;
  final HubStore? store;

  @override
  State<PostureScreen> createState() => _PostureScreenState();
}

class _PostureScreenState extends State<PostureScreen> {
  late final HubStore _store;
  late PostureSource _source;
  Timer? _timer;
  PostureState? _state;
  LinkHealth? _health;
  String? _error;
  bool _busy = false;
  bool _calibrating = false;
  int _ticks = 0;

  /// 보드에 적어 둔 주소. 없으면 빌드에 박힌 값을 쓴다.
  String? _hubOverride;

  /// 주소를 파일에 못 적은 상태. 이번 실행에만 적용되므로 화면이 말해 준다.
  bool _hubUnsaved = false;

  /// 보드 직결(UART)을 못 연 이유. 데모로 떨어진 까닭을 화면이 말해 준다.
  String? _serialNote;

  @override
  void initState() {
    super.initState();
    _store = widget.store ?? HubStore();
    _hubOverride = _store.read();
    _source = _makeSource();
    _startSerialIfIdle();
    _refresh();
    // 노드도 1Hz 로 내보낸다. 더 자주 긁어도 새 값이 없다.
    _timer = Timer.periodic(const Duration(seconds: 1), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _source.close();
    super.dispose();
  }

  /// 저장된 주소 → 빌드에 박힌 주소 → 데모 순.
  PostureSource _makeSource() {
    final injected = widget.source;
    if (injected != null) return injected;
    final url = (_hubOverride ?? _hubUrl).trim();
    return url.isEmpty ? DemoPostureSource() : HttpPostureSource(url);
  }

  /// 주소가 없으면 **보드에 직접 물린 ESP32-CAM** 을 먼저 본다.
  ///
  /// 이게 이 앱의 원래 경로다 - 판정까지 앱 안에서 끝나므로 Pi 4 도 네트워크도
  /// 필요 없다. UART 를 못 열면(개발 PC, 권한 없음, 배선 전) 조용히 데모로 남는다.
  /// 사람이 주소를 찍어 뒀으면 그 뜻을 존중해 시도하지 않는다.
  Future<void> _startSerialIfIdle() async {
    if (widget.source != null) return;
    if ((_hubOverride ?? _hubUrl).trim().isNotEmpty) {
      diag.write('serial: 주소가 저장돼 있어 보드 직결을 건너뜀');
      return;
    }
    if (_source is SerialPostureSource) return;

    diag.write('serial: 보드 직결 시도 (로그 ${diag.path})');
    final serial = SerialPostureSource();
    try {
      await serial.start();
    } catch (error) {
      diag.write('serial: 실패 ${error.runtimeType} / $error');
      serial.close();
      if (mounted) setState(() => _serialNote = '$error');
      return;
    }
    diag.write('serial: 연결됨');
    if (!mounted) {
      serial.close();
      return;
    }
    final previous = _source;
    setState(() {
      _source = serial;
      _serialNote = null;
      _state = null;
      _health = null;
      _error = null;
    });
    previous.close();
    _refresh();
  }

  /// 톱니 버튼. 보드에서 Pi 4 주소를 바꾼다.
  ///
  /// 이 화면이 있는 이유: 주소를 빌드에 박으면 Pi 4 의 DHCP 주소가 바뀔 때마다
  /// 다시 구워서 보드와 같은 망에 있는 사람에게 설치를 부탁해야 한다.
  Future<void> _editHub() async {
    final current = ipFromUrl(_hubOverride ?? _hubUrl);
    final result = await showHubSetup(context, currentIp: current);
    if (result == null || !mounted) return;

    if (result.isEmpty) {
      _store.clear();
      _applyHub(null, unsaved: false);
      _notify('주소를 지웠습니다 — 보드에 물린 센서를 다시 찾아봅니다');
      unawaited(_startSerialIfIdle());
      return;
    }
    final url = hubUrlFor(result);
    final saved = _store.save(url);
    _applyHub(url, unsaved: !saved);
    _notify(saved
        ? '자세 노드를 $result 로 봅니다'
        : '$result 로 봅니다 — 저장할 곳이 없어 이번 실행에만 적용됩니다');
  }

  void _applyHub(String? url, {required bool unsaved}) {
    _source.close();
    setState(() {
      _hubOverride = url;
      _hubUnsaved = unsaved;
      _source = _makeSource();
      // 이전 주소에서 받은 값이 새 주소의 값인 것처럼 남아 있으면 안 된다.
      _state = null;
      _health = null;
      _error = null;
    });
    _refresh();
  }

  Future<void> _refresh() async {
    // 응답이 느려지면 폴링이 서로를 밀어낸다. 한 번에 하나만 보낸다.
    if (_busy) return;
    _busy = true;
    try {
      final state = await _source.fetch();
      if (mounted) {
        setState(() {
          _state = state;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = _message(error));
    } finally {
      _busy = false;
    }
    // 링크 상태는 5초에 한 번이면 충분하다. 판정과 같은 주기로 긁으면 요청만
    // 두 배가 되고, 이 값은 그렇게 빨리 바뀌지 않는다.
    if (_ticks++ % 5 != 0) return;
    final health = await _source.health();
    if (mounted) setState(() => _health = health);
  }

  String _message(Object error) {
    if (error is SocketException) return '자세 노드에 연결하지 못했습니다';
    if (error is TimeoutException) return '자세 노드가 응답하지 않습니다';
    if (error is FormatException) return '알 수 없는 형식의 응답입니다';
    if (error is HttpException) return error.message;
    return error.toString();
  }

  Future<void> _calibrate() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: kSurface,
        title: const Text('기준을 다시 잡을까요?'),
        content: const Text('먼저 자리에서 비켜 주세요. 빈 책상을 재고 나면 화면이 '
            '"바른 자세로 앉아 주세요" 로 바뀝니다. 전부 20초쯤 걸립니다.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('취소')),
          FilledButton(
              key: const ValueKey('calibrate-confirm'),
              onPressed: () => Navigator.pop(context, true),
              child: const Text('다시 잡기')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _calibrating = true);
    try {
      await _source.calibrate();
      _notify('기준을 다시 잡습니다 — 화면 안내를 따라 주세요');
    } catch (error) {
      _notify('기준 다시 잡기에 실패했습니다: ${_message(error)}');
    } finally {
      if (mounted) setState(() => _calibrating = false);
    }
  }

  /// 센서가 보고 있는 것을 띄운다. 화면에 라벨만 있으면 "지금 잡히고 있나" 를
  /// 사람이 확인할 방법이 없다.
  void _openVision() {
    final source = _source;
    if (source is! SerialPostureSource) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => VisionViewPage(
        title: '센서 보기 — ${source.label}',
        read: () => source.vision,
      ),
    ));
  }

  void _notify(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    final look = kPostureLook[state?.label ?? PostureLabel.unknown]!;
    final now = DateTime.now();
    final note = _note(state, now);
    return Scaffold(
      body: Container(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0, -0.45),
            radius: 1.2,
            colors: [look.color.withValues(alpha: .16), kBg],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(28, 18, 28, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _header(state),
                if (note != null) ...[
                  const SizedBox(height: 12),
                  _noteBar(note),
                ],
                Expanded(
                  child: Center(
                    child: SingleChildScrollView(
                      child: state == null ? _loading() : _body(state, look),
                    ),
                  ),
                ),
                _footer(state, now),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _header(PostureState? state) => Row(children: [
        const Icon(Icons.chair_alt, color: kGreen, size: 22),
        const SizedBox(width: 8),
        const Text('DESKMATE · 자세',
            style: TextStyle(
                fontWeight: FontWeight.w800, letterSpacing: 1.5, fontSize: 18)),
        const Spacer(),
        if (!_source.canCalibrate)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
                color: kSurface, borderRadius: BorderRadius.circular(20)),
            child: const Text('DEMO · 실데이터 아님',
                style: TextStyle(fontSize: 11, color: kMuted)),
          ),
        if (_source.canCalibrate)
          TextButton.icon(
            key: const ValueKey('calibrate'),
            onPressed: _calibrating ? null : _calibrate,
            style: TextButton.styleFrom(
              foregroundColor: kInk,
              backgroundColor: kSurface,
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
            ),
            icon: _calibrating
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.restart_alt, size: 18),
            label: const Text('기준 다시 잡기'),
          ),
        if (_source is SerialPostureSource)
          IconButton(
            key: const ValueKey('vision-open'),
            tooltip: '센서 보기',
            onPressed: _openVision,
            color: kMuted,
            icon: const Icon(Icons.visibility_outlined),
          ),
        IconButton(
          key: const ValueKey('hub-setup'),
          tooltip: '자세 노드 주소',
          onPressed: _editHub,
          color: kMuted,
          icon: const Icon(Icons.settings_ethernet),
        ),
        const SizedBox(width: 6),
        _link(state),
      ]);

  /// 연결 상태 점. 판정이 멀쩡해 보여도 링크가 죽어 있을 수 있어서 색을 따로 낸다.
  Widget _link(PostureState? state) {
    final health = _health;
    final color = _error != null
        ? kRed
        : (health != null && !health.healthy)
            ? kAmber
            : kGreen;
    return Tooltip(
      message: _source.label,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(
            color: color.withValues(alpha: .12),
            borderRadius: BorderRadius.circular(99)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.circle, size: 10, color: color),
          const SizedBox(width: 6),
          Text('#${state?.sequence ?? 0}',
              style:
                  const TextStyle(fontWeight: FontWeight.w600, fontSize: 12)),
        ]),
      ),
    );
  }

  /// 지금 화면에서 제일 급한 한 줄. 없으면 null.
  ({IconData icon, Color color, String text})? _note(
      PostureState? state, DateTime now) {
    if (_error != null) {
      return (
        icon: Icons.link_off,
        color: kRed,
        text: '$_error · ${_source.label}'
      );
    }
    final health = _health;
    if (health?.error != null) {
      return (
        icon: Icons.usb_off,
        color: kRed,
        text: '센서 링크가 끊겼습니다 — ${health!.error}'
      );
    }
    if (health != null && health.stale) {
      return (
        icon: Icons.sensors_off,
        color: kAmber,
        text: '센서 프레임이 멈췄습니다 — 보드 USB 와 전원을 확인하세요'
      );
    }
    if (state == null) return null;
    final age = state.ageFrom(now);
    // 노드는 프레임이 멈춰도 1초에 한 번은 내보낸다. 그것마저 멈췄다는 뜻이다.
    if (age.inSeconds >= 5) {
      return (
        icon: Icons.update_disabled,
        color: kAmber,
        text: '판정이 ${age.inSeconds}초째 갱신되지 않았습니다'
      );
    }
    if (!state.valid) {
      return (
        icon: Icons.straighten,
        color: kBlue,
        text: state.scenario ?? '기준을 잡는 중입니다 — 판정은 아직 신뢰할 수 없어요'
      );
    }
    final serialNote = _serialNote;
    if (serialNote != null && _source is DemoPostureSource) {
      return (
        icon: Icons.cable,
        color: kAmber,
        text: '보드에 물린 센서를 못 열었습니다 — $serialNote'
      );
    }
    // 제일 낮은 순위. 링크가 멀쩡할 때만 알려도 늦지 않다.
    if (_hubUnsaved) {
      return (
        icon: Icons.save_as_outlined,
        color: kAmber,
        text: '주소를 보드에 저장하지 못했습니다 — 앱을 다시 켜면 초기화됩니다'
      );
    }
    return null;
  }

  Widget _noteBar(({IconData icon, Color color, String text}) note) =>
      Container(
        key: const ValueKey('note'),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: note.color.withValues(alpha: .14),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: note.color.withValues(alpha: .45)),
        ),
        child: Row(children: [
          Icon(note.icon, size: 18, color: note.color),
          const SizedBox(width: 10),
          Expanded(
            child: Text(note.text,
                style: TextStyle(fontSize: 14, color: note.color)),
          ),
        ]),
      );

  Widget _loading() => Column(mainAxisSize: MainAxisSize.min, children: [
        const SizedBox(
            width: 28,
            height: 28,
            child: CircularProgressIndicator(strokeWidth: 3)),
        const SizedBox(height: 16),
        Text(_error == null ? '자세 노드를 기다리는 중입니다' : '자세 노드를 다시 부르는 중입니다',
            style: const TextStyle(color: kMuted)),
      ]);

  /// 지금 센서가 보고 있는 것. 보드 직결이 아니면 null.
  VisionSnapshot? _vision() {
    final source = _source;
    return source is SerialPostureSource ? source.vision : null;
  }

  /// 기준을 잡는 동안에는 **판정 대신 내 모습을 크게** 보여준다.
  ///
  /// 배경을 뜰 때는 화각에서 비켜야 하고 기준을 잡을 때는 바르게 앉아야 하는데,
  /// 라벨만 보이면 내가 제대로 서 있는지·앉아 있는지 알 수가 없다. 기준이 어긋나면
  /// 그 뒤 판정이 통째로 틀리므로 이 순간이 제일 중요하다.
  Widget _calibrationBody(PostureState state, VisionSnapshot snapshot) {
    final hint = state.scenario ?? '기준을 잡는 중입니다';
    final sitting = hint.startsWith('2/2');
    return Column(mainAxisSize: MainAxisSize.min, children: [
      Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        Icon(sitting ? Icons.event_seat : Icons.directions_walk,
            color: kBlue, size: 22),
        const SizedBox(width: 10),
        Flexible(
          child: Text(hint,
              key: const ValueKey('calibration-hint'),
              textAlign: TextAlign.center,
              style: const TextStyle(
                  fontSize: 20, fontWeight: FontWeight.w700, color: kInk)),
        ),
      ]),
      const SizedBox(height: 14),
      SizedBox(
        width: 430,
        child: VisionPanel(snapshot: snapshot, stale: state.ageFrom(DateTime.now()).inSeconds >= 5),
      ),
      const SizedBox(height: 10),
      Text(
        sitting
            ? '이 자세가 기준이 됩니다 — 초록으로 잡힌 모양이 지금 앉은 모습입니다'
            : '초록이 남아 있으면 아직 화각 안입니다',
        style: const TextStyle(fontSize: 12, color: kDim),
      ),
    ]);
  }

  Widget _body(PostureState state, PostureLook look) {
    // 기준을 잡는 중이면 판정 화면 대신 라이브 화면.
    final snapshot = _vision();
    final calibrating = state.label == PostureLabel.baseline ||
        state.label == PostureLabel.unknown;
    if (calibrating && snapshot != null) {
      return _calibrationBody(state, snapshot);
    }
    return _dashboardBody(state, look);
  }

  Widget _dashboardBody(PostureState state, PostureLook look) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _presence(state),
          const SizedBox(height: 16),
          _hero(state, look),
          if (state.valid && state.scenario != null) ...[
            const SizedBox(height: 8),
            Text(state.scenario!,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: kDim)),
          ],
          const SizedBox(height: 20),
          _metrics(state),
          const SizedBox(height: 14),
          _bars(state),
          const SizedBox(height: 14),
          _reasons(state),
        ],
      );

  /// 화면이 제일 먼저 답해야 하는 질문 — 지금 앉아 있는가.
  Widget _presence(PostureState state) {
    final seated = state.present;
    final color = seated ? kGreen : kGray;
    return Container(
      key: const ValueKey('presence'),
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 9),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .14),
        borderRadius: BorderRadius.circular(99),
        border: Border.all(color: color.withValues(alpha: .45)),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(seated ? Icons.event_seat : Icons.person_off_outlined,
            size: 18, color: color),
        const SizedBox(width: 8),
        Text(seated ? '착석 중' : '자리 비움',
            style: TextStyle(
                color: color, fontWeight: FontWeight.w700, fontSize: 15)),
      ]),
    );
  }

  Widget _hero(PostureState state, PostureLook look) => Column(children: [
        Container(
          width: 200,
          height: 200,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: look.color.withValues(alpha: .12),
            border: Border.all(color: look.color, width: 3),
            boxShadow: [
              BoxShadow(
                  color: look.color.withValues(alpha: .3),
                  blurRadius: 40,
                  spreadRadius: 4)
            ],
          ),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(look.icon, color: look.color, size: 52),
            const SizedBox(height: 6),
            SizedBox(
              width: 164,
              // 라벨 길이가 제각각이라('바른 자세' vs '기준 측정 중') 고정 글자
              // 크기로 두면 원 밖으로 넘친다.
              child: FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(look.ko,
                    key: const ValueKey('posture-label'),
                    style: TextStyle(
                        fontSize: 30,
                        fontWeight: FontWeight.w800,
                        color: look.color)),
              ),
            ),
            Text(look.en,
                style: const TextStyle(
                    fontSize: 11, letterSpacing: 3, color: kMuted)),
          ]),
        ),
        const SizedBox(height: 16),
        SizedBox(
          width: 520,
          child: Text(look.desc,
              textAlign: TextAlign.center,
              maxLines: 2,
              style: const TextStyle(fontSize: 16, color: kInk)),
        ),
      ]);

  Widget _metrics(PostureState state) {
    final nod = state.nodPerMin;
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: 12,
      runSpacing: 12,
      children: [
        _tile('머리 거리', _headText(state.headDeltaMm), '기준 대비 · − 가까움 + 멀어짐'),
        _tile('꾸벅임', nod == null ? '—' : '${nod.toStringAsFixed(1)}회/분',
            '분당 끄덕임 횟수'),
        _tile('움직임', state.motion.toStringAsFixed(2), '직전 프레임 대비 변화'),
        _tile('화면 점유', '${(state.coverage * 100).round()}%', '센서에 잡힌 사람 크기'),
      ],
    );
  }

  static String _headText(double? mm) {
    if (mm == null) return '—';
    return '${mm < 0 ? '−' : '+'}${mm.abs().round()}mm';
  }

  Widget _tile(String label, String value, String hint) => Container(
        width: 190,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: kSurface,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kLine),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: const TextStyle(fontSize: 12, color: kMuted)),
          const SizedBox(height: 4),
          Text(value,
              style: const TextStyle(
                  fontSize: 22, fontWeight: FontWeight.w700, color: kInk)),
          const SizedBox(height: 2),
          Text(hint, style: const TextStyle(fontSize: 10, color: kDim)),
        ]),
      );

  /// 허브 `Signal(phi, delta)` 로 그대로 들어가는 값. 옆 앱과 같은 이름으로 부른다.
  Widget _bars(PostureState state) => SizedBox(
        width: 520,
        child: Row(children: [
          Expanded(child: _bar('집중 저하', state.focusDrop, kBlue)),
          const SizedBox(width: 20),
          Expanded(child: _bar('피로', state.fatigue, kAmber)),
        ]),
      );

  Widget _bar(String label, double value, Color color) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          Text(label, style: const TextStyle(fontSize: 12, color: kMuted)),
          Text('${(value * 100).round()}%',
              style: TextStyle(
                  fontSize: 12, fontWeight: FontWeight.w700, color: color)),
        ]),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: LinearProgressIndicator(
            value: value,
            minHeight: 8,
            backgroundColor: kSurface,
            valueColor: AlwaysStoppedAnimation(color),
          ),
        ),
      ]);

  Widget _reasons(PostureState state) => Wrap(
        alignment: WrapAlignment.center,
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final reason in state.reasons)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: kSurface,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: kLine),
              ),
              child: Text(reasonLabel(reason),
                  style:
                      const TextStyle(fontSize: 13, color: Color(0xFFC7D4EE))),
            ),
        ],
      );

  Widget _footer(PostureState? state, DateTime now) {
    final health = _health;
    final bits = <String>[
      if (state?.node != null) 'node ${state!.node}',
      if (health != null)
        'stage ${health.stage} · ${health.fps.toStringAsFixed(1)}fps',
      if (state != null) '갱신 ${state.ageFrom(now).inSeconds}초 전',
      _source.label,
    ];
    return Text(bits.join('   ·   '),
        textAlign: TextAlign.center,
        style: const TextStyle(fontSize: 11, color: kDim));
  }
}
