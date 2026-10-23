/*
 * Fan logic with no hardware calls: command parsing, off-timer, start kick,
 * natural-wind modulation and the state record sent over BLE.
 * main.c drives the PWM pins from fan_duty_permille(); test/ runs this on a PC.
 */
#ifndef FAN_CORE_H
#define FAN_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Below this duty the small DC motor on the L9110 module stalls; speed 1 % maps here. */
#ifndef FAN_MIN_DUTY_PERMILLE
#define FAN_MIN_DUTY_PERMILLE 350
#endif
/* Full power for this long whenever the motor starts from rest. */
#ifndef FAN_KICK_MS
#define FAN_KICK_MS 250
#endif
/* Motor held off this long before spinning the other way. */
#ifndef FAN_REVERSE_PAUSE_MS
#define FAN_REVERSE_PAUSE_MS 400
#endif
#ifndef FAN_LEVEL1_SPEED
#define FAN_LEVEL1_SPEED 30
#endif
#ifndef FAN_LEVEL2_SPEED
#define FAN_LEVEL2_SPEED 60
#endif
#ifndef FAN_LEVEL3_SPEED
#define FAN_LEVEL3_SPEED 100
#endif
/* Timer button steps, minutes: off -> 30 -> 60 -> 120 -> 240 -> off. */
#ifndef FAN_TIMER_PRESETS_MIN
#define FAN_TIMER_PRESETS_MIN 30, 60, 120, 240
#endif
/* 0 = forward. Set 1 if the propeller blows backwards and swapping INA/INB is not an option. */
#ifndef FAN_DEFAULT_DIR
#define FAN_DEFAULT_DIR 0
#endif

#define FAN_STATE_VERSION 1
#define FAN_STATE_LEN 8
#define FAN_TEXT_MAX 40

/* Binary opcodes: <op> <arg>, several may follow each other in one write. */
enum {
    FAN_OP_POWER = 0x01, /* 0 off, 1 on, 2 toggle */
    FAN_OP_SPEED = 0x02, /* 0..100 %, 0 = off */
    FAN_OP_LEVEL = 0x03, /* 0 off, 1..3 */
    FAN_OP_MODE = 0x04,  /* 0 normal, 1 natural wind */
    FAN_OP_DIR = 0x05,   /* 0 forward, 1 reverse */
    FAN_OP_TIMER = 0x06, /* u16 little-endian seconds, 0 = cancel */
};

typedef enum { FAN_MODE_NORMAL = 0, FAN_MODE_NATURAL = 1 } fan_mode_t;
typedef enum { FAN_DIR_FORWARD = 0, FAN_DIR_REVERSE = 1 } fan_dir_t;

typedef enum {
    FAN_OK = 0,
    FAN_ERR_EMPTY,
    FAN_ERR_UNKNOWN,
    FAN_ERR_LENGTH,
    FAN_ERR_ARGUMENT,
    FAN_ERR_RANGE,
} fan_result_t;

typedef struct {
    bool power;
    uint8_t speed; /* 1..100, kept while off so "on" resumes it */
    fan_mode_t mode;
    fan_dir_t dir;
    uint32_t timer_ms;  /* off-timer remaining, 0 = none */
    uint8_t timer_step; /* 1.. = preset in use (FAN_TIMER_PRESETS_MIN), 0 = none or a custom time */
    uint32_t kick_ms;
    uint32_t pause_ms;
    uint32_t clock_ms; /* natural-wind clock */
} fan_state_t;

void fan_init(fan_state_t *s);

/* Text ("speed 60", "on; timer 30m") or binary frames. All or nothing: on error s is unchanged. */
fan_result_t fan_command(fan_state_t *s, const uint8_t *buf, size_t len);

/* Advance timers; true when the off-timer switched the fan off. */
bool fan_tick(fan_state_t *s, uint32_t dt_ms);

uint16_t fan_duty_permille(const fan_state_t *s);
uint8_t fan_level(const fan_state_t *s);

/* Front-panel buttons, like a household fan. */
void fan_button_level(fan_state_t *s);   /* off -> 1 -> 2 -> 3 -> off */
void fan_button_off(fan_state_t *s);     /* long press on the level button */
void fan_button_natural(fan_state_t *s); /* natural wind on/off; starts at level 1 when off */
bool fan_button_timer(fan_state_t *s);   /* next preset, past the last = no timer; false (no change) while off */

/* [version, power, speed, level, mode, dir, timer_s lo, timer_s hi] */
size_t fan_encode_state(const fan_state_t *s, uint8_t out[FAN_STATE_LEN]);

const char *fan_result_name(fan_result_t r);

#endif
