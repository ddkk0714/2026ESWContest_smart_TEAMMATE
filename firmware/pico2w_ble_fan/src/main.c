/*
 * DESKMATE BLE fan: Raspberry Pi Pico 2 W + L9110 fan module.
 *
 * GATT peripheral "DESKMATE-FAN". Write commands to f1970002-..., read or
 * subscribe to the 8-byte state on f1970003-... . The same text commands work
 * over USB serial, one per line, for bench tests without Bluetooth.
 * Wiring, protocol and build: ../README.md
 */
#include <stdio.h>
#include <string.h>

#include "btstack.h"
#include "hardware/clocks.h"
#include "hardware/pwm.h"
#include "hardware/watchdog.h"
#include "pico/binary_info.h"
#include "pico/btstack_cyw43.h"
#include "pico/cyw43_arch.h"
#include "pico/stdlib.h"

#include "deskmate_fan.h" /* generated from deskmate_fan.gatt */
#include "fan_core.h"

#ifndef FAN_PIN_INA
#define FAN_PIN_INA 16 /* physical pin 21 -> module INA */
#endif
#ifndef FAN_PIN_INB
#define FAN_PIN_INB 17 /* physical pin 22 -> module INB */
#endif
/* Optional push buttons to GND (pins 17-20 sit together with GND on 18). */
#ifndef FAN_PIN_BTN_LEVEL
#define FAN_PIN_BTN_LEVEL 15 /* physical pin 20: off -> 1 -> 2 -> 3, hold = off */
#endif
#ifndef FAN_PIN_BTN_NATURAL
#define FAN_PIN_BTN_NATURAL 14 /* physical pin 19 */
#endif
#ifndef FAN_PIN_BTN_TIMER
#define FAN_PIN_BTN_TIMER 13 /* physical pin 17: 30 min -> 1 h -> 2 h -> 4 h -> none */
#endif
#ifndef FAN_PWM_HZ
#define FAN_PWM_HZ 20000 /* above hearing; lower it if the motor will not start at low speed */
#endif

#define TICK_MS 20
#define DEBOUNCE_MS 40
#define LONG_PRESS_MS 800
#define WATCHDOG_MS 8000
/* 10-24 Pi 5: BlueZ connects with a 420 ms supervision timeout, so any short radio gap (its Wi-Fi shares the
 * antenna) dropped the link. Once connected, ask for 4 s at the same 30-50 ms interval. */
#define CONN_PARAM_DELAY_MS 2000     /* after the central's service discovery */
#define CONN_INTERVAL_MIN 24         /* x1.25 ms */
#define CONN_INTERVAL_MAX 40
#define CONN_SUPERVISION_TIMEOUT 400 /* x10 ms */
#define PWM_WRAP 999 /* level = duty permille */

#define COMMAND_VALUE_HANDLE ATT_CHARACTERISTIC_f1970002_51e1_4b0e_a7f1_8d27d8e34683_01_VALUE_HANDLE
#define STATE_VALUE_HANDLE ATT_CHARACTERISTIC_f1970003_51e1_4b0e_a7f1_8d27d8e34683_01_VALUE_HANDLE
#define STATE_CCC_HANDLE ATT_CHARACTERISTIC_f1970003_51e1_4b0e_a7f1_8d27d8e34683_01_CLIENT_CONFIGURATION_HANDLE

static const uint8_t adv_data[] = {
    0x02, BLUETOOTH_DATA_TYPE_FLAGS, 0x06,
    0x0d, BLUETOOTH_DATA_TYPE_COMPLETE_LOCAL_NAME, 'D', 'E', 'S', 'K', 'M', 'A', 'T', 'E', '-', 'F', 'A', 'N',
};
/* Service UUID f1970001-51e1-4b0e-a7f1-8d27d8e34683, little-endian. Name + UUID do not fit one 31-byte packet. */
static const uint8_t scan_resp_data[] = {
    0x11, BLUETOOTH_DATA_TYPE_COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
    0x83, 0x46, 0xe3, 0xd8, 0x27, 0x8d, 0xf1, 0xa7, 0x0e, 0x4b, 0xe1, 0x51, 0x01, 0x00, 0x97, 0xf1,
};

static fan_state_t fan;
static hci_con_handle_t con_handle = HCI_CON_HANDLE_INVALID;
static bool notify_enabled;
static btstack_timer_source_t tick_timer;
static btstack_timer_source_t conn_param_timer;
static btstack_packet_callback_registration_t hci_event_callback_registration;
static uint32_t last_tick_ms;

