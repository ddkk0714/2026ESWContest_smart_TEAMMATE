#pragma once

#include <stdint.h>

namespace deskmate {

class C1001Passive;

// ============================================================================
//  책상에 앉은 사람의 졸음 감지
//
//  출처: 76EHwan/ESP32-mmWave. 임계값은 전부 실측 로그로 맞춘 것이라 숫자와
//  그 근거 주석을 함께 옮긴다. DESKMATE 로 옮기면서 바꾼 것은 namespace 와,
//  #define 대신 constexpr 을 써서 전역 매크로가 다른 헤더로 새지 않게 한 것,
//  그리고 상태 enum 이름을 기존 전송 계약(WireDrowsyState)에 맞춘 것뿐이다.
//
//  실측으로 확인된 제약 (로그 기반)
//   - 센서 내장 수면 판정은 못 쓴다. 앉은 자세에서도 bed=1 은 서지만
//     수면 상태·통계 필드는 전부 0으로 온다.
//   - 심박 추정값은 사람이 가만히 있어도 3bpm씩 계단식으로 40bpm을 왕복한다.
//     86→126→81 을 90초에 걸쳐 오간 기록이 있다. 순간값은 쓸 수 없다.
//   - HRV는 불가능하다. 정수 bpm이 3초마다 갱신될 뿐 박동 간격은 주지 않는다.
//   - 호흡·심박은 락온이 풀리면 통째로 끊긴다. 이건 자세/거리의 지표다.
//
//  실측 갱신 주기 (C1001Passive 로 측정)
//   체동 1000ms · 거리 2001ms · 호흡 3001ms · 심박 3001ms
//   재실/움직임은 값이 바뀔 때만 올라오는 이벤트다.
//
//  판정 구조
//   주 신호 : 체동 이동평균이 낮게 유지  (이것 없이는 졸음으로 보지 않는다)
//   보조 증거 (1) 사람은 있는데 호흡이 안 잡힘  -> 엎드린 자세
//   보조 증거 (2) 심박 중앙값이 각성 기준선보다 하락
//   증거가 많을수록 확정에 필요한 지속 시간이 짧아진다.
//
//  자세 판별은 ESP32-CAM 스켈레톤 판정(display/atlas/camsvc)이 담당한다.
//  여기서는 자세를 추정하지 않는다.
// ============================================================================

// --- 체동 ---
constexpr float kMoveTauS = 15.0f;       // 체동 이동평균 시정수 [초]

// "안 움직인다"의 판정은 이동평균이 아니라 큰 스파이크의 부재로 한다.
// 실측 분포가 그렇게 하라고 말한다.
//
//   작업 중        mAvg 5.04 ~ 20.36,  body 최대 82,  30 이상이 10~30초마다
//   눈 감고 앉음   mAvg 6.50 ~ 11.60,  body 최대 25,  30 이상이 88초간 0개
//
// mAvg 범위는 거의 완전히 겹쳐서 어떤 임계를 잡아도 둘이 안 갈린다.
// 사람이 있는 한 호흡 자체가 체동으로 잡혀 0으로 내려가지 않기 때문이다.
// 반면 큰 스파이크의 유무는 깨끗하게 갈린다
constexpr uint16_t kMoveQuietSpike = 30;        // 이 값을 넘으면 "방금 움직였다"
constexpr uint32_t kStillAfterSpikeMs = 5000;   // 스파이크 후 이만큼 지나야 다시 정지

// 스파이크 없이 잔움직임만 계속되는 경우를 걸러내는 느슨한 상한.
// 눈 감고 앉은 구간의 최대가 11.6 이었으므로 그 위로 잡는다
constexpr float kMoveQuietMax = 15.0f;

// 이 값을 넘으면 실제 움직임 이벤트로 보고 심박을 버린다.
// 처음에 20 으로 뒀더니 책상에서 평범하게 앉아 있는 동안에도 body 가
// 20~100 을 수시로 찍어 blanking 이 242초 내내 풀리지 않았다. 심박 표본이
// 하나도 안 쌓여 심박 증거 경로가 죽어 있었다. 실측 분포를 보고 올렸다.
// blankPercent() 가 계속 100 이면 이 값이 환경 대비 낮은 것이다
constexpr uint16_t kMoveSpike = 40;

// --- 심박 ---
constexpr uint16_t kHrMin = 40;                 // 유효 심박 하한
constexpr uint16_t kHrMax = 150;                // 유효 심박 상한
constexpr uint32_t kHrBlankMs = 60000;          // 체동 이벤트 후 심박을 버릴 시간.
                                                // 추정기 수렴에 1~2분 걸리지만, 90초로
                                                // 두면 스파이크가 한 번만 나도 창이
                                                // 통째로 비어 표본이 안 쌓인다
constexpr uint32_t kHrWinMs = 120000;           // 중앙값을 낼 창 [ms]
constexpr uint8_t kHrWinN = 64;                 // 링버퍼 (3초 주기면 120초에 40샘플)
constexpr uint8_t kHrWinMinN = 8;               // 중앙값을 신뢰할 최소 샘플 수

// 기준선을 "처음 세울 때" 요구하는 표본 수. 중앙값 최소치보다 훨씬 높게 잡는다.
// 기준선은 한 번 잡히면 τ=600초로 천천히 움직이므로, 첫 값이 대표성이 없으면
// 이후의 drop 이 전부 그 값 기준으로 계산돼 버린다. 실측에서 표본 8개로 잡힌
// base=73.6 이 그대로 굳어 drop=6.3 의 근거가 됐는데, 그게 졸음성 하락인지
// 자세 차이인지 가릴 수 없었다.
// 심박은 3초 주기이므로 20개면 최소 60초치의 깨끗한(blanking 아닌) 관측이다
constexpr uint8_t kHrBaseMinN = 20;
constexpr float kHrBaseTauS = 600.0f;           // 각성 기준선 시정수 [초]
constexpr float kHrDropBpm = 4.0f;              // 기준선 대비 이만큼이면 졸음 징후

// 확정 시간 전체를 한 번에 줄이는 배율 [%]. 100 이 실제 운용값이다.
// 로직을 눈으로 확인할 때만 10 (10배 빠름) 정도로 낮춰 쓴다.
// 임계값 자체를 검증하는 용도로는 쓰면 안 된다 — 심박 중앙값 창(120초)과
// 기준선 시정수(600초)는 이 배율을 따라가지 않으므로 증거 경로가 안 선다
#ifndef DESKMATE_DROWSY_HOLD_SCALE_PCT
#define DESKMATE_DROWSY_HOLD_SCALE_PCT 100
#endif
constexpr uint32_t kHoldScalePct = DESKMATE_DROWSY_HOLD_SCALE_PCT;

// 하락이 이만큼 "연속으로" 유지되어야 증거로 인정한다.
// 진폭만으로는 안 갈린다. 5분 각성 측정에서 drop 이 최대 3.0 까지 올라갔고
// (임계 4.0 과 1.0 차이), 반대쪽으로는 -9.55 까지 갔다. 각성 중 심박 중앙값이
// 기준선 대비 ±10bpm 으로 흔들린다는 뜻이라 임계를 올려도 잘 안 갈린다.
// 대신 지속 시간은 깨끗하게 갈렸다:
//   각성 중 최대 3.0  -> 약 3초 유지
//   기대 누웠을 때 6.3 -> 50초 이상 유지
constexpr uint32_t kHrDownHoldMs = 30000 * kHoldScalePct / 100;

// --- 호흡 ---
constexpr uint16_t kRespStateNoBreath = 4;      // getBreatheState: 호흡 없음

// --- 락온 판정 (거리) ---
// 책상 앞 사람으로 볼 수 있는 거리 범위 [cm].
// 실측 거리는 11 / 35 / 47 / 59 / 71 / 83 / 95 / 107 / 154 ... 로 약 12cm
// 단위로 양자화되어 올라온다. 정상 착석은 35~71 사이를 오갔고, 가려졌을 때는
// 11 이었다. 처음에 40~150 으로 잡았더니 35 와 154 가 잘려 나가면서 락온이
// 끊기고 그때마다 카운터가 0으로 죽었다. 양자화 계단 하나씩 넓힌다
constexpr uint16_t kLockDistMin = 30;
constexpr uint16_t kLockDistMax = 170;

// 거리는 2초마다 갱신되는데 가끔 한 샘플만 엉뚱하게 튄다. 그 한 번에
// 락온이 끊기지 않도록, 연속으로 이만큼 벗어나야 상태를 바꾼다
constexpr uint8_t kLockDebounceN = 3;

// --- 확정 시간 (증거 개수에 따라 달라진다) ---
// 증거 없이 체동만으로 판정하는 경로는 짧게 못 줄인다. 집중해서 가만히
// 화면을 보는 것과 조는 것이 체동만으로는 구분되지 않기 때문이다.
// 빠른 반응은 증거(호흡 소실 / 심박 하락)를 확보해야 나온다
constexpr uint32_t kHoldNoEvidenceMs = 180000 * kHoldScalePct / 100;  // 체동만 (3분)
constexpr uint32_t kHoldOneMs = 60000 * kHoldScalePct / 100;          // 증거 1개 (1분)
constexpr uint32_t kHoldTwoMs = 25000 * kHoldScalePct / 100;          // 증거 2개 (25초)
constexpr uint32_t kAwakeHoldMs = 10000 * kHoldScalePct / 100;        // 각성 복귀

// 이 값을 넘는 체동은 자리에서 일어나는 수준이다. 각성 복귀를 기다리지 않고
// 즉시 DROWSY 를 해제한다. 실측에서 일어설 때 84, 100, 57 이 찍혔는데
// 10초 동안 DROWSY 가 남아 있었다
constexpr uint16_t kWakeSpike = 40;

// 첫 호흡·심박 샘플이 들어올 때까지의 유예. 실측상 락온이 좋으면 3~5초,
// 나쁘면 20초 넘게 걸린다. 이 구간의 0 값을 증거로 쓰면 안 된다
constexpr uint32_t kDrowsyWarmupMs = 20000;

/// 전송 계약(include/frame_types.h 의 WireDrowsyState)과 값이 1:1 로 맞는다.
///
/// kNoLock : 사람은 감지되는데 그 사람을 제대로 못 물고 있는 상태.
///   가림(노트북 뒤 등), 거리 이탈에서 발생한다. 이때 체동은 1 근처로 죽고
///   이동평균이 0에 수렴하는데, 이걸 "미동도 없이 앉아 있다"로 읽으면 가려져
///   있는 동안 DROWSY 가 뜬다. 실제로 그렇게 됐다. 판정을 안 하는 게 맞다.
///
///   판별에 bed(0x84/0x01, 재/이석)를 쓰려 했으나 실측에서 폐기했다.
///   20분 연속 측정에서 bed 는 내내 0인데 호흡 15~24, 심박 66~86 이 정상
///   수신됐다. 앉은 자세에서 이 필드는 서지 않는다. 가려진 구간과 정상 구간
///   양쪽 모두 0이라 구분에 아무 도움이 안 된다.
///
///   실제로 구분되는 건 거리다. 정상 착석은 47~59cm 로 안정적이었고,
///   노트북 뒤에 가려졌을 때는 11cm(노트북 화면)를 읽었다
enum class DrowsyState : uint8_t { kNoPerson, kNoLock, kWarmup, kAwake, kDrowsy };

class DrowsyDetector {
 public:
  void begin(uint32_t now);

