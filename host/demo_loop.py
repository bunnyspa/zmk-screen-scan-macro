"""Simple test loop for the command channel: press 'A', wait 1s, left-click,
wait 1s, repeat. Not the real engine - just a manual exercise of every step
in the host -> firmware -> real HID path using protocol.py's encoder.

Usage: python demo_loop.py (Ctrl+C to stop)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from protocol import (  # noqa: E402
    ACTION_KEY_PRESS,
    ACTION_MOUSE_CLICK,
    HID_USAGE_KEY_KEYBOARD_A,
    MOUSE_BUTTON_LEFT,
    encode_command,
)

_USAGE_PAGE = 0xFF60
_USAGE_ID = 0x61


def _load_hid():
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        if getattr(sys, "frozen", False):
            dll_dir = str(Path(sys.executable).parent)
        else:
            dll_dir = str(Path(__file__).parent)
        os.add_dll_directory(dll_dir)
    import hid
    return hid


def find_device(hid):
    for info in hid.enumerate():
        if info.get("usage_page") == _USAGE_PAGE and info.get("usage") == _USAGE_ID:
            return hid.Device(path=info["path"])
    return None


def send(dev, payload: bytes) -> None:
    # Report ID 0x00 prefix required by hidapi on Windows.
    dev.write(bytes([0x00]) + payload)


def main() -> int:
    hid = _load_hid()
    dev = find_device(hid)
    if dev is None:
        print("No Raw HID device found (usage_page=0xFF60, usage=0x61). "
              "Is the keyboard connected and CONFIG_RAW_HID enabled?")
        return 1

    seq = 0
    try:
        while True:
            seq = (seq + 1) % 256
            send(dev, encode_command(ACTION_KEY_PRESS, seq, keycodes=(HID_USAGE_KEY_KEYBOARD_A,)))
            print("sent: key A")
            time.sleep(1)

            seq = (seq + 1) % 256
            send(dev, encode_command(ACTION_MOUSE_CLICK, seq, mouse_buttons=MOUSE_BUTTON_LEFT))
            print("sent: mouse click")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        dev.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
