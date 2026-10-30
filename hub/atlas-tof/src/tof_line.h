// ESP32 가 USB 로 보내는 한 줄을 읽는다. 두 가지 펌웨어 형식을 다 받는다.
//
// (A) tof_logger (firmware/esp32_sensor_node/src/tof_logger, env:tof_logger) — JSON 한 줄:
//   {"t":"tof","seq":N,"ms":N,"w":8,"h":8,"d":[64 ints],"st":[64 ints],"rule":{...}}
//   {"t":"tof_hello","ms":N,"sensor":"VL53L8CX","w":8,"h":8,"hz":15,"flip_rows":false,"flip_cols":true,...}
//   {"t":"tof_err","ms":N,"where":"init","code":-1}
// (B) VL53L8CX I2C bring-up (김태환, 플래시 이미지 tof-bringup-full-4MB.bin, 소스는 레포에 없음) — CSV:
//   F,<ms>,<d0>,…,<d63>[,<s0>,…,<s63>]        프레임. 상태값 64개가 없으면 dist>0 을 유효로 본다
//   P,<ms>,<present>,<posture>,<conf>,…        온보드 규칙 판정(TofPosture). 참고용으로만 읽는다
//   # …  /  Ranging started (8x8 @ 15 Hz)  /  [ERR] …   안내·오류
//   (형식은 펌웨어 문자열에서 복원한 것이라 첫 실기 줄로 확인한다. tof_live.py --echo)
// (C) tof_live.py --record 녹화(2026-10-15 이후) — 위 줄을 감싼 한 줄:
//   {"host_t":<PC epoch s>,"port":"COM5","raw":"<원본 줄, JSON 문자열 이스케이프>"}
//   parseTofLine·peekLineMs 는 감싼 줄을 알아서 풀어 읽는다. 포트 하나만 고르려면 LineSource 에 "녹화.jsonl@COM5".
// (D) hub/atlas UART 디코더가 넘겨 주는 UART2 프레임 줄 (TYPE 0x05 "L8 원본 zone", 제안서 proposal_tof_raw_uart.md):
//   UART\t{"type":5,"seq":S,"ts_ms":T,"payload_hex":"<200 B>"}
//   payload v1 = version u8 · sensor_id u8 · flags u8(b0 valid, b1 has_status, b2 flip_rows_needed, b3 flip_cols_needed) ·
//   w u8 · h u8 · reserved u8 · frame_seq u16 · dist_mm u16×64(0 = 타깃 없음, 0xFFFF 도 무효) · target_status u8×64, little-endian.
//   zone 은 센서 원본 순서로 오고, 받는 쪽이 b2/b3 대로 뒤집는다(TOFPOSTURE_FLIP_* 가 있으면 그것이 우선).
//   **표준 배치(data-spec §13.1, 펌웨어 TofPayload.h — 2026-10-17 실기가 보내는 것)** 도 읽는다. 첫 바이트가 1 이 아니면 이것:
//   grid_w u8 · grid_h u8 · sensor u8 · sensors u8 · frame_id u8 · sensor_mask u8 · flags u8 · reserved u8 ·
//   dist i16×n(-1 = 없음) · status u8×n(255 = 없음). flags 는 펌웨어가 **이미 적용한** 방향 변환이라 받는 쪽은 안 뒤집는다.
//   frame_id 는 u8 이라 delta 를 0xFF 로 잰다(TofFrame::seq_mask). valid 비트가 없어 늘 Frame 이다.
//   frame_seq 는 센서마다 +1 씩 도는 u16: delta=(new-old)&0xFFFF 가 0 이면 중복(버림), 1..10 진행, 그 밖이면 GRU 리셋.
//   한 줄에 센서 번호가 들어 있으므로 한 연결로 여러 센서가 온다(TofLine::sensor_id).
// JSON 라이브러리 없이 키를 직접 찾는다. 두 형식 모두 zone 은 장면 방향(row 0 = 위, col 0 = 센서 기준 왼쪽)으로
// 펌웨어가 돌려 보낸다고 가정한다(flip 설정은 펌웨어 쪽). 시뮬 리플레이 파일의 "label":"..." 은 있으면 읽어 둔다.
#ifndef DESKMATE_TOF_LINE_H
#define DESKMATE_TOF_LINE_H

