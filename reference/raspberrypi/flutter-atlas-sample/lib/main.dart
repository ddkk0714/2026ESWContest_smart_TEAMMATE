import 'dart:async';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import 'controllers/bluetooth_controller.dart';
import 'controllers/audio_controller.dart';
// ── 오디오 백엔드: 아래 두 줄 중 하나만 활성화 ──────────────────
import 'controllers/audio_plugin_controller.dart'; // audioplayers_atlas 플러그인
// import 'controllers/audio_dbus_controller.dart';    // D-Bus 직접 구현
// ────────────────────────────────────────────────────────────────
import 'controllers/video_plugin_controller.dart';
import 'controllers/dbus_config.dart';
import 'controllers/permission_controller.dart';

void main() {
  runApp(const SmartKitchenApp());
}

class SmartKitchenApp extends StatelessWidget {
  const SmartKitchenApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Smart Kitchen Dashboard',
      theme: ThemeData(
        brightness: Brightness.dark,
        primarySwatch: Colors.blueGrey,
        scaffoldBackgroundColor: Colors.transparent,
        cardColor: const Color(0xFF2D2D44),
        fontFamily: 'Inter',
      ),
      home: const DashboardScreen(),
    );
  }
}

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen>
    with SingleTickerProviderStateMixin {

  // ══════════════════════════════════════════════════════════════
  // Lifecycle
  // ══════════════════════════════════════════════════════════════

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    _videoCtrl = VideoPluginController(notifyChanged: () {
      if (mounted) setState(() {});
    });
    _fetchInitialData();
    _initMedia();
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkAndRequestPermissions());
  }

  @override
  void dispose() {
    // Bluetooth
    _bluetoothService.dispose();
    _deviceService.dispose();
    _a2dpService.dispose();
    // Permission
    _permissionController.dispose();
    // Audio
    _positionTimer?.cancel();
    for (final sub in _audioSubs) sub.cancel();
    _mediaController.dispose();
    // Video
    _tabController.dispose();
    _videoCtrl.dispose();
    _urlTextController.dispose();
    super.dispose();
  }

  // ══════════════════════════════════════════════════════════════
  // Permissions
  // ══════════════════════════════════════════════════════════════

  final PermissionController _permissionController = PermissionController();

  Future<void> _checkAndRequestPermissions() async {
    final ungranted = await _permissionController.ungrantedPermissions();
    if (ungranted.isEmpty || !mounted) return;
    final appUid = await _permissionController.getAppUid();
    print('[Permission] appUid=$appUid ungranted=$ungranted');
    if (appUid.isEmpty || !mounted) return;
    await _showPermissionDialog(appUid, ungranted);
  }

  Future<void> _showPermissionDialog(String appUid, List<String> ungranted) async {
    final allowed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF2D2D44),
        title: const Text(
          '앱 권한 요청',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '앱이 정상적으로 동작하려면\n다음 권한이 필요합니다.',
              style: TextStyle(color: Colors.white70, fontSize: 13),
            ),
            const SizedBox(height: 16),
            ...ungranted.map((p) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  const Icon(Icons.check_circle_outline,
                      color: Colors.pinkAccent, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      PermissionController.permissionLabel(p),
                      style: const TextStyle(color: Colors.white, fontSize: 13),
                    ),
                  ),
                ],
              ),
            )),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('나중에', style: TextStyle(color: Colors.white54)),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.pinkAccent,
              foregroundColor: Colors.black,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            ),
            child: const Text('허용'),
          ),
        ],
      ),
    );

    if (allowed == true && mounted) {
      await _permissionController.grantAll(ungranted, appUid);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('권한이 부여되었습니다.'),
            backgroundColor: Color(0xFF2D2D44),
          ),
        );
      }
    }
  }

  // ══════════════════════════════════════════════════════════════
  // Bluetooth
  // ══════════════════════════════════════════════════════════════

  // 서비스
  final BluetoothAdapterController _bluetoothService = BluetoothAdapterController();
  final BluetoothDeviceController _deviceService = BluetoothDeviceController();
  final BluetoothA2dpController _a2dpService = BluetoothA2dpController();

  // 상태
  bool _isBluetoothOn = false;
  bool _isScanning = false;
  List<Map<String, dynamic>> _devices = [];
  Map<String, dynamic>? _adapterInfo;
  String _adapterAddress = '';

  // 제어
  Future<void> _fetchInitialData() async {
    try {
      final info = await _bluetoothService.getInfo();
      if (mounted) setState(() {
        _adapterInfo = info;
        _isBluetoothOn = info['powered'] ?? false;
      });
    } catch (e) {
      print('Failed to get adapter info: $e');
    }
  }

  void _toggleBluetooth() async {
    try {
      if (_isBluetoothOn) {
        await _bluetoothService.cancelDiscovery(_adapterAddress);
        setState(() { _devices = []; _isBluetoothOn = false; });
      } else {
        final info = await _bluetoothService.getInfo();
        _adapterAddress = info['adapter_address'] ?? '';
        setState(() { _isBluetoothOn = true; _adapterInfo = info; });
        _scanForDevices();
      }
    } catch (e) {
      print('Bluetooth toggle error: $e');
    }
  }

  Future<void> _scanForDevices() async {
    if (_isScanning) return;
    setState(() { _isScanning = true; _devices = []; });
    try {
      await _bluetoothService.startDiscovery(_adapterAddress);
      await Future.delayed(const Duration(seconds: 12));
      final devices = await _deviceService.getDiscoverableDevices();
      await _bluetoothService.cancelDiscovery(_adapterAddress);
      if (mounted) setState(() { _devices = devices; _isScanning = false; });
    } catch (e) {
      print('Scan error: $e');
      if (mounted) setState(() => _isScanning = false);
    }
  }

  Future<void> _refreshDevices() async {
    final devices = await _deviceService.getDiscoverableDevices();
    if (!mounted) return;
    for (final d in devices) {
      if (d['paired'] == true) {
        try {
          final status = await _a2dpService.getStatus(d['address'], _adapterAddress);
          d['connected'] = status['connected'] ?? false;
        } catch (_) {
          d['connected'] = false;
        }
      } else {
        d['connected'] = false;
      }
    }
    setState(() => _devices = devices);
  }

  Future<void> _pairDevice(String address) async {
    try {
      await _bluetoothService.cancelDiscovery(_adapterAddress);
      await _bluetoothService.pair(address, _adapterAddress);
      await Future.delayed(const Duration(seconds: 2));
      await _refreshDevices();
    } catch (e) {
      if (e.toString().contains('already paired')) {
        await _refreshDevices();
      } else if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to pair: $e')),
        );
      }
    }
  }

  Future<void> _unpairDevice(String address) async {
    try {
      await _bluetoothService.cancelDiscovery(_adapterAddress);
      await _bluetoothService.unpair(address, _adapterAddress);
      await Future.delayed(const Duration(seconds: 2));
      await _refreshDevices();
    } catch (e) {
      await _refreshDevices();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to unpair: $e')),
        );
      }
    }
  }

  Future<void> _connectDevice(String address) async {
    try {
      await _bluetoothService.cancelDiscovery(_adapterAddress);
      await _a2dpService.connect(address, _adapterAddress);
      await Future.delayed(const Duration(seconds: 2));
      await _refreshDevices();
    } catch (e) {
      print('Connect error: $e');
    }
  }

  Future<void> _disconnectDevice(String address) async {
    try {
      await _a2dpService.disconnect(address, _adapterAddress);
      await Future.delayed(const Duration(seconds: 2));
      await _refreshDevices();
    } catch (e) {
      print('Disconnect error: $e');
    }
  }

  // UI
  Widget _buildBluetoothPanel() {
    return Column(
      children: [
        Card(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
          child: ListTile(
            leading: Icon(
              _isBluetoothOn ? Icons.bluetooth_connected : Icons.bluetooth_disabled,
              color: _isBluetoothOn ? Colors.blueAccent : Colors.grey,
            ),
            title: Text(
              _isBluetoothOn ? 'Bluetooth ON' : 'Bluetooth OFF',
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            subtitle: (_adapterInfo?['adapter_address'] != null)
                ? Text('MAC: ${_adapterInfo!['adapter_address']}',
                    style: const TextStyle(fontSize: 12))
                : null,
            trailing: Switch(
              value: _isBluetoothOn,
              onChanged: (_) => _toggleBluetooth(),
              activeColor: Colors.blueAccent,
            ),
          ),
        ),
        const SizedBox(height: 12),
        Expanded(
          child: _isBluetoothOn
              ? Card(
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                  child: Column(
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(12.0),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            const Text('Discovered Devices',
                                style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                            ElevatedButton.icon(
                              onPressed: _isScanning ? null : _scanForDevices,
                              icon: _isScanning
                                  ? const SizedBox(width: 16, height: 16,
                                      child: CircularProgressIndicator(strokeWidth: 2))
                                  : const Icon(Icons.search),
                              label: Text(_isScanning ? 'Scanning...' : 'Scan'),
                              style: ElevatedButton.styleFrom(
                                shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(10)),
                              ),
                            ),
                          ],
                        ),
                      ),
                      Expanded(
                        child: _isScanning && _devices.isEmpty
                            ? const Center(child: Text('Scanning for Bluetooth devices...'))
                            : _devices.isEmpty
                                ? const Center(child: Text('No devices found.'))
                                : ListView.builder(
                                    itemCount: _devices.length,
                                    itemBuilder: (context, index) {
                                      final device = _devices[index];
                                      return ListTile(
                                        leading: Icon(Icons.devices,
                                            color: device['connected'] == true
                                                ? Colors.blue
                                                : (device['paired'] == true
                                                    ? Colors.green
                                                    : Colors.grey)),
                                        title: Text(device['name']),
                                        subtitle: Text(
                                            '${device['address']}${device['connected'] == true ? " (Connected)" : ""}'),
                                        trailing: Row(
                                          mainAxisSize: MainAxisSize.min,
                                          children: [
                                            if (!device['paired'])
                                              TextButton(
                                                onPressed: () => _pairDevice(device['address']),
                                                child: const Text('Pair'),
                                              ),
                                            if (device['paired'] == true) ...[
                                              if (device['connected'] != true)
                                                TextButton(
                                                  onPressed: () => _connectDevice(device['address']),
                                                  child: const Text('Connect',
                                                      style: TextStyle(color: Colors.green)),
                                                ),
                                              if (device['connected'] == true)
                                                TextButton(
                                                  onPressed: () => _disconnectDevice(device['address']),
                                                  child: const Text('Disconnect',
                                                      style: TextStyle(color: Colors.orange)),
                                                ),
                                              TextButton(
                                                onPressed: () => _unpairDevice(device['address']),
                                                child: const Text('Unpair',
                                                    style: TextStyle(color: Colors.red)),
                                              ),
                                            ],
                                          ],
                                        ),
                                      );
                                    },
                                  ),
                      ),
                    ],
                  ),
                )
              : const Card(
                  child: Center(
                      child: Text('Turn on Bluetooth to scan for devices',
                          style: TextStyle(color: Colors.grey))),
                ),
        ),
      ],
    );
  }

  // ══════════════════════════════════════════════════════════════
  // Audio
  // ══════════════════════════════════════════════════════════════

  // 컨트롤러
  // ── 구현체 교체 시 이 한 줄만 바꾸면 됨 ──────────────────────
  final AudioController _mediaController = AudioPluginController();
  // final AudioController _mediaController = AudioDbusController();
  // ────────────────────────────────────────────────────────────

  // 상태
  bool _isPlaying = false;
  bool _isLoaded = false;
  bool _isBuffering = false;
  int _bufferPercent = 0;
  int _positionMs = 0;
  int _durationMs = 0;
  AudioMetadata _metadata = const AudioMetadata(
    title: 'Cooking Jams',
    artist: 'Smart Kitchen Radio',
    album: '',
    durationMs: 0,
  );
  bool _useHttpStream = false;
  Timer? _positionTimer;
  bool _hasRealPosition = false;
  final List<StreamSubscription> _audioSubs = [];

  // 제어
  String get _currentUri => _useHttpStream
      ? DBusConfig.testHttpStream
      : '${DBusConfig.testLocalDir}/Lite_Saturation-Phonk.mp3';

  Future<void> _initMedia() async {
    await _mediaController.initialize();
    _audioSubs.addAll([
      _mediaController.playbackStatus.listen((status) {
        if (!mounted) return;
        if (status == 'stopped' || status == 'error') {
          _positionTimer?.cancel();
          setState(() => _isPlaying = false);
        }
      }),
      _mediaController.position.listen((ms) {
        if (!mounted) return;
        // D-Bus PositionChanged 시그널이 실제 위치를 전달 → 타이머 폴백 해제
        if (!_hasRealPosition) {
          _hasRealPosition = true;
          _positionTimer?.cancel();
        }
        setState(() => _positionMs = ms);
      }),
      _mediaController.metadata.listen((meta) {
        if (!mounted) return;
        setState(() {
          if (meta.title.isNotEmpty || meta.artist.isNotEmpty || meta.album.isNotEmpty) {
            _metadata = meta;
          }
          if (meta.durationMs > 0) _durationMs = meta.durationMs;
        });
      }),
      _mediaController.bufferingPercent.listen((pct) {
        if (!mounted) return;
        setState(() { _isBuffering = pct < 100; _bufferPercent = pct; });
      }),
    ]);
    // 자동 로드 제거 — 사용자가 명시적으로 Load 버튼을 눌러야 권한 동의 다이얼로그가 뜸
  }

  Future<void> _loadCurrentSource() async {
    _hasRealPosition = false;
    setState(() {
      _isLoaded = false;
      _isPlaying = false;
      _isBuffering = false;
      _bufferPercent = 0;
      _positionMs = 0;
      _durationMs = 0;
      _metadata = _titleFromUri(_currentUri);
    });
    final ok = await _mediaController.load(_currentUri);
    if (ok) await _mediaController.setInitialMetadata(_titleFromUri(_currentUri));
    if (mounted) setState(() => _isLoaded = ok);
  }

  AudioMetadata _titleFromUri(String uri) {
    final name = uri.split('/').last.replaceAll(RegExp(r'\.[^.]+$'), '');
    return AudioMetadata(
      title: _useHttpStream ? 'Groove Salad' : name,
      artist: _useHttpStream ? 'SomaFM' : '',
    );
  }

  Future<void> _togglePlay() async {
    if (!_isLoaded) return;
    if (_isPlaying) {
      await _mediaController.pause();
      _positionTimer?.cancel();
      setState(() => _isPlaying = false);
    } else {
      final ok = await _mediaController.play();
      if (!ok || !mounted) return;
      _hasRealPosition = false;
      setState(() => _isPlaying = true);
      _positionTimer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted && _isPlaying) setState(() => _positionMs += 1000);
      });
    }
  }

  Future<void> _skipNext() async => _mediaController.sendKeyEvent('next');
  Future<void> _skipPrevious() async => _mediaController.sendKeyEvent('previous');

  Future<void> _onSeek(double value) async {
    if (_durationMs <= 0) return;
    await _mediaController.seek((value * _durationMs).toInt());
  }

  String _formatMs(int ms) {
    final s = ms ~/ 1000;
    return '${(s ~/ 60).toString().padLeft(2, '0')}:${(s % 60).toString().padLeft(2, '0')}';
  }

  double get _progress =>
      (_durationMs > 0) ? (_positionMs / _durationMs).clamp(0.0, 1.0) : 0.0;

  // UI
  Widget _buildAudioTab() {
    return Padding(
      padding: const EdgeInsets.all(24.0),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Text('Kitchen Audio Player',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white70)),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Text('File', style: TextStyle(color: Colors.white54, fontSize: 12)),
              Switch(
                value: _useHttpStream,
                onChanged: (val) async {
                  _positionTimer?.cancel();
                  await _mediaController.stop();
                  setState(() {
                    _useHttpStream = val;
                    _isLoaded = false;
                    _isPlaying = false;
                  });
                  if (val) await _loadCurrentSource();
                },
                activeColor: Colors.pinkAccent,
              ),
              const Text('Stream', style: TextStyle(color: Colors.white54, fontSize: 12)),
              const SizedBox(width: 16),
              if (!_useHttpStream)
                ElevatedButton(
                  onPressed: _isLoaded ? null : _loadCurrentSource,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.pinkAccent,
                    foregroundColor: Colors.black,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                  child: const Text('Load'),
                ),
            ],
          ),
          const SizedBox(height: 8),
          Expanded(
            child: Stack(
              alignment: Alignment.center,
              children: [
                Container(
                  width: double.infinity,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(20),
                    gradient: const LinearGradient(
                      colors: [Color(0xFF8A2387), Color(0xFFE94057), Color(0xFFF27121)],
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withOpacity(0.3),
                        blurRadius: 15,
                        offset: const Offset(0, 8),
                      )
                    ],
                  ),
                  child: const Center(
                    child: Icon(Icons.music_note, size: 80, color: Colors.white),
                  ),
                ),
                if (_isBuffering)
                  Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const CircularProgressIndicator(color: Colors.white),
                      const SizedBox(height: 8),
                      Text('Buffering $_bufferPercent%',
                          style: const TextStyle(color: Colors.white)),
                    ],
                  ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Text(
            _metadata.title.isNotEmpty ? _metadata.title : '—',
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 6),
          Text(
            _metadata.artist.isNotEmpty ? _metadata.artist : '—',
            style: const TextStyle(fontSize: 14, color: Colors.white54),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 20),
          Row(
            children: [
              Text(_formatMs(_positionMs),
                  style: const TextStyle(fontSize: 12, color: Colors.white38)),
              Expanded(
                child: Slider(
                  value: _progress,
                  onChanged: _durationMs > 0 ? _onSeek : null,
                  activeColor: Colors.pinkAccent,
                  inactiveColor: Colors.white10,
                ),
              ),
              Text(
                _useHttpStream ? '∞' : _formatMs(_durationMs),
                style: const TextStyle(fontSize: 12, color: Colors.white38),
              ),
            ],
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                icon: const Icon(Icons.skip_previous, color: Colors.white),
                onPressed: _skipPrevious,
              ),
              const SizedBox(width: 16),
              GestureDetector(
                onTap: _togglePlay,
                child: Container(
                  width: 64,
                  height: 64,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _isPlaying ? Colors.greenAccent : Colors.pinkAccent,
                  ),
                  child: Icon(
                    _isPlaying ? Icons.pause : Icons.play_arrow,
                    size: 36,
                    color: Colors.black,
                  ),
                ),
              ),
              const SizedBox(width: 16),
              IconButton(
                icon: const Icon(Icons.skip_next, color: Colors.white),
                onPressed: _skipNext,
              ),
            ],
          ),
        ],
      ),
    );
  }

  // ══════════════════════════════════════════════════════════════
  // Video
  // ══════════════════════════════════════════════════════════════

  // 컨트롤러
  late final VideoPluginController _videoCtrl;
  late final TabController _tabController;
  final TextEditingController _urlTextController = TextEditingController(
    text: 'https://media.w3.org/2010/05/sintel/trailer.mp4',
  );

  // UI
  Widget _buildVideoTab() {
    final ctrl = _videoCtrl.controller;
    final initialized = _videoCtrl.isInitialized && ctrl != null && ctrl.value.isInitialized;
    final position = initialized ? ctrl.value.position : Duration.zero;
    final duration = initialized ? ctrl.value.duration : Duration.zero;
    final progress = (initialized && duration.inMilliseconds > 0)
        ? (position.inMilliseconds / duration.inMilliseconds).clamp(0.0, 1.0)
        : 0.0;

    return Padding(
      padding: const EdgeInsets.all(16.0),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _urlTextController,
                  style: const TextStyle(color: Colors.white, fontSize: 12),
                  decoration: InputDecoration(
                    hintText: 'https://example.com/video.mp4',
                    hintStyle: const TextStyle(color: Colors.white38, fontSize: 12),
                    filled: true,
                    fillColor: const Color(0xFF1E1E2C),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: BorderSide.none,
                    ),
                    contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              ElevatedButton(
                onPressed: _videoCtrl.isLoading
                    ? null
                    : () => _videoCtrl.load(_urlTextController.text),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.pinkAccent,
                  foregroundColor: Colors.black,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                ),
                child: _videoCtrl.isLoading
                    ? const SizedBox(width: 16, height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black))
                    : const Text('Load'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Container(
                color: Colors.black,
                child: !initialized
                    ? Center(
                        child: _videoCtrl.isLoading
                            ? const Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  CircularProgressIndicator(color: Colors.pinkAccent),
                                  SizedBox(height: 12),
                                  Text('Loading...', style: TextStyle(color: Colors.white54)),
                                ],
                              )
                            : _videoCtrl.error != null
                                ? Column(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      const Icon(Icons.error_outline,
                                          color: Colors.redAccent, size: 48),
                                      const SizedBox(height: 8),
                                      const Text('로드 실패',
                                          style: TextStyle(
                                              color: Colors.redAccent,
                                              fontWeight: FontWeight.bold)),
                                      const SizedBox(height: 4),
                                      Padding(
                                        padding: const EdgeInsets.symmetric(horizontal: 16),
                                        child: Text(
                                          _videoCtrl.error!,
                                          style: const TextStyle(color: Colors.white38, fontSize: 11),
                                          textAlign: TextAlign.center,
                                          maxLines: 3,
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                    ],
                                  )
                                : const Column(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(Icons.videocam_off, color: Colors.white38, size: 48),
                                      SizedBox(height: 8),
                                      Text('Enter URL and tap Load',
                                          style: TextStyle(color: Colors.white38)),
                                    ],
                                  ),
                      )
                    : AspectRatio(
                        aspectRatio: ctrl.value.aspectRatio,
                        child: VideoPlayer(ctrl),
                      ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Text(_videoCtrl.formatDuration(position),
                  style: const TextStyle(fontSize: 11, color: Colors.white38)),
              Expanded(
                child: Slider(
                  value: progress.toDouble(),
                  onChanged: initialized && duration.inMilliseconds > 0
                      ? (v) => ctrl.seekTo(duration * v)
                      : null,
                  activeColor: Colors.pinkAccent,
                  inactiveColor: Colors.white10,
                ),
              ),
              Text(_videoCtrl.formatDuration(duration),
                  style: const TextStyle(fontSize: 11, color: Colors.white38)),
            ],
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                icon: const Icon(Icons.replay_10, color: Colors.white),
                onPressed: initialized
                    ? () => ctrl.seekTo(position - const Duration(seconds: 10))
                    : null,
              ),
              const SizedBox(width: 8),
              GestureDetector(
                onTap: initialized ? _videoCtrl.togglePlay : null,
                child: Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: initialized
                        ? (_videoCtrl.isPlaying ? Colors.greenAccent : Colors.pinkAccent)
                        : Colors.grey,
                  ),
                  child: Icon(
                    _videoCtrl.isPlaying ? Icons.pause : Icons.play_arrow,
                    size: 32,
                    color: Colors.black,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton(
                icon: const Icon(Icons.forward_10, color: Colors.white),
                onPressed: initialized
                    ? () => ctrl.seekTo(position + const Duration(seconds: 10))
                    : null,
              ),
            ],
          ),
        ],
      ),
    );
  }

  // ══════════════════════════════════════════════════════════════
  // Build | Scaffold
  // ══════════════════════════════════════════════════════════════

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Smart Kitchen Dashboard',
            style: TextStyle(fontWeight: FontWeight.bold)),
        backgroundColor: const Color(0xFF1E1E2C),
        elevation: 0,
      ),
      body: Container(
        color: const Color(0xFF1E1E2C),
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(flex: 1, child: _buildBluetoothPanel()),
              const SizedBox(width: 24),
              Expanded(flex: 1, child: _buildRightPanel()),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildRightPanel() {
    return Card(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
      color: const Color(0xFF2D2D44),
      child: Column(
        children: [
          TabBar(
            controller: _tabController,
            indicatorColor: Colors.pinkAccent,
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white38,
            tabs: const [
              Tab(icon: Icon(Icons.music_note), text: 'Audio'),
              Tab(icon: Icon(Icons.videocam), text: 'Video'),
            ],
          ),
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: [
                _buildAudioTab(),
                _buildVideoTab(),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
