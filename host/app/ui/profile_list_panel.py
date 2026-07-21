from PyQt5 import QtCore, QtWidgets


class ProfileListPanel(QtWidgets.QWidget):
    """Sidebar listing profiles. Emits requests only — actual profile
    mutation (via ProfileManager) and any "unsaved changes" confirmation
    happens in MainWindow, which then calls set_profiles()/select() to
    reflect the outcome."""

    selection_requested = QtCore.pyqtSignal(str)
    new_requested = QtCore.pyqtSignal()
    rename_requested = QtCore.pyqtSignal(str)
    duplicate_requested = QtCore.pyqtSignal(str)
    delete_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super(ProfileListPanel, self).__init__(parent)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)

        new_button = QtWidgets.QPushButton('New')
        rename_button = QtWidgets.QPushButton('Rename')
        duplicate_button = QtWidgets.QPushButton('Duplicate')
        delete_button = QtWidgets.QPushButton('Delete')

        new_button.clicked.connect(self.new_requested.emit)
        rename_button.clicked.connect(lambda: self._emit_for_current(self.rename_requested))
        duplicate_button.clicked.connect(lambda: self._emit_for_current(self.duplicate_requested))
        delete_button.clicked.connect(lambda: self._emit_for_current(self.delete_requested))

        button_row = QtWidgets.QHBoxLayout()
        for button in (new_button, rename_button, duplicate_button, delete_button):
            button_row.addWidget(button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel('Profiles'))
        layout.addWidget(self.list_widget)
        layout.addLayout(button_row)

        self._suppress_selection_signal = False

    def _emit_for_current(self, signal):
        item = self.list_widget.currentItem()
        if item is not None:
            signal.emit(item.text())

    def _on_current_item_changed(self, current, _previous):
        if self._suppress_selection_signal or current is None:
            return
        self.selection_requested.emit(current.text())

    def set_profiles(self, names, select=None):
        """Repopulates the list without re-triggering selection_requested."""
        self._suppress_selection_signal = True
        self.list_widget.clear()
        self.list_widget.addItems(names)
        if select is not None:
            matches = self.list_widget.findItems(select, QtCore.Qt.MatchExactly)
            if matches:
                self.list_widget.setCurrentItem(matches[0])
        self._suppress_selection_signal = False

    def current_profile(self):
        item = self.list_widget.currentItem()
        return item.text() if item is not None else None
