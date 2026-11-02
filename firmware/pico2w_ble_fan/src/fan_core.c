#include "fan_core.h"

#include <string.h>

static const uint16_t timer_presets_min[] = {FAN_TIMER_PRESETS_MIN};
#define TIMER_PRESET_COUNT (sizeof(timer_presets_min) / sizeof(timer_presets_min[0]))

static uint8_t level_speed(uint8_t level) {
    switch (level) {
    case 1: return FAN_LEVEL1_SPEED;
    case 2: return FAN_LEVEL2_SPEED;
    default: return FAN_LEVEL3_SPEED;
    }
}

static void set_power(fan_state_t *s, bool on) {
    if (on && !s->power) {
        s->kick_ms = FAN_KICK_MS;
        s->pause_ms = 0;
    }
    if (!on) {
        s->timer_ms = 0;
        s->timer_step = 0;
        s->kick_ms = 0;
        s->pause_ms = 0;
    }
    if (on && s->speed == 0) s->speed = FAN_LEVEL2_SPEED;
    s->power = on;
}

static void set_speed(fan_state_t *s, uint8_t speed) {
    if (speed == 0) {
        set_power(s, false);
        return;
    }
    s->speed = speed;
    set_power(s, true);
}

/* A time equal to a preset counts as that preset, so the timer button continues from it. */
static void set_timer(fan_state_t *s, uint32_t seconds) {
    s->timer_ms = seconds * 1000u;
    s->timer_step = 0;
    for (size_t i = 0; i < TIMER_PRESET_COUNT; i++) {
        if (seconds && seconds == timer_presets_min[i] * 60u) s->timer_step = (uint8_t)(i + 1);
    }
}

static void set_dir(fan_state_t *s, fan_dir_t dir) {
    if (dir == s->dir) return;
    s->dir = dir;
    if (s->power) {
        s->pause_ms = FAN_REVERSE_PAUSE_MS;
        s->kick_ms = FAN_KICK_MS;
    }
}

void fan_init(fan_state_t *s) {
    memset(s, 0, sizeof(*s));
    s->speed = FAN_LEVEL2_SPEED;
    s->mode = FAN_MODE_NORMAL;
    s->dir = FAN_DEFAULT_DIR ? FAN_DIR_REVERSE : FAN_DIR_FORWARD;
}

static fan_result_t apply_binary(fan_state_t *s, const uint8_t *buf, size_t len) {
    size_t i = 0;
    while (i < len) {
        uint8_t op = buf[i++];
        if (op < FAN_OP_POWER || op > FAN_OP_TIMER) return FAN_ERR_UNKNOWN;
        size_t need = op == FAN_OP_TIMER ? 2 : 1;
        if (len - i < need) return FAN_ERR_LENGTH;
        uint8_t v = buf[i];
        switch (op) {
        case FAN_OP_POWER:
            if (v > 2) return FAN_ERR_RANGE;
            set_power(s, v == 2 ? !s->power : v == 1);
            break;
        case FAN_OP_SPEED:
            if (v > 100) return FAN_ERR_RANGE;
            set_speed(s, v);
            break;
        case FAN_OP_LEVEL:
            if (v > 3) return FAN_ERR_RANGE;
            set_speed(s, v ? level_speed(v) : 0);
            break;
        case FAN_OP_MODE:
            if (v > 1) return FAN_ERR_RANGE;
            s->mode = (fan_mode_t)v;
            break;
        case FAN_OP_DIR:
            if (v > 1) return FAN_ERR_RANGE;
            set_dir(s, (fan_dir_t)v);
            break;
        case FAN_OP_TIMER:
            set_timer(s, (uint32_t)(buf[i] | (buf[i + 1] << 8)));
            break;
        }
        i += need;
    }
    return FAN_OK;
}

static bool word_is(const char *w, size_t n, const char *lit) {
    return strlen(lit) == n && memcmp(w, lit, n) == 0;
}

static bool parse_uint(const char *w, size_t n, uint32_t max, uint32_t *out) {
    if (n == 0 || n > 6) return false;
    uint32_t v = 0;
    for (size_t i = 0; i < n; i++) {
        if (w[i] < '0' || w[i] > '9') return false;
        v = v * 10 + (uint32_t)(w[i] - '0');
    }
    if (v > max) return false;
    *out = v;
    return true;
}

