import 'dart:async';
import 'dart:math' as math;
import 'package:dbus/dbus.dart';
import 'package:flutter/material.dart';

class DeskNetworkStatus {
  const DeskNetworkStatus(
      {this.available = true,
      this.checking = false,
      this.wifiConnected = false,
      this.wifiConnecting = false,
      this.ethernetConnected = false,
      this.ssid,
      this.strength});
  final bool available;
  final bool checking;
  final bool wifiConnected;
  final bool wifiConnecting;
  final bool ethernetConnected;
  final String? ssid;
  final int? strength;

  String get label {
    if (checking) return '네트워크 확인 중';
    if (!available) return '네트워크 상태를 확인할 수 없어요.';
    final wifi = wifiConnected
        ? 'Wi-Fi 연결됨${ssid == null || ssid!.isEmpty ? '' : ' · $ssid'}${strength == null ? '' : ' · 신호 $strength%'}'
        : wifiConnecting
            ? 'Wi-Fi 연결 중'
            : 'Wi-Fi 미연결';
    return '$wifi${ethernetConnected ? '\n유선 LAN 연결됨' : ''}';
  }

  /// Saved or visible access points are not connections; ready/online are.
  static DeskNetworkStatus fromServices(
      Iterable<Map<String, Object?>> services) {
    var ethernet = false;
    Map<String, Object?>? wifi;
    int rank(Object? state) => switch (state) {
          'online' => 4,
          'ready' => 3,
          'configuration' => 2,
          'association' => 1,
          _ => 0
        };
    for (final service in services) {
      if (service['Type'] == 'ethernet' && rank(service['State']) >= 3) {
        ethernet = true;
      }
      if (service['Type'] == 'wifi' &&
          rank(service['State']) > 0 &&
          (wifi == null || rank(service['State']) > rank(wifi['State']))) {
        wifi = service;
      }
    }
    final value = wifi?['Strength'];
    return DeskNetworkStatus(
        wifiConnected: rank(wifi?['State']) >= 3,
        wifiConnecting: wifi != null && rank(wifi['State']) < 3,
        ethernetConnected: ethernet,
        ssid: wifi?['Name'] is String ? wifi!['Name'] as String : null,
        strength: value is int && value >= 0 && value <= 100 ? value : null);
  }
}

abstract interface class NetworkStatusSource {
  Future<DeskNetworkStatus> read();
  Future<void> close();
}

/// ConnMan status is publicly readable on Atlas; no scan, connection changes,
/// secrets or additional network-management permission is requested.
class AtlasNetworkStatusSource implements NetworkStatusSource {
  AtlasNetworkStatusSource({DBusClient? client})
      : _client = client ?? DBusClient.system(),
        _ownsClient = client == null;
  final DBusClient _client;
  final bool _ownsClient;
  @override
  Future<DeskNetworkStatus> read() async {
    final manager = DBusRemoteObject(_client,
        name: 'net.connman', path: DBusObjectPath('/'));
    final result = await manager.callMethod(
        'net.connman.Manager', 'GetServices', [],
        replySignature: DBusSignature('a(oa{sv})'), noAutoStart: true);
    if (result.returnValues.length != 1 ||
        result.returnValues.single is! DBusArray) {
      throw const FormatException('Invalid ConnMan services reply');
    }
    return decode(result.returnValues.single as DBusArray);
  }

  static DeskNetworkStatus decode(DBusArray array) {
    final services = <Map<String, Object?>>[];
    for (final entry in array.children) {
      if (entry is! DBusStruct ||
          entry.children.length != 2 ||
          entry.children[1] is! DBusDict) {
        continue;
      }
      final properties = <String, Object?>{};
      for (final property in (entry.children[1] as DBusDict).children.entries) {
        if (property.key is! DBusString) continue;
        final value = property.value;
        properties[(property.key as DBusString).value] =
            value is DBusVariant ? value.value.toNative() : value.toNative();
      }
      services.add(properties);
    }
    return DeskNetworkStatus.fromServices(services);
  }

