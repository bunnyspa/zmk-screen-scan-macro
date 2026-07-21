import os

from NodeGraphQt.widgets.node_widgets import NodeBaseWidget
from Qt import QtCore, QtGui, QtWidgets

THUMBNAIL_SIZE = (140, 100)

KEY_EDIT_STYLE = """
QKeySequenceEdit {
    background-color: rgba(40, 40, 40, 200);
    border: 1px solid rgba(100, 100, 100, 255);
    border-radius: 3px;
    color: rgba(255, 255, 255, 180);
    padding: 2px 4px;
}
QKeySequenceEdit:focus {
    border: 1px solid rgba(150, 150, 150, 255);
}
"""


class _ClickableLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super(_ClickableLabel, self).mousePressEvent(event)


class _SingleChordKeySequenceEdit(QtWidgets.QKeySequenceEdit):
    """QKeySequenceEdit normally accumulates up to 4 chords when you press
    several key combos in a row (built for multi-key shortcuts like
    Ctrl+K, Ctrl+S). This field represents a single key/combo, so each new
    keypress should replace whatever was captured, not append to it - and
    this Qt version doesn't expose setMaximumSequenceLength() to enforce
    that natively, so it's done by clearing right before each keypress."""

    def keyPressEvent(self, event):
        if self.keySequence().count() >= 1:
            self.clear()
        super(_SingleChordKeySequenceEdit, self).keyPressEvent(event)


class NodeKeySequenceEdit(NodeBaseWidget):
    """Captures a key/combo by focus + keypress (Qt's QKeySequenceEdit)
    instead of the user typing a key-name string by hand. Value is stored
    lowercase and '+'-joined (e.g. 'ctrl+shift+a') to match the key-name
    convention the execution engine expects. Multi-chord entry is disabled
    (see _SingleChordKeySequenceEdit); the get_value() truncation below is a
    defense-in-depth fallback in case a multi-chord value ever arrives some
    other way (paste, programmatic set)."""

    def __init__(self, parent=None, name='', label=''):
        super(NodeKeySequenceEdit, self).__init__(parent, name, label)
        self._key_edit = _SingleChordKeySequenceEdit()
        self._key_edit.setStyleSheet(KEY_EDIT_STYLE)
        self._key_edit.keySequenceChanged.connect(self.on_value_changed)
        self.set_custom_widget(self._key_edit)

    @property
    def type_(self):
        return 'KeySequenceNodeWidget'

    def get_value(self):
        text = self._key_edit.keySequence().toString()
        return text.split(', ')[0].lower() if text else ''

    def set_value(self, text):
        if text != self.get_value():
            self._key_edit.setKeySequence(QtGui.QKeySequence(text or ''))
            self.on_value_changed()


class NodeImageThumbnail(NodeBaseWidget):
    """Read-only preview of a reference image, embedded in a node. Shows the
    processed (cropped-to-content, mask-as-alpha) reference image that's
    actually used for matching; clicking it pops up the original full
    uploaded image for reference. Not wired to a text field's value_changed
    signal - the owning node calls set_value()/set_full_image_path()
    directly whenever the backing paths change (on browse, or after a
    profile reload)."""

    def __init__(self, parent=None, name='', label=''):
        super(NodeImageThumbnail, self).__init__(parent, name, label)
        self._path = ''
        self._full_path = ''
        self._image_label = _ClickableLabel('No image selected')
        self._image_label.setFixedSize(*THUMBNAIL_SIZE)
        self._image_label.setAlignment(QtCore.Qt.AlignCenter)
        self._image_label.setWordWrap(True)
        self._image_label.setStyleSheet(
            'background-color: rgba(0, 0, 0, 120);'
            'border: 1px solid rgba(255, 255, 255, 60);'
            'color: rgba(255, 255, 255, 120);'
        )
        self._image_label.clicked.connect(self._show_full_image_popup)
        self.set_custom_widget(self._image_label)

    @property
    def type_(self):
        return 'ImageThumbnailNodeWidget'

    def get_value(self):
        # Deliberately not self._path: this widget is a derived display only
        # (see DecisionNode.resolve_thumbnail), and self._path is a local
        # absolute path that shouldn't leak into the portable profile JSON.
        return ''

    def set_value(self, path):
        self._path = path or ''
        pixmap = QtGui.QPixmap(self._path) if self._path and os.path.exists(self._path) else None
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                *THUMBNAIL_SIZE,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)
            self._image_label.setText('')
        else:
            self._image_label.setPixmap(QtGui.QPixmap())
            self._image_label.setText('No image selected')

    def set_full_image_path(self, path):
        self._full_path = path or ''

    def _show_full_image_popup(self):
        if not self._full_path or not os.path.exists(self._full_path):
            return
        pixmap = QtGui.QPixmap(self._full_path)
        if pixmap.isNull():
            return

        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            max_size = screen.availableSize() * 0.8
            if pixmap.width() > max_size.width() or pixmap.height() > max_size.height():
                pixmap = pixmap.scaled(
                    max_size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation,
                )

        dialog = QtWidgets.QDialog(QtWidgets.QApplication.activeWindow())
        dialog.setWindowTitle('Reference Image')
        label = QtWidgets.QLabel()
        label.setPixmap(pixmap)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(label)
        dialog.exec_()
