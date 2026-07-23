# zmk-screen-scan-macro

A [ZMK](https://zmk.dev) module + Windows desktop app, bundled together: the
desktop app captures a target window and walks a graph of Action/Decision/Wait
nodes you build, sending commands to the firmware over Raw HID; the firmware
emits the actual keyboard/mouse HID reports — real hardware input, not
OS-level injection.

- Action / Decision / Wait node graph, with cycles allowed (retry/re-check loops)
- Decision nodes match a masked reference image against the live captured window
- Confirmation mode — pause before each click/key-press for a manual OK
- `&ssm_tog` / `&ssm_confirm` physical keys mirror the desktop app's Run/Stop
  and Confirm controls

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
    ssm_tog: screen_scan_macro_toggle {
        compatible = "zmk,behavior-ssm-tog";
        #binding-cells = <0>;
    };

    ssm_confirm: screen_scan_macro_confirm {
        compatible = "zmk,behavior-ssm-confirm";
        #binding-cells = <0>;
    };
};
```

Bind `&ssm_tog` and `&ssm_confirm` to keys of your choice in your keymap layers.

### Host app

```bash
pip install -r host/requirements.txt
python host/main.py
```

Without it, the firmware compiles in and the physical keys broadcast their
triggers, but nothing is listening on the other end — no commands are ever
sent back.

## Parameters

**`CONFIG_ZMK_SCREEN_SCAN_MACRO_CMD_MARKER`** *(hex, default `0x4D`)* — Raw
HID marker byte, host → keyboard action-command channel. Change only to
resolve a collision with another Raw HID listener sharing the transport;
the host app's marker constant must be updated to match by hand.

**`CONFIG_ZMK_SCREEN_SCAN_MACRO_TRIGGER_MARKER`** *(hex, default `0x4E`)* —
Raw HID marker byte, keyboard → host trigger channel (`&ssm_tog` /
`&ssm_confirm`). Same caveat as above.

## Using the desktop app

1. Create a profile and set its target window title.
2. Build a graph — Action (click or key press), Decision (match a masked
   reference image, branch or wait-until-true), Wait (fixed delay) — wired
   together; cycles are allowed for retry/re-check loops.
3. Turn on Confirmation mode (per profile) to pause before every
   click/key-press until you hit OK or press `&ssm_confirm`.
4. Hit Run, or press `&ssm_tog` on the keyboard — same control either way.

Focus policy (per profile) controls what happens if the target window loses
focus mid-run: pause until refocused, or grab focus and resume automatically.

## Notes

See `docs/wire-protocol.md` for the full Raw HID packet layout,
`docs/design-decisions.md` for design rationale, and `docs/dev-phases.md`
for build/verification history.