  @override
  Future<void> close() async {
    if (_ownsClient) await _client.close();
  }
}

class NetworkStatusIndicator extends StatefulWidget {
  const NetworkStatusIndicator({super.key, this.source});
  final NetworkStatusSource? source;
  @override
  State<NetworkStatusIndicator> createState() => _NetworkStatusIndicatorState();
}

class _NetworkStatusIndicatorState extends State<NetworkStatusIndicator> {
  late final NetworkStatusSource _source;
  late final Timer _poll;
  Timer? _readDeadline;
  DeskNetworkStatus _status = const DeskNetworkStatus(checking: true);
  bool _busy = false;
  @override
  void initState() {
    super.initState();
    _source = widget.source ?? AtlasNetworkStatusSource();
    unawaited(_refresh());
    _poll = Timer.periodic(const Duration(seconds: 5), (_) => _refresh());
  }

  Future<void> _refresh() async {
    if (_busy) return;
    _busy = true;
    _readDeadline = Timer(const Duration(seconds: 3), () {
      if (mounted) {
        setState(() => _status = const DeskNetworkStatus(available: false));
      }
    });
    DeskNetworkStatus status;
    try {
      status = await _source.read();
    } catch (_) {
      status = const DeskNetworkStatus(available: false);
    }
    _busy = false;
    _readDeadline?.cancel();
    _readDeadline = null;
    if (mounted) setState(() => _status = status);
  }

  @override
  void dispose() {
    _poll.cancel();
    _readDeadline?.cancel();
    if (widget.source == null) {
      unawaited(_source.close().catchError((Object _) {}));
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Tooltip(
        message: _status.label,
        triggerMode: TooltipTriggerMode.tap,
        child: Semantics(
            label: _status.label,
            child: SizedBox(
                height: 38,
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  if (_status.ethernetConnected) ...[
                    Icon(Icons.settings_ethernet_rounded,
                        key: const ValueKey('network-ethernet'),
                        size: 18,
                        color: scheme.primary),
                    const SizedBox(width: 8),
                  ],
                  if (_status.checking ||
                      !_status.available ||
                      _status.wifiConnecting)
                    Icon(Icons.wifi_find_rounded,
                        key: const ValueKey('network-wifi-pending'),
                        size: 22,
                        color: scheme.onSurfaceVariant)
                  else
                    CustomPaint(
                        key: const ValueKey('network-wifi'),
                        size: const Size(24, 24),
                        painter: _WifiPainter(
                            _status.wifiConnected,
                            _status.strength,
                            scheme.primary,
                            scheme.onSurfaceVariant)),
                ]))));
  }
}

class _WifiPainter extends CustomPainter {
  _WifiPainter(this.connected, this.strength, this.ink, this.muted);
  final bool connected;
  final int? strength;
  final Color ink;
  final Color muted;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 24, size.height / 24);
    final level = strength == null
        ? 0
        : strength! >= 67
            ? 3
            : strength! >= 34
                ? 2
                : strength! > 0
                    ? 1
                    : 0;
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeCap = StrokeCap.round;
    for (var i = 0; i < 3; i++) {
      paint.color = connected && i < level
          ? ink
          : muted.withValues(alpha: connected ? .22 : .40);
      canvas.drawArc(
          Rect.fromCircle(
              center: const Offset(12, 21), radius: [6.0, 11.5, 17.0][i]),
          -130 * math.pi / 180,
          80 * math.pi / 180,
          false,
          paint);
    }
    canvas.drawCircle(const Offset(12, 20), 1.6,
        Paint()..color = connected ? ink : muted.withValues(alpha: .40));
    if (!connected) {
      canvas.drawLine(
          const Offset(4, 4), const Offset(21, 21), paint..color = muted);
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_WifiPainter old) =>
      connected != old.connected ||
      strength != old.strength ||
      ink != old.ink ||
      muted != old.muted;
}
