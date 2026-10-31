// Manual repro, not part of the suite — run.sh copies it into test/ inside docker.
// The app's MqttStateSource against a real broker that run.sh restarts mid-run;
// prints every control/cmd it receives.
import 'dart:async';
import 'dart:io';

import 'package:deskmate_display/state_source.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('receives after broker restart', () async {
    final host = Platform.environment['MQTT_HOST']!;
    final seconds = int.parse(Platform.environment['RUN_S'] ?? '75');
    String t() => DateTime.now().toIso8601String().substring(11, 19);
    final source = MqttStateSource(host,
        port: 1883,
        onTopicEvent: (topic, env) =>
            print('${t()} GOT $topic ${(env['data'] as Map?)?['command_id']}'));
    final end = DateTime.now().add(Duration(seconds: seconds));
    var ready = false;
    while (DateTime.now().isBefore(end)) {
      if (!ready && source.isConnected) {
        ready = true;
        print('${t()} READY');
      }
      try {
        await source.fetch().timeout(const Duration(seconds: 3));
      } catch (e) {
        final text = '$e';
        if (!text.contains('기다리고')) print('${t()} fetch: ${text.split('\n').first}');
      }
      await Future<void>.delayed(const Duration(seconds: 1));
    }
    source.close();
  }, timeout: const Timeout(Duration(minutes: 5)));
}
