# Phase 0 spike

Smallest possible proof-of-concept, not the real module (see the plan for the full
design): confirms `zmk_hid_keyboard_press()` / `zmk_endpoint_send_report()` are
safely callable from a Raw HID listener callback, on real hardware.

- `firmware/` — a minimal Zephyr module. Any Raw HID packet whose first byte is
  `0xA0` triggers a hardcoded 'A' key tap.
- `host/send_once.py` — sends one such packet and exits.

## No standalone test harness

Same as the other Raw HID modules in this ecosystem — verification is
hardware-in-the-loop only:

1. Point a `west.yml` manifest (e.g. `zmk-config`'s, temporarily) at this repo's
   `spike/firmware` path, with `CONFIG_RAW_HID=y` and
   `CONFIG_ZMK_SCREEN_SCAN_MACRO_SPIKE=y` set on the central half's `.conf`.
2. Build and flash the central half.
3. `pip install -r host/requirements.txt`
4. Fetch `hidapi.dll` (the `hid` package's native dependency, not bundled) into
   `host/`, next to `send_once.py` — same binary `korean-ime-reporter-windows`
   uses:
   ```
   curl -fL -o hidapi-win.zip https://github.com/libusb/hidapi/releases/download/hidapi-0.14.0/hidapi-win.zip
   unzip -j hidapi-win.zip x64/hidapi.dll -d host/
   ```
5. `python host/send_once.py`
6. Confirm a literal `a` appears in a focused text field.

Before merging any real module work, remove the spike's manifest entry/config —
it exists only to answer the Phase 0 question in the plan.