static void motor_init(void) {
    const uint pins[2] = {FAN_PIN_INA, FAN_PIN_INB};
    float div = (float)clock_get_hz(clk_sys) / ((float)FAN_PWM_HZ * (PWM_WRAP + 1));
    if (div < 1.0f) div = 1.0f;
    for (int i = 0; i < 2; i++) {
        gpio_set_function(pins[i], GPIO_FUNC_PWM);
        uint slice = pwm_gpio_to_slice_num(pins[i]);
        pwm_config cfg = pwm_get_default_config();
        pwm_config_set_clkdiv(&cfg, div);
        pwm_config_set_wrap(&cfg, PWM_WRAP);
        pwm_init(slice, &cfg, false);
        pwm_set_gpio_level(pins[i], 0);
        pwm_set_enabled(slice, true);
    }
}

/* L9110: INA=PWM, INB=low spins one way; swapped the other; both low stops. */
static void motor_apply(void) {
    uint16_t duty = fan_duty_permille(&fan);
    bool forward = fan.dir == FAN_DIR_FORWARD;
    pwm_set_gpio_level(FAN_PIN_INA, forward ? duty : 0);
    pwm_set_gpio_level(FAN_PIN_INB, forward ? 0 : duty);
}

static void print_state(const char *why) {
    uint8_t st[FAN_STATE_LEN];
    fan_encode_state(&fan, st);
    printf("[fan] %s: power=%s speed=%u level=%u mode=%s dir=%s timer=%us\n", why, fan.power ? "on" : "off",
           st[2], st[3], fan.mode == FAN_MODE_NATURAL ? "natural" : "normal",
           fan.dir == FAN_DIR_FORWARD ? "forward" : "reverse", (unsigned)(st[6] | (st[7] << 8)));
}

static void state_changed(const char *why) {
    motor_apply();
    print_state(why);
    if (notify_enabled && con_handle != HCI_CON_HANDLE_INVALID) {
        att_server_request_can_send_now_event(con_handle);
    }
}

/* Board LED answers a button: n short blinks, or one long blink for "off". */
static struct {
    uint32_t start_ms;
    uint8_t count;
    uint16_t on_ms, off_ms;
} flash;

static void led_flash(uint32_t now, uint8_t count) {
    flash.start_ms = now;
    flash.count = count ? count : 1;
    flash.on_ms = count ? 150 : 600;
    flash.off_ms = 250;
}

typedef struct {
    uint pin;
    bool has_long; /* otherwise a long hold counts as a normal press */
    bool raw, pressed, long_fired;
    uint32_t raw_since, pressed_since;
} button_t;

typedef enum { BUTTON_NONE, BUTTON_PRESS, BUTTON_HOLD } button_event_t;

static button_t btn_level = {.pin = FAN_PIN_BTN_LEVEL, .has_long = true};
static button_t btn_natural = {.pin = FAN_PIN_BTN_NATURAL};
static button_t btn_timer = {.pin = FAN_PIN_BTN_TIMER};

static void button_init(const button_t *b) {
    gpio_init(b->pin);
    gpio_set_dir(b->pin, GPIO_IN);
    gpio_pull_up(b->pin); /* unwired buttons read released */
}

static button_event_t button_poll(button_t *b, uint32_t now) {
    bool raw = !gpio_get(b->pin);
    if (raw != b->raw) {
        b->raw = raw;
        b->raw_since = now;
    }
    if (raw != b->pressed && now - b->raw_since >= DEBOUNCE_MS) {
        b->pressed = raw;
        if (raw) {
            b->pressed_since = now;
            b->long_fired = false;
        } else if (!b->long_fired) {
            return BUTTON_PRESS;
        }
    }
    if (b->has_long && b->pressed && !b->long_fired && now - b->pressed_since >= LONG_PRESS_MS) {
        b->long_fired = true;
        return BUTTON_HOLD;
    }
    return BUTTON_NONE;
}

static void poll_buttons(uint32_t now) {
    switch (button_poll(&btn_level, now)) {
    case BUTTON_PRESS:
        fan_button_level(&fan);
        led_flash(now, fan_level(&fan));
        state_changed("button level");
        break;
    case BUTTON_HOLD:
        fan_button_off(&fan);
        led_flash(now, 0);
        state_changed("button off");
        break;
    default:
        break;
    }
    if (button_poll(&btn_natural, now) == BUTTON_PRESS) {
        fan_button_natural(&fan);
        led_flash(now, fan.mode == FAN_MODE_NATURAL ? 3 : 0);
        state_changed("button natural");
    }
    if (button_poll(&btn_timer, now) == BUTTON_PRESS && fan_button_timer(&fan)) {
        led_flash(now, fan.timer_step);
        state_changed("button timer");
    }
}

