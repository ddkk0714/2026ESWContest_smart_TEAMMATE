import 'package:deskmate_display/display_state.dart';
import 'package:deskmate_display/settings_store.dart';
import 'package:deskmate_display/state_source.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('buildFeedbackData (feedback/user data)', () {
    test('keeps the contract fields and adds note only when given', () {
      expect(
        buildFeedbackData(
            requestId: 'session-12-q-3', verdict: 'accept', responseMs: 4200),
        {'request_id': 'session-12-q-3', 'verdict': 'accept', 'response_ms': 4200},
      );
      expect(
        buildFeedbackData(
            requestId: 'atlas-display',
            verdict: 'correct',
            correctedState: 'REST',
            note: ' 전화 받는 중 '),
        {
          'request_id': 'atlas-display',
          'verdict': 'correct',
          'corrected_state': 'REST',
          'response_ms': 0,
          'note': '전화 받는 중',
        },
      );
    });

    test('an empty note is dropped and a long one is clipped', () {
      expect(
          buildFeedbackData(requestId: 'atlas-display', verdict: 'reject', note: '   ')
              .containsKey('note'),
          isFalse);
      final long = 'a' * (kFeedbackNoteMaxChars + 50);
      final data =
          buildFeedbackData(requestId: 'atlas-display', verdict: 'reject', note: long);
      expect((data['note'] as String).length, kFeedbackNoteMaxChars);
    });
  });

  group('DisplayState preview fields', () {
    Map<String, dynamic> envelope(Map<String, dynamic> summary) => {
          'schema_version': '1.0',
          'ts': 1769000000.0,
          'seq': 3,
          'data': {
            'fsm_state': 'FOCUS_PC',
            'phase': 'focus',
            'context': 'pc',
            'c_focus': 0.2,
            'c_fatigue': 0.3,
            'confidence': 0.5,
            'gate': 'none',
            'sensor_summary': summary,
          },
        };

    test('respiration only with resp_valid, drowsy state and explanation', () {
      final s = DisplayState.fromEnvelope(envelope({
        'mmwave': {
          'resp_bpm': 15,
          'resp_valid': true,
          'drowsy_state': 'DROWSY',
        },
        'personalize': {'explanation': '  CO₂ 높음 · 환기 제안 '},
      }));
      expect(s.mmwaveRespBpm, 15);
      expect(s.mmwaveDrowsyState, 'DROWSY');
      expect(s.interventionExplanation, 'CO₂ 높음 · 환기 제안');

      final noLock = DisplayState.fromEnvelope(envelope({
        'mmwave': {'resp_bpm': 15, 'resp_valid': false},
        'personalize': {'explanation': ''},
      }));
      expect(noLock.mmwaveRespBpm, isNull);
      expect(noLock.mmwaveDrowsyState, isNull);
      expect(noLock.interventionExplanation, isNull);

      final none = DisplayState.fromEnvelope(envelope({}));
      expect(none.mmwaveRespBpm, isNull);
      expect(none.interventionExplanation, isNull);
    });
  });

  group('DeskmateSettings preview_host', () {
    test('round trip preview_host and drop an invalid one', () {
      const settings = DeskmateSettings(previewHost: '172.20.10.3');
      final json = settings.toJson();
      expect(json['preview_host'], '172.20.10.3');
      expect(json.containsKey('preview_mode'), isFalse);
      final back = DeskmateSettings.fromJson(json);
      expect(back.previewHost, '172.20.10.3');
      expect(back.isEmpty, isFalse);
      expect(DeskmateSettings.empty.toJson().containsKey('preview_host'), isFalse);

      final loaded = DeskmateSettings.fromJson({
        'preview_host': 'http://bad host',
        'mqtt_host': '192.168.1.20',
      });
      expect(loaded.previewHost, isNull);
      expect(loaded.mqttHost, '192.168.1.20');
      expect(loaded.copyWith(previewHost: 'pi4').previewHost, 'pi4');
      expect(loaded.copyWith(previewHost: 'pi4').mqttHost, '192.168.1.20');
      expect(
          loaded
              .copyWith(previewHost: 'pi4')
              .copyWith(clearPreviewHost: true)
              .previewHost,
          isNull);
    });
  });
}
