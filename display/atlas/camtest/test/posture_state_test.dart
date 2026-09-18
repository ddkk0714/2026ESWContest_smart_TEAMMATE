import 'package:flutter_test/flutter_test.dart';
import 'package:camtest/posture_state.dart';

/// Pi 4 노드가 실제로 내는 `/api/state` 한 장.
/// 값은 `espcam_with_decision` 의 `pi/README.md` 예시를 그대로 옮겼다.
Map<String, dynamic> envelope({
  Map<String, dynamic>? posture,
  bool dropPosture = false,
  String fsmState = 'POSTURE_SLUMP',
  String schema = '1.0',
}) {
  final sensors = <String, dynamic>{
    'present': true,
    'scenario': '엎드림 · 자세 판정만 표시 중 (허브 미연결)',
    if (!dropPosture)
      'posture': <String, dynamic>{
        'present': true,
        'posture': 'slouch',
        'posture_detail': 'slump',
        'motion_score': 0.03,
        'head_delta_mm': -104.2,
        'nod_rate_hz': 0.0,
        'valid_zones': 2268,
        'grid_width': 54,
        'grid_height': 42,
        'coverage_ratio': 0.19,
        'phi': 0.02,
        'delta': 0.86,
        'valid': true,
        ...?posture,
      },
  };
  return <String, dynamic>{
    'schema_version': schema,
    'ts': 1758000000.0,
    'node': 'pi4-posture',
    'boot_id': 'boot-1',
    'seq': 42,
    'data': <String, dynamic>{
      'fsm_state': fsmState,
      'phase': 'fatigue',
      'context': 'mixed',
      'c_focus': 0.02,
      'c_fatigue': 0.86,
      'confidence': 0.86,
      'source': 'posture',
      'gate': 'none',
      'cause': 'posture',
      'reasons': ['posture_only', 'head_dropped_and_held'],
      'sensor_summary': sensors,
    },
  };
}

void main() {
  test('노드 payload 를 그대로 읽는다', () {
    final state = PostureState.fromEnvelope(envelope());

    expect(state.label, PostureLabel.slump);
    expect(state.present, isTrue);
    expect(state.valid, isTrue);
    expect(state.needsCorrection, isTrue);
    expect(state.headDeltaMm, closeTo(-104.2, 1e-9));
    expect(state.nodPerMin, 0.0);
    expect(state.motion, closeTo(0.03, 1e-9));
    expect(state.coverage, closeTo(0.19, 1e-9));
    expect(state.focusDrop, closeTo(0.02, 1e-9));
    expect(state.fatigue, closeTo(0.86, 1e-9));
    expect(state.sequence, 42);
    expect(state.node, 'pi4-posture');
    expect(state.reasons, ['posture_only', 'head_dropped_and_held']);
    expect(state.scenario, contains('허브 미연결'));
  });

  test('꾸벅임은 Hz 로 와서 분당 횟수로 나간다', () {
    final state = PostureState.fromEnvelope(
        envelope(posture: {'nod_rate_hz': 0.075, 'posture_detail': 'drowsy'}));

    expect(state.nodPerMin, closeTo(4.5, 1e-9));
  });

  test('꾸벅임은 계약 enum 에 좁혀져 와도 detail 로 되살린다', () {
    // 계약에는 drowsy 가 없어 lean_forward 로 좁혀 보낸다. detail 을 먼저
    // 보지 않으면 화면이 꾸벅임을 '앞으로 기움'으로 읽는다.
    final state = PostureState.fromEnvelope(envelope(
        posture: {'posture': 'lean_forward', 'posture_detail': 'drowsy'}));

    expect(state.label, PostureLabel.drowsy);
  });

  test('detail 이 없으면 계약 enum 으로 읽는다', () {
    final json = envelope();
    final posture = (json['data'] as Map)['sensor_summary']['posture'] as Map;
    posture.remove('posture_detail');
    posture['posture'] = 'lean_back';

    expect(PostureState.fromEnvelope(json).label, PostureLabel.recline);
  });

  test('posture 블록이 통째로 없으면 fsm_state 로 읽는다', () {
    final state = PostureState.fromEnvelope(
        envelope(dropPosture: true, fsmState: 'POSTURE_ABSENT'));

    expect(state.label, PostureLabel.absent);
    // present 는 sensor_summary 쪽 값이 남아 있으면 그쪽을 쓴다.
    expect(state.present, isTrue);
    expect(state.focusDrop, closeTo(0.02, 1e-9));
    expect(state.fatigue, closeTo(0.86, 1e-9));
  });

  test('못 잰 값은 0 이 아니라 null 로 남는다', () {
    final json = envelope();
    final posture = (json['data'] as Map)['sensor_summary']['posture'] as Map;
    // 머리를 못 찾은 프레임에서 노드가 null 로 보낸다.
    posture['head_delta_mm'] = null;
    posture['nod_rate_hz'] = null;

    final state = PostureState.fromEnvelope(json);
    expect(state.headDeltaMm, isNull);
    expect(state.nodPerMin, isNull);
  });

  test('기준을 잡는 중이면 판정으로 세지 않는다', () {
    final state = PostureState.fromEnvelope(envelope(
        posture: {'posture_detail': 'baseline', 'valid': false},
        fsmState: 'POSTURE_BASELINE'));

    expect(state.label, PostureLabel.baseline);
    expect(state.calibrating, isTrue);
    expect(state.valid, isFalse);
    expect(state.needsCorrection, isFalse);
  });

  test('자리 비움은 못 잰 것이 아니라 잰 결과다', () {
    final state = PostureState.fromEnvelope(envelope(posture: {
      'present': false,
      'posture': 'away',
      'posture_detail': 'absent',
      'valid': true,
    }));

    expect(state.label, PostureLabel.absent);
    expect(state.present, isFalse);
    expect(state.valid, isTrue);
    expect(state.calibrating, isFalse);
    expect(state.needsCorrection, isFalse);
  });

  test('valid 가 안 오면 노드와 같은 규칙으로 정한다', () {
    final json = envelope();
    final posture = (json['data'] as Map)['sensor_summary']['posture'] as Map;
    posture.remove('valid');
    posture['posture_detail'] = 'unknown';

    expect(PostureState.fromEnvelope(json).valid, isFalse);
  });

  test('모르는 schema 는 읽지 않는다', () {
    expect(() => PostureState.fromEnvelope(envelope(schema: '2.0')),
        throwsFormatException);
  });

  test('시계가 뒤로 가도 나이는 음수가 되지 않는다', () {
    final state = PostureState.fromEnvelope(envelope());
    final before = state.timestamp.subtract(const Duration(seconds: 5));

    expect(state.ageFrom(before), Duration.zero);
    expect(state.ageFrom(state.timestamp.add(const Duration(seconds: 7))),
        const Duration(seconds: 7));
  });
}
