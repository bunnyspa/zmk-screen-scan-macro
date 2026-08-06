"""Entrypoint: pywebview's 'qt' GUI backend hosting the web UI
(host/webui/index.html + graph_editor.js).

Two real, non-obvious constraints shape this file - both found the hard
way, not designed in up front:

1. QtWebEngineWidgets (which pywebview's qt backend uses internally) MUST
   be imported before any QApplication/QCoreApplication instance exists -
   it needs to set Qt.AA_ShareOpenGLContexts first, a real Qt/Chromium
   requirement, not a pywebview quirk. This has to be the first Qt-related
   import in this file, before QtWidgets or webview.
2. window.evaluate_js() (pushing state from Python into the page) is
   dangerous when called from a Qt slot that runs on the GUI thread -
   confirmed via a real freeze when window.events.closing tried it
   (evaluate_js() needs Qt's event loop to process an async Chromium
   JS-result callback, but a GUI-thread slot calling it is already
   blocking that very loop waiting for it to return - a deadlock). Both
   window-closing (_confirm_close) and the physical &ssm_tog/&ssm_confirm
   keys (_on_ssm_tog, both queued onto the GUI thread by HidLink's Qt
   signals) avoid evaluate_js entirely: WebBridge.dirty/._pending_graph_document/
   ._pending_meta mirror whatever index.html last reported (pushed on
   every setDirty() call - see bridge.py) so these GUI-thread handlers
   never need to ask JS anything synchronously, and native QMessageBox
   dialogs stand in for what would otherwise be JS's window.confirm()/
   window.prompt(). Everything else that needs to report live state to
   the page (Run/Stop button, pending-confirmation status) is polled by
   index.html instead of pushed, for the same reason - see
   run_controller.py's module docstring.

The web Run button and the physical &ssm_tog key share one code path
(bridge.run_saved_profile() - always loads the profile fresh from disk,
never live/unsaved browser state) and one behavior (prompt to save first
if there are unsaved changes, do nothing if declined) - only the save-
prompt's popup mechanism differs, per constraint 2 above: a native
QMessageBox in _on_ssm_tog (GUI thread), index.html's own window.confirm()
for the web button (an ordinary JS-side call, not a GUI-thread Qt slot).

Run with: python host/main.py
"""
import logging
import os
import sys

# See constraint 1 above - must precede QtWidgets/webview.
from PyQt5 import QtWebEngineWidgets  # noqa: F401,E402

from PyQt5 import QtWidgets  # noqa: E402

import webview  # noqa: E402

# Without this, the root logger sits at its default WARNING level, so the
# per-node/per-action logger.info() calls in engine/runner.py (which node is
# being visited, when an action actually fires, focus wait/resume decisions)
# and engine/command.py (bytes actually written per HID report) are
# silently dropped - only ERROR-level failures would ever be seen.
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

from app.hid_link import HidLink, find_device  # noqa: E402
from app.profiles.profile_manager import ProfileError, ProfileManager  # noqa: E402
from app.pick_controller import PickController  # noqa: E402
from app.run_controller import RunController  # noqa: E402
from webui.bridge import WebBridge  # noqa: E402

PROFILES_ROOT = os.path.join(os.getcwd(), 'profiles')
INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webui', 'index.html')


def _confirm_close(bridge):
    """Checked from pywebview's window.events.closing - see constraint 2
    in this module's docstring for why it reads WebBridge.dirty directly
    and shows a native QMessageBox, instead of asking JS anything."""
    if not bridge.dirty:
        return True
    answer = QtWidgets.QMessageBox.question(
        None, 'Unsaved changes',
        'Discard unsaved changes and close?',
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No,
    )
    return answer == QtWidgets.QMessageBox.Yes


def _on_ssm_tog(bridge, run_controller, profile_manager):
    """&ssm_tog is stateless (see docs/wire-protocol.md) - flips whatever
    this app is currently doing. Runs on the GUI thread (HidLink's signal
    is queued there) - see constraint 2 in this module's docstring for why
    it reads the profile from disk (prompting to save first if dirty)
    instead of asking JS for its live state."""
    if run_controller.is_running:
        run_controller.stop()
        return

    profile_name = bridge._current_profile_name
    if not profile_name:
        QtWidgets.QMessageBox.information(None, 'Run', 'No profile is open.')
        return

    if bridge.dirty:
        answer = QtWidgets.QMessageBox.question(
            None, 'Unsaved changes',
            f"Save changes to '{profile_name}' before running?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Save,
        )
        if answer != QtWidgets.QMessageBox.Save:
            return
        meta = bridge._pending_meta or {}
        try:
            profile_manager.save(
                profile_name, bridge._pending_graph_document or {},
                target_window_title=meta.get('target_window_title', ''),
                focus_policy=meta.get('focus_policy', 'pause_until_focused'),
                confirmation_mode=meta.get('confirmation_mode', False),
                target_executable=meta.get('target_executable', ''),
            )
            bridge.dirty = False
        except ProfileError as exc:
            QtWidgets.QMessageBox.warning(None, 'Save Failed', str(exc))
            return

    result = bridge.run_saved_profile(profile_name)
    if not result['ok']:
        QtWidgets.QMessageBox.warning(None, 'Run', result['error'])


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)  # noqa: F841
    profile_manager = ProfileManager(PROFILES_ROOT)

    dev = find_device()
    hid_link = HidLink(dev) if dev is not None else None
    run_controller = RunController(hid_link)
    pick_controller = PickController()
    bridge = WebBridge(profile_manager, run_controller, pick_controller)

    if hid_link is not None:
        hid_link.toggle_received.connect(lambda: _on_ssm_tog(bridge, run_controller, profile_manager))
        hid_link.confirm_received.connect(run_controller.confirm)
        hid_link.start()

    window = webview.create_window(
        'Screen Scan Macro',
        url=INDEX_HTML,
        js_api=bridge,
        width=1400, height=900,
    )
    window.events.closing += lambda: _confirm_close(bridge)
    webview.start(gui='qt')


if __name__ == '__main__':
    main()
