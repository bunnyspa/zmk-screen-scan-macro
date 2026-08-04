import os
import shutil
import time

import cv2
from Qt import QtWidgets

from .base import MacroBaseNode
from .reference_processing import MaskDetectionError, process_masked_reference
from .widgets import ImageEntryEditorDialog, NodeImageStrip

EVAL_MODE_BRANCH = 'Branch (True/False)'
EVAL_MODE_WAIT = 'Wait Until True'

IMAGES_PROP = 'images'


class DecisionNode(MacroBaseNode):
    """Condition is an OR match across one or more uploaded reference
    images: the host checks each in upload order and takes the first one
    that matches (see engine/runner.py::_run_decision) - one output port
    per image, named by its 1-based position ("1", "2", ...), plus 'false'.
    match_threshold is shared across every image on the node, not per-image.

    Each image is browsed in and masked exactly like the old single-image
    node (see process_masked_reference) and stored as one entry in the
    'images' property - a list of {reference_path, reference_full_path,
    region_x/y/w/h} dicts, in match-priority order. That order also
    determines port numbering: reordering an entry (via the left/right
    move-arrow buttons in the ImageEntryEditorDialog opened from the
    node's "Edit Images..." button - a real top-level window, not this
    node's own embedded widget, per that class's docstring) carries its
    existing connections to the new port position with it (see
    _sync_output_ports) rather than leaving them behind on the old port
    number.

    `evaluation_mode` controls how the execution engine treats this node:
      - Branch (True/False): evaluate every image once, take the first
        match's port, or 'false' if none matched.
      - Wait Until True: keep re-evaluating every image until any matches,
        then take that image's port. The 'false' output port is hidden
        (and disconnected) in this mode, since it's never used."""

    __identifier__ = 'macro'
    NODE_NAME = 'Decision'

    def __init__(self):
        super(DecisionNode, self).__init__()
        self.add_input('in', multi_input=True)
        self.set_port_deletion_allowed(True)
        self.add_output('false')

        self._images_dir_resolver = None
        self._editor_dialog = None
        self.create_property(IMAGES_PROP, [])

        self._strip_widget = NodeImageStrip(self.view, 'images_editor', '')
        self._strip_widget.on_edit = self._open_image_editor
        self.add_custom_widget(self._strip_widget)

        self.add_spinbox(
            'match_threshold', 'Match Threshold',
            value=0.85, min_value=0.0, max_value=1.0, double=True,
        )

        self.add_combo_menu(
            'evaluation_mode', 'Evaluation Mode',
            items=[EVAL_MODE_BRANCH, EVAL_MODE_WAIT],
        )
        self.add_spinbox(
            'poll_interval_ms', 'Poll Interval (ms)',
            value=200, min_value=10, max_value=60000,
        )

        self._update_false_port_visibility(self.get_property('evaluation_mode'))
        self._update_poll_interval_visibility(self.get_property('evaluation_mode'))

    def set_images_dir_resolver(self, resolver):
        """resolver() -> absolute path to the current profile's images/
        folder, or None if no profile is open. Injected by MainWindow so
        this node doesn't need to know about ProfileManager directly."""
        self._images_dir_resolver = resolver

    def resolve_thumbnail(self):
        """Refreshes the inline thumbnail strip (and the editor dialog, if
        open) from the current 'images' property. Needed after a profile
        load, since deserialize_session() restores properties directly on
        the model without going through set_property(), so it can't
        trigger a refresh on its own. Named to match the single-image
        convention this replaced (DecisionNode.resolve_thumbnail), even
        though it now refreshes a whole list rather than one thumbnail."""
        self._refresh_list_widget()

    def get_reference_abs_path(self, index):
        """Absolute path to the processed (cropped) reference image at
        `index` - the one the execution engine actually matches against -
        or None if `index` is out of range or no profile is open to
        resolve the path against."""
        images = self.get_property(IMAGES_PROP)
        if not (0 <= index < len(images)):
            return None
        return self._resolve_abs_path(images[index]['reference_path'])

    def get_region(self, index):
        """(x, y, w, h) of the reference image at `index` within its
        originally uploaded screenshot. (0, 0, 0, 0) if `index` is out of
        range."""
        images = self.get_property(IMAGES_PROP)
        if not (0 <= index < len(images)):
            return (0, 0, 0, 0)
        entry = images[index]
        return (
            int(entry['region_x']), int(entry['region_y']),
            int(entry['region_w']), int(entry['region_h']),
        )

    def _resolve_abs_path(self, relative_path):
        images_dir = self._images_dir_resolver() if self._images_dir_resolver else None
        if not images_dir or not relative_path:
            return None
        profile_dir = os.path.dirname(images_dir)
        return os.path.join(profile_dir, relative_path)

    def _on_add_image(self):
        parent = QtWidgets.QApplication.activeWindow()
        images_dir = self._images_dir_resolver() if self._images_dir_resolver else None
        if images_dir is None:
            QtWidgets.QMessageBox.warning(
                parent, 'No Profile',
                'Select or create a profile before choosing a reference image.',
            )
            return

        src_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent, 'Select Reference Image', '',
            'Images (*.png *.jpg *.jpeg *.bmp)',
        )
        if not src_path:
            return

        try:
            cropped_bgra, bounding_box, _kept_pixels = process_masked_reference(src_path)
        except MaskDetectionError as exc:
            QtWidgets.QMessageBox.warning(parent, 'Reference Image', str(exc))
            return

        images = self.get_property(IMAGES_PROP)
        stem = f"{self.id.replace('0x', '')}_{len(images)}_{int(time.time() * 1000)}"
        full_filename = f"{stem}_full{os.path.splitext(src_path)[1] or '.png'}"
        cropped_filename = f"{stem}_cropped.png"
        full_dest_abs = os.path.join(images_dir, full_filename)
        cropped_dest_abs = os.path.join(images_dir, cropped_filename)

        shutil.copyfile(src_path, full_dest_abs)
        cv2.imwrite(cropped_dest_abs, cropped_bgra)

        region_x, region_y, region_w, region_h = bounding_box
        old_count = len(images)
        new_images = images + [{
            'reference_path': os.path.join('images', cropped_filename),
            'reference_full_path': os.path.join('images', full_filename),
            'region_x': region_x, 'region_y': region_y,
            'region_w': region_w, 'region_h': region_h,
        }]
        self.set_property(IMAGES_PROP, new_images, push_undo=False)
        mapping = {i: i for i in range(old_count)}
        mapping[old_count] = None
        self._sync_output_ports(mapping)
        self._refresh_list_widget()

    def _on_delete_image(self, index):
        images = self.get_property(IMAGES_PROP)
        if not (0 <= index < len(images)):
            return
        new_images = images[:index] + images[index + 1:]
        mapping = {}
        for new_i in range(len(new_images)):
            mapping[new_i] = new_i if new_i < index else new_i + 1
        self.set_property(IMAGES_PROP, new_images, push_undo=False)
        self._sync_output_ports(mapping)
        self._refresh_list_widget()

    def _on_move_image(self, index, delta):
        """Swaps the entry at `index` with its neighbor `delta` away
        (delta is -1 or +1, from the editor dialog's left/right move-arrow
        buttons - see ImageEntryEditorDialog's docstring for why reordering
        is click-only rather than drag-and-drop)."""
        images = self.get_property(IMAGES_PROP)
        target = index + delta
        if not (0 <= index < len(images)) or not (0 <= target < len(images)):
            return
        new_images = list(images)
        new_images[index], new_images[target] = new_images[target], new_images[index]
        mapping = {i: i for i in range(len(images))}
        mapping[index], mapping[target] = target, index
        self.set_property(IMAGES_PROP, new_images, push_undo=False)
        self._sync_output_ports(mapping)
        self._refresh_list_widget(select_index=target)  # selection follows the moved entry, not the old row

    def _on_show_region_for_index(self, index):
        self._request_pick(f'show_region:{index}')

    def _open_image_editor(self):
        if self._editor_dialog is None:
            self._editor_dialog = ImageEntryEditorDialog(QtWidgets.QApplication.activeWindow())
            self._editor_dialog.on_add = self._on_add_image
            self._editor_dialog.on_delete = self._on_delete_image
            self._editor_dialog.on_show_region = self._on_show_region_for_index
            self._editor_dialog.on_move = self._on_move_image
        self._refresh_list_widget()
        self._editor_dialog.show()
        self._editor_dialog.raise_()
        self._editor_dialog.activateWindow()

    def _refresh_list_widget(self, select_index=None):
        images = self.get_property(IMAGES_PROP)
        thumbnails = [self.get_reference_abs_path(i) for i in range(len(images))]
        self._strip_widget.set_entries(thumbnails)
        if self._editor_dialog is not None:
            self._editor_dialog.set_entries(thumbnails, select_index=select_index)

    def _sync_output_ports(self, position_mapping):
        """Rebuilds every image-indexed output port ('1', '2', ...) plus
        'false' from scratch, in the current 'images' order, carrying over
        each port's prior connections per `position_mapping`
        ({new_index: old_index_or_None}) - a newly-added entry has no
        prior port, so its mapped value is None. Recomputing from scratch
        on every add/delete/reorder is simpler and less error-prone than
        three separate incremental rename/reconnect code paths for the
        same result."""
        num_images = len(self.get_property(IMAGES_PROP))

        prior_connections = {}
        for name, port in list(self.outputs().items()):
            prior_connections[name] = list(port.connected_ports())
            port.clear_connections(push_undo=False)
        for name in list(self.outputs().keys()):
            self.delete_output(name)

        for new_index in range(num_images):
            port = self.add_output(str(new_index + 1))
            old_index = position_mapping.get(new_index)
            if old_index is not None:
                for other in prior_connections.get(str(old_index + 1), []):
                    port.connect_to(other, push_undo=False)

        false_port = self.add_output('false')
        for other in prior_connections.get('false', []):
            false_port.connect_to(other, push_undo=False)

        self._update_false_port_visibility(self.get_property('evaluation_mode'))
        self.view.draw_node()

    def _update_false_port_visibility(self, evaluation_mode):
        false_port = self.get_output('false')
        if evaluation_mode == EVAL_MODE_WAIT:
            false_port.clear_connections(push_undo=False)
        false_port.set_visible(evaluation_mode != EVAL_MODE_WAIT, push_undo=False)

    def _update_poll_interval_visibility(self, evaluation_mode):
        """poll_interval_ms only means anything in Wait Until True mode -
        Branch mode evaluates once and never polls."""
        self.set_field_visible('poll_interval_ms', evaluation_mode == EVAL_MODE_WAIT)
        self.redraw()

    def set_property(self, name, value, push_undo=True):
        super(DecisionNode, self).set_property(name, value, push_undo=push_undo)
        if name == 'evaluation_mode':
            self._update_false_port_visibility(value)
            self._update_poll_interval_visibility(value)
