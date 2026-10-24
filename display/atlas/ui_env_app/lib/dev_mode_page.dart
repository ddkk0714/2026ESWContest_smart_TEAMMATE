/// 개발자 모드 화면 — 예쁘게 말고, 한 화면(1024×600)에 **ToF 열지도 두 장 · hub 판정 · 명령 로그**.
///
/// 주변기기 블루투스 연동이 불안정할 때 램프·선풍기를 실제로 켜지 않아도 hub 가 **무엇을 켜라고 했는지**
/// 글로 확인하려는 화면이다(10-24). 보여 주는 것:
///   - ToF A·B 8×8 열지도: Pi 4 중계기 `:8772/raw` 폴링([TofPoller]). 얼굴·영상 없음.
///   - 판정([JudgmentPanel]): FSM state·C_fatigue·C_focus·cause·gate, 자세 서비스(`:8771`) 라벨과
///     hub 가 실제로 쓴 자세·출처, 재실, 개인화 설명.
///   - 명령 로그([CommandLogPanel]): control/cmd·control/result·interaction/request·feedback/user.
/// 제어 동작은 바꾸지 않는다 — 램프 미연결이면 기존처럼 `failed lamp_not_configured` 가 나가고, 그 줄도 로그에 남는다.
library;

import 'package:flutter/material.dart';

import 'command_log.dart';
import 'command_log_panel.dart';
import 'device_state_board.dart';
import 'deskmate_theme.dart';
import 'display_state.dart';
import 'judgment_panel.dart';
import 'tof_heatmap.dart';
import 'tof_poller.dart';
import 'tof_raw_client.dart';

class DevModePage extends StatefulWidget {
  const DevModePage({
    super.key,
    required this.state,
    required this.host,
    required this.onHostChanged,
    required this.log,
    this.board,
    this.deviceAvailable = const {},
    this.bluetoothLine,
    this.placement,
    required this.defaultOnStart,
    required this.onDefaultOnStartChanged,
    this.connectionLabel,
    this.sourceFactory = HttpPreviewTofSource.new,
    this.pollInterval = const Duration(milliseconds: 300),
  });

  final DisplayState? state;

  /// ToF 를 읽을 Pi 4 주소. null 이면 입력칸.
  final String? host;
  final ValueChanged<String> onHostChanged;
  final CommandLog log;

  /// 기기별 "현재 상태"(hub 마지막 명령 기준, 블루투스 실행 여부 무관). null 이면 줄을 그리지 않는다.
  final DeviceStateBoard? board;

  /// target_id → 앱에 등록된 기기인지. false 면 "기기 없음 · 제어 못함" 을 덧붙인다.
  final Map<String, bool> deviceAvailable;

  /// 블루투스 기기 연결 상태·자동 피드백 스위치 한 줄(main 이 만든다). null 이면 "-".
  final String? bluetoothLine;

  /// 앱에 저장된 장치 위치(left|center|right). 자세 서비스가 준 값이 있으면 그것을 우선 강조한다.
  final String? placement;

  /// 설정 `dev_mode_default` — 앱을 켤 때 이 화면으로.
  final bool defaultOnStart;
  final ValueChanged<bool> onDefaultOnStartChanged;

  /// 상단에 보일 MQTT 연결 상태 글.
  final String? connectionLabel;
  final TofSourceFactory sourceFactory;
  final Duration pollInterval;

  @override
  State<DevModePage> createState() => _DevModePageState();
}

class _DevModePageState extends State<DevModePage> {
  late final TofPoller _poller;
  late final TextEditingController _hostInput;
  bool _editingHost = false;

  static const _mono = TextStyle(
      fontFamily: 'monospace',
      fontFamilyFallback: ['Courier New', 'Menlo', 'DejaVu Sans Mono'],
      fontSize: 12,
      color: DeskmateColors.inkMuted);

  @override
  void initState() {
    super.initState();
    _poller = TofPoller(
        sourceFactory: widget.sourceFactory, interval: widget.pollInterval);
    _poller.addListener(_changed);
    _hostInput = TextEditingController(text: widget.host ?? '');
    _poller.open(widget.host);
  }

  @override
  void didUpdateWidget(DevModePage old) {
    super.didUpdateWidget(old);
    if (old.host != widget.host) {
      _hostInput.text = widget.host ?? '';
      _poller.open(widget.host);
    }
  }

  @override
  void dispose() {
    _poller.removeListener(_changed);
    _poller.dispose();
    _hostInput.dispose();
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() {});
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

