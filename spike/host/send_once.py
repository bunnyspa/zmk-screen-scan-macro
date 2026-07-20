"""Phase 0 spike: send one fixed Raw HID packet to trigger ssm_spike.c's hardcoded
'A' key tap. Not the real sender (see plan Phase 1) - just enough to hardware-confirm
the firmware -> OS HID emission path works end to end.

Usage: python send_once.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_USAGE_PAGE = 0xFF60
_USAGE_ID = 0x61
_PACKET_SIZE = 32
_MARKER = 0xA0  # must match SSM_SPIKE_MARKER in ssm_spike.c


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

    # Report ID 0x00 prefix required by hidapi on Windows, then the 32-byte packet:
    # marker byte + zero padding.
    payload = bytes([0x00, _MARKER]) + bytes(_PACKET_SIZE - 1)
    dev.write(payload)
    dev.close()

    print(f"Sent trigger packet (marker=0x{_MARKER:02X}). "
          "Check a focused text field for a literal 'a'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
