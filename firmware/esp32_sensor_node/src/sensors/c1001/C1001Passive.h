#pragma once

#include <Arduino.h>

#include "sensors/c1001/MmwaveSample.h"

namespace deskmate {

// ============================================================================
//  C1001 수신 전용 프레임 파서
//
//  출처: 76EHwan/ESP32-mmWave (실제 하드웨어 G60SM1SY / R60A 로그로 확인한 구현).
//  DESKMATE 로 옮기면서 namespace 와 MmwaveSample 변환만 덧붙였고, 파서 본체와
//  실측 근거 주석은 그대로 둔다.
//
//  벤더 라이브러리의 getData() 는 호출할 때마다
//    1. 수신 버퍼를 통째로 비우고        <- 이미 올라온 프레임을 버린다
//    2. 질의를 보낸 뒤
//    3. 응답을 기다리며 바이트당 delay   <- 호출당 90ms 대기
//  를 반복한다. 그래서 폴링 주기가 센서의 실제 갱신 시점과 어긋난다.
//
//  이 클래스는 반대로 동작한다. 아무것도 버리지 않고, 기다리지도 않는다.
//  loop() 에서 poll() 을 계속 부르면 올라온 바이트를 그때그때 소화하고
//  프레임이 완성될 때만 해당 필드를 갱신한다.
//
//  프레임 형식
//    53 59 | con | cmd | lenH lenL | data[len] | sum | 54 43
//    sum = 헤더 6바이트 + data 를 더한 값의 하위 8비트
//
//  con/cmd 는 실제 하드웨어 로그로 확인한 값이다. 이 모듈은 요청 없이 능동
//  보고를 하며, 보고는 0x0X, 질의 응답은 0x8X 를 쓴다. 다만 con=0x84(수면) 는
//  0x0C(종합 8바이트)와 0x8C(보고 모드 1바이트)처럼 두 계열이 충돌하므로,
//  상위 비트를 떼지 않고 (con,cmd) 쌍을 그대로 매핑한다.
// ============================================================================

/// 호흡 상태(0x81/0x01) 중 "호흡 없음".
constexpr uint16_t kRespStateNone = 4;

/// 유효 심박 범위 [BPM]. 밖의 값은 추정기가 아직 수렴하지 않은 것이다.
constexpr uint16_t kHeartRateMin = 40;
constexpr uint16_t kHeartRateMax = 150;

/// 마지막 프레임 이후 이만큼 조용하면 표본을 신뢰하지 않는다 [ms].
constexpr uint32_t kMmwaveStaleAfterMs = 5000;

class C1001Passive {
 public:
  static const uint16_t kMaxData = 64;   // 이보다 긴 프레임은 버린다

  /// 값 하나와 그 값이 언제 몇 번 갱신됐는지
  struct Field {
    uint16_t value = 0;
    uint32_t stamp = 0;      ///< 마지막 갱신 시각 [ms]
    uint32_t count = 0;      ///< 갱신 횟수
    float periodMs = 0;      ///< 갱신 간격의 이동평균 [ms]
    bool valid = false;
  };

  explicit C1001Passive(HardwareSerial& port) : _s(port) {}

  /// 벤더 라이브러리로 모드 전환·리셋까지 끝낸 뒤 UART 를 파서가 가져간다.
  /// 포트의 begin() 은 호출자가 미리 한다. 내부에서 sensorRet() 이 10초 대기한다.
  /// @return 모듈 초기화에 성공했으면 true
  bool begin();

  /// 파서 상태와 통계만 초기화한다 (값 자체는 유지).
  void resetStats();

  /// 논블로킹. 수신 버퍼에 쌓인 바이트를 전부 소화한다.
  /// @return 이번 호출에서 완성된 프레임이 하나라도 있으면 true
  bool poll();

  /// 질의 프레임 하나를 보내고 즉시 반환한다 (응답을 기다리지 않는다)
  void request(uint8_t con, uint8_t cmd, uint8_t arg = 0x0f);

  /// 관심 항목을 한 번에 하나씩 돌아가며 질의한다.
  /// 모듈이 능동 보고를 하지 않는 설정일 때의 폴백 경로다
  void nudge();

  /// 능동 보고가 끊겼으면 질의로 깨운다. loop() 에서 매번 불러도 된다.
  void nudgeIfSilent(uint32_t now_ms);

  /// 지금까지 받은 필드로 전송용 표본을 만든다.
  MmwaveSample sample(uint32_t now_ms) const;

