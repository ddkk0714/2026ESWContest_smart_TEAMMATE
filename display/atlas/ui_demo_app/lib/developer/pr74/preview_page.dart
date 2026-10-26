/// 프리뷰(개발자) 모드 화면 — 정식 기능이 아니다.
///
/// 모든 센서를 한 화면에 모아 보고, "지금 판정이 맞나요?" 에 답을 남기는 곳이다.
/// 발표(11-06)의 키포인트인 **ToF 8×8 열지도 두 장**이 위에 있다: 카메라 없이 이 64 칸이
/// 자세 판정의 전부라는 걸 눈으로 보여 준다. 얼굴·영상은 없다.
///
/// - ToF 격자·자세 라벨: Pi 4 중계기(:8772/raw)·자세 서비스(:8771)를 직접 폴링한다
///   ([PreviewTofSource]). 주소는 설정 → MQTT 호스트 순으로 받고, 없으면 입력칸을 띄운다.
/// - 나머지 값(mmWave·환경·키스트로크·FSM·개입 설명)은 `deskmate/state/phase` 로 이미
///   들어온 [DisplayState] 를 그대로 보여 준다. 없는 값은 "—".
/// - 피드백은 기존 `feedback/user` 경로를 그대로 쓴다. 이 화면은 [PreviewFeedback] 만 만들고
///   전송은 main 이 한다(연결·request_id 를 main 이 안다).
///
/// 카메라 기반 자세 판정(`posture_screen.dart`)은 이 화면에 넣지 않는다.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';
import 'intervention.dart' show correctionOptions;
import 'tof_heatmap.dart';
import 'tof_raw_client.dart';

/// 화면이 만든 피드백. main 이 `StateSource.feedback` 으로 보낸다.
class PreviewFeedback {
  const PreviewFeedback(this.verdict, {this.correctedState, this.note});

  /// accept | reject | correct
  final String verdict;

  /// correct 일 때만. FOCUS_PC · FATIGUE · REST · IDLE 중 하나.
  final String? correctedState;

  /// 짧은 메모(선택). 비어 있으면 null.
  final String? note;

  @override
  String toString() =>
      'PreviewFeedback($verdict, corrected=$correctedState, note=$note)';
}

typedef PreviewTofSourceFactory = PreviewTofSource Function(String host);

class PreviewPage extends StatefulWidget {
  const PreviewPage({
    super.key,
    required this.state,
    required this.host,
    required this.onHostChanged,
    required this.onFeedback,
    this.feedbackEnabled = true,
    this.sourceFactory = HttpPreviewTofSource.new,
    this.pollInterval = const Duration(milliseconds: 500),
  });

  /// 지금 화면에 그리는 허브 상태. 아직 없으면 null.
  final DisplayState? state;

  /// ToF 를 읽을 Pi 4 주소. null 이면 입력칸을 띄운다.
  final String? host;
  final ValueChanged<String> onHostChanged;
  final Future<void> Function(PreviewFeedback feedback) onFeedback;

  /// 개발자 화면으로 상태를 고정 중이면 false — 가짜 상태에 대한 라벨을 hub 에 남기지 않는다.
  final bool feedbackEnabled;
  final PreviewTofSourceFactory sourceFactory;
  final Duration pollInterval;

  @override
  State<PreviewPage> createState() => _PreviewPageState();
}

class _PreviewPageState extends State<PreviewPage> {
  PreviewTofSource? _source;
  Timer? _timer;
  bool _inFlight = false;
  TofRawSnapshot? _raw;
  TofPostureLabel? _posture;
  String? _rawError;
  String? _postureError;
  int _fails = 0;
  DateTime? _lastOkAt;

