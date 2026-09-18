import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';

/// DESKMATE가 현재 수신 중인 센서값을 한곳에서 보여주는 모니터 페이지다.
///
/// 센서군마다 섹션을 하나씩 둔다. 새 센서군(ToF 등)은 섹션을 추가하면 된다.
/// 값이 안 들어오는 항목은 0 이 아니라 `--` 로 둔다 — mmWave 심박은 락온이
/// 풀리면 통째로 끊기는데, 그걸 0 으로 그리면 "심박이 0" 으로 읽힌다.
class SensorOverviewPage extends StatelessWidget {
  const SensorOverviewPage({super.key, required this.state});

  final DisplayState state;

  @override
  Widget build(BuildContext context) {
    final mmwave = state.mmwave;
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 16, 8, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('센서 전체', style: Theme.of(context).textTheme.headlineLarge),
          const SizedBox(height: 5),
          Text(
            '실시간 mmWave · 환경센서 데이터',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 18),
          Expanded(
            child: ListView(
              padding: EdgeInsets.zero,
              children: [
                SensorSection(
                  title: '생체 · mmWave',
                  subtitle: _mmwaveSubtitle(state, mmwave),
                  children: [
                    SensorValueCard(
                      icon: Icons.favorite_outline,
                      label: '심박',
                      value: mmwave?.heartBpm?.toString() ?? '--',
                      unit: 'bpm',
                    ),
                    SensorValueCard(
                      icon: Icons.air_rounded,
                      label: '호흡',
                      value: mmwave?.respBpm?.toString() ?? '--',
                      unit: '회/분',
                    ),
                    SensorValueCard(
                      icon: Icons.straighten_rounded,
                      label: '거리',
                      value: mmwave?.distanceCm?.toString() ?? '--',
                      unit: 'cm',
                    ),
                    SensorValueCard(
                      icon: Icons.directions_walk_rounded,
                      label: '체동',
                      value: mmwave?.motionLevel?.toString() ?? '--',
                      unit: '/100',
                    ),
                  ],
                ),
                const SizedBox(height: 22),
                SensorSection(
                  title: '환경',
                  children: [
                    SensorValueCard(
                      icon: Icons.co2_rounded,
                      label: 'CO₂',
                      value: state.co2Ppm?.toString() ?? '--',
                      unit: 'ppm',
                    ),
                    SensorValueCard(
                      icon: Icons.thermostat_rounded,
                      label: '온도',
                      value: state.temperatureC?.toStringAsFixed(1) ?? '--',
                      unit: '°C',
                    ),
                    SensorValueCard(
                      icon: Icons.water_drop_outlined,
                      label: '습도',
                      value: state.humidityPct?.toStringAsFixed(1) ?? '--',
                      unit: '%',
                    ),
                    SensorValueCard(
                      icon: Icons.light_mode_outlined,
                      label: '조도',
                      value: state.lux?.toString() ?? '--',
                      unit: 'lx',
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 재실·졸음 판정은 ESP32 가 이미 끝내서 보낸 값이다. 화면은 그 라벨을 그대로
/// 옮겨 적는다 — 같은 상태를 보드 로그와 화면이 다르게 부르면 시연 중에
/// 맞춰 볼 수가 없다.
String _mmwaveSubtitle(DisplayState state, MmwaveSummary? mmwave) {
  if (mmwave == null) return '수신 없음';
  final present = state.present;
  final drowsy = mmwave.drowsyState;
  return [
    if (present != null) present ? '재실' : '자리 비움',
    if (drowsy != null) drowsyLabel(drowsy),
  ].join(' · ');
}

/// 펌웨어 `drowsyStateName()` 이 보내는 코드 → 사람이 읽는 한국어.
/// 모르는 코드는 그대로 보여준다 — 안 보여주면 화면에서 확인할 길이 없어진다.
String drowsyLabel(String code) => switch (code) {
      'NOPERSON' => '사람 없음',
      'NOLOCK' => '판정 불가 (가림·거리)',
      'WARMUP' => '측정 준비 중',
      'AWAKE' => '각성',
      'DROWSY' => '졸음',
      _ => code,
    };

class SensorSection extends StatelessWidget {
  const SensorSection({
    super.key,
    required this.title,
    required this.children,
    this.subtitle,
  });

  final String title;
  final String? subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final subtitle = this.subtitle;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          if (subtitle != null) ...[
            const SizedBox(width: 10),
            Flexible(
              child: Text(
                subtitle,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.labelMedium,
              ),
            ),
          ],
        ]),
        const SizedBox(height: 10),
        GridView.count(
          padding: EdgeInsets.zero,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          crossAxisCount: 2,
          crossAxisSpacing: 16,
          mainAxisSpacing: 16,
          childAspectRatio: 2.7,
          children: children,
        ),
      ],
    );
  }
}

class SensorValueCard extends StatelessWidget {
  const SensorValueCard({
    super.key,
    required this.icon,
    required this.label,
    required this.value,
    required this.unit,
  });

  final IconData icon;
  final String label;
  final String value;
  final String unit;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 18),
        decoration: BoxDecoration(
          color: DeskmateColors.surface,
          borderRadius: BorderRadius.circular(DeskmateRadius.panel),
          border: Border.all(color: DeskmateColors.line),
        ),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: const BoxDecoration(
                color: DeskmateColors.surfaceRaised,
                shape: BoxShape.circle,
              ),
              child: Icon(icon, color: DeskmateColors.accentStrong, size: 23),
            ),
            const SizedBox(width: 18),
            Expanded(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label, style: Theme.of(context).textTheme.labelMedium),
                  const SizedBox(height: 8),
                  Text(
                    '$value $unit',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.headlineMedium,
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}
