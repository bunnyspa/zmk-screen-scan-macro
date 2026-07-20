# zmk-screen-scan-macro

**Current state: Phase 1 firmware (confirmed working on hardware) + Phase 2
execution engine (unit-tested, not yet run against real capture/hardware).**
Not the full system yet — the graphical editor (Phase 3, porting VisionGraph's
UI) is still to come. See the design plan for the full picture.

### Firmware (Phase 1)

- `zephyr/`, `Kconfig`, `CMakeLists.txt`, `src/screen_scan_macro.c` — a
  Zephyr module at repo root (matching this ecosystem's other Raw HID
  modules). Listens for host action commands (marker `0x4D`) and emits real
  keyboard/mouse HID reports; also provides the `&ssm_tog` behavior, which
  notifies the host of a physical toggle press (marker `0x4E`, stateless —
  host owns the running/stopped boolean, not firmware).
- `dts/bindings/behaviors/zmk,behavior-ssm-tog.yaml` — devicetree binding for
  `&ssm_tog` (zero-argument behavior).
- `docs/wire-protocol.md` — full packet layout for both channels.

### Host (Phase 1 leftovers + Phase 2 engine)

- `host/protocol.py` — command-channel packet encode/decode, shared by
  everything below.
- `host/send_once.py`, `demo_loop.py`, `tog_listener.py` — Phase 0/1 manual
  test scripts, not the real app.
- `engine/` — the execution engine: `runner.py` (`MacroRunner`, walks a
  hand-authored graph — see `tests/fixtures/example_graph.json` for the
  schema), `matcher.py` (masked template matching), `cursor.py`
  (click-targeting via relative cursor jog), `command.py` (`Command` +
  real/recording sinks), `window_capture.py` (ported from VisionGraph). No
  graphical editor yet — a graph is a plain JSON file for now; Phase 3 wires
  the real editor's output into this schema.
- `tests/` — unit tests for the engine (`pytest`, no hardware needed):
  `test_matcher.py`, `test_cursor.py`, `test_runner.py`.

## Verification

**Firmware**: no standalone test harness, same as the other Raw HID modules
in this ecosystem — hardware-in-the-loop only:

1. Point a `west.yml` manifest (e.g. `zmk-config`'s) at this repo, with
   `CONFIG_RAW_HID=y` and `CONFIG_ZMK_SCREEN_SCAN_MACRO=y` set on the central
   half's `.conf`, and a `zmk,behavior-ssm-tog` devicetree node present in the
   keymap (required — the module `#error`s at compile time without one).
2. Build and flash the central half.
3. Exercise the command channel: send a 32-byte packet per
   `docs/wire-protocol.md` (marker `0x4D`) and confirm the corresponding real
   HID output (keystroke/mouse move/click). **Confirmed working.**
4. Exercise the trigger channel: bind `&ssm_tog` to a key, press it, confirm
   the host receives a marker-`0x4E` packet (stateless — host owns its own
   running/stopped boolean, flipped once per event received). **Confirmed
   working.**

**Engine**: `pip install -r engine/requirements.txt` then `pytest` from the
repo root — no hardware needed, everything's mocked (`FakeCapture`,
`RecordingCommandSink`, injectable cursor-position getters). Not yet run
end-to-end against a real captured window + real firmware — that manual
smoke test (per the design plan) is still outstanding.

## Marker bytes

`0x4D` (host→keyboard command), `0x4E` (keyboard→host `&ssm_tog` trigger) —
both distinct from `zmk-korean-ime-layer`'s `0xD5`. The retired Phase-0 spike
used `0xA0`; not in use anymore.
