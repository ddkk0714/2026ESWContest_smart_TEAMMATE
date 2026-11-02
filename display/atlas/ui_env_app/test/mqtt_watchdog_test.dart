import 'package:deskmate_display/mqtt_watchdog.dart';
import 'package:flutter_test/flutter_test.dart';

final _t0 = DateTime(2026, 9, 30, 12);
DateTime _at(int seconds) => _t0.add(Duration(seconds: seconds));

void main() {
  test('never acts before the first state arrives', () {
    final dog = MqttWatchdog();
    expect(dog.shouldReconnect(_at(600), null), isFalse);
  });

  test('waits until the state is older than 15 s', () {
    final dog = MqttWatchdog();
    expect(dog.shouldReconnect(_at(15), _t0), isFalse);
    expect(dog.shouldReconnect(_at(16), _t0), isTrue);
    expect(dog.attempts, 1);
  });

  test('backs off 15 s, 30 s, 60 s and caps at 2 min', () {
    final dog = MqttWatchdog();
    expect(dog.shouldReconnect(_at(16), _t0), isTrue); // 1st
    expect(dog.shouldReconnect(_at(30), _t0), isFalse);
    expect(dog.shouldReconnect(_at(31), _t0), isTrue); // +15 s
    expect(dog.backoff, const Duration(seconds: 30));
    expect(dog.shouldReconnect(_at(60), _t0), isFalse);
    expect(dog.shouldReconnect(_at(61), _t0), isTrue); // +30 s
    expect(dog.shouldReconnect(_at(121), _t0), isTrue); // +60 s
    expect(dog.shouldReconnect(_at(241), _t0), isTrue); // +120 s
    expect(dog.backoff, const Duration(minutes: 2));
    expect(dog.shouldReconnect(_at(361), _t0), isTrue); // stays at 2 min
    expect(dog.backoff, const Duration(minutes: 2));
  });

  test('over MQTT only a new hub sequence counts as fresh state', () {
    // 10-26 Pi 5: fetch() kept returning the cached state of a link that had
    // stopped delivering, so every fetch looked fresh and the watchdog never fired.
    expect(isFreshState(mqtt: true, previousSequence: null, sequence: 450), isTrue);
    expect(isFreshState(mqtt: true, previousSequence: 450, sequence: 450), isFalse);
    expect(isFreshState(mqtt: true, previousSequence: 450, sequence: 451), isTrue);
    expect(isFreshState(mqtt: true, previousSequence: 451, sequence: 1), isTrue); // hub restarted
    expect(isFreshState(mqtt: false, previousSequence: 3, sequence: 3), isTrue);
  });

  test('a received state resets the backoff', () {
    final dog = MqttWatchdog();
    dog.shouldReconnect(_at(16), _t0);
    dog.shouldReconnect(_at(31), _t0);
    dog.stateReceived();
    expect(dog.attempts, 0);
    expect(dog.backoff, const Duration(seconds: 15));
    expect(dog.shouldReconnect(_at(50), _at(34)), isTrue);
  });
}
