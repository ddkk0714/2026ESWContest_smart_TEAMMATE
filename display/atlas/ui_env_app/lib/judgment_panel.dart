/// 개발자 모드의 판정 패널 — hub 판정을 큰 글씨·단색·고정폭으로.
///
/// 꾸미지 않는다. 값이 없으면 "-". 두 자세를 나란히 둔다:
///   - 자세 서비스(:8771) 가 지금 말하는 라벨·확신 — Pi 5 가 직접 읽은 것
///   - hub 가 FSM 입력으로 **실제로 쓴** 자세(`sensor_summary.tof.posture`) 와 그 출처(service|mlp|rule)
/// 둘이 다르면 hub 설정(`ingest.yaml tof.posture_source`)이 서비스를 안 보고 있다는 뜻이다.
library;

import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';
import 'tof_raw_client.dart';

class JudgmentPanel extends StatelessWidget {
  const JudgmentPanel({super.key, required this.state, required this.posture});

  /// `deskmate/state/phase` 의 최신 상태. 아직 없으면 null.
  final DisplayState? state;

  /// 자세 서비스(:8771) 의 최신 라벨. 아직 없으면 null.
  final TofPostureLabel? posture;

  static const _mono = TextStyle(
      fontFamily: 'monospace',
      fontFamilyFallback: ['Courier New', 'Menlo', 'DejaVu Sans Mono'],
      fontSize: 14,
      height: 1.25,
      color: DeskmateColors.ink);

  @override
  Widget build(BuildContext context) {
    final s = state;
    final p = posture;
    final rows = <(String, String, String)>[
      ('C_fatigue', ratio(s?.fatigue), 'judge-fatigue'),
      ('C_focus', ratio(s?.focus), 'judge-focus'),
      ('cause', s?.cause ?? '-', 'judge-cause'),
      ('gate', s?.gate ?? '-', 'judge-gate'),
      ('자세(hub)', hubPosture(s), 'judge-posture-hub'),
      ('자세(svc)', servicePosture(p), 'judge-posture-svc'),
      ('재실', presence(s), 'judge-presence'),
      ('위치', p?.placement ?? '-', 'judge-placement'),
      ('개인화', s?.interventionExplanation ?? '-', 'judge-personalize'),
    ];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            const Text('FSM ', style: _mono),
            Expanded(
              child: Text(
                s?.fsmState ?? '-',
                key: const ValueKey('judge-state'),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: _mono.copyWith(fontSize: 28, fontWeight: FontWeight.w700),
              ),
            ),
            Text(s == null ? '' : 'seq ${s.sequence}',
                style: _mono.copyWith(
                    fontSize: 12, color: DeskmateColors.inkMuted)),
          ]),
      const SizedBox(height: 2),
      for (final (label, value, key) in rows)
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
              width: 130,
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: _mono.copyWith(color: DeskmateColors.inkMuted))),
          Expanded(
            child: Text(value,
                key: ValueKey(key),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: _mono),
          ),
        ]),
    ]);
  }

  static String ratio(double? v) => v == null ? '-' : v.toStringAsFixed(2);

  /// `posture (source, conf)`. 자세가 없으면 "-".
  static String hubPosture(DisplayState? s) {
    final posture = s?.tofPosture;
    if (posture == null || posture.isEmpty) return '-';
    final detail = <String>[
      s?.tofPostureSource ?? '?',
      if (s?.tofPostureConfidence != null)
        s!.tofPostureConfidence!.toStringAsFixed(2),
    ];
    return '$posture (${detail.join(', ')})';
  }

  /// `label conf` + 센서 수. 서비스 응답이 없으면 "-", 입력이 없으면(valid=false) 라벨만.
  static String servicePosture(TofPostureLabel? p) {
    if (p == null) return '-';
    if (!p.valid) return '${p.posture} (입력 없음, 센서 ${p.onlineSensors})';
    return '${p.posture} ${p.conf.toStringAsFixed(2)}'
        '${p.misplaced ? ' misplaced' : ''} (센서 ${p.onlineSensors})';
  }

  /// `present <source> · in_room <bool> · <distance> cm`. 전부 없으면 "-".
  static String presence(DisplayState? s) {
    if (s == null) return '-';
    final parts = <String>[
      if (s.present != null) 'present=${s.present}',
      if (s.presenceSource != null) 'src=${s.presenceSource}',
      if (s.inRoom != null) 'in_room=${s.inRoom}',
      if (s.mmwaveDistanceCm != null) '${s.mmwaveDistanceCm} cm',
    ];
    return parts.isEmpty ? '-' : parts.join(' · ');
  }
}
