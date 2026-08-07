"""Focus tracking for MacroRunner.

Real HID input goes wherever the OS currently has keyboard/mouse focus -
unlike the read-only capture path (which can see a window's content even
while it's in the background), an action can only land on the *target*
window if that window is actually foreground when the action fires.

is_window_focused()/focus_window() themselves are pure win32 utility, not
MacroRunner-specific - they live in host/win32_focus.py (shared with
app/pick_controller.py) and are just re-exported here so this module's
existing consumers (runner.py's `from .focus import (..., focus_window,
is_window_focused)`) don't need to change.
"""
from __future__ import annotations

import sys
from pathlib import Path

# host/ is the parent of engine/ - see cursor.py's own comment on this
# same pattern for importing protocol.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from win32_focus import focus_window, is_window_focused  # noqa: E402,F401

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
