"""The web UI's overlay-picking/preview logic: same three operations the
old NodeGraphQt desktop app had wired directly to a node's Qt widgets
(pick a click region by dragging over the live target window, briefly show
an Action node's saved click region, briefly show a Branch/Branch (Wait)
node's reference image at its saved region) - reworked into bridge-callable
methods that return a plain dict instead.

pick_click_region() is the one operation that needs a result back (the
picked x/y/w/h) rather than being fire-and-forget - it BLOCKS the calling
thread (a threading.Event, the same pattern engine/runner.py's own
_await_confirmation() already uses to block MacroRunner's background
thread) until the user finishes dragging or presses Escape. This is safe
specifically because pywebview dispatches js_api calls on their own
background thread, never the GUI thread (confirmed - see
run_controller.py's module docstring for the general rule this app
follows: never block the GUI thread waiting on anything) - blocking here
only delays that one JS call's Promise from resolving, exactly matching
the modal nature of dragging out a region.

A QObject (not a plain class) for its signals, same reason as
RunController: the overlay widgets themselves may only be constructed/
touched on the GUI thread, but every method here can be called from
pywebview's own background API thread."""
import os
import sys
import threading
from pathlib import Path

from PyQt5 import QtCore, QtGui

from .ui.overlays import (
    ClickRegionOverlay, RegionHighlightOverlay, StaticReferenceOverlay,
    get_window_extended_frame_bounds, get_window_rect,
)

# host/ is the parent of app/ - see engine/cursor.py's own comment on this
# same pattern for importing protocol.py. win32_focus.py is shared with
# engine/focus.py (MacroRunner's focus policy) - neither app/ nor engine/
# owns it, so it lives at host/ root rather than app/ reaching into
# engine/ (or vice versa) for it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from win32_focus import find_window_handle, focus_window  # noqa: E402


class PickController(QtCore.QObject):
    _open_picker_signal = QtCore.pyqtSignal(tuple, object)
    _show_click_region_signal = QtCore.pyqtSignal(tuple)
    _show_reference_signal = QtCore.pyqtSignal(tuple, str)

    def __init__(self):
        super().__init__()
        self._active_pick_overlay = None  # keep a reference so Qt doesn't GC it mid-pick
        self._active_highlight_overlay = None

        self._open_picker_signal.connect(self._open_picker_on_gui_thread)
        self._show_click_region_signal.connect(self._show_click_region_on_gui_thread)
        self._show_reference_signal.connect(self._show_reference_on_gui_thread)

    def pick_click_region(self, target_window_title):
        """Blocks until the user finishes dragging a region or cancels
        (Escape) - see this module's docstring for why that's safe here.
        Same threading.Event-based blocking-wait pattern as
        engine/runner.py's own _await_confirmation()."""
        window_rect, error = self._resolve_window_rect(target_window_title)
        if error:
            return {'ok': False, 'error': error}

        done = threading.Event()
        result = {}
        self._open_picker_signal.emit(window_rect, (done, result))
        done.wait()
        return result

    def _open_picker_on_gui_thread(self, window_rect, done_and_result):
        done, result = done_and_result

        def _finish(payload):
            result.update(payload)
            self._active_pick_overlay = None
            done.set()

        self._active_pick_overlay = ClickRegionOverlay(
            window_rect,
            on_picked=lambda x, y, w, h: _finish({'ok': True, 'x': x, 'y': y, 'w': w, 'h': h}),
            on_cancelled=lambda: _finish({'ok': False, 'cancelled': True}),
        )
        self._active_pick_overlay.show()

    def show_click_region(self, target_window_title, x, y, w, h):
        """Fire-and-forget (RegionHighlightOverlay auto-closes itself) -
        no result to wait for, so no blocking needed here."""
        window_rect, error = self._resolve_window_rect(target_window_title)
        if error:
            return {'ok': False, 'error': error}
        self._bring_target_window_forward(target_window_title)
        win_x, win_y, _win_w, _win_h = window_rect
        screen_rect = (win_x + x, win_y + y, max(w, 1), max(h, 1))
        self._show_click_region_signal.emit(screen_rect)
        return {'ok': True}

    def _show_click_region_on_gui_thread(self, screen_rect):
        self._active_highlight_overlay = RegionHighlightOverlay(screen_rect)
        self._active_highlight_overlay.show()

    def show_reference_region(self, target_window_title, abs_image_path, region_x, region_y):
        """Fire-and-forget, same as show_click_region() - StaticReferenceOverlay
        auto-closes itself. abs_image_path is resolved by the caller
        (webui/bridge.py knows the profile's images/ dir; this module
        doesn't need to)."""
        if not abs_image_path or not os.path.exists(abs_image_path):
            return {'ok': False, 'error': 'No reference image has been set for this node yet.'}
        window_rect, error = self._resolve_window_rect(target_window_title, extended_frame_bounds=True)
        if error:
            return {'ok': False, 'error': error}
        self._bring_target_window_forward(target_window_title)
        win_x, win_y, _win_w, _win_h = window_rect
        screen_pos = (win_x + region_x, win_y + region_y)
        self._show_reference_signal.emit(screen_pos, abs_image_path)
        return {'ok': True}

    def _show_reference_on_gui_thread(self, screen_pos, abs_image_path):
        pixmap = QtGui.QPixmap(abs_image_path)
        if pixmap.isNull():
            return
        self._active_highlight_overlay = StaticReferenceOverlay(screen_pos, pixmap)
        self._active_highlight_overlay.show()

    @staticmethod
    def _resolve_window_rect(target_window_title, extended_frame_bounds=False):
        """Returns (window_rect, None) or (None, error_message) - shared by
        every method above. No QMessageBox here (there's no dialog on this
        side of the bridge; the frontend renders the error itself, same
        convention as every other bridge method)."""
        if not target_window_title:
            return None, 'Set a target window title for this profile first.'
        get_rect = get_window_extended_frame_bounds if extended_frame_bounds else get_window_rect
        window_rect = get_rect(target_window_title)
        if window_rect is None:
            return None, (
                f"Could not find a window titled '{target_window_title}'. "
                'Make sure the target window is running and visible.'
            )
        return window_rect, None

    @staticmethod
    def _bring_target_window_forward(target_window_title):
        """Un-minimizes/raises the target window before a preview overlay
        (RegionHighlightOverlay/StaticReferenceOverlay) is shown over it -
        otherwise WindowStaysOnTopHint (see _PassiveOverlay) means the
        highlight paints on top of whatever window actually happens to be
        in front, which is confusing if that's not the target (e.g. this
        app's own window, or something else the user alt-tabbed to).
        Best-effort: if the window closed in the instant between
        _resolve_window_rect() succeeding and this call, silently skip -
        the overlay will still show, just without this improvement, not
        worth failing the whole preview over."""
        hwnd = find_window_handle(target_window_title)
        if hwnd:
            focus_window(hwnd)