static void poll_usb(void) {
    static uint8_t line[FAN_TEXT_MAX];
    static size_t len;
    static bool overflow;
    int c;
    while ((c = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
        if (c == '\r' || c == '\n') {
            if (overflow) {
                printf("[fan] usb: line too long\n");
            } else if (len) {
                fan_result_t r = fan_command(&fan, line, len);
                if (r == FAN_OK) state_changed("usb");
                else printf("[fan] usb: %s\n", fan_result_name(r));
            }
            len = 0;
            overflow = false;
        } else if (len < sizeof(line)) {
            line[len++] = (uint8_t)c;
        } else {
            overflow = true;
        }
    }
}

static void led_update(uint32_t now) {
    static int led = -1;
    int want;
    uint32_t period = flash.on_ms + flash.off_ms;
    if (flash.count && now - flash.start_ms < flash.count * period) {
        want = (now - flash.start_ms) % period < flash.on_ms;
    } else {
        flash.count = 0;
        /* solid while a central is connected, short blink every second while advertising */
        want = con_handle != HCI_CON_HANDLE_INVALID ? 1 : (now % 1000u) < 100u;
    }
    if (want != led) {
        led = want;
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, want);
    }
}

static void tick_handler(btstack_timer_source_t *ts) {
    uint32_t now = to_ms_since_boot(get_absolute_time());
    watchdog_update();
    if (fan_tick(&fan, now - last_tick_ms)) state_changed("timer");
    last_tick_ms = now;
    poll_buttons(now);
    poll_usb();
    motor_apply(); /* kick, reversal pause and natural wind change the duty every tick */
    led_update(now);
    btstack_run_loop_set_timer(ts, TICK_MS);
    btstack_run_loop_add_timer(ts);
}

static void conn_param_handler(btstack_timer_source_t *ts) {
    UNUSED(ts);
    if (con_handle == HCI_CON_HANDLE_INVALID) return;
    int err = gap_request_connection_parameter_update(con_handle, CONN_INTERVAL_MIN, CONN_INTERVAL_MAX, 0,
                                                      CONN_SUPERVISION_TIMEOUT);
    printf("[fan] asked for %u ms supervision timeout (%d)\n", CONN_SUPERVISION_TIMEOUT * 10u, err);
}

static void packet_handler(uint8_t packet_type, uint16_t channel, uint8_t *packet, uint16_t size) {
    UNUSED(channel);
    UNUSED(size);
    if (packet_type != HCI_EVENT_PACKET) return;
    switch (hci_event_packet_get_type(packet)) {
    case BTSTACK_EVENT_STATE: {
        if (btstack_event_state_get_state(packet) != HCI_STATE_WORKING) return;
        bd_addr_t addr, null_addr;
        gap_local_bd_addr(addr);
        /* 10-24 Pi 5: ATLAS GATT Connect refused this fan on its public address before even calling BlueZ;
         * the iLink lamp it does accept uses a random static one. Advertise the board's own address with the
         * top two bits set (2C:CF:.. -> EC:CF:..), which stays the same across reboots. */
        gap_random_address_set(addr);
        addr[0] |= 0xc0;
        printf("[fan] BLE up as DESKMATE-FAN %s (random static)\n", bd_addr_to_str(addr));
        memset(null_addr, 0, sizeof(null_addr));
        gap_advertisements_set_params(160, 320, 0, 0, null_addr, 0x07, 0x00); /* 100-200 ms, connectable */
        gap_advertisements_set_data(sizeof(adv_data), (uint8_t *)adv_data);
        gap_scan_response_set_data(sizeof(scan_resp_data), (uint8_t *)scan_resp_data);
        gap_advertisements_enable(1);
        break;
    }
    case ATT_EVENT_CONNECTED:
        con_handle = att_event_connected_get_handle(packet);
        printf("[fan] connected\n");
        btstack_run_loop_remove_timer(&conn_param_timer);
        conn_param_timer.process = &conn_param_handler;
        btstack_run_loop_set_timer(&conn_param_timer, CONN_PARAM_DELAY_MS);
        btstack_run_loop_add_timer(&conn_param_timer);
        break;
    case HCI_EVENT_LE_META:
        if (hci_event_le_meta_get_subevent_code(packet) == HCI_SUBEVENT_LE_CONNECTION_UPDATE_COMPLETE) {
            printf("[fan] link params: status=0x%02x interval=%u x1.25ms supervision=%u ms\n",
                   hci_subevent_le_connection_update_complete_get_status(packet),
                   hci_subevent_le_connection_update_complete_get_conn_interval(packet),
                   hci_subevent_le_connection_update_complete_get_supervision_timeout(packet) * 10u);
        }
        break;
    case HCI_EVENT_DISCONNECTION_COMPLETE:
        /* 0x08 supervision timeout, 0x13 the central closed it, 0x3e the link never came up */
        printf("[fan] link lost, reason 0x%02x\n", hci_event_disconnection_complete_get_reason(packet));
        break;
    case ATT_EVENT_DISCONNECTED:
        btstack_run_loop_remove_timer(&conn_param_timer);
        con_handle = HCI_CON_HANDLE_INVALID;
        notify_enabled = false;
        printf("[fan] disconnected, advertising again\n");
        break;
    case ATT_EVENT_CAN_SEND_NOW: {
        uint8_t st[FAN_STATE_LEN];
        fan_encode_state(&fan, st);
        att_server_notify(con_handle, STATE_VALUE_HANDLE, st, sizeof(st));
        break;
    }
    default:
        break;
    }
}

