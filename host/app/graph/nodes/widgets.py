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


class NodeNumberSpinBox(NodeBaseWidget):
    """QSpinBox/QDoubleSpinBox - enforces a numeric value and range at the
    widget level (unlike a text field, which only rejects a bad value once
    the engine tries to parse it). Unlike NodeGraphQt's own NodeSpinBox,
    get_value()/set_value() work with real int/float rather than str, and
    set_value() tolerates a string - a profile saved before this field was
    a spinbox stored it as text (e.g. '150')."""

    def __init__(self, parent=None, name='', label='', value=0, min_value=0,
                 max_value=100, double=False):
        super(NodeNumberSpinBox, self).__init__(parent, name, label)
        self._double = double
        spin_box = QtWidgets.QDoubleSpinBox() if double else QtWidgets.QSpinBox()
        spin_box.setRange(min_value, max_value)
        spin_box.setValue(value)
        spin_box.setAlignment(QtCore.Qt.AlignCenter)
        spin_box.editingFinished.connect(self.on_value_changed)
        self.set_custom_widget(spin_box)

    @property
    def type_(self):
        return 'NumberSpinBoxNodeWidget'

    def get_value(self):
        return self.get_custom_widget().value()

    def set_value(self, value):
        if value in (None, ''):
            return
        coerced = float(value) if self._double else int(float(value))
        if coerced != self.get_value():
            self.get_custom_widget().setValue(coerced)
            self.on_value_changed()


STRIP_THUMBNAIL_SIZE = (32, 32)
EDITOR_THUMBNAIL_SIZE = (90, 90)


class NodeImageStrip(NodeBaseWidget):
    """Compact read-only horizontal strip of small thumbnails shown inline
    on a DecisionNode, one per uploaded image in match-priority order (see
    DecisionNode._sync_output_ports). Editing (add/delete/reorder/show
    region) happens in a separate ImageEntryEditorDialog opened via
    "Edit Images..." here, not inline - see that class's docstring for why
    reordering specifically can't live in an embedded node widget.

    Only a thin UI shell: DecisionNode owns the actual 'images' property
    and drives this widget via set_entries(); interaction is read back out
    via on_edit. This widget's own backing property is never read (see
    get_value()), same convention as NodeImageThumbnail."""

    def __init__(self, parent=None, name='', label=''):
        super(NodeImageStrip, self).__init__(parent, name, label)
        self.on_edit = None  # callback()

        container = QtWidgets.QWidget()
        # Expanding, not a fixed/minimum width: lets the container fill
        # whatever width the node actually lays out to (driven by its
        # widest sibling widget, e.g. the "Poll Interval (ms)" label
        # below) instead of guessing a number - the strip's own natural
        # content width (a couple of small thumbnails) is well under
        # that, which is why it needs centering rather than left-alignment
        # to not look stranded on one side of the wider node body.
        container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        outer = QtWidgets.QVBoxLayout(container)
        outer.setContentsMargins(2, 2, 2, 2)

        self._strip = QtWidgets.QWidget()
        self._strip_layout = QtWidgets.QHBoxLayout(self._strip)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._strip_layout.setSpacing(2)
        self._strip.setFixedHeight(STRIP_THUMBNAIL_SIZE[1] + 4)
        outer.addWidget(self._strip, 0, QtCore.Qt.AlignHCenter)

        edit_button = QtWidgets.QPushButton('Edit Images...')
        edit_button.clicked.connect(lambda: self.on_edit() if self.on_edit else None)
        outer.addWidget(edit_button)

        self.set_custom_widget(container)
        self.set_entries([])

    @property
    def type_(self):
        return 'ImageStripNodeWidget'

    def get_value(self):
        # Deliberately not the images list: DecisionNode manages that itself
        # via its own 'images' property (see class docstring).
        return ''

    def set_value(self, value):
        pass

    def set_entries(self, thumbnail_abs_paths):
        """thumbnail_abs_paths: ordered list of resolved thumbnail paths,
        one per image entry. Rebuilds every thumbnail from scratch -
        simplest correct option since this widget never needs to
        distinguish an in-place edit from a full replace (DecisionNode
        calls this after every add/delete/reorder)."""
        while self._strip_layout.count():
            item = self._strip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not thumbnail_abs_paths:
            empty_label = QtWidgets.QLabel('No images')
            empty_label.setStyleSheet('color: rgba(255, 255, 255, 120);')
            self._strip_layout.addWidget(empty_label)
            return

        for path in thumbnail_abs_paths:
            label = QtWidgets.QLabel()
            label.setFixedSize(*STRIP_THUMBNAIL_SIZE)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setStyleSheet(
                'background-color: rgba(0, 0, 0, 120);'
                'border: 1px solid rgba(255, 255, 255, 60);'
            )
            pixmap = QtGui.QPixmap(path) if path and os.path.exists(path) else None
            if pixmap and not pixmap.isNull():
                label.setPixmap(pixmap.scaled(
                    *STRIP_THUMBNAIL_SIZE, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation,
                ))
            self._strip_layout.addWidget(label)
        # No trailing stretch: the strip's sizeHint should stay exactly
        # the natural content width (thumbnails + spacing) so that
        # outer.addWidget(self._strip, alignment=AlignHCenter) in
        # __init__ actually centers it within the wider container instead
        # of the strip itself first expanding to fill all available width.


