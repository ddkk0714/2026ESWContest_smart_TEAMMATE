import 'package:flutter/material.dart';
import 'client.dart';

void main() => runApp(PocApp(client: NativePocClient()));

class PocApp extends StatelessWidget {
  const PocApp({super.key, required this.client});
  final PocClient client;
  @override
  Widget build(BuildContext context) => MaterialApp(
      theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
      home: TrialPage(client: client));
}

class TrialPage extends StatefulWidget {
  const TrialPage({super.key, required this.client});
  final PocClient client;
  @override
  State<TrialPage> createState() => _TrialPageState();
}

class _TrialPageState extends State<TrialPage> {
  Map<String, dynamic> _result = {'status': 'not_read'};
  Map<String, dynamic> _fields = {};
  Map<String, dynamic> _lastExecution = {};
  Object? _receivedAt;
  Map<String, dynamic>? _state;
  bool _busy = false;
  bool _enabled = false;
  Future<Map<String, dynamic>> _exchange(Map<String, dynamic> command) async {
    if (_busy) return {'status': 'busy'};
    setState(() => _busy = true);
    final result =
        await widget.client.exchange({'backend': 'fixture', ...command});
    if (!mounted) return result;
    setState(() {
      _busy = false;
      _result = result;
      if (result['received_at'] != null) _receivedAt = result['received_at'];
      if (result.containsKey('api_accepted')) _lastExecution = result;
      _enabled = result['post_enabled'] == true;
      if (result['fields'] is Map) {
        _fields = Map<String, dynamic>.from(result['fields']);
      }
      if (result['state'] is Map) {
        _state = Map<String, dynamic>.from(result['state']);
      }
    });
    return result;
  }

  Future<void> _control(String value) async {
    final plan = await _exchange({'op': 'prepare', 'value': value});
    if (!mounted ||
        plan['status'] != 'approval_required' ||
        plan['nonce'] is! String ||
        plan['value'] != value) {
      return;
    }
    var checked = false;
    final approved = await showDialog<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title: Text('Fixture windStrength → $value'),
                  content: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Text(
                        '가상 시험입니다. 실제 ThinQ POST·가전 제어를 실행하지 않습니다. 승인 유효시간 30초.'),
                    CheckboxListTile(
                        value: checked,
                        title:
                            const Text('가상 POWER_ON / CLEAN 상태와 외부 조작 없음을 확인'),
                        onChanged: (value) =>
                            update(() => checked = value == true)),
                  ]),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('취소')),
                    FilledButton(
                        onPressed:
                            checked ? () => Navigator.pop(context, true) : null,
                        child: const Text('승인'))
                  ],
                )));
    if (approved != true || !mounted) return;
    await _exchange({
      'op': 'execute',
      'nonce': plan['nonce'],
      'approved': true,
      'physical_online_confirmed': true,
      'no_external_change': true
    });
  }

  @override
  void dispose() {
    widget.client.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('독립 ThinQ PoC · Fixture ONLY')),
        body: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text('실측 아님 · Live GET/POST 차단 · 자동 제어 없음',
                  style: TextStyle(color: Colors.deepOrange, fontSize: 20)),
              const Text('AS183HWWA / AIR_910604_WW — PM 단위·PM2 의미·측정 시각 미확인'),
              Wrap(spacing: 8, children: [
                FilledButton(
                    onPressed: _busy ? null : () => _exchange({'op': 'read'}),
                    child: const Text('Fixture 목록·Profile·State GET')),
                OutlinedButton(
                    onPressed: _busy
                        ? null
                        : () => _exchange({'op': 'read', 'backend': 'live'}),
                    child: const Text('Live 차단 사유 확인')),
              ]),
              SwitchListTile(
                  title: const Text('Fixture POST 시험 활성화 (기본 OFF)'),
                  value: _enabled,
                  onChanged: _busy
                      ? null
                      : (v) => _exchange(
                          {'op': 'enable_fixture_post', 'enabled': v})),
              Text('상태: ${_result['status']}'),
              if (_result.containsKey('wind_profile_verified'))
                Text(
                    'Profile LOW/AUTO 읽기·쓰기 지원: ${_result['wind_profile_verified']}'),
              if (_result['error'] != null) Text('오류: ${_result['error']}'),
              if (_result['category'] != null)
                Text('오류 종류: ${_result['category']}'),
              if (_result['http_status'] != null)
                Text(
                    'HTTP: ${_result['http_status']} / 코드: ${_result['code']}'),
              if (_state != null)
                Text(
                    '조회한 풍량: ${_state!['windStrength']} · 전원: ${_state!['power']} · 모드: ${_state!['job']} (이전 조회값)'),
              for (final key in [
                'pm1',
                'pm2',
                'pm10',
                'odor',
                'total_pollution',
                'filter_remain_percent',
                'monitoring_mode'
              ])
                Text(
                    '$key: ${_fields[key]?['valid'] == true ? _fields[key]['value'] : '미확인'} · ${_fields[key]?['status'] ?? 'not_read'}'),
              Text('마지막 API 응답 수신시각: $_receivedAt / 실제 측정 시각: 미확인'),
              const Text('표시값은 마지막 성공 조회 스냅샷입니다. 오류 후 새 실측값으로 취급하지 않습니다.'),
              const Text('API 응답 수신 시각과 실제 센서 측정 시각은 다릅니다.'),
              Wrap(spacing: 8, children: [
                FilledButton(
                    onPressed:
                        !_enabled || _busy ? null : () => _control('LOW'),
                    child: const Text('LOW 승인 시험')),
                FilledButton(
                    onPressed:
                        !_enabled || _busy ? null : () => _control('AUTO'),
                    child: const Text('AUTO 복원 승인 시험')),
                OutlinedButton(
                    onPressed: _busy || _result['status'] != 'state_match'
                        ? null
                        : () => _exchange({
                              'op': 'physical_confirmation',
                              'approved': true,
                              'value': _result['value']
                            }),
                    child: const Text('가상 본체 확인 기록')),
              ]),
              Text(
                  'POST 접수: ${_lastExecution['api_accepted'] ?? '미확인'} / 별도 GET 일치: ${_lastExecution['state_match'] ?? '미확인'}'),
              const Text('실제 물리 동작 확인: 미검증 — Fixture 응답은 실기 성공 근거가 아닙니다.'),
              if (_busy) const LinearProgressIndicator(),
            ])),
      );
}