  /// 파서의 새 샘플을 받아 내부 상태를 갱신하고 판정한다.
  /// 값이 실제로 갱신된 것만 골라 넣으므로, 폴링처럼 같은 값을 여러 번
  /// 통계에 밀어 넣는 일이 없다. (보드 빌드 전용 — 호스트 테스트는 아래의
  /// 개별 이벤트를 직접 부른다)
  void update(const C1001Passive& r, uint32_t now);

  // --- 개별 이벤트 (호스트 테스트용으로도 쓴다) ---
  void onPresence(uint16_t v);
  void onBed(uint16_t v);
  void onDistance(uint16_t cm);
  void onBodyMove(uint16_t v, uint32_t t);
  void onRespiration(uint16_t rate, uint16_t state);
  void onHeartRate(uint16_t bpm, uint32_t t);
  void tick(uint32_t now);

  // --- 조회 ---
  DrowsyState state() const { return _state; }
  float moveAvg() const { return _moveAvg; }
  uint8_t hrMedian(uint32_t now) const;
  float hrBase() const { return _hrBase; }
  float hrDrop(uint32_t now) const;
  bool hrBlanking(uint32_t now) const;
  bool respLost() const { return _respLost; }
  bool hrDown() const { return _hrDown; }

  /// 하락 조건이 지금 걸려 있는가 (아직 30초를 못 채웠을 수도 있다)
  bool hrDownPending() const { return _hrDownArmed; }
  /// 하락 조건이 연속으로 유지된 시간 [ms]
  uint32_t hrDownMs(uint32_t now) const { return _hrDownArmed ? (now - _hrDownSince) : 0; }
  uint8_t evidence() const { return static_cast<uint8_t>(_respLost + _hrDown); }
  uint32_t needMs() const;
  bool locked() const { return _locked; }

