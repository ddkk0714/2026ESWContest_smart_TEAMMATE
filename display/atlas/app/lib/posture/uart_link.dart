/// ATLAS `PeripheralManager1` 의 UART 를 D-Bus 로 연다.
///
/// 이 보드에서 앱이 쓸 수 있는 시리얼은 **GPIO UART(`UART_ttyS0`) 하나**다.
/// USB CDC(`/dev/ttyACM*`)는 PeripheralManager 가 아예 노출하지 않는다. 그래서
/// ESP32-CAM 의 TX 를 Pi 5 핀 10(GPIO15/RXD)에 직접 물리고 여기서 읽는다.
///
/// 실기 확인(2026-09-18, 172.16.34.198):
///   * `EnablePeripheral("UART_ttyS0")` -> 객체 경로
///   * `BaudRate` 는 **읽기 전용**이다. 속도는 `Open(u)` 의 인자로 준다.
///     `Open(0)` 은 InvalidParameter, `Open(921600)` 이면 BaudRate 가 921600 이 되고
///     `State` 가 1 로 바뀐다.
///   * **읽을 게 없으면 `Read` 가 에러를 던진다**(`OperationFailed`). 이걸 치명적
///     오류로 다루면 링크가 멀쩡한데도 화면이 죽는다. 빈 결과로 삼킨다.
library;

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:dbus/dbus.dart';

import '../diag.dart';

const String peripheralService = 'com.atlas.PeripheralManager1';
const String peripheralPath = '/com/atlas/PeripheralManager1';
const String peripheralInterface = 'com.atlas.PeripheralManager1';
const String uartInterface = 'com.atlas.PeripheralManager1.UART';

/// ESP32-CAM 링크 속도. 펌웨어의 `LINK_BAUD`·`DEFAULT_BAUD` 와 같아야 한다.
const int visionBaud = 921600;

/// 기본 UART 이름. `Peripherals` 속성이 주는 이름 그대로다.
const String defaultUartName = 'UART_ttyS0';

/// PeripheralManager 가 여는 장치. 이름이 `UART_ttyS0` 라 `/dev/ttyS0` 를 가리키는데
/// Pi 5 에서 그 핀(GPIO14/15)의 실제 노드는 `/dev/ttyAMA0` 다.
const String uartDevicePath = '/dev/ttyAMA0';

/// 장치 파일을 직접 읽는다. **이쪽이 기본 경로다.**
///
/// ATLAS 의 `UART.Read` 는 실제 수신량과 무관하게 버퍼를 채워 돌려준다 - 실기에서
/// 초당 153KB 가 나왔는데 921600 baud 의 이론 최대치는 92KB/s 다. 그 쓰레기가 섞여
/// 프레임이 하나도 CRC 를 통과하지 못했다. 같은 순간 `dd` 로 장치를 직접 읽으면
/// 4,095 바이트에 매직이 2회 - 프레임 크기(2,280B)와 맞는 깨끗한 비율이었다.
///
/// 두 가지를 밖에서 맞춰 줘야 한다(`tools/board-uart-setup.sh`).
///   * 권한 - 기본이 `root:tty 0620` 이라 앱이 못 읽는다. 앱이 가진 그룹으로 바꾼다.
///   * 속도 - Dart 에는 termios 가 없다. `stty` 로 921600 raw 를 한 번 걸어 둔다.
///     ATLAS 의 `Open(u)` 은 속성만 바꾸고 실제 회선 속도를 건드리지 않는다.
class FileUart implements UartTransport {
  FileUart({String? path}) : path = path ?? pickDevice();

  final String path;
  RandomAccessFile? _file;

