import ctypes
import ctypes.wintypes

from PyQt5 import QtCore, QtGui, QtWidgets


_DWMWA_EXTENDED_FRAME_BOUNDS = 9


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


class RegionHighlightOverlay(QtWidgets.QWidget):
    """A translucent, always-on-top, click-through overlay that briefly
    highlights a saved click region directly on screen, so its position
    can be visually confirmed against the live target. Unlike
    ClickRegionOverlay, this is purely a display - it takes no mouse
    input (WindowTransparentForInput lets clicks pass through to the target
    underneath) and closes itself automatically after `duration_ms`."""

    def __init__(self, screen_rect, duration_ms=1500):
        super(RegionHighlightOverlay, self).__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowTransparentForInput | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setGeometry(*screen_rect)
        QtCore.QTimer.singleShot(duration_ms, self.close)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setBrush(QtGui.QColor(0, 255, 0, 70))
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0, 230), 3))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)


class ReferencePreviewOverlay(QtWidgets.QWidget):
    """A translucent, always-on-top, click-through overlay that briefly
    displays a Decision node's reference image directly over the target
    window, so it can be visually compared against the live target
    underneath. Its size is just the image's own size (already cropped to
    content by process_masked_reference); it's positioned at the node's
    stored region_x/y (see DecisionNode.get_region()). Closes itself
    automatically after `duration_ms`."""

    def __init__(self, screen_pos, pixmap, duration_ms=3000):
        super(ReferencePreviewOverlay, self).__init__()
        self._pixmap = pixmap
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowTransparentForInput | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        x, y = screen_pos
        self.setGeometry(x, y, pixmap.width(), pixmap.height())
        QtCore.QTimer.singleShot(duration_ms, self.close)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setOpacity(0.85)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.setOpacity(1.0)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0, 230), 2))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
