"""Live window capture, ported from VisionGraph's app/capture/window_capture.py.

Uses `windows-capture` (Windows Graphics Capture API), which reads frames
straight from the window's own compositor surface instead of grabbing desktop
pixels - this is what makes it work under occlusion.
"""

import threading

from windows_capture import WindowsCapture


class WindowCapture:
    """Runs a WindowsCapture session on a background thread and keeps
    the latest frame available for polling from any other thread."""

    def __init__(self, window_title):
        self.window_title = window_title
        self._latest_frame = None
        self._lock = threading.Lock()
        self._thread = None
        self._capture_control = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._capture_control is not None:
            self._capture_control.stop()
        self._thread = None
        self._capture_control = None
        with self._lock:
            self._latest_frame = None

    def is_running(self):
        return self._thread is not None

    def _run(self):
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_name=self.window_title,
        )

        @capture.event
        def on_frame_arrived(frame, capture_control):
            self._capture_control = capture_control
            with self._lock:
                # frame_buffer is (height, width, 4) uint8 BGRA
                self._latest_frame = frame.frame_buffer.copy()

        @capture.event
        def on_closed():
            with self._lock:
                self._latest_frame = None
            self._thread = None

        capture.start()  # blocks this thread until the session ends

    def get_latest_frame_bgr(self):
        """Returns the latest full-window frame as (H, W, 3) BGR, or None
        if no frame has arrived yet (e.g. window not found / not open)."""
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return None
        return frame[:, :, :3]  # drop alpha, channel order stays BGR