  // --- 최신값 ---
  Field presence;    ///< 재실 0/1            (0x80/0x01, 0x81)
  Field movement;    ///< 0=없음 1=정지 2=활동 (0x80/0x02, 0x82)
  Field bodyMove;    ///< 체동 파라미터 0~100  (0x80/0x03, 0x83)
  Field distance;    ///< 거리 [cm]           (0x80/0x04, 0x84)
  Field respState;   ///< 1=정상 2=빠름 3=느림 4=없음 (0x81/0x01, 0x81)
  Field respRate;    ///< 호흡수 [회/분]       (0x81/0x02, 0x82)
  Field heartRate;   ///< 심박수 [BPM]        (0x85/0x02, 0x82)
  Field inBed;       ///< 재/이석 0/1         (0x84/0x01, 0x81)
  Field sleepState;  ///< 수면 상태           (0x84/0x02, 0x82)

  // 파형 진폭. 레이더가 가슴 움직임을 보고는 있는지 확인하는 용도다.
  // 호흡수·심박수가 0으로 안 나올 때, 파형까지 평평하면 신호 자체가 없는
  // 것이고 (거리·자세 문제), 파형은 흔들리는데 값이 0이면 아직 주파수를
  // 확정하지 못한 것이다 (기다리면 된다)
  Field respWave;    ///< 최근 32샘플 peak-to-peak (0x81/0x05, 기준 0x80)
  Field heartWave;   ///< 최근 32샘플 peak-to-peak (0x85/0x05, 기준 0x80)

  /// 모듈이 리셋/하트비트 통지(0x01/0x01)를 올린 횟수
  uint32_t resetNotices = 0;

  // --- 모듈 식별자 (부팅 직후 한 번 올라온다) ---
  char fwVersion[24] = {0};    ///< 0x02/0xA2  예: "0.0.1"
  char hwModel[24] = {0};      ///< 0x02/0xA3  예: "R60A"
  char serialNo[24] = {0};     ///< 0x02/0xA4  예: "G60SM1SYv010109"

  // --- 통계 ---
  uint32_t frames = 0;        ///< 정상 수신 프레임 수
  uint32_t ignored = 0;       ///< 정체는 알지만 추적하지 않는 프레임
  uint32_t badChecksum = 0;   ///< 체크섬 불일치
  uint32_t unknown = 0;       ///< 매핑에 없는 프레임
  uint32_t resyncs = 0;       ///< 프레임 경계를 다시 잡은 횟수
  uint32_t oversize = 0;      ///< len 이 kMaxData 를 넘어 버린 프레임
  uint32_t lastFrameMs = 0;   ///< 마지막 프레임 수신 시각
  uint32_t startMs = 0;       ///< resetStats() 시각

  bool logUnknown = true;     ///< 모르는 (con,cmd) 를 처음 한 번 찍는다

 private:
  enum State : uint8_t {
    S_H1, S_H2, S_CON, S_CMD, S_LEN_H, S_LEN_L, S_DATA, S_SUM, S_T1, S_T2
  };

  void feed(uint8_t b);
  void resync(uint8_t b);
  void dispatch();
  void noteUnknown();
  void storeText(char* dst, size_t cap);
  void pushWave(uint8_t* ring, uint8_t& idx, uint8_t& fill, Field& f, uint32_t now);
  static void store(Field& f, uint16_t v, uint32_t now);

  static const uint8_t kWaveN = 32;
  uint8_t _respRing[kWaveN], _heartRing[kWaveN];
  uint8_t _respIdx = 0, _respFill = 0;
  uint8_t _heartIdx = 0, _heartFill = 0;

  HardwareSerial& _s;

  State _state = S_H1;
  uint8_t _con = 0, _cmd = 0;
  uint16_t _len = 0, _idx = 0;
  uint16_t _sum = 0;
  uint8_t _data[kMaxData];

  // 모르는 프레임을 한 번씩만 찍기 위한 목록.
  // 이 모듈은 부팅 시 30종 가까이 쏟아내므로 넉넉히 잡는다
  static const uint8_t kUnkSlots = 64;
  uint16_t _unkSeen[kUnkSlots];
  uint8_t _unkCount = 0;

  uint8_t _nudgeIdx = 0;
  uint32_t _lastNudgeMs = 0;
  uint32_t _lastHbMs = 0;     // 직전 0x01/0x01 통지 시각. 간격을 재는 용도
};

}  // namespace deskmate