#include <cstdint>
#include <string>

namespace tof {

constexpr int kMaxZones = 64;

struct TofFrame {
    uint32_t seq = 0;           // CSV 형식은 seq 가 없어 ms 를 넣는다 (되감김 감지용으로 충분)
    uint32_t ms = 0;            // ESP32 millis()
    int w = 0, h = 0;
    int16_t d[kMaxZones] = {};
    uint8_t st[kMaxZones] = {};
    bool has_status = true;     // CSV 에 상태값이 없으면 false (dist>0 → 5, 아니면 255 로 채움)
    std::string label;          // 리플레이 파일의 정답 (없으면 빈 문자열)
    int8_t flip_rows_hint = -1; // UART 0x05 flags b2/b3: 받는 쪽이 뒤집어야 하나 (-1 = 형식에 정보 없음)
    int8_t flip_cols_hint = -1;
    bool seq16 = false;         // seq 가 UART 0x05 순번(delta 규칙)인가
    uint32_t seq_mask = 0xFFFFu; // seq16 의 순환 폭: 옛 v1 frame_seq u16 = 0xFFFF, 표준 배치 frame_id u8 = 0xFF
    // /raw 용 원본(받은 그대로, 뒤집기·-1 규약 변환 전). uart_relay_board 의 GET /raw 와 같은 스키마로 내보낸다
    uint32_t uart_seq = 0;      // UART 봉투 seq (0x05 줄의 "seq")
    int frame_id = 0;           // 표준 배치 frame_id(u8). v1 은 0
    int sensor_mask = 0;        // 표준 배치 sensor_mask. v1 은 0
    int raw_flags = 0;          // 받은 flags 바이트 그대로
    int16_t raw_d[kMaxZones] = {};   // 표준 배치: i16 그대로(-1 = 없음). v1: 0·0xFFFF → -1 (중계기와 같게)
    uint8_t raw_st[kMaxZones] = {};
};

struct TofHello {
    uint32_t ms = 0;
    std::string sensor;
    int w = 0, h = 0, hz = 0;
    bool flip_rows = false, flip_cols = false;
};

// Idle = UART 0x05 의 valid=0 프레임: 센서(ESP32)는 살아 있지만 이번 tick 에 새 측정이 없음. frame_seq 만 쓴다.
enum class LineKind { None, Frame, Hello, Error, Rule, Info, Idle };

struct TofLine {
    LineKind kind = LineKind::None;
    TofFrame frame;
    TofHello hello;
    std::string err_where;      // tof_err 의 where / [ERR] 줄
    int err_code = 0;
    std::string rule_posture;   // P 줄의 posture 이름 (예: upright)
    std::string info;           // # 줄, Ranging started … 등
    int sensor_id = -1;         // UART 0x05 줄의 센서 번호 (그 밖의 형식은 -1 = 포트가 곧 센서)
};

// 알아볼 수 있는 줄이면 kind != None. 부팅 로그·깨진 줄은 None.
bool parseTofLine(const std::string& line, TofLine& out);

// 줄이 센서 프로토콜처럼 보이는가 (보드율 자동 탐색용, 가볍게)
bool looksLikeTofLine(const std::string& line);

// "ms" 값만 빨리 꺼낸다 (리플레이 속도 조절용). 없으면 false.
bool peekLineMs(const std::string& line, uint32_t& ms);

// tof_live.py --record 가 감싼 줄이면 원본 줄과 포트를 꺼내고 true. 아니면 false(그대로 쓰면 된다).
bool unwrapRecordLine(const std::string& line, std::string& raw, std::string& port);

}  // namespace tof

#endif  // DESKMATE_TOF_LINE_H
