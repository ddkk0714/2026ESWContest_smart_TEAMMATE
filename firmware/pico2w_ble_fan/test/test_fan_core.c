/* Host tests for fan_core: gcc -std=c11 -Wall -Wextra -Werror -I../src ../src/fan_core.c test_fan_core.c */
#include <stdio.h>
#include <string.h>

#include "fan_core.h"

static int failures, checks;

#define CHECK(cond)                                                       \
    do {                                                                  \
        checks++;                                                         \
        if (!(cond)) {                                                    \
            failures++;                                                   \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);        \
        }                                                                 \
    } while (0)

static fan_result_t text(fan_state_t *s, const char *t) {
    return fan_command(s, (const uint8_t *)t, strlen(t));
}

static fan_result_t bin(fan_state_t *s, const uint8_t *b, size_t n) { return fan_command(s, b, n); }

static void settle(fan_state_t *s) { fan_tick(s, 5000); } /* past kick and pause */

static void test_defaults(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(!s.power);
    CHECK(s.speed == FAN_LEVEL2_SPEED);
    CHECK(fan_duty_permille(&s) == 0);
    CHECK(fan_level(&s) == 0);
}

static void test_binary(void) {
    fan_state_t s;
    fan_init(&s);
    const uint8_t on_speed[] = {FAN_OP_POWER, 1, FAN_OP_SPEED, 40};
    CHECK(bin(&s, on_speed, sizeof(on_speed)) == FAN_OK);
    CHECK(s.power && s.speed == 40);
    CHECK(fan_duty_permille(&s) == FAN_MAX_DUTY_PERMILLE); /* start kick, capped */
    settle(&s);
    CHECK(fan_duty_permille(&s) == FAN_MIN_DUTY_PERMILLE); /* 40 % of the 60 % ceiling is under the floor */

    const uint8_t level3[] = {FAN_OP_LEVEL, 3};
    CHECK(bin(&s, level3, 2) == FAN_OK);
    CHECK(s.speed == FAN_LEVEL3_SPEED && fan_level(&s) == 3);
    settle(&s);
    CHECK(fan_duty_permille(&s) == FAN_MAX_DUTY_PERMILLE); /* speed 100 % = the cap */

    const uint8_t timer[] = {FAN_OP_TIMER, 0x2c, 0x01}; /* 300 s */
    CHECK(bin(&s, timer, 3) == FAN_OK);
    CHECK(s.timer_ms == 300000);

    const uint8_t off[] = {FAN_OP_SPEED, 0};
    CHECK(bin(&s, off, 2) == FAN_OK);
    CHECK(!s.power && s.speed == FAN_LEVEL3_SPEED && s.timer_ms == 0);

    const uint8_t toggle[] = {FAN_OP_POWER, 2};
    CHECK(bin(&s, toggle, 2) == FAN_OK && s.power);
    CHECK(bin(&s, toggle, 2) == FAN_OK && !s.power);
}

static void test_binary_errors_leave_state(void) {
    fan_state_t s;
    fan_init(&s);
    const uint8_t partial_bad[] = {FAN_OP_POWER, 1, FAN_OP_SPEED, 101};
    CHECK(bin(&s, partial_bad, sizeof(partial_bad)) == FAN_ERR_RANGE);
    CHECK(!s.power); /* first op not committed */
    const uint8_t truncated[] = {FAN_OP_TIMER, 0x10};
    CHECK(bin(&s, truncated, 2) == FAN_ERR_LENGTH);
    const uint8_t no_arg[] = {FAN_OP_SPEED};
    CHECK(bin(&s, no_arg, 1) == FAN_ERR_LENGTH);
    const uint8_t unknown[] = {0x07, 0};
    CHECK(bin(&s, unknown, 2) == FAN_ERR_UNKNOWN);
    const uint8_t zero[] = {0x00};
    CHECK(bin(&s, zero, 1) == FAN_ERR_UNKNOWN);
    CHECK(fan_command(&s, NULL, 0) == FAN_ERR_EMPTY);
    const uint8_t level4[] = {FAN_OP_LEVEL, 4};
    CHECK(bin(&s, level4, 2) == FAN_ERR_RANGE);
    const uint8_t mode2[] = {FAN_OP_MODE, 2};
    CHECK(bin(&s, mode2, 2) == FAN_ERR_RANGE);
}

