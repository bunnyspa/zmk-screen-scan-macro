# zmk-screen-scan-macro

> **⚠️ In development.** The companion desktop app this module depends on
> to be useful is not yet available.

A [ZMK](https://zmk.dev) module: this firmware alone just exposes two
behaviors (`&ssm_pp` / `&ssm_stop`) and a Raw HID listener. The actual
macro logic - capturing a target window, walking a graph of
Action/Decision/Wait nodes, sending commands over Raw HID - lives in a
companion desktop app that's currently in development and not yet
available. The firmware emits the actual keyboard/mouse HID reports once
commanded — real hardware input, not OS-level injection.

- Action / Decision / Wait node graph, with cycles allowed (retry/re-check loops)
- Decision nodes match a masked reference image against the live captured window
- Per-node breakpoints — pause before a flagged click/key-press/decision, resume when ready
- `&ssm_pp` / `&ssm_stop` physical keys mirror the desktop app's own
  Play/Pause/Resume and Stop controls

## Getting Started

### `config/west.yml`

```yaml
manifest:
  remotes:
    - name: zmkfirmware
      url-base: https://github.com/zmkfirmware
    # --- copy from here ---
    - name: zzeneg
      url-base: https://github.com/zzeneg
    - name: bunnyspa
      url-base: https://github.com/bunnyspa
    # --- to here ---
  projects:
    - name: zmk
      remote: zmkfirmware
      revision: main
      import: app/west.yml
    # --- copy from here ---
    - name: zmk-raw-hid
      remote: zzeneg
      revision: main
    - name: zmk-screen-scan-macro
      remote: bunnyspa
      revision: main
    # --- to here ---
  self:
    path: config
```

### `<keyboard>.conf`

```ini
CONFIG_RAW_HID=y
CONFIG_ZMK_SCREEN_SCAN_MACRO=y
```

On split keyboards, enable these only on the central half (Raw HID lives on central).

### `<keyboard>.keymap` or `<keyboard>.overlay`

```c
behaviors {
    ssm_pp: screen_scan_macro_play_pause {
        compatible = "zmk,behavior-ssm-pp";
        #binding-cells = <0>;
    };

    ssm_stop: screen_scan_macro_stop {
        compatible = "zmk,behavior-ssm-stop";
        #binding-cells = <0>;
    };
};
```

Bind `&ssm_pp` and `&ssm_stop` to keys of your choice in your keymap layers.

### Host app

Not available yet - in development. Without it, the firmware compiles in
and the physical keys broadcast their triggers, but nothing is listening
on the other end — no commands are ever sent back.

## Parameters

**`CONFIG_ZMK_SCREEN_SCAN_MACRO_CMD_MARKER`** *(hex, default `0x4D`)* — Raw
HID marker byte, host → keyboard action-command channel. Change only to
resolve a collision with another Raw HID listener sharing the transport;
the host app's marker constant must be updated to match by hand.

**`CONFIG_ZMK_SCREEN_SCAN_MACRO_TRIGGER_MARKER`** *(hex, default `0x4E`)* —
Raw HID marker byte, keyboard → host trigger channel (`&ssm_pp` /
`&ssm_stop`). Same caveat as above.

## Using the desktop app

Describes the app's intended usage once it's available - not usable yet,
see "Host app" above.

1. Create a profile and set its target window title.
2. Build a graph — Action (click or key press), Decision (match a masked
   reference image, branch or wait-until-true), Wait (fixed delay) — wired
   together; cycles are allowed for retry/re-check loops.
3. Flag a node as a breakpoint (per node) to pause and preview it before it
   fires - Resume (in-app, or `&ssm_pp` while paused) continues past it.
4. Hit Run, or press `&ssm_pp` on the keyboard when nothing's running -
   same control either way. `&ssm_pp` also doubles as Pause/Resume while a
   run is active; `&ssm_stop` always ends the run outright.

Focus policy (per profile) controls what happens if the target window loses
focus mid-run: pause until refocused, or grab focus and resume automatically.

## Notes

See `docs/wire-protocol.md` for the full Raw HID packet layout.
