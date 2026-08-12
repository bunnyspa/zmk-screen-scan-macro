"""Resolves a profile's target window by its owning process's executable
name (e.g. 'notepad++.exe'), not its title - a window's title can change
freely at any time (Notepad++ appending the open file's name, an unsaved-
changes marker, etc.), but the executable backing it doesn't change unless
the profile is genuinely pointed at a different program.

Executable-name matching alone can still be ambiguous - zero windows
currently open for it, or more than one open at once - in which case a
title substring (also stored on the profile, but only ever used as a
narrowing hint here, never the primary identifier) is used to narrow the
candidate list. The caller is expected to prompt the user to confirm
whenever resolve_target_window() reports needs_confirmation=True, even if
the title hint happens to narrow things down to exactly one candidate:
falling back at all means the trusted executable-name match failed to
resolve cleanly on its own, and silently trusting a heuristic guess would
mean real HID input could land on the wrong window with nobody noticing.
"""

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass, field

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def list_top_level_windows():
    """Returns [(hwnd, title), ...] for visible top-level windows with a
    non-empty title."""
    windows = []

    def _callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.strip():
            windows.append((hwnd, buf.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return windows


def get_window_executable(hwnd):
    """Basename of the .exe owning hwnd (e.g. 'notepad++.exe'), or None if
    it can't be determined (process exited, access denied, etc.)."""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value)
    finally:
        _kernel32.CloseHandle(handle)


@dataclass
class WindowCandidate:
    hwnd: int
    title: str
    executable: str


@dataclass
class ResolveResult:
    hwnd: int = None
    needs_confirmation: bool = False
    candidates: list = field(default_factory=list)


def resolve_target_window(target_executable, target_window_title='',
                          list_windows=list_top_level_windows,
                          get_executable=get_window_executable):
    """target_executable (e.g. 'notepad++.exe') is the primary identifier.
    target_window_title is only ever a narrowing hint, used solely when
    the executable match doesn't resolve to exactly one window on its own
    - never trusted as the primary match (see module docstring for why)."""
    windows = list_windows()
    exe_matches = [
        (hwnd, title) for hwnd, title in windows
        if (get_executable(hwnd) or '').lower() == (target_executable or '').lower()
    ]

    if len(exe_matches) == 1:
        hwnd, _title = exe_matches[0]
        return ResolveResult(hwnd=hwnd, needs_confirmation=False)

    pool = exe_matches if exe_matches else windows
    if target_window_title:
        narrowed = [
            (hwnd, title) for hwnd, title in pool
            if target_window_title.lower() in title.lower()
        ]
        if narrowed:
            pool = narrowed

    candidates = [
        WindowCandidate(hwnd, title, get_executable(hwnd) or '')
        for hwnd, title in pool
    ]
    return ResolveResult(candidates=candidates, needs_confirmation=True)
