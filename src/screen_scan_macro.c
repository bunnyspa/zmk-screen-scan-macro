#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <zmk/event_manager.h>
#include <zmk/hid.h>
#include <zmk/endpoints.h>
#include <dt-bindings/zmk/hid_usage_pages.h>
#include <raw_hid/events.h>

#include <drivers/behavior.h>
#include <zmk/behavior.h>

LOG_MODULE_REGISTER(screen_scan_macro, CONFIG_ZMK_LOG_LEVEL);

#define DT_DRV_COMPAT zmk_behavior_ssm_tog

#if !DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)
#error "Add a zmk,behavior-ssm-tog node (see docs/wire-protocol.md) to enable CONFIG_ZMK_SCREEN_SCAN_MACRO."
#endif

/* ---- Host -> keyboard: action command listener (marker 0x4D) ---- */

#define SSM_CMD_MARKER 0x4D /* 'M' - Macro command */
#define SSM_CMD_VERSION 0x01
#define SSM_NUM_KEYCODE_SLOTS 6

enum ssm_action_type {
    SSM_ACTION_NOOP = 0x00,
    SSM_ACTION_KEY_PRESS = 0x01,
    SSM_ACTION_KEY_DOWN = 0x02,
    SSM_ACTION_KEY_UP = 0x03,
    SSM_ACTION_MOUSE_CLICK = 0x04,
    SSM_ACTION_MOUSE_MOVE = 0x05,
    SSM_ACTION_MOUSE_BUTTON_DOWN = 0x06,
    SSM_ACTION_MOUSE_BUTTON_UP = 0x07,
};

static uint8_t last_cmd_seq = 0xFF; /* sentinel: no packet processed yet */

static void apply_key_press_tap(uint8_t modifiers, const uint8_t *keycodes) {
    zmk_hid_register_mods(modifiers);
    for (int i = 0; i < SSM_NUM_KEYCODE_SLOTS; i++) {
        if (keycodes[i] != 0) {
            zmk_hid_keyboard_press(keycodes[i]);
        }
    }
    zmk_endpoints_send_report(HID_USAGE_KEY);

    for (int i = 0; i < SSM_NUM_KEYCODE_SLOTS; i++) {
        if (keycodes[i] != 0) {
            zmk_hid_keyboard_release(keycodes[i]);
        }
    }
    zmk_hid_unregister_mods(modifiers);
    zmk_endpoints_send_report(HID_USAGE_KEY);
}

static void apply_key_down(uint8_t modifiers, const uint8_t *keycodes) {
    zmk_hid_register_mods(modifiers);
    for (int i = 0; i < SSM_NUM_KEYCODE_SLOTS; i++) {
        if (keycodes[i] != 0) {
            zmk_hid_keyboard_press(keycodes[i]);
        }
    }
    zmk_endpoints_send_report(HID_USAGE_KEY);
}

static void apply_key_up(uint8_t modifiers, const uint8_t *keycodes) {
    for (int i = 0; i < SSM_NUM_KEYCODE_SLOTS; i++) {
        if (keycodes[i] != 0) {
            zmk_hid_keyboard_release(keycodes[i]);
        }
    }
    zmk_hid_unregister_mods(modifiers);
    zmk_endpoints_send_report(HID_USAGE_KEY);
}

static void apply_mouse_click(uint8_t buttons) {
    zmk_hid_mouse_buttons_press(buttons);
    zmk_endpoints_send_mouse_report();
    zmk_hid_mouse_buttons_release(buttons);
    zmk_endpoints_send_mouse_report();
}

static void apply_mouse_move(int16_t dx, int16_t dy) {
    zmk_hid_mouse_movement_set(dx, dy);
    zmk_endpoints_send_mouse_report();
    zmk_hid_mouse_movement_set(0, 0);
}

static void apply_mouse_button_down(uint8_t buttons) {
    zmk_hid_mouse_buttons_press(buttons);
    zmk_endpoints_send_mouse_report();
}