/* One command such as "speed 60". Words are already lower case. */
static fan_result_t apply_words(fan_state_t *s, const char *seg, size_t len) {
    const char *w[3];
    size_t n[3];
    size_t count = 0, i = 0;
    while (i < len) {
        while (i < len && (seg[i] == ' ' || seg[i] == '\t')) i++;
        if (i >= len) break;
        if (count == 3) return FAN_ERR_ARGUMENT;
        w[count] = seg + i;
        while (i < len && seg[i] != ' ' && seg[i] != '\t') i++;
        n[count] = (size_t)(seg + i - w[count]);
        count++;
    }
    if (count == 0) return FAN_OK;
    const char *cmd = w[0];
    size_t cn = n[0];
    uint32_t v;

    if (count == 1) {
        if (word_is(cmd, cn, "on")) set_power(s, true);
        else if (word_is(cmd, cn, "off")) set_power(s, false);
        else if (word_is(cmd, cn, "toggle")) set_power(s, !s->power);
        else if (word_is(cmd, cn, "natural")) s->mode = FAN_MODE_NATURAL;
        else if (word_is(cmd, cn, "normal")) s->mode = FAN_MODE_NORMAL;
        else if (word_is(cmd, cn, "forward")) set_dir(s, FAN_DIR_FORWARD);
        else if (word_is(cmd, cn, "reverse")) set_dir(s, FAN_DIR_REVERSE);
        else if (word_is(cmd, cn, "status")) { /* no change; caller reports state */ }
        else if (word_is(cmd, cn, "speed") || word_is(cmd, cn, "level") || word_is(cmd, cn, "timer") ||
                 word_is(cmd, cn, "mode") || word_is(cmd, cn, "dir"))
            return FAN_ERR_ARGUMENT;
        else return FAN_ERR_UNKNOWN;
        return FAN_OK;
    }
    if (count != 2) return FAN_ERR_ARGUMENT;
    const char *arg = w[1];
    size_t an = n[1];

    if (word_is(cmd, cn, "speed")) {
        if (!parse_uint(arg, an, 999999, &v)) return FAN_ERR_ARGUMENT;
        if (v > 100) return FAN_ERR_RANGE;
        set_speed(s, (uint8_t)v);
    } else if (word_is(cmd, cn, "level")) {
        if (!parse_uint(arg, an, 999999, &v)) return FAN_ERR_ARGUMENT;
        if (v > 3) return FAN_ERR_RANGE;
        set_speed(s, v ? level_speed((uint8_t)v) : 0);
    } else if (word_is(cmd, cn, "timer")) {
        uint32_t scale = 1;
        if (an > 1 && arg[an - 1] == 'm') {
            scale = 60;
            an--;
        } else if (an > 1 && arg[an - 1] == 's') {
            an--;
        }
        if (!parse_uint(arg, an, 999999, &v)) return FAN_ERR_ARGUMENT;
        if (v * scale > 65535u) return FAN_ERR_RANGE;
        set_timer(s, v * scale);
    } else if (word_is(cmd, cn, "mode")) {
        if (word_is(arg, an, "natural")) s->mode = FAN_MODE_NATURAL;
        else if (word_is(arg, an, "normal")) s->mode = FAN_MODE_NORMAL;
        else return FAN_ERR_RANGE;
    } else if (word_is(cmd, cn, "dir")) {
        if (word_is(arg, an, "forward")) set_dir(s, FAN_DIR_FORWARD);
        else if (word_is(arg, an, "reverse")) set_dir(s, FAN_DIR_REVERSE);
        else return FAN_ERR_RANGE;
    } else {
        return FAN_ERR_UNKNOWN;
    }
    return FAN_OK;
}

static fan_result_t apply_text(fan_state_t *s, const uint8_t *buf, size_t len) {
    char text[FAN_TEXT_MAX];
    size_t n = 0;
    for (size_t i = 0; i < len && buf[i] != 0; i++) {
        if (n == sizeof(text)) return FAN_ERR_LENGTH;
        char c = (char)buf[i];
        if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
        if (c == '\r' || c == '\n' || c == ',') c = ';';
        text[n++] = c;
    }
    size_t start = 0;
    for (size_t i = 0; i <= n; i++) {
        if (i == n || text[i] == ';') {
            fan_result_t r = apply_words(s, text + start, i - start);
            if (r != FAN_OK) return r;
            start = i + 1;
        }
    }
    return FAN_OK;
}

fan_result_t fan_command(fan_state_t *s, const uint8_t *buf, size_t len) {
    if (len == 0) return FAN_ERR_EMPTY;
    fan_state_t next = *s;
    fan_result_t r;
    if (buf[0] >= FAN_OP_POWER && buf[0] <= FAN_OP_TIMER) r = apply_binary(&next, buf, len);
    else if (buf[0] >= 0x20 && buf[0] < 0x7f) r = apply_text(&next, buf, len);
    else r = FAN_ERR_UNKNOWN;
    if (r == FAN_OK) *s = next;
    return r;
}