  /// 현재 징후가 성립해 있는가. 거짓이면 카운터는 진행 중이 아니다
  bool signActive() const { return _prevSign; }

  /// 마지막 큰 체동 이후 경과 [ms]
  uint32_t sinceSpike(uint32_t now) const { return now - _lastSpikeMs; }
  uint32_t signMs(uint32_t now) const { return now - _signSince; }
  uint8_t hrSamples(uint32_t now) const;

  /// blanking 이 걸려 있던 시간 비율 [%]. 이 값이 계속 100 이면
  /// kMoveSpike 가 그 환경에 비해 너무 낮아 심박이 영영 안 쌓인다
  uint8_t blankPercent() const {
    return _ticks ? static_cast<uint8_t>(100UL * _blankTicks / _ticks) : 0;
  }

 private:
  void pushHr(uint8_t v, uint32_t t);

  DrowsyState _state = DrowsyState::kNoPerson;
  uint32_t _startMs = 0;

  // 파서 샘플의 중복 소비를 막기 위한 직전 갱신 횟수
  uint32_t _cPresence = 0, _cBed = 0, _cDist = 0, _cBody = 0, _cResp = 0, _cHr = 0;
  bool _seeded = false;   // 첫 update() 에서 현재 값을 받아왔는가

  uint32_t _ticks = 0, _blankTicks = 0;   // blanking 점유율 통계
  bool _locked = false;                   // 모듈이 사람을 물고 있는가 (거리 기준)