_DELETE_TILE_ROLE = 'add'  # UserRole value marking the trailing '+' tile (not a real image index)
_X_OVERLAY_SIZE = 16
# Horizontal margin needs to comfortably fit a move-arrow badge (see
# _MOVE_ARROW_WIDTH) fully outside the icon, in the margin itself - not
# straddling the icon's edge (which was overlapping the picture) and not
# wide enough to bleed into a neighboring cell (which would make it hit-
# test as the wrong entry). Vertical margin has no equivalent constraint
# (nothing sits above/below the icon that needs clearance from a
# neighboring row), so it's kept separate and set to 0.
_TILE_MARGIN_H = 16
_TILE_MARGIN_V = 4
_LABEL_HEIGHT = 18  # space reserved below the icon for the priority-number label


def _icon_rect(cell_rect):
    """Where the (fixed-size, pre-padded-to-square) icon sits within a
    grid cell - top-left plus a constant margin, always, regardless of
    cell size. This is the one source of truth for icon placement; the
    label and the (X) overlay are both positioned relative to it (see
    _ImageEntryDelegate.paint and _x_overlay_rect) instead of independently
    guessing at Qt's own icon/text layout, which is what produced
    inconsistent-looking margins before."""
    return QtCore.QRect(
        cell_rect.left() + _TILE_MARGIN_H, cell_rect.top() + _TILE_MARGIN_V,
        EDITOR_THUMBNAIL_SIZE[0], EDITOR_THUMBNAIL_SIZE[1],
    )


def _x_overlay_rect(icon_rect):
    # Fully within the label row (below the icon, not straddling the
    # icon/label boundary), at its right edge - next to the centered
    # priority-number text without overlapping the picture at all.
    label_top = icon_rect.bottom() + 2
    label_center_y = label_top + _LABEL_HEIGHT // 2
    return QtCore.QRect(
        icon_rect.right() - _X_OVERLAY_SIZE,
        label_center_y - _X_OVERLAY_SIZE // 2,
        _X_OVERLAY_SIZE, _X_OVERLAY_SIZE,
    )


_MOVE_ARROW_HEIGHT = 16
_MOVE_ARROW_WIDTH = _MOVE_ARROW_HEIGHT // 2  # 8 - see _paint_move_arrow: this is exactly the
                                              # depth a right-angle apex needs for this height,
                                              # so the triangle fills the rect with no wasted space.


def _move_arrow_rect(icon_rect, side):
    """Badge centered within the tile's own margin band (the space between
    the icon's edge and the cell's edge) - not touching the icon (read as
    glued to the thumbnail) and, since it's centered within
    _TILE_MARGIN_H rather than flush against either boundary, never
    reaching far enough to bleed into a neighboring tile's cell (which
    would hit-test as the wrong entry)."""
    gap = (_TILE_MARGIN_H - _MOVE_ARROW_WIDTH) // 2
    x = icon_rect.left() - gap - _MOVE_ARROW_WIDTH if side == 'left' else icon_rect.right() + gap
    return QtCore.QRect(
        x, icon_rect.center().y() - _MOVE_ARROW_HEIGHT // 2,
        _MOVE_ARROW_WIDTH, _MOVE_ARROW_HEIGHT,
    )


