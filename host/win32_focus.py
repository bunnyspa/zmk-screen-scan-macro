"""Win32 window utilities shared by app/ and engine/ - neither is a more
"native" owner of any of these than the other, so they live at host/
root instead of under either, the same convention protocol.py already
establishes for code both app/ and engine/ need without one importing
from the other.

find_window_handle() also absorbs what used to be a duplicate
FindWindowW() lookup - engine/cursor.py had its own find_window(), unused
anywhere (confirmed via grep - dead code, removed), and
app/ui/overlays.py had find_window_handle() with an identical body,
moved here.

get_window_rect_by_hwnd()/get_extended_frame_bounds_by_hwnd() are the raw
GetWindowRect()/DWMWA_EXTENDED_FRAME_BOUNDS win32 calls, factored out
because engine/cursor.py's get_window_screen_origin()/
get_window_extended_frame_origin() (hwnd -> (x, y) origin only) and
app/ui/overlays.py's get_window_rect()/get_window_extended_frame_bounds()
(title -> (x, y, w, h) full rect) were each independently making the same
two win32 calls - down to both defining their own copy of
_DWMWA_EXTENDED_FRAME_BOUNDS = 9. Each of those four functions keeps its
own name, signature, and failure-handling behavior exactly as before
(cursor.py's assume an already-live hwnd and don't check for failure;
overlays.py's do, since the window could have closed between its own
FindWindowW() and the rect lookup) - only the actual ctypes calls
underneath are now shared, not the public API each caller already
depends on (including tests that monkeypatch these functions by name).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None
_dwmapi = ctypes.windll.dwmapi if sys.platform == "win32" else None

_SW_RESTORE = 9
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def find_window_handle(title):
    """The named window's HWND, or None if no such window is currently
    open."""
    return _user32.FindWindowW(None, title) or None


def get_window_rect_by_hwnd(hwnd):
    """(left, top, right, bottom) via GetWindowRect(), or None if the
    call fails (e.g. a stale/closed hwnd)."""
    rect = ctypes.wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def get_extended_frame_bounds_by_hwnd(hwnd):
    """(left, top, right, bottom) via DWMWA_EXTENDED_FRAME_BOUNDS
    (excludes the invisible resize-border margin GetWindowRect()
    includes - see get_window_rect_by_hwnd()), or None if DWM
    composition is unavailable or the call otherwise fails."""
    rect = ctypes.wintypes.RECT()
    hresult = _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect),
    )
    if hresult != 0:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def is_window_focused(hwnd) -> bool:
    return _user32.GetForegroundWindow() == hwnd


def focus_window(hwnd) -> None:
    if _user32.IsIconic(hwnd):
        # SW_RESTORE un-minimizes - but applied to an already-maximized,
        # already-visible window, it also un-maximizes it back to its prior
        # size/position, which would silently invalidate every click_rect/
        # region authored against the maximized layout. Only call it when
        # the window is actually minimized.
        _user32.ShowWindow(hwnd, _SW_RESTORE)

    foreground_hwnd = _user32.GetForegroundWindow()
    if foreground_hwnd == hwnd:
        return

    # Windows refuses SetForegroundWindow() from a process that isn't
    # itself currently the foreground app (a long-standing anti-focus-
    # stealing restriction) - it silently no-ops or just flashes the
    # taskbar icon instead, confirmed against real hardware to otherwise
    # leave this retrying forever. Temporarily attaching this thread's
    # input state to whatever currently owns the foreground relaxes that
    # restriction for the duration of the call.
    current_thread_id = _kernel32.GetCurrentThreadId()
    foreground_thread_id = (
        _user32.GetWindowThreadProcessId(foreground_hwnd, None) if foreground_hwnd else 0
    )

    attached = False
    if foreground_thread_id and foreground_thread_id != current_thread_id:
        attached = bool(_user32.AttachThreadInput(current_thread_id, foreground_thread_id, True))

    try:
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(current_thread_id, foreground_thread_id, False)