  /// 어느 장치를 읽을지 고른다. **USB 브리지가 꽂혀 있으면 그쪽이 우선이다.**
  ///
  /// ESP 가 보내는 건 coverage(`DIFF54`)뿐이고, 판정이 먹는 **이진 마스크는
  /// RP2040 의 `vision.c` 가 만든다**(임계값 + 모폴로지). GPIO 로 ESP 를 직결하면
  /// 그 단계가 빠져 마스크가 영영 안 온다. 그래서 브리지를 USB 로 꽂는 쪽이 기본이고,
  /// GPIO UART 는 나중에 실 ToF 센서를 직결할 때를 위해 남겨 둔다.
  ///
  /// 보드는 CDC 두 개짜리 복합 장치라 포트가 둘 뜬다. 인터페이스 0(브리지)을 열면
  /// 포트는 멀쩡히 열리고 **프레임만 영영 안 온다** - 그 실패는 로그에도 잘 안
  /// 드러나므로 이름으로 가려낸다(`ports.py` 와 같은 규칙).
  static String pickDevice() {
    final dir = Directory('/sys/class/tty');
    if (dir.existsSync()) {
      for (final entry in dir.listSync()) {
        final name = entry.path.split('/').last;
        if (!name.startsWith('ttyACM')) continue;
        final label = File('${entry.path}/device/interface');
        if (!label.existsSync()) continue;
        try {
          if (label.readAsStringSync().trim().toLowerCase() == 'vision stream') {
            return '/dev/$name';
          }
        } on FileSystemException {
          continue;
        }
      }
    }
    return uartDevicePath;
  }

  @override
  Future<void> open(int baud) async {
    final device = File(path);
    if (!device.existsSync()) {
      throw UartUnavailable('시리얼 장치가 없습니다: $path');
    }
    try {
      _file = await device.open();
    } on FileSystemException catch (error) {
      throw UartUnavailable('$path 를 못 엽니다 (${error.osError?.message}). '
          '보드에서 tools/board-uart-setup.sh 를 한 번 돌려야 합니다');
    }
    diag.write('uart: $path 직접 열기 성공 (속도는 stty 설정을 따름)');
  }

  @override
  Future<Uint8List> read(int max) async {
    final file = _file;
    if (file == null) return Uint8List(0);
    try {
      // stty 의 `min 0 time 0` 덕에 읽을 게 없으면 바로 0 바이트로 돌아온다.
      return await file.read(max);
    } on FileSystemException {
      return Uint8List(0);
    }
  }

  @override
  Future<void> write(List<int> bytes) async {
    final file = _file;
    if (file == null) return;
    try {
      await file.writeFrom(bytes);
    } on FileSystemException {
      // 못 보내도 판정은 계속 돈다.
    }
  }

  @override
  Future<void> close() async {
    final file = _file;
    _file = null;
    try {
      await file?.close();
    } on FileSystemException {
      // 이미 닫혔으면 그만이다.
    }
  }
}

class UartUnavailable implements Exception {
  UartUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

/// D-Bus 호출만 감싼 얇은 계층. 테스트에서 갈아끼운다.
abstract interface class UartTransport {
  Future<void> open(int baud);
  Future<Uint8List> read(int max);
  Future<void> write(List<int> bytes);
  Future<void> close();
}

class AtlasUart implements UartTransport {
  AtlasUart({this.name = defaultUartName, DBusClient? client})
      : _client = client ?? DBusClient.system();

  final String name;
  final DBusClient _client;
  DBusRemoteObject? _uart;

  bool get isOpen => _uart != null;

