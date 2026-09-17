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
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('\uC13C\uC11C \uC804\uCCB4',
              style: Theme.of(context).textTheme.headlineLarge),
          const SizedBox(height: 5),
          Text('\uC2E4\uC2DC\uAC04 \uD658\uACBD\uC13C\uC11C \uB370\uC774\uD130',
              style: Theme.of(context).textTheme.bodyMedium),
          const SizedBox(height: 18),
          Expanded(
              child: ListView(children: [
            _SectionTitle(title: '\uD658\uACBD'),
            _SensorGrid(children: [
              _SensorValueCard(
                  icon: Icons.co2_rounded,
                  label: 'CO\u2082',
                  value: state.co2Ppm?.toString() ?? '--',
                  unit: 'ppm'),
              _SensorValueCard(
                  icon: Icons.thermostat_rounded,
                  label: '\uC628\uB3C4',
                  value: state.temperatureC?.toStringAsFixed(1) ?? '--',
                  unit: '\u00B0C'),
              _SensorValueCard(
                  icon: Icons.water_drop_outlined,
                  label: '\uC2B5\uB3C4',
                  value: state.humidityPct?.toStringAsFixed(1) ?? '--',
                  unit: '%'),
              _SensorValueCard(
                  icon: Icons.light_mode_outlined,
                  label: '\uC870\uB3C4',
                  value: state.lux?.toString() ?? '--',
                  unit: 'lx'),
            ]),
            const SizedBox(height: 20),
            _SectionTitle(title: 'mmWave'),
            _SensorGrid(children: [
              _SensorValueCard(
                  icon: Icons.person_search_rounded,
                  label: '\uC7AC\uC2E4',
                  value: state.present == null
                      ? '--'
                      : state.present!
                          ? '\uAC10\uC9C0'
                          : '\uC5C6\uC74C',
                  unit: ''),
              _SensorValueCard(
                  icon: Icons.directions_run_rounded,
                  label: '\uC6C0\uC9C1\uC784',
                  value: _motionLabel(state.mmwaveMotionState),
                  unit: state.mmwaveMotionLevel == null
                      ? ''
                      : state.mmwaveMotionLevel.toString() + ' / 100'),
              _SensorValueCard(
                  icon: Icons.straighten_rounded,
                  label: '\uAC70\uB9AC',
                  value: state.mmwaveDistanceCm?.toString() ?? '--',
                  unit: 'cm'),
              _SensorValueCard(
                  icon: Icons.favorite_outline_rounded,
                  label: '\uC2EC\uBC15',
                  value: state.mmwaveHeartBpm?.toString() ?? '--',
                  unit: 'bpm'),
            ]),
          ])),
        ]),
      );
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle({required this.title});
  final String title;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Text(title, style: Theme.of(context).textTheme.titleMedium),
      );
}

class _SensorGrid extends StatelessWidget {
  const _SensorGrid({required this.children});
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => GridView.count(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        crossAxisCount: 2,
        crossAxisSpacing: 16,
        mainAxisSpacing: 16,
        childAspectRatio: 2.7,
        children: children,
      );
}

String _motionLabel(String? state) {
  switch (state) {
    case 'active':
      return '\uD65C\uBC1C';
    case 'still':
      return '\uC815\uC9C0';
    case 'none':
      return '\uC5C6\uC74C';
    default:
      return '--';
  }
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
