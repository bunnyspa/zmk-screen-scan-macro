"""Minimal test receiver for the &ssm_tog trigger channel (marker 0x4E).

The packet itself carries no meaningful state - firmware just notifies "the
toggle was pressed" on every press. The host is the only thing that owns a
running/stopped boolean, flipped once per event received here. Not the real
receiver - just enough to prove &ssm_tog is transmitting and that this side
owns the state.

Usage: python tog_listener.py (Ctrl+C to stop)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_USAGE_PAGE = 0xFF60
_USAGE_ID = 0x61
_TOG_MARKER = 0x4E


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


def main() -> int:
    hid = _load_hid()
    dev = find_device(hid)
    if dev is None:
        print("No Raw HID device found (usage_page=0xFF60, usage=0x61). "
              "Is the keyboard connected and CONFIG_RAW_HID enabled?")
        return 1

    running = False
    print("Listening for &ssm_tog packets (marker 0x4E)... press Y in ADJUST. Ctrl+C to stop.")
    try:
        while True:
            data = dev.read(32, timeout=1000)
            if not data:
                continue
            if data[0] != _TOG_MARKER:
                print(f"(ignored packet, marker=0x{data[0]:02X})")
                continue
            running = not running
            print(f"ssm_tog received -> host state now: {'running' if running else 'stopped'}")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        dev.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