static void test_text(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(text(&s, "ON") == FAN_OK && s.power);
    CHECK(text(&s, "speed 75\n") == FAN_OK && s.speed == 75);
    CHECK(text(&s, "level 1") == FAN_OK && s.speed == FAN_LEVEL1_SPEED);
    CHECK(text(&s, "natural") == FAN_OK && s.mode == FAN_MODE_NATURAL);
    CHECK(text(&s, "mode normal") == FAN_OK && s.mode == FAN_MODE_NORMAL);
    CHECK(text(&s, "timer 30m") == FAN_OK && s.timer_ms == 1800000);
    CHECK(text(&s, "timer 90s") == FAN_OK && s.timer_ms == 90000);
    CHECK(text(&s, "timer 0") == FAN_OK && s.timer_ms == 0);
    CHECK(text(&s, "  status  ") == FAN_OK);
    CHECK(text(&s, "off") == FAN_OK && !s.power);
    CHECK(text(&s, "on; speed 50, natural") == FAN_OK && s.power && s.speed == 50 && s.mode == FAN_MODE_NATURAL);
    const uint8_t with_nul[] = {'o', 'f', 'f', 0, 'x'};
    CHECK(bin(&s, with_nul, sizeof(with_nul)) == FAN_OK && !s.power);
    CHECK(text(&s, "toggle") == FAN_OK && s.power);
}

static void test_text_errors(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(text(&s, "speed") == FAN_ERR_ARGUMENT);
    CHECK(text(&s, "speed abc") == FAN_ERR_ARGUMENT);
    CHECK(text(&s, "speed 101") == FAN_ERR_RANGE);
    CHECK(text(&s, "level 9") == FAN_ERR_RANGE);
    CHECK(text(&s, "timer 1200m") == FAN_ERR_RANGE);
    CHECK(text(&s, "mode turbo") == FAN_ERR_RANGE);
    CHECK(text(&s, "blow") == FAN_ERR_UNKNOWN);
    CHECK(text(&s, "speed 1 2") == FAN_ERR_ARGUMENT);
    CHECK(text(&s, "on; speed 500") == FAN_ERR_RANGE);
    CHECK(!s.power); /* "on" in a failed write is not applied */
    char longline[FAN_TEXT_MAX + 5];
    memset(longline, 'a', sizeof(longline) - 1);
    longline[sizeof(longline) - 1] = 0;
    CHECK(text(&s, longline) == FAN_ERR_LENGTH);
}

static void test_timer(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(text(&s, "on; timer 2") == FAN_OK);
    CHECK(!fan_tick(&s, 1500));
    CHECK(s.power);
    uint8_t st[FAN_STATE_LEN];
    fan_encode_state(&s, st);
    CHECK(st[6] == 1 && st[7] == 0); /* 500 ms left rounds up to 1 s */
    CHECK(fan_tick(&s, 600));
    CHECK(!s.power && s.timer_ms == 0);
    CHECK(!fan_tick(&s, 1000)); /* no second off event */
}

static void test_reverse_pause(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(text(&s, "on") == FAN_OK);
    settle(&s);
    CHECK(fan_duty_permille(&s) > 0);
    CHECK(text(&s, "reverse") == FAN_OK && s.dir == FAN_DIR_REVERSE);
    CHECK(fan_duty_permille(&s) == 0); /* pause first */
    fan_tick(&s, FAN_REVERSE_PAUSE_MS);
    CHECK(fan_duty_permille(&s) == FAN_MAX_DUTY_PERMILLE); /* then kick */
    fan_tick(&s, FAN_KICK_MS);
    CHECK(fan_duty_permille(&s) > 0 && fan_duty_permille(&s) < FAN_MAX_DUTY_PERMILLE);
    CHECK(text(&s, "dir reverse") == FAN_OK && s.pause_ms == 0); /* same direction: no pause */
}

static void test_natural_range(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(text(&s, "speed 100; natural") == FAN_OK);
    settle(&s);
    uint16_t lo = 1000, hi = 0;
    for (int i = 0; i < 2000; i++) {
        fan_tick(&s, 20);
        uint16_t d = fan_duty_permille(&s);
        if (d < lo) lo = d;
        if (d > hi) hi = d;
    }
    CHECK(lo >= FAN_MIN_DUTY_PERMILLE);
    CHECK(hi <= FAN_MAX_DUTY_PERMILLE);
    CHECK(hi - lo > (FAN_MAX_DUTY_PERMILLE - FAN_MIN_DUTY_PERMILLE) / 3); /* it actually gusts */
}

