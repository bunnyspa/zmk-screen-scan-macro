import os
import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QAction

from .graph import serialization
from .graph.graph_widget import GraphWidget
from .graph.nodes.action_node import ACTION_KEY_PRESS, ActionNode
from .graph.nodes.decision_node import EVAL_MODE_BRANCH, DecisionNode
from .graph.nodes.wait_node import WaitNode
from .profiles.profile_manager import ProfileError, ProfileManager
from .ui.overlay_controller import OverlayController
from .ui.overlays import RegionHighlightOverlay
from .ui.profile_list_panel import ProfileListPanel
from .hid_link import HidLink, find_device
from .window_enum import list_visible_windows

# engine/ deliberately stays a sibling of app/ under host/, not nested one
# level deeper - see the design plan for why. Same process either way; this
# import is what makes it actually one running program, not two.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.command import HidCommandSink  # noqa: E402
from engine.cursor import find_window  # noqa: E402
from engine.focus import FOCUS_POLICY_FOCUS_AND_RESUME, FOCUS_POLICY_PAUSE_UNTIL_FOCUSED  # noqa: E402
from engine.runner import MacroRunner  # noqa: E402
from engine.window_capture import WindowCapture  # noqa: E402

PROFILES_ROOT = os.path.join(os.getcwd(), 'profiles')
LAST_PROFILE_SETTINGS_KEY = 'last_profile'

_FOCUS_POLICY_LABELS = {
    FOCUS_POLICY_PAUSE_UNTIL_FOCUSED: 'If unfocused: pause until focused',
    FOCUS_POLICY_FOCUS_AND_RESUME: 'If unfocused: focus and resume',
}

# RegionHighlightOverlay normally auto-closes after duration_ms - in
# confirmation mode it needs to stay up until the user actually confirms,
# not on a timer, so it's given a duration far longer than any real wait
# and closed explicitly once .confirm() fires instead.
_CONFIRMATION_HIGHLIGHT_DURATION_MS = 3600_000


def _first_connected_node_id(port):
    connected = port.connected_ports()
    return connected[0].node().id if connected else None


