"""Click-targeting: turns a window-relative click rect into relative
mouse-move Command(s) + a click Command.

Real HID mice only report relative motion - there's no "move to absolute
pixel" over Raw HID. So the host reads the current OS cursor position
(GetCursorPos - a read-only query, not injection) plus the target window's
client-area screen offset, computes the delta to the rect's center, and
hands that off as a relative-move Command for the firmware to emit before
the click.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
import protocol as wire  # noqa: E402

from .command import Command  # noqa: E402

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def get_window_client_origin(hwnd) -> tuple[int, int]:
    """Screen-space coordinates of the window's client-area origin (0, 0)."""
    pt = wintypes.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def find_window(title: str):
    return _user32.FindWindowW(None, title)


def click_commands(
    hwnd,
    click_rect: tuple[int, int, int, int],
    mouse_button: int = wire.MOUSE_BUTTON_LEFT,
    get_cursor_pos=get_cursor_pos,
    get_window_client_origin=get_window_client_origin,
) -> list[Command]:
    """Window-relative click rect (x, y, w, h) -> [move Command, click Command]
    targeting the rect's center, computed as a delta from wherever the cursor
    currently is. get_cursor_pos/get_window_client_origin are injectable for
    testing without touching real win32 calls."""
    x, y, w, h = click_rect
    target_client_x = x + w // 2
    target_client_y = y + h // 2

    origin_x, origin_y = get_window_client_origin(hwnd)
    target_screen_x = origin_x + target_client_x
    target_screen_y = origin_y + target_client_y

    cur_x, cur_y = get_cursor_pos()
    dx = target_screen_x - cur_x
    dy = target_screen_y - cur_y

    return [
        Command(action=wire.ACTION_MOUSE_MOVE, dx=dx, dy=dy),
        Command(action=wire.ACTION_MOUSE_CLICK, mouse_buttons=mouse_button),
    ]
