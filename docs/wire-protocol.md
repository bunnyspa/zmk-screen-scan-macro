# Wire protocol

Extends this ecosystem's existing 32-byte Raw HID packet convention (see
`zmk-korean-ime-layer`): marker byte identifies which listener a packet is for,
rest is listener-specific payload, zero-padded to 32 bytes. Report ID `0x00` is
prepended by the host on Windows (hidapi requirement), not part of the 32-byte
payload itself.

Two independent one-way channels share the transport, distinguished by marker
byte — not a single bidirectional protocol:

## Host -> keyboard (action command)

Marker `0xA5`.

| Bytes | Field | Notes |
|---|---|---|
| 0 | Marker | `0xA5` |
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

## Keyboard -> host (`&ssm_tog` trigger)

Marker `0xA6`. Simple state broadcast, no version/sequence needed (not a
command stream where drops/duplicates matter, same shape as the Korean IME
channel).

| Bytes | Field | Notes |
|---|---|---|
| 0 | Marker | `0xA6` |
| 1 | Toggled state | `0x00` stopped, `0x01` running. Firmware flips this on each `&ssm_tog` press — it doesn't know the host's actual run state, just its own last-sent value. |
| 2-31 | Reserved | zero-padded |

## Breaking-change discipline

Changing either marker byte, the action-type enum, packet length, or field
layout requires updating both this repo's firmware and host sides in lockstep,
same as `zmk-korean-ime-layer` / `korean-ime-reporter-windows`.