static void apply_mouse_button_up(uint8_t buttons) {
    zmk_hid_mouse_buttons_release(buttons);
    zmk_endpoints_send_mouse_report();
}

static void apply_command(const uint8_t *data, uint8_t length) {
    if (length < 16) {
        return;
    }

    uint8_t version = data[1];
    if (version != SSM_CMD_VERSION) {
        LOG_DBG("ssm: dropping packet with unrecognized version %d", version);
        return;
    }

    uint8_t seq = data[3];
    if (seq == last_cmd_seq) {
        LOG_DBG("ssm: dropping duplicate sequence %d", seq);
        return;
    }
    last_cmd_seq = seq;

    uint8_t action = data[2];
    uint8_t modifiers = data[4];
    const uint8_t *keycodes = &data[5];
    uint8_t mouse_buttons = data[11];
    int16_t dx = (int16_t)(data[12] | (data[13] << 8));
    int16_t dy = (int16_t)(data[14] | (data[15] << 8));

    switch (action) {
    case SSM_ACTION_KEY_PRESS:
        apply_key_press_tap(modifiers, keycodes);
        break;
    case SSM_ACTION_KEY_DOWN:
        apply_key_down(modifiers, keycodes);
        break;
    case SSM_ACTION_KEY_UP:
        apply_key_up(modifiers, keycodes);
        break;
    case SSM_ACTION_MOUSE_CLICK:
        apply_mouse_click(mouse_buttons);
        break;
    case SSM_ACTION_MOUSE_MOVE:
        apply_mouse_move(dx, dy);
        break;
    case SSM_ACTION_MOUSE_BUTTON_DOWN:
        apply_mouse_button_down(mouse_buttons);
        break;
    case SSM_ACTION_MOUSE_BUTTON_UP:
        apply_mouse_button_up(mouse_buttons);
        break;
    case SSM_ACTION_NOOP:
    default:
        break;
    }
}

static int screen_scan_macro_listener(const zmk_event_t *eh) {
    struct raw_hid_received_event *ev = as_raw_hid_received_event(eh);
    if (ev == NULL || ev->data == NULL || ev->length < 1) {
        return ZMK_EV_EVENT_BUBBLE;
    }
    if (ev->data[0] != SSM_CMD_MARKER) {
        return ZMK_EV_EVENT_BUBBLE;
    }

    apply_command(ev->data, ev->length);

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(screen_scan_macro, screen_scan_macro_listener);
ZMK_SUBSCRIPTION(screen_scan_macro, raw_hid_received_event);

/* ---- Keyboard -> host: &ssm_tog trigger behavior (marker 0x4E) ----
 * Stateless on purpose: firmware doesn't track running/stopped at all, it
 * just notifies the host that the physical toggle was pressed. The host is
 * the only thing that owns a running/stopped boolean, avoiding two
 * independent toggles that could ever drift out of sync (e.g. after a
 * firmware reboot or reconnect). */

#define SSM_TOG_MARKER 0x4E /* 'N' - Notify (host of the toggle) */
#define SSM_TOG_PACKET_SIZE 32

static int ssm_tog_pressed(struct zmk_behavior_binding *binding,
                            struct zmk_behavior_binding_event event) {
    uint8_t packet[SSM_TOG_PACKET_SIZE] = {0};
    packet[0] = SSM_TOG_MARKER;

    raise_raw_hid_sent_event(
        (struct raw_hid_sent_event){.data = packet, .length = sizeof(packet)});

    return ZMK_BEHAVIOR_OPAQUE;
}

static int ssm_tog_released(struct zmk_behavior_binding *binding,
                             struct zmk_behavior_binding_event event) {
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api ssm_tog_driver_api = {
    .binding_pressed = ssm_tog_pressed,
    .binding_released = ssm_tog_released,
};

BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,
                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &ssm_tog_driver_api);
