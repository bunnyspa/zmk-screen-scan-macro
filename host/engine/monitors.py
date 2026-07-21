"""Real monitor geometry (EnumDisplayMonitors), used by cursor.py to
detect a cross-monitor click and cross to it deliberately - see
_cross_to_target_monitor()."""
from __future__ import annotations

import ctypes
import sys

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double,
)


def list_monitor_rects() -> list[tuple[int, int, int, int]]:
    """(left, top, right, bottom) for every connected monitor, in virtual
    desktop coordinates - the same space GetCursorPos()/relative HID
    moves operate in."""
    rects = []

    def _callback(_hmonitor, _hdc, rect_ptr, _data):
        r = rect_ptr.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return 1

    _user32.EnumDisplayMonitors(0, 0, _MONITORENUMPROC(_callback), 0)
    return rects


def find_containing_monitor(x: int, y: int, monitor_rects):
    """The (left, top, right, bottom) rect containing (x, y), or None if
    it isn't inside any connected monitor (shouldn't normally happen for
    a real cursor position, but a stuck-detection fallback shouldn't
    assume it can't)."""
    for rect in monitor_rects:
        left, top, right, bottom = rect
        if left <= x < right and top <= y < bottom:
            return rect
    return None