static void test_buttons(void) {
    fan_state_t s;
    fan_init(&s);
    fan_button_level(&s);
    CHECK(s.power && fan_level(&s) == 1);
    fan_button_level(&s);
    CHECK(fan_level(&s) == 2);
    fan_button_level(&s);
    CHECK(fan_level(&s) == 3);
    fan_button_level(&s);
    CHECK(!s.power);
    fan_button_natural(&s);
    CHECK(s.power && s.mode == FAN_MODE_NATURAL && fan_level(&s) == 1);
    fan_button_natural(&s);
    CHECK(s.mode == FAN_MODE_NORMAL && s.power);
    fan_button_level(&s);
    fan_button_off(&s);
    CHECK(!s.power);
}

static void test_timer_button(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(!fan_button_timer(&s)); /* off: nothing to time */
    CHECK(s.timer_ms == 0);
    fan_button_level(&s);
    const uint32_t want_min[] = {30, 60, 120, 240, 0};
    for (int i = 0; i < 5; i++) {
        CHECK(fan_button_timer(&s));
        CHECK(s.timer_ms == want_min[i] * 60000u);
        CHECK(s.timer_step == (i < 4 ? i + 1 : 0));
    }
    /* steps follow the selected preset, not what is left of it */
    fan_button_timer(&s);
    fan_tick(&s, 10 * 60000u);
    fan_button_timer(&s);
    CHECK(s.timer_ms == 60 * 60000u);
    /* a remote that sends a preset's length continues the same sequence */
    CHECK(text(&s, "timer 120m") == FAN_OK && s.timer_step == 3);
    fan_button_timer(&s);
    CHECK(s.timer_ms == 240 * 60000u);
    /* custom time: next preset above what is left */
    CHECK(text(&s, "timer 45m") == FAN_OK && s.timer_step == 0);
    fan_button_timer(&s);
    CHECK(s.timer_ms == 60 * 60000u && s.timer_step == 2);
    CHECK(text(&s, "timer 300m") == FAN_OK);
    fan_button_timer(&s);
    CHECK(s.timer_ms == 0);
    /* expiry and off forget the step */
    fan_button_timer(&s);
    fan_tick(&s, 30 * 60000u);
    CHECK(!s.power && s.timer_step == 0);
}

/* The duties asked for on 10-24: 30 / 45 / 60 %, floor 30 %, nothing above 60 %. */
static void test_level_duties(void) {
    fan_state_t s;
    fan_init(&s);
    const uint16_t want[] = {0, 300, 450, 600};
    for (uint8_t level = 1; level <= 3; level++) {
        uint8_t cmd[] = {FAN_OP_LEVEL, level};
        CHECK(bin(&s, cmd, 2) == FAN_OK);
        settle(&s);
        CHECK(fan_duty_permille(&s) == want[level]);
    }
    CHECK(text(&s, "speed 5") == FAN_OK);
    settle(&s);
    CHECK(fan_duty_permille(&s) == 300);
    CHECK(text(&s, "off; speed 100") == FAN_OK);
    CHECK(fan_duty_permille(&s) == 600); /* the start kick obeys the ceiling too */
}

static void test_encode(void) {
    fan_state_t s;
    fan_init(&s);
    CHECK(text(&s, "speed 75; natural; reverse; timer 600") == FAN_OK);
    uint8_t st[FAN_STATE_LEN];
    CHECK(fan_encode_state(&s, st) == 8);
    const uint8_t want[] = {FAN_STATE_VERSION, 1, 75, 2, 1, 1, 0x58, 0x02};
    CHECK(memcmp(st, want, sizeof(want)) == 0);
}

int main(void) {
    test_defaults();
    test_binary();
    test_binary_errors_leave_state();
    test_text();
    test_text_errors();
    test_timer();
    test_reverse_pause();
    test_natural_range();
    test_buttons();
    test_timer_button();
    test_level_duties();
    test_encode();
    printf("%d checks, %d failed\n", checks, failures);
    return failures ? 1 : 0;
}
