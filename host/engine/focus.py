"""Focus tracking for MacroRunner.

Real HID input goes wherever the OS currently has keyboard/mouse focus -
unlike the read-only capture path (which can see a window's content even
while it's in the background), an action can only land on the *target*
window if that window is actually foreground when the action fires.
"""
from __future__ import annotations

import ctypes
import sys

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

_SW_RESTORE = 9

FOCUS_POLICY_FOCUS_AND_RESUME = "focus_and_resume"
FOCUS_POLICY_PAUSE_UNTIL_FOCUSED = "pause_until_focused"

FOCUS_POLICIES = (FOCUS_POLICY_FOCUS_AND_RESUME, FOCUS_POLICY_PAUSE_UNTIL_FOCUSED)

# How long MacroRunner._ensure_focus() will keep retrying before giving up
# and raising, rather than looping forever - confirmed against real
# hardware that when Windows keeps refusing to hand over the foreground,
# an unbounded retry loop looks exactly like the whole app freezing.
DEFAULT_MAX_FOCUS_WAIT_SECONDS = 10.0


class FocusTimeoutError(RuntimeError):
    """Raised when the target window can't be brought to (or confirmed
    at) the foreground within max_focus_wait_seconds. Surfaced instead of
    retrying forever, and instead of silently proceeding to act against a
    window that was never actually confirmed focused."""


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
