#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <zmk/event_manager.h>
#include <zmk/hid.h>
#include <zmk/endpoints.h>
#include <dt-bindings/zmk/hid_usage.h>
#include <dt-bindings/zmk/hid_usage_pages.h>
#include <raw_hid/events.h>

LOG_MODULE_REGISTER(ssm_spike, CONFIG_ZMK_LOG_LEVEL);

#define SSM_SPIKE_MARKER 0xA0

static int ssm_spike_listener(const zmk_event_t *eh) {
    struct raw_hid_received_event *ev = as_raw_hid_received_event(eh);
    if (ev == NULL || ev->data == NULL || ev->length < 1) {
        return ZMK_EV_EVENT_BUBBLE;
    }
    if (ev->data[0] != SSM_SPIKE_MARKER) {
        return ZMK_EV_EVENT_BUBBLE;
    }

    LOG_INF("ssm_spike: trigger received, emitting hardcoded 'A' key tap");

    zmk_hid_keyboard_press(HID_USAGE_KEY_KEYBOARD_A);
    zmk_endpoints_send_report(HID_USAGE_KEY);

    zmk_hid_keyboard_release(HID_USAGE_KEY_KEYBOARD_A);
    zmk_endpoints_send_report(HID_USAGE_KEY);

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(ssm_spike, ssm_spike_listener);
ZMK_SUBSCRIPTION(ssm_spike, raw_hid_received_event);
