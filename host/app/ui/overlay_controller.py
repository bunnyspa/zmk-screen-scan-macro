import os

from PyQt5 import QtGui, QtWidgets

from .overlays import (
    ClickRegionOverlay, RegionHighlightOverlay, StaticReferenceOverlay,
    get_window_extended_frame_bounds, get_window_rect,
)


class OverlayController:
    """Wired to nodes' 'Pick ...'/'Show ...' buttons (see
    MacroBaseNode.set_pick_handler). Launches a translucent overlay
    directly over the live target window, in one of two ways depending on
    `mode`:
      - 'click_region': drag out a new rectangle, write it into the
        requesting node's click_x/y/w/h properties (ActionNode).
      - 'show_region': briefly display whatever region the node already
        has - a plain highlighted rectangle from click_x/y/w/h for nodes
        that have one (ActionNode), or the node's own reference image
        positioned at its stored region_x/y/w/h for nodes that have one
        instead (DecisionNode, via get_reference_abs_path()/get_region() -
        that position/size came for free from process_masked_reference()
        cropping to the content's own bounding box within the originally
        uploaded screenshot, so no separate live matching pass is needed
        to locate it). Both node types share this one button/mode so
        "Show Region in Window" means the same thing everywhere; only what
        gets drawn differs.

    Replaces the earlier approach of dragging a region on a captured
    preview panel embedded in the app - there is no preview panel anymore."""

    def __init__(self, window_title_resolver):
        """window_title_resolver() -> the current profile's target window
        title (per-profile, unlike VisionGraph's fixed constant - a click
        region is meaningless against a different window's layout)."""
        self._window_title_resolver = window_title_resolver
        self._active_pick_overlay = None  # keep a reference so Qt doesn't GC it mid-pick
        self._active_highlight_overlay = None

    def request_pick(self, node, mode):
        if mode == 'click_region':
            self._pick_region(node)
        elif mode == 'show_region':
            self._show_region(node)

    def _get_window_rect_or_warn(self):
        window_title = self._window_title_resolver()
        if not window_title:
            QtWidgets.QMessageBox.warning(
                None, 'No Target Window',
                'Set a target window title for this profile first.',
            )
            return None
        window_rect = get_window_rect(window_title)
        if window_rect is None:
            QtWidgets.QMessageBox.warning(
                None, 'Target Window Not Found',
                f"Could not find a window titled '{window_title}'. "
                'Make sure the target window is running and visible.',
            )
        return window_rect

    def _pick_region(self, node):
        window_rect = self._get_window_rect_or_warn()
        if window_rect is None:
            return

        self._active_pick_overlay = ClickRegionOverlay(
            window_rect,
            on_picked=lambda x, y, w, h: self._on_picked(node, x, y, w, h),
            on_cancelled=self._clear_pick_overlay,
        )
        self._active_pick_overlay.show()

    def _on_picked(self, node, x, y, w, h):
        # click_x/y/w/h are QSpinBox-backed (see ActionNode) - setValue()
        # requires an actual int, unlike the old QLineEdit-backed fields.
        node.set_property('click_x', int(x))
        node.set_property('click_y', int(y))
        node.set_property('click_w', int(w))
        node.set_property('click_h', int(h))
        self._clear_pick_overlay()

    def _clear_pick_overlay(self):
        self._active_pick_overlay = None

    def _show_region(self, node):
        if hasattr(node, 'get_reference_abs_path'):
            self._show_reference_preview(node)
        else:
            self._show_click_region(node)

    def _show_click_region(self, node):
        window_rect = self._get_window_rect_or_warn()
        if window_rect is None:
            return

        x, y, w, h = node.get_region()
        win_x, win_y, _win_w, _win_h = window_rect
        screen_rect = (win_x + x, win_y + y, max(w, 1), max(h, 1))
        self._active_highlight_overlay = RegionHighlightOverlay(screen_rect)
        self._active_highlight_overlay.show()

    def _show_reference_preview(self, node):
        window_title = self._window_title_resolver()
        if not window_title:
            QtWidgets.QMessageBox.warning(
                None, 'No Target Window',
                'Set a target window title for this profile first.',
            )
            return

        # Unlike click_x/y (authored/consumed via get_window_rect()'s outer
        # frame throughout, see cursor.py), a Decision node's region_x/y is
        # measured within whatever image the user uploaded - confirmed
        # against real hardware to match DWM's extended frame bounds, not
        # get_window_rect()'s invisible-border-inclusive size. Using
        # get_window_rect() here was the actual bug behind a "Show Region"
        # preview looking shifted by ~7px.
        window_rect = get_window_extended_frame_bounds(window_title)
        if window_rect is None:
            QtWidgets.QMessageBox.warning(
                None, 'Target Window Not Found',
                f"Could not find a window titled '{window_title}'. "
                'Make sure the target window is running and visible.',
            )
            return

        abs_path = node.get_reference_abs_path()
        if not abs_path or not os.path.exists(abs_path):
            QtWidgets.QMessageBox.warning(
                None, 'Show Region', 'No reference image has been set for this node yet.',
            )
            return

        pixmap = QtGui.QPixmap(abs_path)
        if pixmap.isNull():
            QtWidgets.QMessageBox.warning(None, 'Show Region', 'Could not load the reference image.')
            return

        win_x, win_y, _win_w, _win_h = window_rect
        region_x, region_y, _region_w, _region_h = node.get_region()
        screen_pos = (win_x + region_x, win_y + region_y)
        self._active_highlight_overlay = StaticReferenceOverlay(screen_pos, pixmap)
        self._active_highlight_overlay.show()
