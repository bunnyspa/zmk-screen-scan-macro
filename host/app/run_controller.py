"""Run/Stop + confirmation-mode/decision-live-match overlay lifecycle,
originally extracted from the old NodeGraphQt desktop app's MainWindow's
_start_macro/_stop_macro/_show_pending_*/_show_decision_overlay* methods
(that desktop app is gone now - this is the only Run/Stop implementation).

Constructor dependencies (resolve_target_window/window_capture_factory/
macro_runner_factory/command_sink_factory) default to the real engine
objects but can be swapped for fakes in tests, the same dependency-injection
pattern engine/runner.py's own MacroRunner already uses for
is_window_focused/focus_window - lets host/tests/test_run_controller.py
exercise every branch of start()/stop()/confirm() without a real HID
device, target window, or screen capture.

A QObject (not a plain class) purely for its signals: MacroRunner's four
callbacks (show_pending_click/show_pending_key_press/show_decision_overlay/
hide_decision_overlay) fire on its own background thread, and constructing
or mutating a QWidget off the GUI thread is unsafe - Qt's automatic queued
connection (the same cross-thread marshalling host/app/hid_link.py's
HidLink already relies on) is what actually moves the overlay-widget work
onto the GUI thread here.

Deliberately does NOT push state to the web page via window.evaluate_js() -
found live, evaluate_js() called from a GUI-thread Qt slot can deadlock
(confirmed via a real freeze when window.events.closing tried it - see
main.py's docstring), and &ssm_tog/&ssm_confirm's handlers run on the
GUI thread the same way. Run/Stop/pending-status is exposed instead via
plain read-only properties (is_running, pending_status) that
webui/bridge.py's get_run_state() polls from the JS side on a timer - the
one direction (JS calling into Python) already proven safe throughout this
whole migration.
"""
import sys
from pathlib import Path

from PyQt5 import QtCore

from .ui.overlays import LiveReferenceOverlay, PendingKeyPressOverlay, RegionHighlightOverlay

# engine/ is a sibling of app/ under host/ - see main_window.py's own comment
# on this for why.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.command import HidCommandSink  # noqa: E402
from engine.runner import MacroRunner  # noqa: E402
from engine.window_capture import WindowCapture  # noqa: E402
from engine.window_resolve import resolve_target_window as _default_resolve_target_window  # noqa: E402

# RegionHighlightOverlay normally auto-closes after duration_ms - in
# confirmation mode it needs to stay up until the user actually confirms,
# not on a timer, so it's given a duration far longer than any real wait
# and closed explicitly once .confirm() fires instead (verbatim from
# main_window.py).
_CONFIRMATION_HIGHLIGHT_DURATION_MS = 3600_000