bool fan_tick(fan_state_t *s, uint32_t dt_ms) {
    s->clock_ms += dt_ms;
    if (s->pause_ms) {
        s->pause_ms = dt_ms >= s->pause_ms ? 0 : s->pause_ms - dt_ms;
    } else if (s->kick_ms) {
        s->kick_ms = dt_ms >= s->kick_ms ? 0 : s->kick_ms - dt_ms;
    }
    if (s->timer_ms) {
        if (dt_ms >= s->timer_ms) {
            s->timer_ms = 0;
            s->timer_step = 0;
            if (s->power) {
                set_power(s, false);
                return true;
            }
        } else {
            s->timer_ms -= dt_ms;
        }
    }
    return false;
}

static uint32_t triangle(uint32_t t, uint32_t period) {
    uint32_t v = (t % period) * 2000u / period;
    return v > 1000u ? 2000u - v : v;
}

/* 400..1000 permille: two slow triangles with unrelated periods feel like gusts. */
static uint32_t natural_factor(uint32_t clock_ms) {
    uint32_t mix = (triangle(clock_ms, 7300) * 6 + triangle(clock_ms, 3100) * 4) / 10;
    return 400u + mix * 600u / 1000u;
}

uint16_t fan_duty_permille(const fan_state_t *s) {
    if (!s->power || s->speed == 0 || s->pause_ms) return 0;
    if (s->kick_ms) return FAN_MAX_DUTY_PERMILLE;
    uint32_t speed = s->speed;
    if (s->mode == FAN_MODE_NATURAL) {
        speed = speed * natural_factor(s->clock_ms) / 1000u;
        if (speed == 0) speed = 1;
    }
    uint32_t duty = FAN_MAX_DUTY_PERMILLE * speed / 100u;
    return (uint16_t)(duty < FAN_MIN_DUTY_PERMILLE ? FAN_MIN_DUTY_PERMILLE : duty);
}

uint8_t fan_level(const fan_state_t *s) {
    if (!s->power || s->speed == 0) return 0;
    if (s->speed <= (FAN_LEVEL1_SPEED + FAN_LEVEL2_SPEED) / 2) return 1;
    if (s->speed <= (FAN_LEVEL2_SPEED + FAN_LEVEL3_SPEED) / 2) return 2;
    return 3;
}

void fan_button_level(fan_state_t *s) {
    uint8_t level = fan_level(s);
    if (level >= 3) set_power(s, false);
    else set_speed(s, level_speed((uint8_t)(level + 1)));
}

void fan_button_off(fan_state_t *s) { set_power(s, false); }

void fan_button_natural(fan_state_t *s) {
    if (!s->power) {
        s->mode = FAN_MODE_NATURAL;
        set_speed(s, FAN_LEVEL1_SPEED);
    } else {
        s->mode = s->mode == FAN_MODE_NATURAL ? FAN_MODE_NORMAL : FAN_MODE_NATURAL;
    }
}

bool fan_button_timer(fan_state_t *s) {
    if (!s->power) return false;
    size_t next = 0; /* index into presets; == count means no timer */
    if (s->timer_step) {
        next = s->timer_step;
    } else if (s->timer_ms) {
        /* custom time: the first preset longer than what is left */
        while (next < TIMER_PRESET_COUNT && timer_presets_min[next] * 60000u <= s->timer_ms) next++;
    }
    if (next >= TIMER_PRESET_COUNT) {
        s->timer_ms = 0;
        s->timer_step = 0;
    } else {
        set_timer(s, timer_presets_min[next] * 60u);
    }
    return true;
}

size_t fan_encode_state(const fan_state_t *s, uint8_t out[FAN_STATE_LEN]) {
    uint32_t timer_s = (s->timer_ms + 999u) / 1000u;
    if (timer_s > 65535u) timer_s = 65535u;
    out[0] = FAN_STATE_VERSION;
    out[1] = s->power ? 1 : 0;
    out[2] = s->speed;
    out[3] = fan_level(s);
    out[4] = (uint8_t)s->mode;
    out[5] = (uint8_t)s->dir;
    out[6] = (uint8_t)(timer_s & 0xff);
    out[7] = (uint8_t)(timer_s >> 8);
    return FAN_STATE_LEN;
}

const char *fan_result_name(fan_result_t r) {
    switch (r) {
    case FAN_OK: return "ok";
    case FAN_ERR_EMPTY: return "empty";
    case FAN_ERR_UNKNOWN: return "unknown command";
    case FAN_ERR_LENGTH: return "bad length";
    case FAN_ERR_ARGUMENT: return "bad argument";
    case FAN_ERR_RANGE: return "out of range";
    }
    return "?";
}