class MainWindow(QtWidgets.QMainWindow):
    # Cross-thread marshalling for MacroRunner's confirmation-mode callbacks
    # (emitted from its background thread, connected to GUI-thread slots) -
    # same pattern as HidLink's toggle_received/confirm_received.
    _pending_click_signal = QtCore.pyqtSignal(tuple)
    _pending_key_press_signal = QtCore.pyqtSignal(str)

    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle('zmk-screen-scan-macro')
        self.resize(1400, 900)

        self.profile_manager = ProfileManager(PROFILES_ROOT)
        self.graph_widget = GraphWidget(self)
        self.current_profile = None
        self.settings = QtCore.QSettings('zmk-screen-scan-macro', 'MacroApp')

        self.profile_list_panel = ProfileListPanel()
        self.overlay_controller = OverlayController(self._current_target_window_title)

        self.macro_runner = None
        self.capture = None
        self._pending_confirmation_overlay = None
        self.hid_link = self._connect_hid()

        self._build_ui()
        self._wire_signals()
        self._refresh_profile_list()
        self._restore_last_profile()

    def closeEvent(self, event):
        if self._confirm_discard_if_dirty():
            self._stop_macro()
            if self.hid_link is not None:
                self.hid_link.stop()
            event.accept()
        else:
            event.ignore()

    # -- HID connection ---------------------------------------------------

    def _connect_hid(self):
        dev = find_device()
        if dev is None:
            return None
        link = HidLink(dev, self)
        link.toggle_received.connect(self._on_ssm_tog_received)
        link.confirm_received.connect(self._on_confirm_clicked)
        link.start()
        return link

    def _on_ssm_tog_received(self):
        """&ssm_tog is stateless (see docs/wire-protocol.md) - this app is
        the sole owner of running/stopped state, so any trigger just flips
        whatever we're currently doing."""
        self._on_run_clicked()

    # -- UI construction -----------------------------------------------

    def _build_ui(self):
        self.profile_list_panel.setMaximumWidth(280)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.profile_list_panel)
        layout.addWidget(self.graph_widget.widget, stretch=1)
        self.setCentralWidget(central)

        toolbar = self.addToolBar('Main')
        toolbar.setMovable(False)

        save_action = QAction('Save', self)
        save_action.setShortcut(QtGui.QKeySequence.Save)
        save_action.triggered.connect(self._save_current_profile)
        toolbar.addAction(save_action)

        set_start_action = QAction('Set Selected as Start', self)
        set_start_action.triggered.connect(self._set_selected_as_start)
        toolbar.addAction(set_start_action)

        toolbar.addSeparator()
        toolbar.addWidget(QtWidgets.QLabel('Target window: '))
        self.target_window_edit = QtWidgets.QComboBox()
        self.target_window_edit.setEditable(True)  # dropdown of open windows, but still typable
        self.target_window_edit.setMaximumWidth(260)
        self.target_window_edit.activated.connect(self._on_target_window_edited)
        self.target_window_edit.lineEdit().editingFinished.connect(self._on_target_window_edited)
        toolbar.addWidget(self.target_window_edit)

        refresh_windows_action = QAction('⟳', self)
        refresh_windows_action.setToolTip('Refresh window list')
        refresh_windows_action.triggered.connect(self._refresh_window_list)
        toolbar.addAction(refresh_windows_action)
        self._refresh_window_list()

        self.focus_policy_combo = QtWidgets.QComboBox()
        for policy, label in _FOCUS_POLICY_LABELS.items():
            self.focus_policy_combo.addItem(label, policy)
        self.focus_policy_combo.currentIndexChanged.connect(self._on_focus_policy_edited)
        toolbar.addWidget(self.focus_policy_combo)

        self.confirmation_mode_action = QAction('Confirmation mode', self)
        self.confirmation_mode_action.setCheckable(True)
        self.confirmation_mode_action.toggled.connect(self._on_confirmation_mode_edited)
        toolbar.addAction(self.confirmation_mode_action)

        toolbar.addSeparator()
        self.run_action = QAction('Run', self)
        self.run_action.triggered.connect(self._on_run_clicked)
        toolbar.addAction(self.run_action)

        self.confirm_action = QAction('Confirm (OK)', self)
        self.confirm_action.setToolTip(
            'Confirms a pending click/key-press in confirmation mode - same as '
            'pressing the &ssm_confirm physical key.'
        )
        self.confirm_action.triggered.connect(self._on_confirm_clicked)
        toolbar.addAction(self.confirm_action)

        toolbar.addSeparator()
        self.start_node_label = QtWidgets.QLabel('Start: (none)')
        toolbar.addWidget(self.start_node_label)

        self.pending_action_label = QtWidgets.QLabel('')
        toolbar.addWidget(self.pending_action_label)

    def _wire_signals(self):
        self.graph_widget.graph.node_created.connect(self._on_node_created)
        self.graph_widget.start_node_changed.connect(self._on_start_node_changed)
        self.graph_widget.dirty_changed.connect(self._on_dirty_changed)

        self.profile_list_panel.selection_requested.connect(self._on_profile_selection_requested)
        self.profile_list_panel.new_requested.connect(self._on_new_profile)
        self.profile_list_panel.rename_requested.connect(self._on_rename_profile)
        self.profile_list_panel.duplicate_requested.connect(self._on_duplicate_profile)
        self.profile_list_panel.delete_requested.connect(self._on_delete_profile)

        self._pending_click_signal.connect(self._show_pending_click_on_gui_thread)
        self._pending_key_press_signal.connect(self._show_pending_key_press_on_gui_thread)

    # -- node wiring -----------------------------------------------------

    def _on_node_created(self, node):
        self._wire_node(node)
        if hasattr(node, 'resolve_thumbnail'):
            node.resolve_thumbnail()

    def _wire_node(self, node):
        """Injects UI-layer dependencies (the overlay controller, current
        profile's images dir) into a node. Needed both for freshly created
        nodes (via the node_created signal) and for nodes restored by
        deserialize_session() on profile load, which does NOT emit
        node_created."""
        if hasattr(node, 'set_pick_handler'):
            node.set_pick_handler(self.overlay_controller.request_pick)
        if hasattr(node, 'set_images_dir_resolver'):
            node.set_images_dir_resolver(self._current_images_dir)

    def _current_images_dir(self):
        if self.current_profile is None:
            return None
        return self.profile_manager.images_dir(self.current_profile)

    def _current_target_window_title(self):
        if not hasattr(self, 'target_window_edit'):
            return ''
        return self.target_window_edit.currentText().strip()

    def _current_focus_policy(self):
        if not hasattr(self, 'focus_policy_combo'):
            return FOCUS_POLICY_PAUSE_UNTIL_FOCUSED
        return self.focus_policy_combo.currentData()

    def _set_focus_policy(self, focus_policy):
        index = self.focus_policy_combo.findData(focus_policy)
        self.focus_policy_combo.setCurrentIndex(index if index >= 0 else 0)

    def _current_confirmation_mode(self):
        if not hasattr(self, 'confirmation_mode_action'):
            return False
        return self.confirmation_mode_action.isChecked()

    def _set_confirmation_mode(self, confirmation_mode):
        self.confirmation_mode_action.setChecked(bool(confirmation_mode))

    def _on_confirmation_mode_edited(self, _checked):
        if self.current_profile is not None:
            self.graph_widget.mark_dirty()

    def _on_confirm_clicked(self):
        if self.macro_runner is not None:
            self.macro_runner.confirm()
        self._clear_pending_indicator()

    def _clear_pending_indicator(self):
        self.pending_action_label.setText('')
        if self._pending_confirmation_overlay is not None:
            self._pending_confirmation_overlay.close()
            self._pending_confirmation_overlay = None

    def _show_pending_click(self, screen_rect):
        """MacroRunner callback (confirmation mode) - runs on the
        MacroRunner background thread; emitting a Qt signal marshals the
        actual overlay creation onto the GUI thread automatically (same
        cross-thread pattern as HidLink's toggle_received/confirm_received)."""
        self._pending_click_signal.emit(screen_rect)

    def _show_pending_click_on_gui_thread(self, screen_rect):
        self._clear_pending_indicator()
        self.pending_action_label.setText('Pending: click (confirm to proceed)')
        self._pending_confirmation_overlay = RegionHighlightOverlay(
            screen_rect, duration_ms=_CONFIRMATION_HIGHLIGHT_DURATION_MS,
        )
        self._pending_confirmation_overlay.show()

    def _show_pending_key_press(self, key_combo):
        """MacroRunner callback (confirmation mode) - see
        _show_pending_click's threading note."""
        self._pending_key_press_signal.emit(key_combo)

    def _show_pending_key_press_on_gui_thread(self, key_combo):
        self._clear_pending_indicator()
        self.pending_action_label.setText(f'Pending: press "{key_combo}" (confirm to proceed)')

    def _refresh_window_list(self):
        """Repopulates the dropdown with currently visible window titles,
        without losing whatever's already selected/typed (window lists
        change over time, e.g. the target app wasn't open yet at startup)."""
        current = self.target_window_edit.currentText()
        self.target_window_edit.blockSignals(True)
        self.target_window_edit.clear()
        self.target_window_edit.addItems(list_visible_windows())
        self.target_window_edit.setCurrentText(current)
        self.target_window_edit.blockSignals(False)

    def _on_start_node_changed(self, node):
        self.start_node_label.setText(f"Start: {node.name()}" if node is not None else 'Start: (none)')

    def _on_dirty_changed(self, dirty):
        title = 'zmk-screen-scan-macro'
        if self.current_profile is not None:
            title += f' — {self.current_profile}'
            if dirty:
                title += ' *'
        self.setWindowTitle(title)

    def _set_canvas_enabled(self, enabled):
        self.graph_widget.widget.setEnabled(enabled)

    def _on_target_window_edited(self):
        if self.current_profile is not None:
            self.graph_widget.mark_dirty()

    def _on_focus_policy_edited(self):
        if self.current_profile is not None:
            self.graph_widget.mark_dirty()

    def _restore_last_profile(self):
        """Reopens whatever profile was open when the app last closed, so
        saved progress doesn't appear to vanish on restart."""
        last_profile = self.settings.value(LAST_PROFILE_SETTINGS_KEY)
        if last_profile and self.profile_manager.exists(last_profile):
            self._load_profile(last_profile)
            self._refresh_profile_list(select=last_profile)
        else:
            self._set_canvas_enabled(False)

    def _set_selected_as_start(self):
        selected = self.graph_widget.graph.selected_nodes()
        if not selected:
            QtWidgets.QMessageBox.information(self, 'Set Start Node', 'Select a node first.')
            return
        self.graph_widget.set_start_node(selected[0])

    # -- Run / Stop --------------------------------------------------------

    def _on_run_clicked(self):
        if self.macro_runner is not None:
            self._stop_macro()
        else:
            self._start_macro()

    def _build_engine_graph(self):
        """Translates the live NodeGraphQt graph into the plain-JSON schema
        MacroRunner consumes (see engine/runner.py's module docstring).
        Runs against whatever's currently in the editor, not a saved
        profile.json - the simplest option, avoids a save-then-reload dance
        (see the design plan's Phase 3 notes)."""
        start_node = None
        for node in self.graph_widget.graph.all_nodes():
            if hasattr(node, 'is_start_node') and node.is_start_node():
                start_node = node
                break
        if start_node is None:
            return None

        engine_nodes = {}
        for node in self.graph_widget.graph.all_nodes():
            if isinstance(node, ActionNode):
                entry = {'out': _first_connected_node_id(node.get_output('out'))}
                if node.get_property('action_type') == ACTION_KEY_PRESS:
                    entry['type'] = 'action'
                    entry['action_type'] = 'key_press'
                    entry['key_combo'] = node.get_property('key_combo')
                else:
                    x, y, w, h = node.get_region()
                    entry['type'] = 'action'
                    entry['action_type'] = 'click'
                    entry['click_rect'] = [x, y, w, h]
                    entry['mouse_button'] = node.get_property('mouse_button').lower()
                engine_nodes[node.id] = entry
            elif isinstance(node, WaitNode):
                engine_nodes[node.id] = {
                    'type': 'wait',
                    'duration_ms': int(node.get_property('duration_ms')),
                    'out': _first_connected_node_id(node.get_output('out')),
                }
            elif isinstance(node, DecisionNode):
                mode = node.get_property('evaluation_mode')
                entry = {
                    'type': 'decision',
                    'reference_path': node.get_property('reference_path'),
                    'region': list(node.get_region()),
                    'match_threshold': float(node.get_property('match_threshold')),
                    'evaluation_mode': 'branch' if mode == EVAL_MODE_BRANCH else 'wait_until_true',
                    'true': _first_connected_node_id(node.get_output('true')),
                }
                if entry['evaluation_mode'] == 'branch':
                    entry['false'] = _first_connected_node_id(node.get_output('false'))
                engine_nodes[node.id] = entry

        return {'start_node': start_node.id, 'nodes': engine_nodes}

    def _start_macro(self):
        if self.hid_link is None:
            QtWidgets.QMessageBox.warning(self, 'Run', 'No Raw HID device connected.')
            return

        engine_graph = self._build_engine_graph()
        if engine_graph is None:
            QtWidgets.QMessageBox.information(self, 'Run', 'Set a start node first.')
            return

        target_window_title = self._current_target_window_title()
        hwnd = find_window(target_window_title) if target_window_title else None
        if not hwnd:
            QtWidgets.QMessageBox.warning(
                self, 'Run', f"Target window '{target_window_title}' not found.",
            )
            return

        profile_dir = (
            self.profile_manager.profile_dir(self.current_profile)
            if self.current_profile is not None else '.'
        )

        self.capture = WindowCapture(target_window_title)
        self.capture.start()

        sink = HidCommandSink(self.hid_link)
        self.macro_runner = MacroRunner(
            engine_graph, self.capture, sink, hwnd=hwnd, profile_dir=profile_dir,
            focus_policy=self._current_focus_policy(),
            confirmation_mode=self._current_confirmation_mode(),
            show_pending_click=self._show_pending_click,
            show_pending_key_press=self._show_pending_key_press,
        )
        self.macro_runner.start()
        self.run_action.setText('Stop')

    def _stop_macro(self):
        if self.macro_runner is not None:
            self.macro_runner.stop()
            self.macro_runner = None
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self.run_action.setText('Run')
        self._clear_pending_indicator()

    # -- profile list handling -------------------------------------------

    def _refresh_profile_list(self, select=None):
        self.profile_list_panel.set_profiles(self.profile_manager.list_profiles(), select=select)

    def _confirm_discard_if_dirty(self):
        """Returns True if it's OK to proceed (no unsaved changes, or user
        chose to discard/save them)."""
        if not self.graph_widget.is_dirty() or self.current_profile is None:
            return True
        result = QtWidgets.QMessageBox.question(
            self, 'Unsaved Changes',
            f"Save changes to '{self.current_profile}' before continuing?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
        )
        if result == QtWidgets.QMessageBox.Save:
            self._save_current_profile()
            return True
        return result == QtWidgets.QMessageBox.Discard

    def _load_profile(self, name):
        target_window_title, focus_policy, confirmation_mode = serialization.load_profile_into_graph(
            self.profile_manager, self.graph_widget, name,
        )
        self.current_profile = name
        self.target_window_edit.setCurrentText(target_window_title)
        self._set_focus_policy(focus_policy)
        self._set_confirmation_mode(confirmation_mode)
        for node in self.graph_widget.graph.all_nodes():
            self._wire_node(node)
            if hasattr(node, 'resolve_thumbnail'):
                node.resolve_thumbnail()
        self._on_dirty_changed(False)
        self._set_canvas_enabled(True)
        self.settings.setValue(LAST_PROFILE_SETTINGS_KEY, name)

    def _save_current_profile(self):
        if self.current_profile is None:
            return
        try:
            serialization.save_graph_to_profile(
                self.profile_manager, self.graph_widget, self.current_profile,
                self._current_target_window_title(), self._current_focus_policy(),
                self._current_confirmation_mode(),
            )
        except ProfileError as exc:
            QtWidgets.QMessageBox.warning(self, 'Save Failed', str(exc))

    def _on_profile_selection_requested(self, name):
        if name == self.current_profile:
            return
        if not self._confirm_discard_if_dirty():
            self._refresh_profile_list(select=self.current_profile)
            return
        self._load_profile(name)
        self._refresh_profile_list(select=name)

    def _on_new_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(self, 'New Profile', 'Profile name:')
        if not ok or not name.strip():
            return
        if not self._confirm_discard_if_dirty():
            return
        try:
            created_name = self.profile_manager.create(name.strip())
        except ProfileError as exc:
            QtWidgets.QMessageBox.warning(self, 'New Profile Failed', str(exc))
            return
        self._load_profile(created_name)
        self._refresh_profile_list(select=created_name)

    def _on_rename_profile(self, old_name):
        new_name, ok = QtWidgets.QInputDialog.getText(self, 'Rename Profile', 'New name:', text=old_name)
        if not ok or not new_name.strip():
            return
        try:
            renamed = self.profile_manager.rename(old_name, new_name.strip())
        except ProfileError as exc:
            QtWidgets.QMessageBox.warning(self, 'Rename Failed', str(exc))
            return
        if self.current_profile == old_name:
            self.current_profile = renamed
            self._on_dirty_changed(self.graph_widget.is_dirty())
            self.settings.setValue(LAST_PROFILE_SETTINGS_KEY, renamed)
        self._refresh_profile_list(select=renamed)

    def _on_duplicate_profile(self, name):
        new_name, ok = QtWidgets.QInputDialog.getText(self, 'Duplicate Profile', 'New profile name:', text=f'{name} copy')
        if not ok or not new_name.strip():
            return
        try:
            duplicated = self.profile_manager.duplicate(name, new_name.strip())
        except ProfileError as exc:
            QtWidgets.QMessageBox.warning(self, 'Duplicate Failed', str(exc))
            return
        self._refresh_profile_list(select=duplicated)

    def _on_delete_profile(self, name):
        result = QtWidgets.QMessageBox.question(
            self, 'Delete Profile', f"Delete profile '{name}'? This cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if result != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.profile_manager.delete(name)
        except ProfileError as exc:
            QtWidgets.QMessageBox.warning(self, 'Delete Failed', str(exc))
            return
        if self.current_profile == name:
            self.current_profile = None
            self.graph_widget.new_graph()
            self.target_window_edit.setCurrentText('')
            self._set_focus_policy(FOCUS_POLICY_PAUSE_UNTIL_FOCUSED)
            self._set_confirmation_mode(False)
            self._set_canvas_enabled(False)
            self.settings.remove(LAST_PROFILE_SETTINGS_KEY)
        self._refresh_profile_list(select=self.current_profile)