  late final TextEditingController _hostInput;
  late final TextEditingController _noteInput;
  bool _editingHost = false;
  String? _verdict;
  String? _corrected;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _hostInput = TextEditingController(text: widget.host ?? '');
    _noteInput = TextEditingController();
    _openSource(widget.host);
  }

  @override
  void didUpdateWidget(PreviewPage old) {
    super.didUpdateWidget(old);
    if (old.host != widget.host) {
      _hostInput.text = widget.host ?? '';
      _openSource(widget.host);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _source?.close();
    _hostInput.dispose();
    _noteInput.dispose();
    super.dispose();
  }

  void _openSource(String? host) {
    _timer?.cancel();
    _source?.close();
    _source = null;
    _raw = null;
    _posture = null;
    _rawError = null;
    _postureError = null;
    _fails = 0;
    _lastOkAt = null;
    if (host == null || host.isEmpty) return;
    _source = widget.sourceFactory(host);
    _timer = Timer.periodic(widget.pollInterval, (_) => _tick());
    _tick();
  }

  Future<void> _tick() async {
    // 응답이 폴링 주기보다 늦으면 요청을 겹치지 않는다(보드 TCP 소켓을 안 쌓이게).
    if (_inFlight) return;
    final source = _source;
    if (source == null) return;
    _inFlight = true;
    try {
      final results = await Future.wait<Object?>([
        source.fetchRaw().then<Object?>((v) => v, onError: (Object e) => e),
        source.fetchPosture().then<Object?>((v) => v, onError: (Object e) => e),
      ]);
      if (!mounted || !identical(source, _source)) return;
      final raw = results[0];
      final posture = results[1];
      setState(() {
        if (raw is TofRawSnapshot) {
          _raw = raw;
          _rawError = null;
          _fails = 0;
          _lastOkAt = DateTime.now();
        } else {
          _rawError = _shortError(raw);
          _fails++;
        }
        if (posture is TofPostureLabel) {
          _posture = posture;
          _postureError = null;
        } else {
          _postureError = _shortError(posture);
        }
      });
    } finally {
      _inFlight = false;
    }
  }

  static String _shortError(Object? e) {
    final text = e.toString();
    final nl = text.indexOf('\n');
    final line = nl < 0 ? text : text.substring(0, nl);
    return line.length > 80 ? '${line.substring(0, 80)}…' : line;
  }

  void _applyHost() {
    final host = normalizePreviewHost(_hostInput.text);
    if (host == null) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('주소 모양이 아니에요. 예: 172.20.10.3')));
      return;
    }
    setState(() => _editingHost = false);
    widget.onHostChanged(host);
  }

  bool get _canSend {
    if (!widget.feedbackEnabled || _sending || _verdict == null) return false;
    if (_verdict == 'correct' && _corrected == null) return false;
    return true;
  }

  Future<void> _send() async {
    final verdict = _verdict;
    if (verdict == null || !_canSend) return;
    final note = _noteInput.text.trim();
    final feedback = PreviewFeedback(
      verdict,
      correctedState: verdict == 'correct' ? _corrected : null,
      note: note.isEmpty ? null : note,
    );
    setState(() => _sending = true);
    try {
      await widget.onFeedback(feedback);
      if (!mounted) return;
      setState(() {
        _verdict = null;
        _corrected = null;
        _noteInput.clear();
      });
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = widget.state;
    final host = widget.host;
    // 한 화면보다 길어 스크롤한다. 지연 생성(ListView)이 아니라 전부 만들어 두는 이유:
    // 아래쪽 피드백 패널까지 항상 트리에 있어야 외부 상태(고정 해제 등)가 바로 반영된다.
    return SingleChildScrollView(
      key: const ValueKey('preview-page'),
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        // ── 머리: 프리뷰 배지 · 프라이버시 문구 ───────────────────────────────
        Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
          const _PreviewBadge(),
          const SizedBox(width: 10),
          Text('전 센서 프리뷰', style: theme.textTheme.titleLarge),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              '얼굴·영상 없음 · 64칸 깊이값만',
              key: const ValueKey('preview-privacy-note'),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(fontWeight: FontWeight.w600),
            ),
          ),
          _hostControl(theme, host),
        ]),
        const SizedBox(height: 10),
        // ── ToF 열지도 두 장 + 자세 라벨 ──────────────────────────────────────
        _Panel(
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              flex: 3,
              child: TofHeatmap(
                frame: _raw?.sensor(1),
                title: 'ToF B · 오른쪽',
                keyPrefix: 'tof-b',
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              flex: 3,
              child: TofHeatmap(
                frame: _raw?.sensor(0),
                title: 'ToF A · 왼쪽',
                keyPrefix: 'tof-a',
              ),
            ),
            const SizedBox(width: 14),
            Expanded(flex: 4, child: _postureCard(theme, host)),
          ]),
        ),
        const SizedBox(height: 10),
        // ── 나머지 센서 · FSM ─────────────────────────────────────────────────
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: _Panel(
              child: _Section('mmWave', [
                _Value('재실', _present(s?.present)),
                _Value(
                    '움직임', _motion(s?.mmwaveMotionState, s?.mmwaveMotionLevel)),
                _Value('졸음', _drowsy(s?.mmwaveDrowsyState)),
                _Value('호흡', _unit(s?.mmwaveRespBpm, 'bpm')),
                _Value('거리', _unit(s?.mmwaveDistanceCm, 'cm')),
              ]),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: _Panel(
              child: _Section('환경', [
                _Value('CO₂', _unit(s?.co2Ppm, 'ppm')),
                _Value('온도', _unit(s?.temperatureC?.toStringAsFixed(1), '°C')),
                _Value('습도', _unit(s?.humidityPct?.toStringAsFixed(0), '%')),
                _Value('조도', _unit(s?.lux, 'lx')),
                _Value(
                    '플래그',
                    (s?.envFlags.isEmpty ?? true)
                        ? '—'
                        : s!.envFlags.join(', ')),
              ]),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: _Panel(
              child: _Section('키스트로크', [
                _Value('타이핑', _typing(s?.keystroke)),
                _Value('이벤트/창', _unit(s?.keystroke?.eventCount, '회')),
                _Value(
                    'flight', _unit(s?.keystroke?.flightMeanMs?.round(), 'ms')),
                _Value('리듬 CV', _ratio(s?.keystroke?.flightCv)),
                _Value('교정률', _ratio(s?.keystroke?.correctionRate)),
              ]),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            flex: 1,
            child: _Panel(
              child: _Section('FSM', [
                _Value('상태', s?.fsmState ?? '—', key: 'preview-fsm-state'),
                _Value('국면', s?.phase ?? '—'),
                _Value('C_fatigue', _ratio(s?.fatigue)),
                _Value('C_focus', _ratio(s?.focus)),
                _Value('게이트', s?.gate ?? '—'),
                _Value('원인', s?.cause ?? '—'),
              ]),
            ),
          ),
        ]),
        const SizedBox(height: 10),
        _Panel(
          child: Row(children: [
            const Icon(Icons.tips_and_updates_outlined,
                size: 18, color: DeskmateColors.inkMuted),
            const SizedBox(width: 8),
            Text('마지막 개입', style: theme.textTheme.labelMedium),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                s?.interventionExplanation ?? '—',
                key: const ValueKey('preview-explanation'),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyLarge,
              ),
            ),
            if (s != null && s.reasons.isNotEmpty) ...[
              const SizedBox(width: 12),
              Text(s.reasons.join(' · '),
                  style: theme.textTheme.labelMedium,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
            ],
          ]),
        ),
        const SizedBox(height: 10),
        // ── 피드백 ───────────────────────────────────────────────────────────
        _Panel(child: _feedbackBlock(theme, s)),
      ]),
    );
  }

  Widget _hostControl(ThemeData theme, String? host) {
    if (host == null || _editingHost) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        SizedBox(
          width: 190,
          child: TextField(
            key: const ValueKey('preview-host-input'),
            controller: _hostInput,
            autofocus: host != null,
            decoration: const InputDecoration(
              isDense: true,
              labelText: 'Pi 4 주소',
              hintText: '172.20.10.3',
            ),
            onSubmitted: (_) => _applyHost(),
          ),
        ),
        const SizedBox(width: 6),
        FilledButton(
          key: const ValueKey('preview-host-apply'),
          onPressed: _applyHost,
          child: const Text('연결'),
        ),
      ]);
    }
    return TextButton.icon(
      key: const ValueKey('preview-host-edit'),
      onPressed: () => setState(() => _editingHost = true),
      icon: const Icon(Icons.lan_outlined, size: 16),
      label: Text(host),
    );
  }

  Widget _postureCard(ThemeData theme, String? host) {
    final p = _posture;
    final String big;
    final Color color;
    if (host == null) {
      big = '주소 없음';
      color = DeskmateColors.inkFaint;
    } else if (p == null) {
      big = _postureError == null ? '연결 중…' : '연결 없음';
      color = DeskmateColors.inkFaint;
    } else if (!p.valid) {
      big = p.label;
      color = DeskmateColors.inkMuted;
    } else {
      big = p.label;
      color = DeskmateColors.ink;
    }
    final status = <String>[
      if (p != null && p.valid)
        '확신 ${(p.conf * 100).round()} %'
      else if (p != null)
        '센서 입력 없음',
      if (p != null) 'present ${p.present}',
      if (p != null && p.misplaced) '위치 재조정',
      if (p != null) '센서 ${p.onlineSensors}개',
    ].join(' · ');
    final String link;
    if (host == null) {
      link = 'Pi 4 주소를 넣으면 ToF 격자와 자세 라벨을 읽어요';
    } else if (_rawError != null) {
      link = 'raw 연결 실패 ($_fails): $_rawError';
    } else if (_postureError != null) {
      link = '자세 서비스 연결 실패: $_postureError';
    } else if (_lastOkAt == null) {
      link = '$host 연결 중…';
    } else {
      final t = _lastOkAt!;
      link = 'ok ${_two(t.hour)}:${_two(t.minute)}:${_two(t.second)}'
          '${_raw == null ? '' : ' · frames ${_raw!.frames} · bad ${_raw!.bad}'}';
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('ToF 자세 판정 (Pi 4)',
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w800)),
      const SizedBox(height: 6),
      FittedBox(
        fit: BoxFit.scaleDown,
        alignment: Alignment.centerLeft,
        child: Text(big,
            key: const ValueKey('preview-posture-label'),
            style: theme.textTheme.displayLarge?.copyWith(
                fontSize: 44, fontWeight: FontWeight.w600, color: color)),
      ),
      const SizedBox(height: 4),
      Text(status.isEmpty ? '—' : status, style: theme.textTheme.bodyMedium),
      const SizedBox(height: 8),
      Text(link,
          key: const ValueKey('preview-tof-status'),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.labelMedium?.copyWith(
              color: (_rawError ?? _postureError) == null
                  ? DeskmateColors.inkMuted
                  : DeskmateColors.offline)),
      const SizedBox(height: 8),
      Text(
        '색 = 거리 (붉음 20 cm … 푸름 2 m), 회색 = 측정 없음. 숫자는 cm.\n'
        '센서가 보는 방향: B · A 순서. 칸은 펌웨어 방향 그대로, 다시 뒤집지 않음.',
        style: theme.textTheme.labelMedium,
      ),
    ]);
  }

  Widget _feedbackBlock(ThemeData theme, DisplayState? s) {
    final current = s?.fsmState ?? '—';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text('지금 판정이 맞나요?',
            style: theme.textTheme.titleMedium
                ?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(width: 10),
        Chip(
          label: Text(current),
          visualDensity: VisualDensity.compact,
          backgroundColor: DeskmateColors.surfaceMuted,
          side: BorderSide.none,
        ),
        const Spacer(),
        if (!widget.feedbackEnabled)
          Text('테스트 상태 고정 중 · hub 로 보내지 않아요',
              key: const ValueKey('preview-feedback-disabled'),
              style: theme.textTheme.labelMedium),
      ]),
      const SizedBox(height: 4),
      Text('판정은 바꾸지 않고 라벨로만 기록해요. 메모는 짧게(키 값·센서 원본은 싣지 않아요).',
          style: theme.textTheme.bodyMedium),
      const SizedBox(height: 10),
      Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            _verdictButton('accept', '맞아요', Icons.check_rounded),
            _verdictButton('reject', '아니에요', Icons.close_rounded),
            _verdictButton('correct', '정정할게요', Icons.edit_outlined),
            if (_verdict == 'correct') ...[
              const SizedBox(width: 6),
              for (final (state, label) in correctionOptions)
                ChoiceChip(
                  key: ValueKey('preview-correct-$state'),
                  label: Text(label),
                  selected: _corrected == state,
                  onSelected: (_) => setState(() => _corrected = state),
                ),
            ],
          ]),
      const SizedBox(height: 10),
      Row(children: [
        Expanded(
          child: TextField(
            key: const ValueKey('preview-note'),
            controller: _noteInput,
            maxLength: 200,
            maxLines: 1,
            decoration: const InputDecoration(
              isDense: true,
              labelText: '메모 (선택)',
              hintText: '예: 전화 받느라 자리를 비웠어요',
              counterText: '',
            ),
          ),
        ),
        const SizedBox(width: 10),
        FilledButton.icon(
          key: const ValueKey('preview-send'),
          onPressed: _canSend ? _send : null,
          icon: const Icon(Icons.send_rounded, size: 18),
          label: Text(_sending ? '보내는 중…' : '보내기'),
        ),
      ]),
    ]);
  }

  Widget _verdictButton(String verdict, String label, IconData icon) {
    final selected = _verdict == verdict;
    final child = Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(icon, size: 16),
      const SizedBox(width: 6),
      Text(label),
    ]);
    void pick() => setState(() {
          _verdict = verdict;
          if (verdict != 'correct') _corrected = null;
        });
    return selected
        ? FilledButton(
            key: ValueKey('preview-verdict-$verdict'),
            onPressed: pick,
            child: child)
        : OutlinedButton(
            key: ValueKey('preview-verdict-$verdict'),
            onPressed: pick,
            child: child);
  }

  static String _two(int v) => v.toString().padLeft(2, '0');

  static String _present(bool? v) => v == null ? '—' : (v ? '감지' : '없음');

  static String _motion(String? state, int? level) {
    final name = switch (state) {
      'active' => '활발',
      'still' => '정지',
      'none' => '없음',
      _ => null,
    };
    if (name == null) return '—';
    return level == null ? name : '$name · $level/100';
  }

  static String _drowsy(String? v) => switch (v) {
        'AWAKE' => '깨어 있음',
        'DROWSY' => '졸음',
        'WARMUP' => '준비 중',
        'NOLOCK' => '락 없음',
        'NOPERSON' => '사람 없음',
        null => '—',
        _ => v,
      };

  static String _typing(KeystrokeMetrics? k) {
    if (k == null) return '—';
    if (k.valid == false) return '무효';
    return switch (k.typingActive) {
      true => '타이핑 중',
      false => '멈춤',
      null => '—',
    };
  }

  static String _unit(Object? v, String unit) => v == null ? '—' : '$v $unit';

  static String _ratio(double? v) => v == null ? '—' : v.toStringAsFixed(2);
}