  @override
  Future<void> open(int baud) async {
    diag.write('uart: EnablePeripheral($name) 시도');
    final manager = DBusRemoteObject(_client,
        name: peripheralService, path: DBusObjectPath(peripheralPath));
    DBusMethodSuccessResponse? reply;
    try {
      reply = await manager.callMethod(
          peripheralInterface, 'EnablePeripheral', [DBusString(name)],
          replySignature: DBusSignature('o'));
    } on DBusMethodResponseException catch (error) {
      // 앱이 강제로 멈추면 UART 가 켜진 채로 남는다(AppManager Stop 이 흔하다).
      // 그때 Enable 은 실패하지만 객체는 멀쩡히 살아 있으므로 그대로 이어 쓴다 -
      // 여기서 포기하면 한 번 강제 종료된 뒤로는 영영 못 연다.
      if (error.errorName.endsWith('.PeripheralAlreadyEnabled')) {
        diag.write('uart: 이미 켜져 있어 그대로 이어 씀');
        reply = null;
      } else {
        diag.write('uart: EnablePeripheral 실패 ${error.errorName} / ${error.response}');
        throw UartUnavailable('UART 를 열 수 없습니다 ($name): ${error.errorName}');
      }
    } catch (error) {
      // 버스에 아예 못 붙는 경우도 여기로 온다.
      diag.write('uart: EnablePeripheral 예외 ${error.runtimeType} / $error');
      rethrow;
    }
    // 이미 켜져 있던 경로에서는 응답이 없다. 객체 경로는 이름으로 정해진다.
    final path = reply == null
        ? DBusObjectPath('$peripheralPath/$name')
        : reply.returnValues.first as DBusObjectPath;
    final uart = DBusRemoteObject(_client, name: peripheralService, path: path);
    try {
      // 속도는 여기서 정해진다. BaudRate 속성은 읽기 전용이다.
      await uart.callMethod(uartInterface, 'Open', [DBusUint32(baud)],
          replySignature: DBusSignature(''));
    } on DBusMethodResponseException catch (error) {
      diag.write('uart: Open($baud) 실패 ${error.errorName} / ${error.response}');
      // 앞선 실행이 남긴 열린 포트일 수 있다. 한 번 닫고 다시 열어 본다.
      try {
        await uart.callMethod(uartInterface, 'Close', [],
            replySignature: DBusSignature(''));
        await uart.callMethod(uartInterface, 'Open', [DBusUint32(baud)],
            replySignature: DBusSignature(''));
        diag.write('uart: 닫았다 다시 열어 성공');
      } on DBusMethodResponseException catch (retry) {
        diag.write('uart: 재시도도 실패 ${retry.errorName}');
        await _disable(manager);
        throw UartUnavailable('UART Open($baud) 실패: ${error.errorName}');
      }
    }
    _uart = uart;
    diag.write('uart: 열림 ${path.value} @ $baud');
  }

  @override
  Future<Uint8List> read(int max) async {
    final uart = _uart;
    if (uart == null) return Uint8List(0);
    try {
      final reply = await uart.callMethod(
          uartInterface, 'Read', [DBusUint32(max)],
          replySignature: DBusSignature('ay'));
      final value = reply.returnValues.first;
      if (value is DBusArray) {
        return Uint8List.fromList(
            [for (final item in value.children) (item as DBusByte).value]);
      }
      return Uint8List(0);
    } on DBusMethodResponseException {
      // 읽을 게 없을 때도 여기로 온다. 링크 문제와 구분할 수 없으므로 조용히 넘긴다.
      return Uint8List(0);
    }
  }

  @override
  Future<void> write(List<int> bytes) async {
    final uart = _uart;
    if (uart == null) return;
    try {
      await uart.callMethod(uartInterface, 'Write',
          [DBusArray.byte(bytes)],
          replySignature: DBusSignature(''));
    } on DBusMethodResponseException {
      // 보낼 수 없어도 판정은 계속 돈다. 캘리브레이션 명령 한 번을 놓칠 뿐이다.
    }
  }

  @override
  Future<void> close() async {
    final uart = _uart;
    _uart = null;
    if (uart != null) {
      try {
        await uart.callMethod(uartInterface, 'Close', [],
            replySignature: DBusSignature(''));
      } on DBusMethodResponseException {
        // 이미 닫혔을 수 있다.
      }
    }
    final manager = DBusRemoteObject(_client,
        name: peripheralService, path: DBusObjectPath(peripheralPath));
    await _disable(manager);
    await _client.close();
  }

  Future<void> _disable(DBusRemoteObject manager) async {
    try {
      await manager.callMethod(
          peripheralInterface, 'DisablePeripheral', [DBusString(name)],
          replySignature: DBusSignature(''));
    } on DBusMethodResponseException {
      // 이미 꺼져 있으면 그만이다.
    }
  }
}
