import 'package:deskmate_display/link_status.dart';
import 'package:flutter_test/flutter_test.dart';

final _t0 = DateTime(2026, 9, 30, 10);
DateTime _at(int seconds) => _t0.add(Duration(seconds: seconds));

void main() {
  group('evaluateMqtt', () {
    test('demo build is not a problem', () {
      final s = evaluateMqtt(now: _t0, configured: false, demo: true);
      expect(s.cause, LinkCause.demo);
      expect(s.needsAttention, isFalse);
    });

    test('missing broker address asks for configuration', () {
      final s = evaluateMqtt(now: _t0, configured: false);
      expect(s.health, LinkHealth.down);
      expect(s.cause, LinkCause.notConfigured);
      expect(s.guidance, contains('Pi 4 IP'));
    });

    test('before the first attempt finishes it is only checking', () {
      final s = evaluateMqtt(now: _t0, configured: true);
      expect(s.health, LinkHealth.checking);
      expect(s.needsAttention, isFalse);
    });

    test('connect failure reports the diagnosed step', () {
      for (final cause in [
        LinkCause.networkUnreachable,
        LinkCause.brokerRefused
      ]) {
        final s = evaluateMqtt(now: _t0, configured: true, connectError: cause);
        expect(s.health, LinkHealth.down);
        expect(s.cause, cause);
      }
      expect(linkGuidance(LinkId.mqtt, LinkCause.brokerRefused),
          contains('pi4-broker-start.sh'));
    });

    test('state within the stale window is ok even if the flag flickers', () {
      final s = evaluateMqtt(
          now: _at(15), configured: true, connected: false, lastStateAt: _t0);
      expect(s.health, LinkHealth.ok);
      expect(s.lastSeen, _t0);
    });

    test('connected but silent past 15 s is stale', () {
      final s = evaluateMqtt(
          now: _at(16), configured: true, connected: true, lastStateAt: _t0);
      expect(s.health, LinkHealth.degraded);
      expect(s.cause, LinkCause.stale);
    });

    test('lost after having state is disconnected, not a setup problem', () {
      final s = evaluateMqtt(
          now: _at(30),
          configured: true,
          connected: false,
          lastStateAt: _t0,
          connectError: LinkCause.networkUnreachable);
      expect(s.cause, LinkCause.disconnected);
    });

    test('connected without a first state waits 10 s, then blames the hub side',
        () {
      final waiting = evaluateMqtt(
          now: _at(10), configured: true, connected: true, connectedAt: _t0);
      expect(waiting.health, LinkHealth.checking);
      final late = evaluateMqtt(
          now: _at(11), configured: true, connected: true, connectedAt: _t0);
      expect(late.health, LinkHealth.degraded);
      expect(late.cause, LinkCause.noState);
    });

    test('parse error wins over freshness so it is not hidden', () {
      final s = evaluateMqtt(
          now: _at(1),
          configured: true,
          connected: true,
          lastStateAt: _t0,
          parseError: 'deskmate/state/phase: FormatException');
      expect(s.cause, LinkCause.parseError);
      expect(s.detail, contains('FormatException'));
    });
  });

  group('evaluateHub', () {
    final mqttOk =
        LinkStatus(id: LinkId.mqtt, health: LinkHealth.ok, lastSeen: _t0);

    test('offline LWT is down with the activate script hint', () {
      final s = evaluateHub(
          now: _t0, mqtt: mqttOk, healthStatus: 'offline', lastStateAt: _t0);
      expect(s.health, LinkHealth.down);
      expect(s.guidance, contains('pi4-hub-activate.sh'));
    });

    test('fresh state means the hub is alive without a health message', () {
      final s = evaluateHub(now: _at(5), mqtt: mqttOk, lastStateAt: _t0);
      expect(s.health, LinkHealth.ok);
    });

    test('unknown while the broker itself is unreachable', () {
      const mqttDown = LinkStatus(
          id: LinkId.mqtt,
          health: LinkHealth.down,
          cause: LinkCause.networkUnreachable);
      final s = evaluateHub(now: _t0, mqtt: mqttDown);
      expect(s.health, LinkHealth.checking);
      expect(s.needsAttention, isFalse);
    });

    test('online but no state points at the FSM side', () {
      const mqttSilent = LinkStatus(
          id: LinkId.mqtt,
          health: LinkHealth.degraded,
          cause: LinkCause.noState);
      final s = evaluateHub(now: _t0, mqtt: mqttSilent, healthStatus: 'online');
      expect(s.cause, LinkCause.noState);
    });
  });

  group('sensors', () {
    test('summary sensor is judged only while state is fresh', () {
      final missing = evaluateSummarySensor(
          id: LinkId.mmwave,
          now: _at(3),
          summaryHasSensor: false,
          lastStateAt: _t0);
      expect(missing.health, LinkHealth.down);
      expect(missing.guidance, contains('ESP32'));

      final present = evaluateSummarySensor(
          id: LinkId.environment,
          now: _at(3),
          summaryHasSensor: true,
          lastStateAt: _t0);
      expect(present.health, LinkHealth.ok);

      final unknown = evaluateSummarySensor(
          id: LinkId.mmwave,
          now: _at(60),
          summaryHasSensor: false,
          lastStateAt: _t0);
      expect(unknown.health, LinkHealth.checking);
    });

    test('keystroke never seen is unused, then stale after 10 s', () {
      expect(evaluateKeystroke(now: _t0).needsAttention, isFalse);
      expect(
          evaluateKeystroke(now: _at(10), sampleAt: _t0).health, LinkHealth.ok);
      final stale = evaluateKeystroke(now: _at(11), sampleAt: _t0);
      expect(stale.cause, LinkCause.stale);
      expect(stale.guidance, contains('collector'));
    });
  });

  group('LinkSnapshot', () {
    LinkSnapshot snapshot(List<LinkStatus> list) => LinkSnapshot(list);

    test('orders attention by severity, then by tab order', () {
      final snap = snapshot([
        const LinkStatus(
            id: LinkId.keystroke,
            health: LinkHealth.degraded,
            cause: LinkCause.stale),
        const LinkStatus(
            id: LinkId.lamp,
            health: LinkHealth.down,
            cause: LinkCause.disconnected),
        const LinkStatus(
            id: LinkId.mmwave, health: LinkHealth.down, cause: LinkCause.stale),
        const LinkStatus(
            id: LinkId.speaker,
            health: LinkHealth.unconfigured,
            cause: LinkCause.notConfigured),
      ]);
      expect(snap.attention.map((s) => s.id),
          [LinkId.mmwave, LinkId.lamp, LinkId.keystroke]);
      expect(snap.headline, 'mmWave 센서 외 2건 확인 필요');
      expect(snap.all.first.id, LinkId.mmwave);
    });

    test('no problem means no badge and no headline', () {
      final snap = snapshot([
        LinkStatus(id: LinkId.mqtt, health: LinkHealth.ok, lastSeen: _t0),
        const LinkStatus(id: LinkId.speaker, health: LinkHealth.unconfigured),
      ]);
      expect(snap.hasProblem, isFalse);
      expect(snap.headline, isNull);
      expect(snap.problemSignature, isEmpty);
    });

    test('signature ignores lastSeen so ticking time does not re-show the bar',
        () {
      final a = snapshot([
        LinkStatus(
            id: LinkId.mqtt,
            health: LinkHealth.degraded,
            cause: LinkCause.stale,
            lastSeen: _t0),
      ]);
      final b = snapshot([
        LinkStatus(
            id: LinkId.mqtt,
            health: LinkHealth.degraded,
            cause: LinkCause.stale,
            lastSeen: _at(9)),
      ]);
      expect(a.problemSignature, b.problemSignature);
    });
  });

  group('StatusBarDismissal', () {
    const staleMqtt = LinkStatus(
        id: LinkId.mqtt, health: LinkHealth.degraded, cause: LinkCause.stale);
    const lampDown = LinkStatus(
        id: LinkId.lamp,
        health: LinkHealth.down,
        cause: LinkCause.disconnected);

    test('stays hidden for the same problem after dismissing', () {
      final bar = StatusBarDismissal();
      final snap = LinkSnapshot([staleMqtt]);
      expect(bar.shouldShow(snap), isTrue);
      bar.dismiss(snap);
      expect(bar.shouldShow(LinkSnapshot([staleMqtt])), isFalse);
    });

    test('shows again when a new problem appears', () {
      final bar = StatusBarDismissal();
      bar.dismiss(LinkSnapshot([staleMqtt]));
      expect(bar.shouldShow(LinkSnapshot([staleMqtt, lampDown])), isTrue);
    });

    test('forgets the dismissal once everything recovers', () {
      final bar = StatusBarDismissal();
      bar.dismiss(LinkSnapshot([staleMqtt]));
      expect(bar.shouldShow(LinkSnapshot(const [])), isFalse);
      expect(bar.shouldShow(LinkSnapshot([staleMqtt])), isTrue);
    });
  });
}