def _paint_move_arrow(painter, rect, side):
    # A genuine right triangle, right angle at the apex: base spans the
    # full height at the "back" edge, apex extends out from the base's
    # midpoint by half the base length. That specific 1:2 (depth:height)
    # proportion is what makes the apex angle exactly 90 degrees - e.g. on
    # a 1w x 2h canvas with the back edge at x=0, the base runs (0,0) to
    # (0,2) and the apex sits at (1,1); the vectors from the apex to each
    # base corner are (-1,-1) and (-1,1), whose dot product is 0.
    depth = rect.height() // 2
    mid_y = rect.center().y()
    if side == 'right':
        back_x, apex_x = rect.left(), rect.left() + depth
    else:
        back_x, apex_x = rect.right(), rect.right() - depth
    points = [
        QtCore.QPoint(back_x, rect.top()), QtCore.QPoint(back_x, rect.bottom()),
        QtCore.QPoint(apex_x, mid_y),
    ]

    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setBrush(QtGui.QColor(255, 255, 255, 220))
    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 160), 1))
    painter.drawPolygon(QtGui.QPolygon(points))
    painter.restore()


def _grid_size():
    return QtCore.QSize(
        EDITOR_THUMBNAIL_SIZE[0] + 2 * _TILE_MARGIN_H,
        EDITOR_THUMBNAIL_SIZE[1] + 2 * _TILE_MARGIN_V + _LABEL_HEIGHT,
    )


def _pad_to_square_icon(pixmap, size):
    """Scales `pixmap` to fit within `size` (preserving aspect ratio, same
    as before) and centers it on an otherwise-transparent square canvas of
    exactly `size` - without this, a portrait crop and a landscape crop
    render at different cell widths in the icon grid (only their heights
    or widths respectively get bounded to `size`), which looks uneven next
    to the uniformly-square '+' tile."""
    canvas = QtGui.QPixmap(*size)
    canvas.fill(QtCore.Qt.transparent)
    scaled = pixmap.scaled(*size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    painter = QtGui.QPainter(canvas)
    painter.drawPixmap((size[0] - scaled.width()) // 2, (size[1] - scaled.height()) // 2, scaled)
    painter.end()
    return QtGui.QIcon(canvas)


def _make_plus_icon(size):
    pixmap = QtGui.QPixmap(*size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 160), 4))
    cx, cy = size[0] // 2, size[1] // 2
    arm = min(size) // 3
    painter.drawLine(cx - arm, cy, cx + arm, cy)
    painter.drawLine(cx, cy - arm, cx, cy + arm)
    painter.end()
    return QtGui.QIcon(pixmap)


class _ImageEntryDelegate(QtWidgets.QStyledItemDelegate):
    """Fully custom item painting: the icon and its label are placed at a
    fixed pixel offset from the cell's own top-left corner (see
    _icon_rect), not through QStyledItemDelegate's built-in icon/text
    layout. That built-in layout follows the view's viewMode() (icon
    top-and-centered for IconMode, icon left-of-text for ListMode) and
    centers the icon within however much leftover space the grid cell
    has - which is what produced inconsistent-looking margins here, since
    this view is deliberately ListMode for its more reliable InternalMove
    drag reordering (see ImageEntryEditorDialog's docstring) while still
    wanting an IconMode-style tile look. Painting at a fixed offset
    ourselves keeps every tile pixel-identical regardless of view mode.

    Also paints the (X) close-button overlay on every real image entry's
    icon - the trailing '+' tile (see _DELETE_TILE_ROLE) gets no overlay,
    since it isn't deletable - and, on whichever real entry is currently
    selected, left/right move-arrow badges (hidden at whichever end has
    nowhere to move to). Only paints; click hit-testing against all of
    these rects happens in _EntryListWidget.mousePressEvent, since a
    delegate doesn't receive input events.

    sizeHint() is likewise forced to a fixed size: QStyledItemDelegate's
    default sizeHint() is text-metric-based (a longer label like '+ Add'
    measures wider than '1'), and that per-item size - not setGridSize() -
    is what the view actually uses for each item's own occupied rect, so
    without this override, items overlap their neighbors and the row
    wraps earlier or later than the grid size implies."""

    def sizeHint(self, option, index):
        return _grid_size()

    def paint(self, painter, option, index):
        painter.save()

        # Selection/hover background only, via the current style (so it
        # still matches the app's theme) - icon and text are blanked out
        # here since we paint them ourselves below.
        style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
        blank_opt = QtWidgets.QStyleOptionViewItem(option)
        blank_opt.icon = QtGui.QIcon()
        blank_opt.text = ''
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, blank_opt, painter, option.widget)

        icon_rect = _icon_rect(option.rect)
        icon = index.data(QtCore.Qt.DecorationRole)
        if icon:
            icon.paint(painter, icon_rect)

        is_selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        painter.setPen(
            option.palette.highlightedText().color() if is_selected else option.palette.text().color()
        )
        label_rect = QtCore.QRect(
            option.rect.left(), icon_rect.bottom() + 2, option.rect.width(), _LABEL_HEIGHT,
        )
        painter.drawText(label_rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, index.data(QtCore.Qt.DisplayRole) or '')

        this_role = index.data(QtCore.Qt.UserRole)
        if this_role != _DELETE_TILE_ROLE:
            x_rect = _x_overlay_rect(icon_rect)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtGui.QPen(QtGui.QColor(220, 30, 30), 3))
            painter.drawLine(x_rect.topLeft() + QtCore.QPoint(3, 3), x_rect.bottomRight() - QtCore.QPoint(3, 3))
            painter.drawLine(x_rect.topRight() + QtCore.QPoint(-3, 3), x_rect.bottomLeft() + QtCore.QPoint(3, -3))

            if is_selected:
                total_real = index.model().rowCount() - 1  # every row except the trailing '+' tile
                if this_role > 0:
                    _paint_move_arrow(painter, _move_arrow_rect(icon_rect, 'left'), 'left')
                if this_role < total_real - 1:
                    _paint_move_arrow(painter, _move_arrow_rect(icon_rect, 'right'), 'right')

        painter.restore()


