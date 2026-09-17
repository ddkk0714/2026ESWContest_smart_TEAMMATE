import 'package:flutter/material.dart';

import 'deskmate_theme.dart';
import 'display_state.dart';

/// DESKMATE가 현재 수신 중인 센서값을 한곳에서 보여주는 모니터 페이지다.
///
/// 새 센서군은 별도 섹션으로 추가할 수 있도록 환경 섹션과 값 카드를 분리한다.
class SensorOverviewPage extends StatelessWidget {
  const SensorOverviewPage({super.key, required this.state});

  final DisplayState state;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(8, 16, 8, 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('센서 전체', style: Theme.of(context).textTheme.headlineLarge),
            const SizedBox(height: 5),
            Text(
              '실시간 환경센서 데이터',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const SizedBox(height: 18),
            Expanded(
              child: _SensorSection(
                title: '환경',
                children: [
                  _SensorValueCard(
                    icon: Icons.co2_rounded,
                    label: 'CO₂',
                    value: state.co2Ppm?.toString() ?? '--',
                    unit: 'ppm',
                  ),
                  _SensorValueCard(
                    icon: Icons.thermostat_rounded,
                    label: '온도',
                    value: state.temperatureC?.toStringAsFixed(1) ?? '--',
                    unit: '°C',
                  ),
                  _SensorValueCard(
                    icon: Icons.water_drop_outlined,
                    label: '습도',
                    value: state.humidityPct?.toStringAsFixed(1) ?? '--',
                    unit: '%',
                  ),
                  _SensorValueCard(
                    icon: Icons.light_mode_outlined,
                    label: '조도',
                    value: state.lux?.toString() ?? '--',
                    unit: 'lx',
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}

class _SensorSection extends StatelessWidget {
  const _SensorSection({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          Expanded(
            child: GridView.count(
              physics: const NeverScrollableScrollPhysics(),
              crossAxisCount: 2,
              crossAxisSpacing: 16,
              mainAxisSpacing: 16,
              childAspectRatio: 2.7,
              children: children,
            ),
          ),
        ],
      );
}

class _SensorValueCard extends StatelessWidget {
  const _SensorValueCard({
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
