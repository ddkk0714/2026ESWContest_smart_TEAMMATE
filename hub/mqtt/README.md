# Pi 4 MQTT broker

Pi 4 is the MQTT broker for the direct Pi 4↔Pi 5 Ethernet link. The broker
must not be part of the FSM decision path: its absence limits display and
development monitoring, while the hub remains safe.

The initial laboratory configuration permits anonymous clients only on the
private Pi 4 development-LAN binding and carries feature envelopes only. Do
not expose TCP 1883 to the Internet or send raw ToF arrays, key contents,
tokens, or credentials. Add a runtime-provisioned password file and ACL before
using an untrusted shared network.

The Pi 5 app connects with these build defines:

```bash
--dart-define=DESKMATE_MQTT_HOST=<Pi4-IP> \
--dart-define=DESKMATE_MQTT_PORT=1883
```

See [`../../docs/mqtt-topics.md`](../../docs/mqtt-topics.md) for the topic and
payload contract.
