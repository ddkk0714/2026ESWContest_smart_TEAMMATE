#include "sensors/c1001/DrowsyDetector.h"

#ifdef ARDUINO
#include "sensors/c1001/C1001Passive.h"
#endif

namespace deskmate {

void DrowsyDetector::begin(uint32_t now) {
  _state = DrowsyState::kNoPerson;
  _startMs = now;
  _cPresence = _cBed = _cDist = _cBody = _cResp = _cHr = 0;
  _presence = _bed = _dist = 0;
  _distIn = _distOut = 0;
  _locked = false;
  _respRate = 0;
  _respState = kRespStateNoBreath;
  _moveAvg = 0.0f;
  _lastMoveMs = now;
  _lastSpikeMs = now;
  _hrBlankUntil = now;
  _hrHead = _hrFill = 0;
  _hrEverValid = false;
  _hrBase = 0.0f;
  _lastBaseMs = now;
  _respLost = _hrDown = false;
  _hrDownArmed = false;
  _hrDownSince = now;
  _prevSign = false;
  _signSince = now;
  _drowsy = false;
  _seeded = false;
  _ticks = _blankTicks = 0;
}

#ifdef ARDUINO
// 파서의 count 를 보고 "새로 갱신된 것"만 소비한다.
// 폴링 방식에서는 3초마다 갱신되는 심박을 1초마다 읽어 같은 값을 세 번씩
// 중앙값 창에 밀어 넣었다. 그러면 오래된 값이 과대 대표된다
void DrowsyDetector::update(const C1001Passive& r, uint32_t now) {
  // 첫 호출에서는 현재 값을 그대로 받아온다.
  // presence/bed 는 값이 바뀔 때만 올라오는 이벤트라, 부팅 덤프로 받은 값
  // 이후 몇 분 동안 프레임이 한 장도 안 오는 게 정상이다. count 변화만
  // 기다리면 그동안 초기값 0(사람 없음)에 갇힌다
  if (!_seeded) {
    _seeded = true;
    if (r.presence.valid) { _cPresence = r.presence.count; onPresence(r.presence.value); }
    if (r.inBed.valid) { _cBed = r.inBed.count; onBed(r.inBed.value); }
    if (r.distance.valid) { _cDist = r.distance.count; onDistance(r.distance.value); }
    if (r.bodyMove.valid) { _cBody = r.bodyMove.count; }
    if (r.respRate.valid) {
      _cResp = r.respRate.count;
      onRespiration(r.respRate.value, r.respState.value);
    }
    if (r.heartRate.valid) { _cHr = r.heartRate.count; }
  }

  if (r.presence.count != _cPresence) {
    _cPresence = r.presence.count;
    onPresence(r.presence.value);
  }
  if (r.inBed.count != _cBed) {
    _cBed = r.inBed.count;
    onBed(r.inBed.value);
  }
  if (r.distance.count != _cDist) {
    _cDist = r.distance.count;
    onDistance(r.distance.value);
  }
  if (r.bodyMove.count != _cBody) {
    _cBody = r.bodyMove.count;
    onBodyMove(r.bodyMove.value, r.bodyMove.stamp);
  }
  if (r.respRate.count != _cResp) {
    _cResp = r.respRate.count;
    onRespiration(r.respRate.value, r.respState.value);
  }
  if (r.heartRate.count != _cHr) {
    _cHr = r.heartRate.count;
    onHeartRate(r.heartRate.value, r.heartRate.stamp);
  }
  tick(now);
}
#endif  // ARDUINO

void DrowsyDetector::onPresence(uint16_t v) { _presence = v; }
void DrowsyDetector::onBed(uint16_t v) { _bed = v; }   // 표시용. 판정에는 안 쓴다

// 거리 한 샘플이 튀는 것만으로 락온이 끊기지 않도록 디바운스한다.
// 연속 kLockDebounceN 번 같은 방향이어야 _locked 를 바꾼다
void DrowsyDetector::onDistance(uint16_t cm) {
  _dist = cm;
  if (cm >= kLockDistMin && cm <= kLockDistMax) {
    _distOut = 0;
    if (_distIn < kLockDebounceN) _distIn++;
    if (_distIn >= kLockDebounceN) _locked = true;
  } else {
    _distIn = 0;
    if (_distOut < kLockDebounceN) _distOut++;
    if (_distOut >= kLockDebounceN) _locked = false;
  }
}

void DrowsyDetector::onRespiration(uint16_t rate, uint16_t state) {
  _respRate = rate;
  _respState = state;
}

// 체동은 1초 주기로 올라온다. 그래도 시정수는 시간 기반으로 계산해
// 프레임을 몇 개 놓쳐도 평균이 흔들리지 않게 한다
void DrowsyDetector::onBodyMove(uint16_t v, uint32_t t) {
  float dt = (t - _lastMoveMs) / 1000.0f;
  _lastMoveMs = t;
  if (dt <= 0.0f) dt = 0.001f;
  _moveAvg += (dt / (kMoveTauS + dt)) * (static_cast<float>(v) - _moveAvg);

  // 정지 판정의 기준. 타이핑·마우스·자세 고치기가 여기에 걸린다
  if (v > kMoveQuietSpike) _lastSpikeMs = t;

  // 자리에서 일어나는 수준의 움직임이면 각성 유예를 기다리지 않고 즉시 해제
  if (v > kWakeSpike) _drowsy = false;

  // 체동 스파이크 뒤에는 추정기가 흔들린 채로 남으므로 심박을 통째로 버린다
  if (v > kMoveSpike) _hrBlankUntil = t + kHrBlankMs;
}

void DrowsyDetector::pushHr(uint8_t v, uint32_t t) {
  _hrBuf[_hrHead] = v;
  _hrTime[_hrHead] = t;
  _hrHead = static_cast<uint8_t>((_hrHead + 1) % kHrWinN);
  if (_hrFill < kHrWinN) _hrFill++;
}

void DrowsyDetector::onHeartRate(uint16_t bpm, uint32_t t) {
  const bool ok = (bpm >= kHrMin) && (bpm <= kHrMax);
  if (ok) _hrEverValid = true;

  // 체동 직후, 사람이 없을 때, 락온이 풀렸을 때의 값은 넣지 않는다.
  // 여기에 bed==1 을 걸어 뒀던 탓에 실측에서 심박 표본이 단 하나도 안 쌓여
  // (med=--/0) 심박 증거 경로가 통째로 죽어 있었다
  const bool distOk = (_dist >= kLockDistMin) && (_dist <= kLockDistMax);
  if (!ok || hrBlanking(t) || _presence != 1 || !distOk) return;
  pushHr(static_cast<uint8_t>(bpm), t);

  // 각성 기준선. 졸기 시작한 뒤의 낮은 심박이 기준선을 끌어내리면
  // 하락 폭이 사라져 증거가 무의미해지므로, 각성 상태에서만 갱신한다
  const uint8_t med = hrMedian(t);

  // 중앙값이 없거나 졸음 중이어도 시각은 갱신한다. 안 그러면 한동안 표본이
  // 끊겼다가 돌아왔을 때 dt 가 커져 그 한 번에 기준선이 크게 끌려간다.
  // 실측에서 blanking 으로 100초쯤 표본이 끊긴 뒤 첫 갱신에 기준선이
  // 76.9 -> 87.6 으로 한 번에 뛰었다. τ=600초를 두는 의미가 없어진다
  float dt = (t - _lastBaseMs) / 1000.0f;
  _lastBaseMs = t;
  if (med == 0) return;

  if (_hrBase == 0.0f) {
    // 표본이 충분히 모이기 전에는 기준선을 세우지 않는다.
    // 한 번 잡히면 τ=600초로 굳으므로 첫 값이 가장 중요하다
    if (hrSamples(t) < kHrBaseMinN) return;
    _hrBase = med;
  } else if (!_drowsy) {
    if (dt <= 0.0f) dt = 0.001f;
    _hrBase += (dt / (kHrBaseTauS + dt)) * (static_cast<float>(med) - _hrBase);
  }
}

bool DrowsyDetector::hrBlanking(uint32_t now) const {
  return static_cast<int32_t>(_hrBlankUntil - now) > 0;
}

uint8_t DrowsyDetector::hrSamples(uint32_t now) const {
  uint8_t n = 0;
  for (uint8_t i = 0; i < _hrFill; i++) {
    if ((now - _hrTime[i]) <= kHrWinMs) n++;
  }
  return n;
}

// 최근 kHrWinMs 안의 샘플로 중앙값을 낸다. 표본이 부족하면 0
uint8_t DrowsyDetector::hrMedian(uint32_t now) const {
  uint8_t tmp[kHrWinN];
  uint8_t n = 0;

  for (uint8_t i = 0; i < _hrFill; i++) {
    if ((now - _hrTime[i]) <= kHrWinMs) tmp[n++] = _hrBuf[i];
  }
  if (n < kHrWinMinN) return 0;

  for (uint8_t i = 1; i < n; i++) {          // 삽입 정렬
    const uint8_t key = tmp[i];
    int16_t j = static_cast<int16_t>(i) - 1;
    while (j >= 0 && tmp[j] > key) {
      tmp[j + 1] = tmp[j];
      j--;
    }
    tmp[j + 1] = key;
  }
  return tmp[n / 2];
}

float DrowsyDetector::hrDrop(uint32_t now) const {
  const uint8_t med = hrMedian(now);
  if (_hrBase <= 0.0f || med == 0) return 0.0f;
  return _hrBase - static_cast<float>(med);
}

uint32_t DrowsyDetector::needMs() const {
  const uint8_t ev = evidence();
  return (ev >= 2) ? kHoldTwoMs : (ev == 1) ? kHoldOneMs : kHoldNoEvidenceMs;
}

void DrowsyDetector::tick(uint32_t now) {
  _ticks++;
  if (hrBlanking(now)) _blankTicks++;

  // _locked 는 onDistance() 에서 디바운스를 거쳐 갱신된다.
  // bed 는 앉은 자세에서 서지 않아 쓸 수 없다
  const bool present = (_presence == 1);

  // 보조 증거 (1) 사람은 있고 거리도 정상인데 호흡이 안 잡힌다
  //              = 가슴이 가려진 자세(엎드림).
  // 거리가 정상 범위일 것을 함께 요구한다. 센서가 가려지면 호흡·심박이
  // 똑같이 끊기는데, 그걸 "엎드렸다"로 읽으면 가려진 동안 졸음으로 판정된다
  _respLost = present && _locked &&
              (_respRate == 0 || _respState == kRespStateNoBreath);

  // 보조 증거 (2) 심박 중앙값이 기준선보다 유의하게 낮은 상태가 지속된다.
  // 순간적으로 넘는 것은 각성 중에도 일어나므로 지속 시간을 함께 요구한다
  const bool hrDownRaw = (hrMedian(now) > 0) && (hrDrop(now) >= kHrDropBpm);
  if (hrDownRaw) {
    if (!_hrDownArmed) {
      _hrDownArmed = true;
      _hrDownSince = now;
    }
  } else {
    _hrDownArmed = false;
  }
  _hrDown = _hrDownArmed && ((now - _hrDownSince) >= kHrDownHoldMs);

  // 락온이 없으면 체동도 믿을 수 없다. 가려진 동안 body 는 1 근처로 죽고
  // 이동평균이 0으로 수렴하는데, 그건 "안 움직인다"가 아니라 "안 보인다"다.
  //
  // 정지 판정은 큰 스파이크의 부재가 주 기준이다. 이동평균은 스파이크 없이
  // 잔움직임만 계속되는 경우를 걸러내는 느슨한 상한으로만 쓴다
  const bool still =
      ((now - _lastSpikeMs) >= kStillAfterSpikeMs) && (_moveAvg < kMoveQuietMax);
  const bool sign = present && _locked && still;

  if (sign != _prevSign) {
    _prevSign = sign;
    _signSince = now;
  }

  const uint32_t held = now - _signSince;
  if (!_drowsy && sign && held >= needMs()) {
    _drowsy = true;
  } else if (_drowsy && !sign && held >= kAwakeHoldMs) {
    _drowsy = false;
  }

  // 표시 상태
  if (!present) {
    _state = DrowsyState::kNoPerson;
  } else if (!_locked) {
    _state = DrowsyState::kNoLock;   // 가림/거리 이탈. 졸음 여부를 알 수 없다
  } else if (_drowsy) {
    _state = DrowsyState::kDrowsy;
  } else if (!_hrEverValid && (now - _startMs) < kDrowsyWarmupMs) {
    _state = DrowsyState::kWarmup;   // 아직 호흡·심박이 한 번도 안 잡혔다
  } else {
    _state = DrowsyState::kAwake;
  }
}

}  // namespace deskmate
