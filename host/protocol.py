"""Command-channel packet encoding for zmk-screen-scan-macro.

Marker 0x4D, matching firmware/src/screen_scan_macro.c and docs/wire-protocol.md.
Not the full sender (QThread/reconnect/queue) yet - just the pure encode logic,
reusable from a one-shot script or the eventual real sender.
"""
from __future__ import annotations

MARKER = 0x4D
VERSION = 0x01
PACKET_SIZE = 32
NUM_KEYCODE_SLOTS = 6

ACTION_NOOP = 0x00
ACTION_KEY_PRESS = 0x01
ACTION_KEY_DOWN = 0x02
ACTION_KEY_UP = 0x03
ACTION_MOUSE_CLICK = 0x04
ACTION_MOUSE_MOVE = 0x05
ACTION_MOUSE_BUTTON_DOWN = 0x06
ACTION_MOUSE_BUTTON_UP = 0x07

MOUSE_BUTTON_LEFT = 0x01
MOUSE_BUTTON_RIGHT = 0x02
MOUSE_BUTTON_MIDDLE = 0x04

# HID keyboard usage IDs (subset) - zmk_hid_keyboard_press() takes these
# directly, not combined with a usage page.
HID_USAGE_KEY_KEYBOARD_A = 0x04


def keycode_for_letter(letter: str) -> int:
    """HID usage ID for a single lowercase a-z letter (sequential from 0x04)."""
    letter = letter.lower()
    if len(letter) != 1 or not "a" <= letter <= "z":
        raise ValueError(f"expected a single a-z letter, got {letter!r}")
    return HID_USAGE_KEY_KEYBOARD_A + (ord(letter) - ord("a"))


def encode_command(
    action: int,
    seq: int,
    modifiers: int = 0,
    keycodes: tuple[int, ...] = (),
    mouse_buttons: int = 0,
    dx: int = 0,
    dy: int = 0,
) -> bytes:
    """Build the 32-byte command payload (no leading report-ID byte)."""
    if len(keycodes) > NUM_KEYCODE_SLOTS:
        raise ValueError(f"at most {NUM_KEYCODE_SLOTS} keycode slots, got {len(keycodes)}")

    packet = bytearray(PACKET_SIZE)
    packet[0] = MARKER
    packet[1] = VERSION
    packet[2] = action
    packet[3] = seq & 0xFF
    packet[4] = modifiers
    for i, code in enumerate(keycodes):
        packet[5 + i] = code
    packet[11] = mouse_buttons
    packet[12:14] = int(dx).to_bytes(2, "little", signed=True)
    packet[14:16] = int(dy).to_bytes(2, "little", signed=True)
    # bytes 16-31 stay zero (reserved)
    return bytes(packet)
