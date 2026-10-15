#pragma once

#include <stddef.h>
#include <stdint.h>

#include "preprocess/tof/TofPosture.h"

namespace deskmate {

// ============================================================================
//  ToF 학습 데이터 수집 모드의 USB JSON 줄
//
//  운영 펌웨어(env:esp32dev)에는 들어가지 않는다. env:tof_logger 로 따로 구워서
//  PC 수집기(dataset/tof_logger)에 USB 로만 보낸다 — 원본 zone 배열을 운영 경로로
//  내보내지 않는다는 원칙(firmware/README "원칙")은 그대로다.
//
//  줄은 기존 USB JSON 규약처럼 `{"t":` 로 시작한다. 같은 포트의 부팅 로그·디버그
//  문구는 PC 쪽이 버린다.
//
//    {"t":"tof","seq":12,"ms":3456,"w":8,"h":8,"d":[...],"st":[...],"rule":{...}}
//
//  `d`·`st` 는 **센서가 낸 zone 순서 그대로**(ST 인덱스)다. 화면 방향으로 돌리는 것은
//  hello 의 flip_rows·flip_cols 로 PC 가 한다. 방향을 잘못 잡아도 원본은 남으니
//  나중에 다시 돌려서 쓸 수 있다. 타깃이 없는 zone 은 d = -1, st = 255.
//  `rule` 은 같은 프레임에서 기존 규칙 추정기(TofPosture)가 낸 판정이다.
// ============================================================================

// 64 zone 기준 한 줄은 약 700 B. 여유를 두고 잡는다.
constexpr size_t kTofLineMax = 1400;

// 센서 순서 → 추정기 좌표(row 0 = 위, col 은 센서가 바라보는 방향 기준 왼→오른쪽).
void remapZones(const int16_t *src_d, const uint8_t *src_st, int16_t *dst_d, uint8_t *dst_st,
                uint8_t w, uint8_t h, bool flip_rows, bool flip_cols);

// 성공하면 '\n' 까지 포함한 길이, 버퍼가 모자라면 0.
size_t formatTofLine(char *buf, size_t cap, uint32_t seq, uint32_t ms, uint8_t w, uint8_t h,
                     const int16_t *d, const uint8_t *st, const TofFeatures &f);

struct TofLoggerInfo {
  const char *sensor;
  uint8_t w;
  uint8_t h;
  uint8_t hz;
  bool flip_rows;
  bool flip_cols;
  const char *target_order;  // "closest" | "strongest"
  uint16_t fw_version;
};

size_t formatHelloLine(char *buf, size_t cap, uint32_t ms, const TofLoggerInfo &info);
size_t formatErrorLine(char *buf, size_t cap, uint32_t ms, const char *where, int code);

}  // namespace deskmate
