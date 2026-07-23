# Wire protocol

Extends this ecosystem's existing 32-byte Raw HID packet convention: marker
byte identifies which listener a packet is for, rest is listener-specific
payload, zero-padded to 32 bytes. Report ID `0x00` is prepended by the host on
Windows (hidapi requirement), not part of the 32-byte payload itself.

Two independent one-way channels share the transport, distinguished by marker
byte — not a single bidirectional protocol:

## Host -> keyboard (action command)

Marker `0x4D` by default - configurable via `CONFIG_ZMK_SCREEN_SCAN_MACRO_CMD_MARKER`
(firmware side only; changing it means updating `protocol.py`'s marker
constant to match by hand).

| Bytes | Field | Notes |
|---|---|---|
| 0 | Marker | `0x4D` |
| 1 | Protocol version | `0x01`. Firmware drops + debug-logs packets from an unrecognized version, never attempts to interpret them. |
| 2 | Action type | See below |
| 3 | Sequence number | Wraps at 256. A packet whose sequence number equals the last one processed is treated as a duplicate and skipped (retried write, not a new command). |
| 4 | HID modifier bitmask | ctrl/shift/alt/gui, passed to `zmk_hid_register_mods()`/`zmk_hid_unregister_mods()` |
| 5-10 | HID keycode slots (6) | `zmk_hid_keyboard_press(usage)` once per non-zero slot. Not tied to any ZMK boot-report struct — our own choice. |
| 11 | Mouse button bitmask | bit0=left, bit1=right, bit2=middle |
| 12-13 | Mouse delta X | signed int16, little-endian |
| 14-15 | Mouse delta Y | signed int16, little-endian |
| 16-31 | Reserved | zero-padded |

Action types (byte 2):

| Value | Action |
|---|---|
| `0x00` | No-op / heartbeat |
| `0x01` | Key press (tap: press all populated keycode slots + modifiers, send report, release, send report again) |
| `0x02` | Key down (hold: press, send report, stay down) |
| `0x03` | Key up (release exactly the keycodes/modifiers in this packet, send report) |
| `0x04` | Mouse click (press button(s), send report, release, send report again) |
| `0x05` | Mouse move (relative delta from bytes 12-15) |
| `0x06` | Mouse button down (hold) |
| `0x07` | Mouse button up (release) |

## Keyboard -> host (trigger channel)

Marker `0x4E` by default - configurable via `CONFIG_ZMK_SCREEN_SCAN_MACRO_TRIGGER_MARKER`
(firmware side only; changing it means updating `hid_link.py`'s marker
constant to match by hand). Stateless - firmware doesn't track any state at
all, it just fires this on every press of a trigger key, with byte 2
identifying which one. The host owns all the actual state (running/stopped,
pending-confirmation), flipping/resolving it once per event received. This
avoids independent firmware-side state ever drifting out of sync with the
host, e.g. after a firmware reboot or reconnect.

| Bytes | Field | Notes |
|---|---|---|
| 0 | Marker | `0x4E` |
| 1 | Protocol version | `0x01`. Host drops packets from an unrecognized version, never attempts to interpret them. |
| 2 | Trigger type | `0x00` = `&ssm_tog` (start/stop), `0x01` = `&ssm_confirm` (confirm a pending action) |
| 3-31 | Reserved | zero-padded, no meaningful payload |

## Breaking-change discipline

Changing either marker byte, the action-type enum, packet length, or field
layout requires updating both this repo's firmware and host sides in lockstep.
