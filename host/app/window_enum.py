"""Enumerates visible top-level window titles, so the target-window field
can offer a dropdown instead of requiring the exact title typed by hand."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

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
