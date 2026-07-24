import ctypes
import ctypes.wintypes

from PyQt5 import QtCore, QtGui, QtWidgets


_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def _overlay_label_font():
    """Shared font for every overlay's text label (PendingKeyPressOverlay,
    LiveReferenceOverlay's percentage) - built lazily, not at import time,
    since QFont needs a QApplication to already exist."""
    return QtGui.QFont('Segoe UI', 12, QtGui.QFont.Bold)


def get_window_rect(title):
    """Screen-coordinate (x, y, width, height) of the named window's outer
    frame, or None if no such window is currently open. Includes an
    invisible resize-border margin DWM adds around modern-themed windows
    on Windows 10/11 (confirmed ~7-8px per side on this machine) - this is
    the convention click_x/y/w/h are authored and consumed in throughout
    (ClickRegionOverlay picks against it, cursor.py's
    get_window_screen_origin() targets against it), so don't switch this
    one to extended frame bounds without also updating cursor.py - see
    get_window_extended_frame_bounds() for the other convention."""
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rect = ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def get_window_extended_frame_bounds(title):
    """Screen-coordinate (x, y, width, height) of the named window's
    visible bounds (DWMWA_EXTENDED_FRAME_BOUNDS - excludes the invisible
    resize-border margin get_window_rect() includes), or None if no such
    window is currently open or DWM composition is unavailable.

    This is the convention a Decision node's region_x/y/w/h are in -
    process_masked_reference() measures them within whatever image the
    user uploaded, and confirmed directly against real hardware:
    WindowsCapture (this app's own live-capture library, used for actual
    runtime matching) produces frames at exactly this size, not
    get_window_rect()'s outer-frame size. A screenshot tool's "capture
    this window" mode (e.g. Windows' Snipping Tool) also captures at this
    size, matching what a human visually sees as "the window" - not
    get_window_rect()'s invisible padding."""
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rect = ctypes.wintypes.RECT()
    hresult = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect),
    )
    if hresult != 0:
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


class ClickRegionOverlay(QtWidgets.QWidget):
    """A translucent, always-on-top, frameless overlay placed directly over
    the target window, so a click region can be picked on the live target
    itself rather than on a captured preview image. Drag out a rectangle;
    on release its bounds (relative to the target window, matching what
    get_window_rect() measured) are reported via on_picked(x, y, w, h), and
    the overlay closes itself. A plain click with no drag reports a 1x1
    region at that point. Esc cancels."""

    def __init__(self, window_rect, on_picked, on_cancelled=None):
        super(ClickRegionOverlay, self).__init__()
        self._on_picked = on_picked
        self._on_cancelled = on_cancelled
        self._origin = None
        self._current_rect = QtCore.QRect()

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setGeometry(*window_rect)

        hint = QtWidgets.QLabel('Drag to select a click region  •  Esc to cancel', self)
        hint.setStyleSheet(
            'background-color: rgba(0, 0, 0, 170); color: white;'
            'padding: 6px 10px; font-weight: bold;'
        )
        hint.adjustSize()
        hint.move(12, 12)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(255, 0, 255, 30))
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 0, 255, 200), 2))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        if not self._current_rect.isNull():
            painter.setBrush(QtGui.QColor(0, 255, 255, 60))
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 255, 255, 220), 2))
            painter.drawRect(self._current_rect)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._origin = event.pos()
            self._current_rect = QtCore.QRect(self._origin, QtCore.QSize())
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._current_rect = QtCore.QRect(self._origin, event.pos()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton or self._origin is None:
            return
        rect = QtCore.QRect(self._origin, event.pos()).normalized()
        self._origin = None
        self.close()
        # a plain click with no drag still reports a valid (1x1) region
        width = max(rect.width(), 1)
        height = max(rect.height(), 1)
        self._on_picked(rect.x(), rect.y(), width, height)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            if self._on_cancelled:
                self._on_cancelled()


class _PassiveOverlay(QtWidgets.QWidget):
    """Shared plumbing for every click-through, display-only overlay in
    this module (RegionHighlightOverlay, StaticReferenceOverlay,
    LiveReferenceOverlay): translucent, frameless, always-on-top,
    positioned at a screen rect, optionally auto-closing on a timer.

    ClickRegionOverlay is deliberately NOT built on this - it needs real
    mouse input to let the user drag out a region, the opposite of
    WindowTransparentForInput."""

    def __init__(self, screen_rect, duration_ms=None):
        super(_PassiveOverlay, self).__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowTransparentForInput | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setGeometry(*screen_rect)
        if duration_ms is not None:
            QtCore.QTimer.singleShot(duration_ms, self.close)


class RegionHighlightOverlay(_PassiveOverlay):
    """Briefly highlights a saved click region directly on screen, so its
    position can be visually confirmed against the live target."""

    def __init__(self, screen_rect, duration_ms=1500):
        super(RegionHighlightOverlay, self).__init__(screen_rect, duration_ms)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setBrush(QtGui.QColor(0, 255, 0, 70))
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0, 230), 3))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)


