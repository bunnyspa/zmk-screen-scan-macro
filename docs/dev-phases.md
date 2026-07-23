# Dev phases & verification status

Development history and what's actually been confirmed vs. still assumed.
Moved out of `README.md` (now user-facing) - this is the internal build log.
See the design plan for the original phase-by-phase rationale.

## Phase 0 — spike (confirmed on real hardware, code removed)

Smallest possible firmware (`ssm_spike.c`, marker `0xA0`) + a one-shot
`send_once.py` script, built only to confirm `zmk_hid_keyboard_press()` /
`zmk_endpoints_send_report()` are safely callable from a Raw HID listener
callback (`raw_hid_received_event`). Confirmed: a literal `'a'` keystroke
round-tripped host → firmware → OS. The spike code has since been deleted
entirely, replaced by the real module below. `0xA0` isn't used by anything
currently in this repo.

## Phase 1 — firmware (confirmed working on hardware)

`zephyr/`, `Kconfig`, `CMakeLists.txt`, `src/screen_scan_macro.c` — the real
Zephyr module. Listens for host action commands (marker `0x4D`, Kconfig-
overridable) and emits real keyboard/mouse HID reports; also provides
`&ssm_tog` and `&ssm_confirm`, which notify the host of a physical trigger
key press (marker `0x4E`, also Kconfig-overridable, stateless — host owns
all running/stopped/pending-confirmation state, not firmware).

## Phase 2 — execution engine (unit-tested)

`host/engine/`: `runner.py` (`MacroRunner`, walks a graph), `matcher.py`
(masked template matching), `cursor.py` (click-targeting via relative
cursor jog, gain-adaptive, monitor-crossing aware), `command.py` (`Command`
+ real/recording sinks), `window_capture.py` (ported from VisionGraph).

## Phase 3 — graph editor (ported, wired to the engine)

`host/app/`: the graph editor, ported from VisionGraph (UI + data model
only there; wired to the engine here). Adds a per-profile target-window
field, focus policy, confirmation mode, a Run/Stop toolbar action, and
`hid_link.py` (the single open Raw HID connection shared by sending
commands and receiving `&ssm_tog`/`&ssm_confirm` triggers).

## Confirmation mode (built, engine-tested)

Per-profile setting: before every click/key-press, `MacroRunner` pauses -
moving the cursor into position and highlighting the region (click), or
showing what's about to be pressed (key) - until either the in-app OK
button or the physical `&ssm_confirm` key resolves it. See
`docs/wire-protocol.md` for the trigger-channel packet shape.

## Verification

**Firmware**: no standalone test harness, same as the other Raw HID modules
in this ecosystem — hardware-in-the-loop only.

- Command channel: send a 32-byte packet per `docs/wire-protocol.md` and
  confirm the corresponding real HID output (keystroke/mouse move/click).
  **Confirmed working.**
- `&ssm_tog` trigger: bind it to a key, press it, confirm the host receives
  a marker-`0x4E` packet. **Confirmed working.**
- `&ssm_confirm` trigger: same channel, different trigger-type byte.
  **Not yet exercised on real hardware** - only unit-tested on the host
  side so far.

**Engine**: `pip install -r host/requirements.txt` then `pytest` from
`host/` — no hardware needed, everything's mocked (`FakeCapture`,
`RecordingCommandSink`, injectable cursor-position getters, fake
`is_window_focused`). **37/37 passing**, including confirmation-mode's
wait-for-confirm/cursor-move-then-wait/stop-while-waiting cases. Not yet
run end-to-end against a real captured window + real firmware - that
manual smoke test is still outstanding.

**Editor + full app**: `python host/main.py` (or the offscreen construction
path used to verify it) successfully imports and constructs `MainWindow`,
including connecting to the real Raw HID device when one is present, and
including all confirmation-mode toolbar widgets. Two environment quirks
had to be worked around (both handled automatically in `main.py`, not
manual setup):

- `Qt.py` (NodeGraphQt's binding shim) can pick `PyQt6` over `PyQt5` if both
  are installed, which crashes NodeGraphQt outright (see
  `docs/design-decisions.md`) — `main.py` forces `QT_PREFERRED_BINDING=PyQt5`.
- NodeGraphQt 0.6.44 imports the stdlib `distutils` module directly, removed
  in Python 3.12+ — `main.py` imports `setuptools` first with
  `SETUPTOOLS_USE_DISTUTILS=local`, which provides a shim.

**Not yet done**: actually clicking around the running GUI interactively
(no display in this dev environment), building a real profile and hitting
Run against a real captured window, exercising `&ssm_tog`/`&ssm_confirm`
through the full app rather than standalone scripts, or seeing the
confirmation-mode highlight overlay render on a real screen. No
VisionGraph test suite (`test_action_node.py` etc.) has been ported either
- a real, acknowledged gap, not an oversight.