class _PreviewBadge extends StatelessWidget {
  const _PreviewBadge();

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('preview-badge'),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: DeskmateColors.warning.withValues(alpha: .18),
          borderRadius: BorderRadius.circular(DeskmateRadius.pill),
          border: Border.all(color: DeskmateColors.warning),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: const [
          Icon(Icons.science_outlined, size: 15, color: DeskmateColors.warning),
          SizedBox(width: 5),
          Text('센서 프리뷰',
              style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w800,
                  color: DeskmateColors.warning)),
        ]),
      );
}

class _Section extends StatelessWidget {
  const _Section(this.title, this.rows);
  final String title;
  final List<_Value> rows;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title,
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w800)),
      const SizedBox(height: 6),
      for (final r in rows) ...[
        Row(children: [
          SizedBox(
            width: 68,
            child: Text(r.label,
                style: theme.textTheme.labelMedium,
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
          ),
          Expanded(
            child: Text(r.value,
                key: r.key == null ? null : ValueKey(r.key),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyLarge
                    ?.copyWith(fontWeight: FontWeight.w600)),
          ),
        ]),
        const SizedBox(height: 2),
      ],
    ]);
  }
}

class _Value {
  const _Value(this.label, this.value, {this.key});
  final String label;
  final String value;
  final String? key;
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: DeskmateColors.surfaceRaised,
          borderRadius: BorderRadius.circular(DeskmateRadius.panel),
          border: Border.all(color: DeskmateColors.line),
        ),
        child: child,
      );
}
