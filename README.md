# zmk-screen-scan-macro

**Current state: Phase 1 firmware (host->keyboard relay + `&ssm_tog` trigger).**
Not the full system yet — see the design plan for the full picture (host-side
screen capture + decision-graph macro engine come in later phases). The
Phase 0 spike (confirming real HID emission works on real hardware) passed
and has been replaced by this real module.

- `zephyr/`, `Kconfig`, `CMakeLists.txt`, `src/screen_scan_macro.c` — a
  Zephyr module at repo root (matching this ecosystem's other Raw HID
  modules). Listens for host action commands (marker `0x4D`) and emits real
  keyboard/mouse HID reports; also provides the `&ssm_tog` behavior, which
  broadcasts a start/stop toggle back to the host (marker `0x4E`).
- `dts/bindings/behaviors/zmk,behavior-ssm-tog.yaml` — devicetree binding for
  `&ssm_tog` (zero-argument behavior).
- `docs/wire-protocol.md` — full packet layout for both channels.
- `host/send_once.py` — Phase 0 leftover, single fixed-packet sender. The
  real host sender/receiver (QThread-based, matching
  `korean-ime-reporter-windows`'s pattern) is still to be built.

## No standalone test harness

Same as the other Raw HID modules in this ecosystem — verification is
hardware-in-the-loop only:

1. Point a `west.yml` manifest (e.g. `zmk-config`'s) at this repo, with
   `CONFIG_RAW_HID=y` and `CONFIG_ZMK_SCREEN_SCAN_MACRO=y` set on the central
   half's `.conf`, and a `zmk,behavior-ssm-tog` devicetree node present in the
   keymap (required — the module `#error`s at compile time without one).
2. Build and flash the central half.
3. Exercise the command channel: send a 32-byte packet per
   `docs/wire-protocol.md` (marker `0x4D`) and confirm the corresponding real
   HID output (keystroke/mouse move/click).
4. Exercise the trigger channel: bind `&ssm_tog` to a key, press it, confirm
   the host receives a marker-`0x4E` packet with the toggled state byte.

## Marker bytes

`0x4D` (host→keyboard command), `0x4E` (keyboard→host `&ssm_tog` trigger) —
both distinct from `zmk-korean-ime-layer`'s `0xD5`. The retired Phase-0 spike
used `0xA0`; not in use anymore.