class _EntryListWidget(QtWidgets.QListWidget):
    """QListWidget with every interaction baked into mousePressEvent
    (rather than separate buttons, and deliberately not drag-and-drop -
    see ImageEntryEditorDialog's docstring for why reordering is click-
    only): clicking the trailing '+' tile requests a new image, clicking
    the (X) overlay on a real entry requests deleting it, and clicking a
    move-arrow badge on the *selected* entry (see _ImageEntryDelegate)
    requests moving it one position left or right."""

    add_requested = QtCore.Signal()
    delete_requested = QtCore.Signal(int)
    move_requested = QtCore.Signal(int, int)  # (index, delta) - delta is -1 or +1

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            item = self.itemAt(event.pos())
            if item is not None:
                role = item.data(QtCore.Qt.UserRole)
                if role == _DELETE_TILE_ROLE:
                    self.add_requested.emit()
                    return
                if role is not None:
                    icon_rect = _icon_rect(self.visualItemRect(item))
                    if _x_overlay_rect(icon_rect).contains(event.pos()):
                        self.delete_requested.emit(role)
                        return
                    if item.isSelected():
                        if role > 0 and _move_arrow_rect(icon_rect, 'left').contains(event.pos()):
                            self.move_requested.emit(role, -1)
                            return
                        total_real = sum(
                            1 for i in range(self.count())
                            if self.item(i).data(QtCore.Qt.UserRole) != _DELETE_TILE_ROLE
                        )
                        if role < total_real - 1 and _move_arrow_rect(icon_rect, 'right').contains(event.pos()):
                            self.move_requested.emit(role, 1)
                            return
        super(_EntryListWidget, self).mousePressEvent(event)