  uint16_t _presence = 0, _bed = 0, _dist = 0;
  uint8_t _distIn = 0, _distOut = 0;      // 거리 범위 연속 진입/이탈 횟수
  uint16_t _respRate = 0, _respState = kRespStateNoBreath;

  float _moveAvg = 0.0f;
  uint32_t _lastMoveMs = 0;
  uint32_t _lastSpikeMs = 0;      // 마지막으로 kMoveQuietSpike 를 넘은 시각
  uint32_t _hrBlankUntil = 0;

  uint8_t _hrBuf[kHrWinN];
  uint32_t _hrTime[kHrWinN];
  uint8_t _hrHead = 0, _hrFill = 0;
  bool _hrEverValid = false;

  float _hrBase = 0.0f;
  uint32_t _lastBaseMs = 0;

  bool _respLost = false, _hrDown = false;
  bool _hrDownArmed = false;      // 하락 조건이 걸린 상태인가
  uint32_t _hrDownSince = 0;      // 그 상태가 시작된 시각
  bool _prevSign = false;
  uint32_t _signSince = 0;
  bool _drowsy = false;
};

inline const char* drowsyStateName(DrowsyState state) {
  switch (state) {
    case DrowsyState::kNoLock:
      return "NOLOCK";
    case DrowsyState::kWarmup:
      return "WARMUP";
    case DrowsyState::kAwake:
      return "AWAKE";
    case DrowsyState::kDrowsy:
      return "DROWSY";
    default:
      return "NOPERSON";
  }
}

}  // namespace deskmate