static uint16_t att_read_callback(hci_con_handle_t connection_handle, uint16_t att_handle, uint16_t offset,
                                  uint8_t *buffer, uint16_t buffer_size) {
    UNUSED(connection_handle);
    if (att_handle != STATE_VALUE_HANDLE) return 0;
    uint8_t st[FAN_STATE_LEN];
    fan_encode_state(&fan, st);
    return att_read_callback_handle_blob(st, sizeof(st), offset, buffer, buffer_size);
}

static int att_write_callback(hci_con_handle_t connection_handle, uint16_t att_handle, uint16_t transaction_mode,
                              uint16_t offset, uint8_t *buffer, uint16_t buffer_size) {
    if (att_handle == STATE_CCC_HANDLE) {
        if (buffer_size < 2) return ATT_ERROR_INVALID_ATTRIBUTE_VALUE_LENGTH;
        notify_enabled = little_endian_read_16(buffer, 0) == GATT_CLIENT_CHARACTERISTICS_CONFIGURATION_NOTIFICATION;
        con_handle = connection_handle;
        if (notify_enabled) att_server_request_can_send_now_event(con_handle);
        return 0;
    }
    if (att_handle != COMMAND_VALUE_HANDLE) return 0;
    if (transaction_mode != ATT_TRANSACTION_MODE_NONE || offset != 0) return ATT_ERROR_REQUEST_NOT_SUPPORTED;
    fan_result_t r = fan_command(&fan, buffer, buffer_size);
    if (r != FAN_OK) {
        printf("[fan] ble: %s\n", fan_result_name(r));
        return ATT_ERROR_VALUE_NOT_ALLOWED;
    }
    state_changed("ble");
    return 0;
}

int main(void) {
    bi_decl(bi_2pins_with_names(FAN_PIN_INA, "L9110 INA", FAN_PIN_INB, "L9110 INB"));
    bi_decl(bi_3pins_with_names(FAN_PIN_BTN_LEVEL, "button level (optional)", FAN_PIN_BTN_NATURAL,
                                "button natural wind (optional)", FAN_PIN_BTN_TIMER, "button timer (optional)"));
    motor_init(); /* both inputs low before anything else: the fan never twitches at boot */
    button_init(&btn_level);
    button_init(&btn_natural);
    button_init(&btn_timer);
    stdio_init_all();
    fan_init(&fan);

    if (cyw43_arch_init()) {
        printf("[fan] cyw43 init failed\n");
        return -1;
    }
    if (watchdog_caused_reboot()) printf("[fan] restarted by watchdog\n");

    l2cap_init();
    sm_init();
    att_server_init(profile_data, att_read_callback, att_write_callback);
    hci_event_callback_registration.callback = &packet_handler;
    hci_add_event_handler(&hci_event_callback_registration);
    att_server_register_packet_handler(packet_handler);

    last_tick_ms = to_ms_since_boot(get_absolute_time());
    tick_timer.process = &tick_handler;
    btstack_run_loop_set_timer(&tick_timer, TICK_MS);
    btstack_run_loop_add_timer(&tick_timer);

    hci_power_control(HCI_POWER_ON);
    watchdog_enable(WATCHDOG_MS, true);
    btstack_run_loop_execute(); /* never returns */
    return 0;
}