class ImageEntryEditorDialog(QtWidgets.QDialog):
    """Real top-level window (deliberately NOT a NodeGraphQt-embedded
    widget) for managing a DecisionNode's OR-matched image list: add
    (click the trailing '+' tile), delete (click the (X) on a thumbnail),
    show-region, and reorder (click the left/right move-arrow on the
    selected thumbnail - see _ImageEntryDelegate/_EntryListWidget).

    Reordering is deliberately click-only, not drag-and-drop: an earlier
    version used QAbstractItemView.InternalMove drag reordering here, but
    it surfaced a series of distinct, hard-to-verify Qt/PyQt5 drag-and-drop
    bugs one at a time under real interactive testing (IconMode's
    InternalMove being long-standing-buggy upstream; mutating the item
    list synchronously from inside dropEvent corrupting the view;
    Qt's own post-drop bookkeeping on the source row; dragEnterEvent's
    default drop-target validity check rejecting the drag independently of
    our own dragMoveEvent override) - each fixed in turn, but with no way
    to drive a real mouse-drag gesture in a headless dev environment to
    confirm any of it firsthand. Buttons/clicks have been reliable
    throughout (this dialog's Add/Delete/Show Region all are and always
    were plain clicks), so reordering was moved onto the same footing
    rather than continuing to chase Qt's DnD internals blind.

    Each row's thumbnail is the QListWidgetItem's own icon, not a widget
    attached via setItemWidget() - purely a leftover of how this was first
    built (icon data needing to survive a drag), but no reason to change
    now that it works.

    Only a thin UI shell: DecisionNode owns the actual 'images' property
    and all port/connection bookkeeping, driving this dialog via
    set_entries() and reading interaction back out via the on_* callbacks."""

    def __init__(self, parent=None):
        super(ImageEntryEditorDialog, self).__init__(parent)
        self.setWindowTitle('Edit Reference Images')
        self.on_add = None            # callback()
        self.on_delete = None         # callback(index)
        self.on_show_region = None    # callback(index)
        self.on_move = None           # callback(index, delta) - delta is -1 or +1

        layout = QtWidgets.QVBoxLayout(self)

        self._list = _EntryListWidget()
        # ListMode, not IconMode: kept from when reordering used
        # QAbstractItemView.InternalMove drag-and-drop (IconMode's is
        # long-standing-buggy upstream - see the class docstring), even
        # though that no longer applies now that reordering is click-only.
        # No reason to change it back - flow/wrapping/gridSize below
        # already make it present as a wrapping grid of square thumbnails
        # regardless of viewMode().
        self._list.setViewMode(QtWidgets.QListView.ListMode)
        self._list.setResizeMode(QtWidgets.QListView.Adjust)
        self._list.setWrapping(True)
        self._list.setFlow(QtWidgets.QListView.LeftToRight)
        self._list.setIconSize(QtCore.QSize(*EDITOR_THUMBNAIL_SIZE))
        self._list.setGridSize(_grid_size())
        self._list.setSpacing(6)
        self._list.setItemDelegate(_ImageEntryDelegate(self._list))
        self._list.setMinimumSize(
            4 * _grid_size().width(), 2 * _grid_size().height(),
        )
        self._list.currentRowChanged.connect(self._on_row_selected)
        self._list.add_requested.connect(lambda: self.on_add() if self.on_add else None)
        self._list.delete_requested.connect(lambda index: self.on_delete(index) if self.on_delete else None)
        self._list.move_requested.connect(lambda index, delta: self.on_move(index, delta) if self.on_move else None)
        layout.addWidget(self._list)

        self._show_region_button = QtWidgets.QPushButton('Show Region in Window')
        self._show_region_button.setEnabled(False)
        self._show_region_button.clicked.connect(self._on_show_region_clicked)
        layout.addWidget(self._show_region_button)

        close_button = QtWidgets.QPushButton('Close')
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def set_entries(self, thumbnail_abs_paths, select_index=None):
        """Rebuilds every item from scratch, plus a trailing '+' tile -
        simplest correct option since this dialog never needs to
        distinguish an in-place edit from a full replace (DecisionNode
        calls this after every add/delete/reorder, and whenever the
        dialog is (re)opened). select_index re-selects a specific entry
        by its new position (used after a move, so the selection follows
        the moved entry rather than staying at its old row); None (the
        default) preserves whatever row was already selected, by
        position, same as before."""
        selected_row = self._list.currentRow() if select_index is None else select_index
        self._list.blockSignals(True)
        self._list.clear()
        for i, path in enumerate(thumbnail_abs_paths):
            pixmap = QtGui.QPixmap(path) if path and os.path.exists(path) else None
            # Decision node reference crops are often much smaller than
            # EDITOR_THUMBNAIL_SIZE (a matched region can be a handful of
            # pixels), and can be any aspect ratio - _pad_to_square_icon
            # both upscales (QIcon doesn't do that on its own) and letterboxes
            # onto a fixed square canvas, so every entry occupies the same
            # cell size in the grid regardless of its original crop shape.
            icon = _pad_to_square_icon(pixmap, EDITOR_THUMBNAIL_SIZE) if pixmap and not pixmap.isNull() else QtGui.QIcon()
            item = QtWidgets.QListWidgetItem(icon, f'{i + 1}')
            item.setData(QtCore.Qt.UserRole, i)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)  # index label is display-only
            self._list.addItem(item)

        add_item = QtWidgets.QListWidgetItem(_make_plus_icon(EDITOR_THUMBNAIL_SIZE), '+ Add')
        add_item.setData(QtCore.Qt.UserRole, _DELETE_TILE_ROLE)
        add_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self._list.addItem(add_item)

        self._list.blockSignals(False)
        if 0 <= selected_row < len(thumbnail_abs_paths):
            self._list.setCurrentRow(selected_row)
        else:
            self._on_row_selected(-1)

    def _on_row_selected(self, row):
        item = self._list.item(row) if row >= 0 else None
        has_selection = item is not None and item.data(QtCore.Qt.UserRole) != _DELETE_TILE_ROLE
        self._show_region_button.setEnabled(has_selection)

    def _on_show_region_clicked(self):
        row = self._list.currentRow()
        if self.on_show_region and row >= 0:
            self.on_show_region(row)


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
