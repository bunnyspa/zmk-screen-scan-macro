"""Focus tracking for MacroRunner.

Real HID input goes wherever the OS currently has keyboard/mouse focus -
unlike the read-only capture path (which can see a window's content even
while it's in the background), an action can only land on the *target*
window if that window is actually foreground when the action fires.

is_window_focused()/focus_window() themselves are pure win32 utility, not
MacroRunner-specific - they live in host/windows/win32_focus.py (a flat
sibling in this same windows/ directory, shared with pick_controller.py)
and are just re-exported here so this module's existing consumers
(runner.py's `from focus import (..., focus_window, is_window_focused)`)
don't need to change.
"""
from __future__ import annotations

from win32_focus import focus_window, is_window_focused  # noqa: F401

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
