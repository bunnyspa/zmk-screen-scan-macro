"""Enumerates visible top-level window titles, so the target-window field
can offer a dropdown instead of requiring the exact title typed by hand."""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32

# engine/ is a sibling of app/ under host/ - see main_window.py's own
# comment on this for why.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.window_resolve import get_window_executable, list_top_level_windows  # noqa: E402

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def list_visible_windows() -> list[str]:
    """Titles of currently visible top-level windows with a non-empty
    title, deduplicated, alphabetically sorted."""
    titles = set()

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.strip():
            titles.add(buf.value)
        return True

    user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return sorted(titles)


def list_running_executables() -> list[str]:
    """Basenames (e.g. 'notepad++.exe') of executables currently owning at
    least one visible top-level window, deduplicated, alphabetically
    sorted - for the target-executable field's dropdown."""
    executables = set()
    for hwnd, _title in list_top_level_windows():
        exe = get_window_executable(hwnd)
        if exe:
            executables.add(exe)
    return sorted(executables)