  @override
  Widget build(BuildContext context) {
    final host = widget.host;
    return Column(
      key: const ValueKey('devmode-page'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── 한 줄 머리: 제목 · 연결 · 주소 · 시작 화면 스위치 ─────────────────
        Row(children: [
          const Text('개발자 모드',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w800)),
          const SizedBox(width: 10),
          Expanded(
            child: Text(_linkText(host),
                key: const ValueKey('devmode-link'),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: _mono.copyWith(
                    color: (_poller.rawError ?? _poller.postureError) == null
                        ? DeskmateColors.inkMuted
                        : DeskmateColors.offline)),
          ),
          _hostControl(host),
          const SizedBox(width: 8),
          const Text('시작 시 이 화면', style: _mono),
          Switch(
            key: const ValueKey('devmode-default-switch'),
            value: widget.defaultOnStart,
            onChanged: widget.onDefaultOnStartChanged,
          ),
        ]),
        // ── 위: 열지도 A · B · 판정 ───────────────────────────────────────────
        SizedBox(
          height: 226,
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(
              width: 150,
              child: TofHeatmap(
                frame: _poller.raw?.sensor(0),
                title: 'ToF A · 왼쪽',
                keyPrefix: 'tof-a',
              ),
            ),
            const SizedBox(width: 10),
            SizedBox(
              width: 150,
              child: TofHeatmap(
                frame: _poller.raw?.sensor(1),
                title: 'ToF B · 오른쪽',
                keyPrefix: 'tof-b',
              ),
            ),
            const SizedBox(width: 14),
            Expanded(
              flex: 5,
              child: _Box(
                child: JudgmentPanel(
                    state: widget.state,
                    posture: _poller.posture,
                    placement: widget.placement),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              flex: 4,
              child: _Box(
                child: DevInfoPanel(
                    state: widget.state, bluetoothLine: widget.bluetoothLine),
              ),
            ),
          ]),
        ),
        const SizedBox(height: 6),
        // ── 기기 현재 상태(hub 명령 기준) ─────────────────────────────────────
        if (widget.board != null) ...[
          ListenableBuilder(
            listenable: widget.board!,
            builder: (context, _) => Text(
              '${widget.board!.summary(available: widget.deviceAvailable)}'
              '  — hub 마지막 명령 기준, 블루투스 실행과 무관',
              key: const ValueKey('devmode-device-state'),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: _mono.copyWith(fontSize: 13, color: DeskmateColors.ink),
            ),
          ),
          const SizedBox(height: 4),
        ],
        // ── 아래: 명령 로그 ───────────────────────────────────────────────────
        Expanded(child: _Box(child: CommandLogPanel(log: widget.log))),
      ],
    );
  }

  String _linkText(String? host) {
    final parts = <String>[
      if (widget.connectionLabel != null && widget.connectionLabel!.isNotEmpty)
        widget.connectionLabel!,
    ];
    if (host == null) {
      parts.add('Pi 4 주소 없음 — ToF 안 읽음');
    } else if (_poller.rawError != null) {
      parts.add('raw 실패(${_poller.fails}): ${_poller.rawError}');
    } else if (_poller.postureError != null) {
      parts.add('자세 서비스 실패: ${_poller.postureError}');
    } else if (_poller.lastOkAt == null) {
      parts.add('$host 연결 중…');
    } else {
      final t = _poller.lastOkAt!;
      final raw = _poller.raw;
      parts.add('$host ok ${_two(t.hour)}:${_two(t.minute)}:${_two(t.second)}'
          '${raw == null ? '' : ' frames ${raw.frames} bad ${raw.bad}'}');
    }
    return parts.join(' · ');
  }

  Widget _hostControl(String? host) {
    if (host == null || _editingHost) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        SizedBox(
          width: 170,
          height: 36,
          child: TextField(
            key: const ValueKey('devmode-host-input'),
            controller: _hostInput,
            autofocus: host != null,
            style: const TextStyle(fontSize: 13),
            decoration: const InputDecoration(
              isDense: true,
              labelText: 'Pi 4 주소',
              hintText: '172.20.10.3',
            ),
            onSubmitted: (_) => _applyHost(),
          ),
        ),
        const SizedBox(width: 4),
        FilledButton(
          key: const ValueKey('devmode-host-apply'),
          onPressed: _applyHost,
          style: FilledButton.styleFrom(visualDensity: VisualDensity.compact),
          child: const Text('연결'),
        ),
      ]);
    }
    return TextButton.icon(
      key: const ValueKey('devmode-host-edit'),
      onPressed: () => setState(() => _editingHost = true),
      style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
      icon: const Icon(Icons.lan_outlined, size: 14),
      label: Text(host, style: const TextStyle(fontSize: 12)),
    );
  }

  static String _two(int v) => v.toString().padLeft(2, '0');
}