class RunController(QtCore.QObject):
    _pending_click_signal = QtCore.pyqtSignal(tuple)
    _pending_key_press_signal = QtCore.pyqtSignal(str, object)
    _decision_overlay_signal = QtCore.pyqtSignal(dict)
    _decision_overlay_hide_signal = QtCore.pyqtSignal()
    _clear_pending_signal = QtCore.pyqtSignal()

    def __init__(self, hid_link,
                 resolve_target_window=_default_resolve_target_window,
                 window_capture_factory=WindowCapture,
                 macro_runner_factory=MacroRunner,
                 command_sink_factory=HidCommandSink):
        super().__init__()
        self._hid_link = hid_link
        self._resolve_target_window = resolve_target_window
        self._window_capture_factory = window_capture_factory
        self._macro_runner_factory = macro_runner_factory
        self._command_sink_factory = command_sink_factory

        self.macro_runner = None
        self._capture = None
        self.pending_status = None
        self.last_error = None
        self._pending_confirmation_overlay = None
        self._decision_overlay = None
        self._decision_overlay_key = None

        self._pending_click_signal.connect(self._show_pending_click_on_gui_thread)
        self._pending_key_press_signal.connect(self._show_pending_key_press_on_gui_thread)
        self._decision_overlay_signal.connect(self._show_decision_overlay_on_gui_thread)
        self._decision_overlay_hide_signal.connect(self._hide_decision_overlay_on_gui_thread)
        self._clear_pending_signal.connect(self._clear_pending_indicator)

    @property
    def is_running(self):
        return self.macro_runner is not None

    def poll(self):
        """Called from webui/bridge.py's get_run_state() - i.e. on every
        JS polling tick (see run_controller.py's module docstring for why
        polling, not pushing). Reaps a MacroRunner whose background thread
        already finished on its own - a dead-end node (no outgoing
        connection) ending a run is normal, not an error (see
        engine/runner.py's module docstring) - without .stop() ever being
        called. Without this, is_running stayed True forever after such a
        run, confirmed via a real report of the web UI's Run button never
        reverting to "Run" after reaching a dead end."""
        if self.macro_runner is not None and not self.macro_runner.is_running():
            # Distinguishes a genuine failure (e.g. FocusTimeoutError - the
            # target window never came to foreground) from a dead end -
            # engine/runner.py's _run() only ever sets .error on the former.
            # Surfaced once via webui/bridge.py's get_run_state(), which
            # consumes it immediately after reading (see that method's
            # docstring) - without this, such a failure ended the run with
            # zero indication anywhere, confirmed via a real report of an
            # action that silently never fired.
            self.last_error = self.macro_runner.error
            self.macro_runner = None
            if self._capture is not None:
                self._capture.stop()
                self._capture = None
            self._clear_pending_signal.emit()
            self._decision_overlay_hide_signal.emit()

    def start(self, engine_graph, target_executable, target_window_title,
              profile_dir, focus_policy, confirmation_mode):
        """Returns {'ok': True} or {'ok': False, 'error': ...} - never
        raises, matching every other bridge-facing method's contract (see
        webui/bridge.py). Unlike main_window.py's _start_macro(), an
        ambiguous target (resolve_target_window's needs_confirmation=True)
        returns an error asking for a more specific target rather than
        showing an interactive window-choice dialog - this method can run
        on pywebview's own background API thread (called from
        bridge.run_macro()), not necessarily the GUI thread, and a
        synchronous native dialog needs the GUI thread."""
        if self._hid_link is None:
            return {'ok': False, 'error': 'No Raw HID device connected.'}
        if self.is_running:
            return {'ok': False, 'error': 'Already running.'}
        self.last_error = None  # clear whatever the previous run left behind
        if engine_graph is None:
            return {'ok': False, 'error': 'Set a start node first.'}
        if not target_executable:
            return {'ok': False, 'error': 'Set a target executable first.'}

        result = self._resolve_target_window(target_executable, target_window_title)
        if result.needs_confirmation:
            if result.candidates:
                return {
                    'ok': False,
                    'error': (
                        f"'{target_executable}' matched multiple windows - set a "
                        'more specific target window title to disambiguate.'
                    ),
                }
            return {'ok': False, 'error': f"No window found for executable '{target_executable}'."}

        self._capture = self._window_capture_factory(window_hwnd=result.hwnd)
        self._capture.start()

        sink = self._command_sink_factory(self._hid_link)
        self.macro_runner = self._macro_runner_factory(
            engine_graph, self._capture, sink, hwnd=result.hwnd, profile_dir=profile_dir,
            focus_policy=focus_policy, confirmation_mode=confirmation_mode,
            show_pending_click=self._show_pending_click,
            show_pending_key_press=self._show_pending_key_press,
            show_decision_overlay=self._show_decision_overlay,
            hide_decision_overlay=self._hide_decision_overlay,
        )
        self.macro_runner.start()
        return {'ok': True}

    def stop(self):
        """Callable from any thread (see this class's own callback methods
        below) - critically, also from pywebview's background API thread
        via webui/bridge.py's stop_macro(), NOT just the GUI thread (&ssm_tog's
        handler runs there). Confirmed via a real freeze: closing
        _pending_confirmation_overlay directly from here used to work by
        accident whenever stop() happened to be called from the GUI thread
        (&ssm_tog) and froze the whole app whenever it wasn't (the web UI's
        Stop button, while a confirmation-mode overlay was showing) - a
        QWidget can only safely be touched from the GUI thread. Emitting a
        signal is safe from any thread (Qt auto-queues it onto the
        receiver's own thread, the same marshalling MacroRunner's
        callbacks already rely on below); calling a QWidget method
        directly is not."""
        if self.macro_runner is not None:
            self.macro_runner.stop()
            self.macro_runner = None
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        self._clear_pending_signal.emit()
        self._decision_overlay_hide_signal.emit()
        return {'ok': True}

    def confirm(self):
        """Same cross-thread caveat as stop() above - callable from
        webui/bridge.py's confirm_macro() (background API thread) or
        &ssm_confirm's handler (GUI thread)."""
        if self.macro_runner is not None:
            self.macro_runner.confirm()
        self._clear_pending_signal.emit()
        return {'ok': True}

    def _clear_pending_indicator(self):
        """Only ever reached via _clear_pending_signal (connected in
        __init__) or a direct call from another method that's already
        running on the GUI thread (e.g. _show_pending_click_on_gui_thread
        below) - never called directly from stop()/confirm(), which can't
        assume they're on the GUI thread (see stop()'s docstring)."""
        self.pending_status = None
        if self._pending_confirmation_overlay is not None:
            self._pending_confirmation_overlay.close()
            self._pending_confirmation_overlay = None

    # -- MacroRunner callbacks (background thread) -------------------------

    def _show_pending_click(self, screen_rect):
        self._pending_click_signal.emit(screen_rect)

    def _show_pending_click_on_gui_thread(self, screen_rect):
        self._clear_pending_indicator()
        self.pending_status = 'Pending: click (confirm to proceed)'
        self._pending_confirmation_overlay = RegionHighlightOverlay(
            screen_rect, duration_ms=_CONFIRMATION_HIGHLIGHT_DURATION_MS,
        )
        self._pending_confirmation_overlay.show()

    def _show_pending_key_press(self, key_combo, screen_pos):
        self._pending_key_press_signal.emit(key_combo, screen_pos)

    def _show_pending_key_press_on_gui_thread(self, key_combo, screen_pos):
        self._clear_pending_indicator()
        self.pending_status = f'Pending: press "{key_combo}" (confirm to proceed)'
        if screen_pos is not None:
            self._pending_confirmation_overlay = PendingKeyPressOverlay(screen_pos, key_combo)
            self._pending_confirmation_overlay.show()

    def _show_decision_overlay(self, details):
        self._decision_overlay_signal.emit(details)

    def _show_decision_overlay_on_gui_thread(self, details):
        # A multi-image Decision node can show a different reference/region
        # from one poll to the next (whichever image is currently the best
        # match candidate - see engine/runner.py's _run_decision()) -
        # recreate the overlay whenever they change instead of reusing a
        # stale region/pixmap (verbatim from main_window.py).
        overlay_key = (details['screen_rect'], details['reference_path'])
        if self._decision_overlay is not None and overlay_key != self._decision_overlay_key:
            self._decision_overlay.close()
            self._decision_overlay = None

        if self._decision_overlay is None:
            self._decision_overlay = LiveReferenceOverlay(
                details['screen_rect'], details['reference_path'],
            )
            self._decision_overlay_key = overlay_key
            self._decision_overlay.show()
        self._decision_overlay.update_score(details['score'], details['threshold'])

    def _hide_decision_overlay(self):
        self._decision_overlay_hide_signal.emit()

    def _hide_decision_overlay_on_gui_thread(self):
        if self._decision_overlay is not None:
            self._decision_overlay.close()
            self._decision_overlay = None
            self._decision_overlay_key = None