_PENDING_KEY_PRESS_SIZE = (220, 40)
_PENDING_KEY_PRESS_MARGIN = 12


class PendingKeyPressOverlay(_PassiveOverlay):
    """Shows the key/combo about to be pressed in confirmation mode. Unlike
    a click, a key-press action has no on-screen region to anchor a
    preview to, so this floats near the target window's top-left corner
    instead - visible without needing to look at the toolbar's
    pending_action_label, matching how click confirmation already gets a
    real on-screen overlay (RegionHighlightOverlay), not just toolbar
    text."""

    def __init__(self, screen_pos, key_combo):
        x, y = screen_pos
        w, h = _PENDING_KEY_PRESS_SIZE
        super(PendingKeyPressOverlay, self).__init__(
            (x + _PENDING_KEY_PRESS_MARGIN, y + _PENDING_KEY_PRESS_MARGIN, w, h),
            duration_ms=None,
        )
        self._key_combo = key_combo

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 190))
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0, 230), 2))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255)))
        painter.setFont(_overlay_label_font())
        painter.drawText(self.rect(), QtCore.Qt.AlignCenter, f'Press: {self._key_combo}')


class StaticReferenceOverlay(_PassiveOverlay):
    """Briefly displays a Decision node's reference image directly over the
    target window, so it can be visually compared against the live target
    underneath. Its size is just the image's own size (already cropped to
    content by process_masked_reference); it's positioned at the node's
    stored region_x/y (see DecisionNode.get_region())."""

    def __init__(self, screen_pos, pixmap, duration_ms=3000):
        x, y = screen_pos
        super(StaticReferenceOverlay, self).__init__(
            (x, y, pixmap.width(), pixmap.height()), duration_ms,
        )
        self._pixmap = pixmap

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setOpacity(0.85)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.setOpacity(1.0)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0, 230), 2))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)


_DECISION_OVERLAY_LABEL_HEIGHT = 20
# Enough to fit "100.0%" in the label font without clipping - a narrow
# Decision region (the real profile this feature was built against is
# 8px wide) would otherwise clip the label to the region's own width.
_DECISION_OVERLAY_MIN_LABEL_WIDTH = 60


class LiveReferenceOverlay(_PassiveOverlay):
    """Shows a Decision node's reference image at 50% opacity over its live
    region, plus a text label with the current match percentage - shown
    during Wait Until True polling (regardless of confirmation mode)
    and/or right before a decision resolves in confirmation mode (see
    engine/runner.py's _run_decision()).

    Unlike RegionHighlightOverlay/StaticReferenceOverlay, this persists
    across repeated update_score() calls (one per poll) instead of being
    recreated each time - and never auto-closes on a timer, since a Wait
    Until True poll can run indefinitely; the caller explicitly closes it
    once the decision resolves."""

    def __init__(self, screen_rect, reference_path):
        x, y, w, h = screen_rect
        # The label sits in a strip above the region itself, not overlapping
        # it - the region can be a handful of pixels tall (see the real
        # profile this feature was built against: 8x15), too small to fit
        # readable text inside. The overlay is also widened to at least
        # _DECISION_OVERLAY_MIN_LABEL_WIDTH for the same reason (that same
        # profile's region is only 8px wide) - expanded symmetrically so
        # the pixmap still lines up with the real region on screen, only
        # the label strip actually needs the extra width.
        overlay_width = max(w, _DECISION_OVERLAY_MIN_LABEL_WIDTH)
        left_pad = (overlay_width - w) // 2
        super(LiveReferenceOverlay, self).__init__(
            (x - left_pad, y - _DECISION_OVERLAY_LABEL_HEIGHT,
             overlay_width, h + _DECISION_OVERLAY_LABEL_HEIGHT),
            duration_ms=None,
        )
        self._pixmap = QtGui.QPixmap(reference_path)
        self._region_rect = QtCore.QRect(left_pad, _DECISION_OVERLAY_LABEL_HEIGHT, w, h)
        self._score = 0.0
        self._threshold = 1.0

    def update_score(self, score, threshold):
        self._score = score
        self._threshold = threshold
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        if not self._pixmap.isNull():
            painter.setOpacity(0.5)
            painter.drawPixmap(self._region_rect, self._pixmap)
            painter.setOpacity(1.0)

        matched = self._score >= self._threshold
        border_color = QtGui.QColor(0, 255, 0, 230) if matched else QtGui.QColor(255, 180, 0, 230)
        painter.setPen(QtGui.QPen(border_color, 2))
        painter.drawRect(self._region_rect.adjusted(0, 0, -1, -1))

        label_rect = QtCore.QRect(0, 0, self.width(), _DECISION_OVERLAY_LABEL_HEIGHT)
        painter.fillRect(label_rect, QtGui.QColor(0, 0, 0, 190))
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255)))
        painter.setFont(_overlay_label_font())
        painter.drawText(label_rect, QtCore.Qt.AlignCenter, f'{self._score * 100:.1f}%')
