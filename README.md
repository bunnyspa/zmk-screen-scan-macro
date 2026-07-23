# zmk-screen-scan-macro

**Current state: Phase 1 firmware (confirmed working on hardware) + Phase 2
execution engine (unit-tested) + Phase 3 graph editor (ported, imports and
constructs successfully — not yet exercised interactively).** One whole
program: the editor, engine, and HID connection all run in a single process
(`host/main.py`) — see `docs/design-decisions.md`'s addendum for how they're
wired together.

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

### Host — one program (`host/main.py`)

- `host/protocol.py` — command-channel packet encode/decode, shared by
  everything below.
- `host/engine/` — the execution engine: `runner.py` (`MacroRunner`, walks a
  graph — hand-authored JSON for now, or translated live from the editor's
  NodeGraphQt session, see `main_window.py._build_engine_graph()`),
  `matcher.py` (masked template matching), `cursor.py` (click-targeting via
  relative cursor jog), `command.py` (`Command` + real/recording sinks),
  `window_capture.py` (ported from VisionGraph). Deliberately a sibling of
  `app/` under `host/`, not nested inside it — see `docs/design-decisions.md`
  for why that's still "one program."
- `host/app/` — the graph editor, ported from VisionGraph (UI + data model
  only there; here it's wired to the engine). `main_window.py` adds: a
  per-profile target-window field, a Run/Stop toolbar action, and
  `hid_link.py` (the single open Raw HID connection shared by sending
  commands and receiving `&ssm_tog` — pressing the physical key and clicking
  Run/Stop are two paths to the same toggle).
- `host/tests/` — unit tests for the engine (`pytest`, no hardware needed).

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

**Engine**: `pip install -r host/requirements.txt` then `pytest` from
`host/` — no hardware needed, everything's mocked (`FakeCapture`,
`RecordingCommandSink`, injectable cursor-position getters). **10/10 passing.**
Not yet run end-to-end against a real captured window + real firmware — that
manual smoke test is still outstanding.

**Editor + full app**: `python host/main.py` (or the offscreen construction
path used to verify it) **successfully imports and constructs `MainWindow`**,
including connecting to the real Raw HID device when one is present. Two
environment quirks had to be worked around (both handled automatically in
`main.py`, not manual setup):
- `Qt.py` (NodeGraphQt's binding shim) can pick `PyQt6` over `PyQt5` if both
  are installed, which crashes NodeGraphQt outright (the exact issue
  `docs/design-decisions.md` already documented from VisionGraph) —
  `main.py` forces `QT_PREFERRED_BINDING=PyQt5`.
- NodeGraphQt 0.6.44 imports the stdlib `distutils` module directly, removed
  in Python 3.12+ — `main.py` imports `setuptools` first with
  `SETUPTOOLS_USE_DISTUTILS=local`, which provides a shim.

**Not yet done**: actually clicking around the running GUI (no display in
this environment to verify interactively), building a real profile, hitting
Run against a real captured window, or exercising `&ssm_tog` through the full
app rather than the standalone `tog_listener.py` script. No VisionGraph test
suite (`test_action_node.py` etc.) has been ported either — a real,
acknowledged gap, not an oversight.

## Marker bytes

`0x4D` (host→keyboard command), `0x4E` (keyboard→host trigger channel). The
retired Phase-0 spike used `0xA0`; not in use anymore.