class _Box extends StatelessWidget {
  const _Box({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(10, 6, 10, 6),
        decoration: BoxDecoration(
          color: DeskmateColors.surfaceRaised,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: DeskmateColors.line),
        ),
        child: child,
      );
}

/// 개발자 모드 오른쪽 정보 패널: 블루투스·자동 피드백, 센서 요약(mmWave·CO₂·온습도·조도·키 입력),
/// 2단계 피로 관찰(personalization status·label·confidence)과 개인화 설명. 값이 없으면 "-".
class DevInfoPanel extends StatelessWidget {
  const DevInfoPanel({super.key, required this.state, this.bluetoothLine});

  final DisplayState? state;
  final String? bluetoothLine;

  static const _mono = TextStyle(
      fontFamily: 'monospace',
      fontFamilyFallback: ['Courier New', 'Menlo', 'DejaVu Sans Mono'],
      fontSize: 12.5,
      height: 1.25,
      color: DeskmateColors.ink);

  @override
  Widget build(BuildContext context) {
    final s = state;
    final rows = <(String, String, String)>[
      ('BT', bluetoothLine ?? '-', 'info-bt'),
      ('mmWave', mmwave(s), 'info-mmwave'),
      ('환경', environment(s), 'info-env'),
      ('키 입력', keystroke(s?.keystroke), 'info-keys'),
      ('2단계', observation(s), 'info-personalization'),
      ('설명', s?.interventionExplanation ?? '-', 'info-explanation'),
    ];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      for (final (label, value, key) in rows)
        Padding(
          padding: const EdgeInsets.only(bottom: 3),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(
                width: 62,
                child: Text(label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: _mono.copyWith(color: DeskmateColors.inkMuted))),
            Expanded(
              child: Text(value,
                  key: ValueKey(key),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: _mono),
            ),
          ]),
        ),
    ]);
  }

  static String mmwave(DisplayState? s) {
    if (s == null) return '-';
    final parts = <String>[
      if (s.present != null) (s.present! ? '재실' : '부재'),
      if (s.mmwaveMotionState != null)
        '${s.mmwaveMotionState}${s.mmwaveMotionLevel == null ? '' : ' ${s.mmwaveMotionLevel}'}',
      if (s.mmwaveDistanceCm != null) '${s.mmwaveDistanceCm} cm',
      if (s.mmwaveDrowsyState != null) s.mmwaveDrowsyState!,
      if (s.mmwaveRespBpm != null) '호흡 ${s.mmwaveRespBpm}',
    ];
    return parts.isEmpty ? '-' : parts.join(' · ');
  }

  static String environment(DisplayState? s) {
    if (s == null) return '-';
    final parts = <String>[
      if (s.co2Ppm != null) 'CO₂ ${s.co2Ppm} ppm',
      if (s.temperatureC != null) '${s.temperatureC!.toStringAsFixed(1)}°C',
      if (s.humidityPct != null) '${s.humidityPct!.round()}%',
      if (s.lux != null) '${s.lux} lx',
      if (s.envFlags.isNotEmpty) s.envFlags.join(','),
    ];
    return parts.isEmpty ? '-' : parts.join(' · ');
  }

  static String keystroke(KeystrokeMetrics? k) {
    if (k == null) return '-';
    final parts = <String>[
      if (k.valid == false) '무효',
      if (k.typingActive != null) (k.typingActive! ? '타이핑' : '멈춤'),
      if (k.eventCount != null) '${k.eventCount}회',
      if (k.flightMeanMs != null) 'flight ${k.flightMeanMs!.round()}ms',
      if (k.correctionRate != null) '교정 ${k.correctionRate!.toStringAsFixed(2)}',
    ];
    return parts.isEmpty ? '-' : parts.join(' · ');
  }

  /// `predicted FATIGUE 0.71` 처럼. status 만 있으면 그것(warming_up 등).
  static String observation(DisplayState? s) {
    final status = s?.personalizationStatus;
    if (status == null) return '-';
    final parts = <String>[
      status,
      if (s!.personalizationLabel != null) s.personalizationLabel!,
      if (s.personalizationConfidence != null)
        s.personalizationConfidence!.toStringAsFixed(2),
    ];
    return parts.join(' ');
  }
}
